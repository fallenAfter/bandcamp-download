"""Download a purchased album as a ZIP (or a single file for tracks)."""

from __future__ import annotations

import json
import random
import re
import time
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlencode, urlparse, urlunparse

from bcdl.collection import Item
from bcdl.config import FORMAT_PREFERENCE
from bcdl.session import BandcampError, Client, dotted

UNSAFE_FILENAME = re.compile(r'[<>:"/\\|?*]')
CONTENT_DISPOSITION_NAME = re.compile(r"filename\*=UTF-8''(.+)|filename=\"?([^\";]+)\"?", re.I)


def format_order(preferred: str) -> tuple[str, ...]:
    order: list[str] = []
    for name in (preferred, *FORMAT_PREFERENCE):
        if name not in order:
            order.append(name)
    return tuple(order)


def pick_format(
    available: dict[str, dict],
    preferred: str,
) -> tuple[str, dict]:
    for name in format_order(preferred):
        entry = available.get(name) or {}
        if entry.get("url"):
            return name, entry
    for name, entry in available.items():
        if entry.get("url"):
            return name, entry
    raise BandcampError(f"No downloadable format found (offered: {sorted(available)})")


def sanitize_filename(name: str) -> str:
    cleaned = UNSAFE_FILENAME.sub("_", name).strip(" .")
    return cleaned or "download"


def album_filename(item: Item, fmt: str, extension: str = ".zip") -> str:
    stem = sanitize_filename(f"{item.band_name} - {item.item_title}")
    if fmt:
        stem = f"{stem} [{fmt}]"
    return f"{stem}{extension}"


def filename_from_disposition(header: str | None) -> str | None:
    if not header:
        return None
    match = CONTENT_DISPOSITION_NAME.search(header)
    if not match:
        return None
    value = match.group(1) or match.group(2)
    return sanitize_filename(unquote(value))


def to_stat_url(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path.replace("/download/", "/statdownload/", 1)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query[".rand"] = str(int(time.time() * 1000 * random.random()))
    return urlunparse(parsed._replace(path=path, query=urlencode(query)))


def parse_stat_body(text: str) -> dict:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise BandcampError("statdownload did not return JSON")
    return json.loads(text[start : end + 1])


def resolve_cdn_url(client: Client, format_url: str) -> str:
    if "/download/" not in urlparse(format_url).path:
        return format_url
    stat = parse_stat_body(client.get(to_stat_url(format_url)).text)
    cdn = stat.get("retry_url") or stat.get("download_url")
    if not cdn:
        raise BandcampError("statdownload response had no download URL")
    if cdn.startswith("//"):
        return f"https:{cdn}"
    return cdn


def formats_for(client: Client, item: Item) -> dict[str, dict]:
    if not item.download_page_url:
        raise BandcampError(f"{item.band_name} — {item.item_title} has no download page")
    data = client.pagedata(item.download_page_url)
    download_items = dotted(data, "download_items", default=[]) or []
    if not download_items:
        raise BandcampError(f"No download_items on the download page for {item.item_title}")
    return dotted(download_items[0], "downloads", default={}) or {}


def download_item(
    client: Client,
    item: Item,
    dest_dir: Path,
    *,
    preferred_format: str,
) -> tuple[Path, str]:
    if not item.downloadable:
        raise BandcampError(f"{item.band_name} — {item.item_title} is not downloadable")
    available = formats_for(client, item)
    fmt, entry = pick_format(available, preferred_format)
    cdn_url = resolve_cdn_url(client, entry["url"])
    dest_dir.mkdir(parents=True, exist_ok=True)
    default_name = album_filename(item, fmt)
    dest = dest_dir / default_name

    with client.http.stream("GET", cdn_url) as response:
        if response.status_code >= 400:
            raise BandcampError(f"Download failed: HTTP {response.status_code}")
        header_name = filename_from_disposition(response.headers.get("content-disposition"))
        if header_name:
            dest = dest_dir / header_name
        length = response.headers.get("content-length")
        expected = int(length) if length and length.isdigit() else None
        written = 0
        with dest.open("wb") as fh:
            for chunk in response.iter_bytes(chunk_size=64 * 1024):
                fh.write(chunk)
                written += len(chunk)
        if expected is not None and written != expected:
            dest.unlink(missing_ok=True)
            raise BandcampError(
                f"Incomplete download for {item.item_title}: got {written} of {expected} bytes"
            )
    return dest, fmt
