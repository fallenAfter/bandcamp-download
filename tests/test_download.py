import html
import json
from pathlib import Path

import httpx

from bcdl.collection import Item, find_by_id, find_by_url, normalize_item_url
from bcdl.download import (
    album_filename,
    download_item,
    existing_download,
    file_extension,
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
    zip_path = tmp_path / "a.zip"
    zip_path.write_bytes(b"PK")
    assert is_downloaded("p1") is False
    record_download("p1", artist="A", title="B", fmt="flac", path=str(zip_path))
    assert is_downloaded("p1") is True
    zip_path.unlink()
    assert is_downloaded("p1") is False


def test_existing_download_finds_fallback_format(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("BCDL_HOME", str(tmp_path))
    item = Item(1, "p", "Artist", "Album", "album", "https://a.bandcamp.com/album/x", "u")
    fallback = tmp_path / album_filename(item, "mp3-320")
    fallback.write_bytes(b"PK")
    found = existing_download(item, tmp_path, "flac")
    assert found == fallback


def test_track_extension_is_not_zip() -> None:
    item = Item(1, "t", "Artist", "Song", "track", "https://a.bandcamp.com/track/x", "u")
    assert file_extension(item, "flac") == ".flac"
    assert album_filename(item, "flac").endswith(".flac")


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
    assert dest.name == "Artist - Album [flac].zip"


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


def test_stream_resumes_partial_file(tmp_path: Path) -> None:
    from bcdl.download import stream_to_file

    body = b"ABCDEFGH"
    dest = tmp_path / "album.zip"
    part = tmp_path / "album.zip.part"
    part.write_bytes(body[:3])

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("range") == "bytes=3-"
        return httpx.Response(
            206,
            content=body[3:],
            headers={"content-length": "5"},
        )

    client = Client("token", http=httpx.Client(transport=httpx.MockTransport(handler)))
    stream_to_file(client, "https://p4.bcbits.com/album.zip", dest)
    assert dest.read_bytes() == body
    assert not part.exists()


def test_download_retries_then_succeeds(tmp_path: Path) -> None:
    format_url = "https://popplers5.bandcamp.com/download/album?enc=flac&id=1"
    cdn_url = "https://p4.bcbits.com/download/album.zip"
    payload = b"PK\x03\x04 ok"
    calls = {"cdn": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/download":
            return httpx.Response(200, text=_download_html(format_url))
        if path.startswith("/statdownload/"):
            return httpx.Response(200, text=json.dumps({"retry_url": cdn_url}))
        if path.endswith("album.zip"):
            calls["cdn"] += 1
            if calls["cdn"] == 1:
                return httpx.Response(500, text="nope")
            return httpx.Response(
                200,
                content=payload,
                headers={"content-length": str(len(payload))},
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
    dest, fmt = download_item(
        client, item, tmp_path, preferred_format="flac", retries=2, retry_wait=0
    )
    assert fmt == "flac"
    assert dest.read_bytes() == payload
    assert calls["cdn"] == 2
