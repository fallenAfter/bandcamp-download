from pathlib import Path

from bcdl.cli import main
from bcdl.session import BandcampError


def test_login_saves_cookie_without_verify(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("BCDL_HOME", str(tmp_path))
    assert main(["login", "--identity", "test-cookie", "--no-verify"]) == 0
    out = capsys.readouterr().out
    assert "Saved session" in out
    saved = (tmp_path / "cookies.json").read_text()
    assert "test-cookie" in saved


def test_login_does_not_save_when_verify_fails(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("BCDL_HOME", str(tmp_path))

    def boom(_identity, *, client=None):
        raise BandcampError("Not logged in")

    monkeypatch.setattr("bcdl.cli.whoami", boom)
    assert main(["login", "--identity", "bad-cookie"]) == 1
    assert "Not logged in" in capsys.readouterr().err
    assert not (tmp_path / "cookies.json").exists()
