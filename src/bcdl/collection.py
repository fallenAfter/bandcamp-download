"""Fetch and cache the logged-in fan's Bandcamp purchases."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from bcdl.config import config_dir, ensure_config_dir
from bcdl.session import (
    COLLECTION_ITEMS_URL,
    HIDDEN_ITEMS_URL,
    BandcampError,
    Client,
    dotted,
)

CACHE_FILE = "collection.json"
PAGE_SIZE = 50


@dataclass(frozen=True)
class Item:
    sale_item_id: int
    sale_item_type: str
    band_name: str
    item_title: str
    item_type: str
    item_url: str
    download_page_url: str | None
    hidden: bool = False
    is_preorder: bool = False

    @property
    def key(self) -> str:
        return f"{self.sale_item_type}{self.sale_item_id}"

    @property
    def downloadable(self) -> bool:
        return self.download_page_url is not None

    def matches(self, query: str) -> bool:
        needle = query.casefold()
        haystack = f"{self.band_name} {self.item_title} {self.item_url}".casefold()
        return needle in haystack

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["key"] = self.key
        return data


def cache_path() -> Path:
    return config_dir() / CACHE_FILE


def item_from_json(raw: dict[str, Any], redownload_urls: dict[str, str]) -> Item:
    sale_item_id = dotted(raw, "sale_item_id", required=True)
    sale_item_type = dotted(raw, "sale_item_type", default="p") or "p"
    key = f"{sale_item_type}{sale_item_id}"
    return Item(
        sale_item_id=int(sale_item_id),
        sale_item_type=str(sale_item_type),
        band_name=str(dotted(raw, "band_name", default="") or ""),
        item_title=str(dotted(raw, "item_title", default="") or ""),
        item_type=str(dotted(raw, "item_type", default="") or ""),
        item_url=str(dotted(raw, "item_url", default="") or ""),
        download_page_url=redownload_urls.get(key),
        hidden=bool(dotted(raw, "hidden", default=False)),
        is_preorder=bool(dotted(raw, "is_preorder", default=False)),
    )


def save_collection(items: list[Item]) -> Path:
    path = ensure_config_dir() / CACHE_FILE
    payload = {"items": [item.to_dict() for item in items]}
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return path


def load_collection() -> list[Item]:
    path = cache_path()
    if not path.exists():
        raise BandcampError(f"No cached collection at {path}. Run `bcdl list` first.")
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise BandcampError(f"Could not parse collection cache at {path}") from exc
    items = []
    try:
        for raw in data.get("items", []):
            items.append(
                Item(
                    sale_item_id=int(raw["sale_item_id"]),
                    sale_item_type=str(raw["sale_item_type"]),
                    band_name=str(raw.get("band_name") or ""),
                    item_title=str(raw.get("item_title") or ""),
                    item_type=str(raw.get("item_type") or ""),
                    item_url=str(raw.get("item_url") or ""),
                    download_page_url=raw.get("download_page_url"),
                    hidden=bool(raw.get("hidden", False)),
                    is_preorder=bool(raw.get("is_preorder", False)),
                )
            )
    except (KeyError, TypeError, ValueError) as exc:
        raise BandcampError(f"Collection cache at {path} is invalid") from exc
    return items


def fetch_collection(
    client: Client,
    *,
    include_hidden: bool = True,
    page_delay: float = 0.5,
) -> list[Item]:
    fan_id, username = client.whoami()
    page = client.pagedata(f"https://bandcamp.com/{username}")

    page_fan_id = dotted(page, "fan_data.fan_id", required=True)
    me = dotted(page, "identities.fan.id")
    if me is None:
        raise BandcampError("Not logged in — no identity in page data.")
    if int(me) != int(page_fan_id) or int(page_fan_id) != fan_id:
        raise BandcampError("The collection page does not belong to the logged-in fan.")

    items: list[Item] = []
    items.extend(
        _page_items(
            client,
            fan_id,
            dotted(page, "collection_data", required=True),
            dotted(page, "item_cache.collection", default={}) or {},
            COLLECTION_ITEMS_URL,
            page_delay,
        )
    )
    hidden_data = dotted(page, "hidden_data", default={}) or {}
    if hidden_data:
        items.extend(
            _page_items(
                client,
                fan_id,
                hidden_data,
                dotted(page, "item_cache.hidden", default={}) or {},
                HIDDEN_ITEMS_URL,
                page_delay,
            )
        )

    unique: dict[str, Item] = {}
    for item in items:
        unique.setdefault(item.key, item)
    result = list(unique.values())
    if not include_hidden:
        result = [item for item in result if not item.hidden]
    return result


def _page_items(
    client: Client,
    fan_id: int,
    section: dict[str, Any],
    cache: dict[str, Any],
    endpoint: str,
    page_delay: float,
) -> list[Item]:
    total = int(dotted(section, "item_count", default=0) or 0)
    redownload = dotted(section, "redownload_urls", default={}) or {}
    items: list[Item] = []
    for raw in cache.values():
        items.append(item_from_json(raw, redownload))

    token = dotted(section, "last_token")
    while token and len(items) < total:
        payload = {
            "fan_id": fan_id,
            "older_than_token": token,
            "count": PAGE_SIZE,
        }
        batch = client.post_json(endpoint, payload)
        page_redownload = dotted(batch, "redownload_urls", default={}) or {}
        for raw in dotted(batch, "items", default=[]) or []:
            items.append(item_from_json(raw, page_redownload))
        if not dotted(batch, "more_available", default=False):
            break
        token = dotted(batch, "last_token")
        if page_delay:
            time.sleep(page_delay)
    return items


def filter_items(items: list[Item], query: str | None) -> list[Item]:
    if not query:
        return items
    return [item for item in items if item.matches(query)]


def normalize_item_url(url: str) -> str:
    parsed = urlparse(url.strip())
    host = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path.rstrip("/")
    return f"{host}{path}".casefold()


def find_by_url(items: list[Item], url: str) -> Item | None:
    target = normalize_item_url(url)
    for item in items:
        if item.item_url and normalize_item_url(item.item_url) == target:
            return item
        if item.download_page_url and normalize_item_url(item.download_page_url) == target:
            return item
    return None


def find_by_id(items: list[Item], value: str) -> Item | None:
    value = value.strip()
    for item in items:
        if item.key == value or str(item.sale_item_id) == value:
            return item
    return None


def parse_targets_file(path: Path) -> tuple[list[str], list[str]]:
    """Return (urls, ids) from a text file. Blank lines and # comments are ignored."""
    urls: list[str] = []
    ids: list[str] = []
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("http://") or line.startswith("https://"):
            urls.append(line)
        else:
            ids.append(line)
    return urls, ids


def resolve_targets(
    items: list[Item],
    urls: list[str],
    ids: list[str],
) -> tuple[list[Item], list[str]]:
    found: list[Item] = []
    seen: set[str] = set()
    missing: list[str] = []

    def add(item: Item | None, label: str) -> None:
        if item is None:
            missing.append(label)
            return
        if item.key in seen:
            return
        seen.add(item.key)
        found.append(item)

    for url in urls:
        add(find_by_url(items, url), url)
    for value in ids:
        add(find_by_id(items, value), value)
    return found, missing


def matching_artist_names(items: list[Item], query: str) -> list[str]:
    """Exact artist name match first; otherwise unique substring matches."""
    needle = query.strip().casefold()
    if not needle:
        return []
    names = sorted({item.band_name for item in items if item.band_name}, key=str.casefold)
    exact = [name for name in names if name.casefold() == needle]
    if exact:
        return exact
    return [name for name in names if needle in name.casefold()]


def items_for_artist(items: list[Item], artist_name: str) -> list[Item]:
    wanted = artist_name.casefold()
    return [item for item in items if item.band_name.casefold() == wanted]


def resolve_artists(
    items: list[Item],
    queries: list[str],
) -> tuple[list[Item], list[str], dict[str, list[str]]]:
    """Return (found, missing_queries, ambiguous query -> matching names)."""
    found: list[Item] = []
    seen: set[str] = set()
    missing: list[str] = []
    ambiguous: dict[str, list[str]] = {}
    for query in queries:
        names = matching_artist_names(items, query)
        if not names:
            missing.append(query)
            continue
        if len(names) > 1:
            ambiguous[query] = names
            continue
        for item in items_for_artist(items, names[0]):
            if item.key in seen:
                continue
            seen.add(item.key)
            found.append(item)
    return found, missing, ambiguous
