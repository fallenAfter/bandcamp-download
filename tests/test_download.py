import html
import json
from pathlib import Path

import httpx
import pytest

from bcdl.collection import Item, find_by_id, find_by_url, normalize_item_url
from bcdl.download import (
    absolute_url,
    album_filename,
    download_item,
    existing_download,
    file_extension,
    format_order,
    pick_format,
    sanitize_filename,
    stream_to_file,
)
from bcdl.manifest import is_downloaded, record_download
from bcdl.session import BandcampError, Client


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


def test_absolute_url_upgrades_protocol_relative() -> None:
    assert absolute_url("//p4.bcbits.com/f.zip") == "https://p4.bcbits.com/f.zip"
    assert absolute_url("https://p4.bcbits.com/f.zip") == "https://p4.bcbits.com/f.zip"


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
    payload = b"PK\x03\x04 fake zip"
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        paths.append(path)
        if path == "/download":
            return httpx.Response(200, text=_download_html(format_url))
        if path == "/download/album":
            return httpx.Response(
                200,
                content=payload,
                headers={
                    "content-length": str(len(payload)),
                    "content-type": "application/zip",
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
    # statdownload is a telemetry ping that returns no URL; we must not need it.
    assert not any("statdownload" in path for path in paths)


def test_stream_rejects_html_body(tmp_path: Path) -> None:
    """A page where the file should be means the link is stale or still packaging."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text="<html>Your download is being prepared</html>",
            headers={"content-type": "text/html; charset=utf-8"},
        )

    client = Client("token", http=httpx.Client(transport=httpx.MockTransport(handler)))
    dest = tmp_path / "album.zip"
    with pytest.raises(BandcampError, match="web page, not a file"):
        stream_to_file(client, "https://popplers5.bandcamp.com/download/album", dest)
    assert not dest.exists()
    assert not dest.with_suffix(".zip.part").exists()


def test_stream_resumes_partial_file(tmp_path: Path) -> None:
    body = b"ABCDEFGH"
    dest = tmp_path / "album.zip"
    part = tmp_path / "album.zip.part"
    part.write_bytes(body[:3])

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("range") == "bytes=3-"
        return httpx.Response(
            206,
            content=body[3:],
            headers={"content-length": "5", "content-type": "application/zip"},
        )

    client = Client("token", http=httpx.Client(transport=httpx.MockTransport(handler)))
    stream_to_file(client, "https://p4.bcbits.com/album.zip", dest)
    assert dest.read_bytes() == body
    assert not part.exists()


def test_download_retries_then_succeeds(tmp_path: Path) -> None:
    format_url = "https://popplers5.bandcamp.com/download/album?enc=flac&id=1"
    payload = b"PK\x03\x04 ok"
    calls = {"cdn": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/download":
            return httpx.Response(200, text=_download_html(format_url))
        if path == "/download/album":
            calls["cdn"] += 1
            if calls["cdn"] == 1:
                return httpx.Response(500, text="nope")
            return httpx.Response(
                200,
                content=payload,
                headers={
                    "content-length": str(len(payload)),
                    "content-type": "application/zip",
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
    dest, fmt = download_item(
        client, item, tmp_path, preferred_format="flac", retries=2, retry_wait=0
    )
    assert fmt == "flac"
    assert dest.read_bytes() == payload
    assert calls["cdn"] == 2
