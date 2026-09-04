from pathlib import Path

from bcdl.cli import main


def test_login_saves_cookie_without_verify(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("BCDL_HOME", str(tmp_path))
    assert main(["login", "--identity", "test-cookie", "--no-verify"]) == 0
    out = capsys.readouterr().out
    assert "Saved session" in out
    saved = (tmp_path / "cookies.json").read_text()
    assert "test-cookie" in saved
