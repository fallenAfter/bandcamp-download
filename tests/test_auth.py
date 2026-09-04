import json
import stat
from pathlib import Path

import httpx
import pytest

from bcdl.auth import (
    AuthError,
    cookies_dict,
    load_identity,
    parse_cookies_txt,
    parse_identity,
    save_identity,
)
from bcdl.session import BandcampError, whoami


def test_parse_identity_raw() -> None:
    assert parse_identity("  abc123  ") == "abc123"


def test_parse_identity_header() -> None:
    raw = "Cookie: foo=bar; identity=secret-value; other=1"
    assert parse_identity(raw) == "secret-value"


def test_parse_identity_empty() -> None:
    with pytest.raises(AuthError):
        parse_identity("   ")


def test_parse_cookies_txt(tmp_path: Path) -> None:
    path = tmp_path / "cookies.txt"
    path.write_text(
        "# Netscape HTTP Cookie File\n"
        ".bandcamp.com\tTRUE\t/\tTRUE\t0\tidentity\tcookie-from-txt\n"
        ".bandcamp.com\tTRUE\t/\tFALSE\t0\tjs_logged_in\t1\n"
    )
    assert parse_cookies_txt(path) == "cookie-from-txt"


def test_parse_cookies_txt_missing(tmp_path: Path) -> None:
    path = tmp_path / "cookies.txt"
    path.write_text(".example.com\tTRUE\t/\tFALSE\t0\tfoo\tbar\n")
    with pytest.raises(AuthError, match="no identity cookie"):
        parse_cookies_txt(path)


def test_save_and_load_identity(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("BCDL_HOME", str(tmp_path))
    path = save_identity("sess-token")
    assert path.exists()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.read_text()) == {"identity": "sess-token"}
    assert load_identity() == "sess-token"
    assert cookies_dict() == {"identity": "sess-token"}


def test_load_identity_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("BCDL_HOME", str(tmp_path))
    with pytest.raises(AuthError, match="bcdl login"):
        load_identity()


def test_whoami_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/collection_summary")
        return httpx.Response(
            200,
            json={"fan_id": 42, "collection_summary": {"username": "tester"}},
        )

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        fan_id, username = whoami("token", client=client)
    assert fan_id == 42
    assert username == "tester"


def test_whoami_expired() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        with pytest.raises(BandcampError, match="Not logged in"):
            whoami("token", client=client)
