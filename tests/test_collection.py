from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from bcdl.collection import (
    Item,
    fetch_collection,
    filter_items,
    item_from_json,
    load_collection,
    parse_targets_file,
    resolve_targets,
    save_collection,
)
from bcdl.session import Client, parse_pagedata


def _pagedata_html(blob: dict[str, Any]) -> str:
    escaped = html.escape(json.dumps(blob), quote=True)
    return f'<html><body><div id="pagedata" data-blob="{escaped}"></div></body></html>'


def test_parse_pagedata_roundtrip() -> None:
    blob = {"fan_data": {"fan_id": 9}}
    assert parse_pagedata(_pagedata_html(blob)) == blob


def test_item_matches_artist_and_title() -> None:
    item = Item(
        sale_item_id=1,
        sale_item_type="p",
        band_name="Slowdive",
        item_title="Souvlaki",
        item_type="album",
        item_url="https://slowdive.bandcamp.com/album/souvlaki",
        download_page_url="https://bandcamp.com/download?id=1",
    )
    assert item.matches("souv")
    assert item.matches("SLOW")
    assert not item.matches("mbv")
    assert item.key == "p1"


def test_item_from_json_attaches_redownload() -> None:
    raw = {
        "sale_item_id": 7,
        "sale_item_type": "p",
        "band_name": "A",
        "item_title": "B",
        "item_type": "album",
        "item_url": "https://a.bandcamp.com/album/b",
    }
    item = item_from_json(raw, {"p7": "https://bandcamp.com/download?from=p7"})
    assert item.download_page_url.endswith("p7")
    assert item.downloadable


def test_filter_and_cache(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BCDL_HOME", str(tmp_path))
    items = [
        Item(1, "p", "Alpha", "One", "album", "https://a.bandcamp.com/album/one", "u1"),
        Item(2, "p", "Beta", "Two", "album", "https://b.bandcamp.com/album/two", "u2"),
    ]
    save_collection(items)
    loaded = load_collection()
    assert [i.key for i in loaded] == ["p1", "p2"]
    assert [i.band_name for i in filter_items(loaded, "beta")] == ["Beta"]


def test_load_collection_rejects_corrupt_cache(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("BCDL_HOME", str(tmp_path))
    from bcdl.collection import cache_path
    from bcdl.config import ensure_config_dir
    from bcdl.session import BandcampError

    ensure_config_dir()
    cache_path().write_text("{not json")
    with pytest.raises(BandcampError, match="Could not parse"):
        load_collection()


def test_parse_targets_file(tmp_path: Path) -> None:
    path = tmp_path / "albums.txt"
    path.write_text(
        "# comment\n"
        "https://a.bandcamp.com/album/one\n"
        "\n"
        "p2\n"
        "3\n"
    )
    urls, ids = parse_targets_file(path)
    assert urls == ["https://a.bandcamp.com/album/one"]
    assert ids == ["p2", "3"]


def test_resolve_targets_dedupes_and_reports_missing() -> None:
    items = [
        Item(1, "p", "Alpha", "One", "album", "https://a.bandcamp.com/album/one", "u1"),
        Item(2, "p", "Beta", "Two", "album", "https://b.bandcamp.com/album/two", "u2"),
    ]
    found, missing = resolve_targets(
        items,
        urls=["https://a.bandcamp.com/album/one", "https://missing.bandcamp.com/album/x"],
        ids=["p1", "p2"],
    )
    assert [i.key for i in found] == ["p1", "p2"]
    assert missing == ["https://missing.bandcamp.com/album/x"]


def test_fetch_collection_paginates() -> None:
    first = {
        "fan_data": {"fan_id": 42},
        "identities": {"fan": {"id": 42}},
        "collection_data": {
            "item_count": 2,
            "last_token": "t1",
            "redownload_urls": {"p1": "https://bandcamp.com/download?id=1"},
        },
        "item_cache": {
            "collection": {
                "x": {
                    "sale_item_id": 1,
                    "sale_item_type": "p",
                    "band_name": "First",
                    "item_title": "Record",
                    "item_type": "album",
                    "item_url": "https://first.bandcamp.com/album/record",
                }
            }
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/collection_summary"):
            return httpx.Response(
                200,
                json={"fan_id": 42, "collection_summary": {"username": "tester"}},
            )
        if path == "/tester":
            return httpx.Response(200, text=_pagedata_html(first))
        if path.endswith("/collection_items"):
            payload = json.loads(request.content)
            assert payload["fan_id"] == 42
            assert payload["older_than_token"] == "t1"
            return httpx.Response(
                200,
                json={
                    "more_available": False,
                    "last_token": "t2",
                    "redownload_urls": {"p2": "https://bandcamp.com/download?id=2"},
                    "items": [
                        {
                            "sale_item_id": 2,
                            "sale_item_type": "p",
                            "band_name": "Second",
                            "item_title": "Tape",
                            "item_type": "album",
                            "item_url": "https://second.bandcamp.com/album/tape",
                        }
                    ],
                },
            )
        return httpx.Response(404, text=path)

    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport)
    client = Client("token", http=http)
    items = fetch_collection(client, page_delay=0)
    assert [(i.band_name, i.item_title) for i in items] == [
        ("First", "Record"),
        ("Second", "Tape"),
    ]
    assert all(i.downloadable for i in items)


def test_fetch_collection_rejects_wrong_fan() -> None:
    blob = {
        "fan_data": {"fan_id": 99},
        "identities": {"fan": {"id": 42}},
        "collection_data": {"item_count": 0, "redownload_urls": {}},
        "item_cache": {"collection": {}},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/collection_summary"):
            return httpx.Response(
                200,
                json={"fan_id": 42, "collection_summary": {"username": "tester"}},
            )
        return httpx.Response(200, text=_pagedata_html(blob))

    transport = httpx.MockTransport(handler)
    client = Client("token", http=httpx.Client(transport=transport))
    with pytest.raises(Exception, match="does not belong"):
        fetch_collection(client, page_delay=0)
