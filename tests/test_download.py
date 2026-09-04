import html
import json
from pathlib import Path

import httpx
import pytest

from bcdl.collection import Item, find_by_id, find_by_url, normalize_item_url
from bcdl.download import (
    album_filename,
    download_item,
    format_order,
    parse_stat_body,
    pick_format,
    resolve_cdn_url,
    sanitize_filename,
    to_stat_url,
)
from bcdl.manifest import is_downloaded, record_download
from bcdl.session import Client


def test_format_order_puts_flac_first_by_default() -> None:
    assert format_order("flac")[0] == "flac"
    assert format_order("mp3-320")[0] == "mp3-320"
    assert "flac" in format_order("mp3-320")


def test_pick_format_prefers_flac_then_fallback() -> None:
    available = {
        "mp3-v0": {"url": "http://x/mp3v0"},
        "mp3-320": {"url": "http://x/mp3"},
        "flac": {"url": "http://x/flac"},
    }
    name, entry = pick_format(available, "flac")
    assert name == "flac"
    assert entry["url"].endswith("flac")


def test_pick_format_falls_back_when_preferred_missing() -> None:
    available = {"mp3-320": {"url": "http://x/mp3"}}
    name, _entry = pick_format(available, "flac")
    assert name == "mp3-320"


def test_sanitize_and_album_filename() -> None:
    assert "/" not in sanitize_filename('A / B: "x"')
    item = Item(1, "p", "Artist", "Album", "album", "https://a.bandcamp.com/album/x", "u")
    assert album_filename(item, "flac") == "Artist - Album [flac].zip"


def test_normalize_and_find() -> None:
    items = [
        Item(
            9,
            "p",
            "A",
            "B",
            "album",
            "https://www.Artist.bandcamp.com/album/the-record/",
            "https://bandcamp.com/download?id=9",
        )
    ]
    assert normalize_item_url("https://artist.bandcamp.com/album/the-record") == (
        "artist.bandcamp.com/album/the-record"
    )
    assert find_by_url(items, "https://artist.bandcamp.com/album/the-record") is items[0]
    assert find_by_id(items, "p9") is items[0]
    assert find_by_id(items, "9") is items[0]
    assert find_by_url(items, "https://other.bandcamp.com/album/nope") is None


def test_parse_stat_body_strips_js_wrapper() -> None:
    body = 'foo({"result":"ok","retry_url":"https://p4.bcbits.com/file.zip"})'
    assert parse_stat_body(body)["retry_url"].endswith("file.zip")


def test_to_stat_url_swaps_path() -> None:
    url = "https://popplers5.bandcamp.com/download/album?enc=flac&id=1"
    stat = to_stat_url(url)
    assert "/statdownload/" in stat
    assert ".rand=" in stat


def test_manifest_roundtrip(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("BCDL_HOME", str(tmp_path))
    assert is_downloaded("p1") is False
    record_download("p1", artist="A", title="B", fmt="flac", path="/tmp/a.zip")
    assert is_downloaded("p1") is True


def _download_html(format_url: str) -> str:
    blob = {
        "download_items": [
            {"title": "Album", "downloads": {"flac": {"url": format_url, "size_mb": "10MB"}}}
        ]
    }
    escaped = html.escape(json.dumps(blob), quote=True)
    return f'<div id="pagedata" data-blob="{escaped}"></div>'


def test_download_item_writes_zip(tmp_path: Path) -> None:
    format_url = "https://popplers5.bandcamp.com/download/album?enc=flac&id=1"
    cdn_url = "https://p4.bcbits.com/download/album.zip"
    payload = b"PK\x03\x04 fake zip"

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/download":
            return httpx.Response(200, text=_download_html(format_url))
        if path.startswith("/statdownload/"):
            return httpx.Response(200, text=json.dumps({"retry_url": cdn_url}))
        if path.endswith("album.zip"):
            return httpx.Response(
                200,
                content=payload,
                headers={
                    "content-length": str(len(payload)),
                    "content-disposition": 'attachment; filename="Artist - Album.zip"',
                },
            )
        return httpx.Response(404, text=str(request.url))

    item = Item(
        1,
        "p",
        "Artist",
        "Album",
        "album",
        "https://artist.bandcamp.com/album/album",
        "https://bandcamp.com/download?id=1",
    )
    client = Client("token", http=httpx.Client(transport=httpx.MockTransport(handler)))
    dest, fmt = download_item(client, item, tmp_path, preferred_format="flac")
    assert fmt == "flac"
    assert dest.read_bytes() == payload
    assert dest.name == "Artist - Album.zip"


def test_resolve_cdn_url_https_prefix() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, text='({"retry_url":"//p4.bcbits.com/x.zip"})'
        )

    client = Client("token", http=httpx.Client(transport=httpx.MockTransport(handler)))
    url = resolve_cdn_url(
        client, "https://popplers5.bandcamp.com/download/album?enc=flac"
    )
    assert url == "https://p4.bcbits.com/x.zip"
