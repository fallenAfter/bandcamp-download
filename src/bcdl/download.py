"""Download a purchased album as a ZIP (or a single file for tracks)."""

from __future__ import annotations

import json
import random
import re
import time
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx

from bcdl.collection import Item
from bcdl.config import FORMAT_EXTENSIONS, FORMAT_PREFERENCE, KNOWN_FORMATS
from bcdl.manifest import load_manifest
from bcdl.session import BandcampError, Client, dotted

UNSAFE_FILENAME = re.compile(r'[<>:"/\\|?*]')
STAT_CALLBACK = re.compile(r"statResult\s*\(")
STAT_KEYS = ("result", "download_url", "retry_url", "url")


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


def file_extension(item: Item, fmt: str) -> str:
    if (item.item_type or "").lower() == "track":
        return FORMAT_EXTENSIONS.get(fmt, ".zip")
    return ".zip"


def album_filename(item: Item, fmt: str, extension: str | None = None) -> str:
    stem = sanitize_filename(f"{item.band_name} - {item.item_title}")
    if fmt:
        stem = f"{stem} [{fmt}]"
    return f"{stem}{extension if extension is not None else file_extension(item, fmt)}"


def existing_download(item: Item, dest_dir: Path, preferred: str) -> Path | None:
    """Return a local file for this purchase, if one is still on disk."""
    seen: set[Path] = set()
    for fmt in (*format_order(preferred), *KNOWN_FORMATS):
        path = dest_dir / album_filename(item, fmt)
        if path in seen:
            continue
        seen.add(path)
        if path.exists():
            return path
    entry = load_manifest().get("items", {}).get(item.key) or {}
    recorded = entry.get("path")
    if recorded:
        path = Path(recorded)
        if path.exists():
            return path
    return None


def to_stat_url(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path.replace("/download/", "/statdownload/", 1)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query[".rand"] = str(int(time.time() * 1000 * random.random()))
    return urlunparse(parsed._replace(path=path, query=urlencode(query)))


def _balanced_object(text: str, start: int) -> str | None:
    """Slice the brace-balanced object beginning at text[start], ignoring braces in strings."""
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def _object_starts(body: str) -> list[int]:
    """Offsets of `{` worth trying, the one following `statResult(` first."""
    starts: list[int] = []
    call = STAT_CALLBACK.search(body)
    if call:
        brace = body.find("{", call.end())
        if brace != -1:
            starts.append(brace)
    seen = set(starts)
    starts.extend(index for index, char in enumerate(body) if char == "{" and index not in seen)
    return starts


def parse_stat_body(text: str) -> dict:
    """Pull the payload out of statdownload's JSONP reply.

    The body is JavaScript, not JSON:
    `if ( window.Downloads ) { window.Downloads.statResult( {...} ) }`
    so the first brace opens the if-block rather than the payload.
    """
    body = text.strip()
    for start in _object_starts(body):
        blob = _balanced_object(body, start)
        if blob is None:
            continue
        try:
            data = json.loads(blob)
        except json.JSONDecodeError:
            continue
        # Requiring a known key keeps stray object literals in an HTML
        # error page from being mistaken for the payload.
        if isinstance(data, dict) and any(key in data for key in STAT_KEYS):
            return data
    raise BandcampError(
        "statdownload did not return a recognisable payload; "
        "the session may have expired (try `bcdl login`)"
    )


def resolve_cdn_url(client: Client, format_url: str) -> str:
    if "/download/" not in urlparse(format_url).path:
        return format_url
    stat = parse_stat_body(client.get(to_stat_url(format_url)).text)
    cdn = stat.get("retry_url") or stat.get("download_url")
    if not cdn:
        reason = stat.get("reason") or stat.get("message") or stat.get("result") or "unknown"
        raise BandcampError(f"statdownload gave no download URL (result: {reason})")
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


def stream_to_file(client: Client, url: str, dest: Path) -> None:
    """Write url to dest, resuming a sibling .part file when possible."""
    part = dest.with_suffix(dest.suffix + ".part")
    existing = part.stat().st_size if part.exists() else 0
    headers: dict[str, str] = {}
    if existing:
        headers["Range"] = f"bytes={existing}-"

    with client.http.stream("GET", url, headers=headers) as response:
        if existing and response.status_code == 200:
            existing = 0
            part.unlink(missing_ok=True)
        elif response.status_code not in (200, 206):
            response.read()
            raise BandcampError(f"Download failed: HTTP {response.status_code}")

        length = response.headers.get("content-length")
        exact_total: int | None = None
        if length and length.isdigit():
            exact_total = int(length) + existing

        mode = "ab" if existing else "wb"
        written = existing
        dest.parent.mkdir(parents=True, exist_ok=True)
        with part.open(mode) as fh:
            for chunk in response.iter_bytes(chunk_size=64 * 1024):
                fh.write(chunk)
                written += len(chunk)

    if exact_total is not None and written != exact_total:
        raise BandcampError(
            f"Incomplete download: got {written} of {exact_total} bytes. "
            "Partial file kept; re-run to resume."
        )
    part.replace(dest)


def download_item(
    client: Client,
    item: Item,
    dest_dir: Path,
    *,
    preferred_format: str,
    retries: int = 5,
    retry_wait: float = 5.0,
) -> tuple[Path, str]:
    if not item.downloadable:
        raise BandcampError(f"{item.band_name} — {item.item_title} is not downloadable")
    last_error: Exception | None = None
    attempts = max(1, retries)
    for attempt in range(1, attempts + 1):
        try:
            available = formats_for(client, item)
            fmt, entry = pick_format(available, preferred_format)
            cdn_url = resolve_cdn_url(client, entry["url"])
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / album_filename(item, fmt)
            stream_to_file(client, cdn_url, dest)
            return dest, fmt
        except (BandcampError, httpx.HTTPError, OSError) as exc:
            last_error = exc
            if attempt == attempts:
                break
            time.sleep(retry_wait)
    raise BandcampError(
        f"Giving up on {item.item_title} after {attempts} attempt(s): {last_error}"
    ) from last_error
