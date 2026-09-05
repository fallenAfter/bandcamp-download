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
    monkeypatch.delenv("BANDCAMP_IDENTITY", raising=False)
    path = save_identity("sess-token")
    assert path.exists()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.read_text()) == {"identity": "sess-token"}
    assert load_identity() == "sess-token"
    assert cookies_dict() == {"identity": "sess-token"}


def test_load_identity_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("BCDL_HOME", str(tmp_path))
    monkeypatch.delenv("BANDCAMP_IDENTITY", raising=False)
    with pytest.raises(AuthError, match="bcdl login"):
        load_identity()


def test_load_identity_prefers_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("BCDL_HOME", str(tmp_path))
    monkeypatch.setenv("BANDCAMP_IDENTITY", "from-env")
    assert load_identity() == "from-env"


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


def test_client_retries_server_error(monkeypatch) -> None:
    monkeypatch.setattr("bcdl.session.time.sleep", lambda _seconds: None)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(500, text="nope")
        return httpx.Response(
            200,
            json={"fan_id": 7, "collection_summary": {"username": "retry"}},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        fan_id, username = whoami("token", client=client)
    assert (fan_id, username) == (7, "retry")
    assert calls["n"] == 2
