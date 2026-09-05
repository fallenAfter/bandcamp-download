import pytest

import bcdl.cli as cli
from bcdl.cli import build_parser, main
from bcdl.collection import Item


def test_parser_has_core_commands() -> None:
    parser = build_parser()
    dests = {action.dest for action in parser._subparsers._group_actions}
    assert "command" in dests
    args = parser.parse_args(["list", "--search", "foo"])
    assert args.command == "list"
    assert args.search == "foo"


def test_download_defaults_to_flac() -> None:
    parser = build_parser()
    args = parser.parse_args(["download", "https://artist.bandcamp.com/album/x"])
    assert args.format == "flac"
    assert args.urls == ["https://artist.bandcamp.com/album/x"]


def test_help_exits_zero() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0


def test_no_command_prints_help(capsys) -> None:
    assert main([]) == 0
    out = capsys.readouterr().out
    assert "login" in out
    assert "list" in out
    assert "download" in out


def test_download_requires_a_selection(capsys) -> None:
    assert main(["download"]) == 2
    err = capsys.readouterr().err
    assert "--artist" in err


def test_download_accepts_artist_flag() -> None:
    parser = build_parser()
    args = parser.parse_args(["download", "--artist", "Slowdive", "--dry-run"])
    assert args.artists == ["Slowdive"]
    assert args.dry_run is True


class _FakeClient:
    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        return False


def _stub_collection(monkeypatch, items: list[Item]) -> None:
    monkeypatch.setattr(cli, "load_identity", lambda: "cookie")
    monkeypatch.setattr(cli, "Client", lambda identity: _FakeClient())
    monkeypatch.setattr(cli, "fetch_collection", lambda *a, **k: items)
    monkeypatch.setattr(cli, "save_collection", lambda items: None)


def test_artist_download_skips_merch_and_preorders(monkeypatch, capsys) -> None:
    items = [
        Item(1, "p", "Slowdive", "Souvlaki", "album", "https://a.bandcamp.com/album/one", "u1"),
        Item(2, "p", "Slowdive", "Tour Shirt", "package", "https://a.bandcamp.com/merch/x", None),
        Item(
            3,
            "p",
            "Slowdive",
            "Next Album",
            "album",
            "https://a.bandcamp.com/album/next",
            "u3",
            is_preorder=True,
        ),
    ]
    _stub_collection(monkeypatch, items)

    assert main(["download", "--artist", "Slowdive", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "Tour Shirt (no digital download)" in out
    assert "Next Album (unreleased preorder" in out
    assert "Selected 1 owned release(s)" in out
    assert "Souvlaki" in out


def test_include_preorders_keeps_preorder(monkeypatch, capsys) -> None:
    items = [
        Item(
            3,
            "p",
            "Slowdive",
            "Next Album",
            "album",
            "https://a.bandcamp.com/album/next",
            "u3",
            is_preorder=True,
        ),
    ]
    _stub_collection(monkeypatch, items)

    assert main(["download", "--artist", "Slowdive", "--dry-run", "--include-preorders"]) == 0
    out = capsys.readouterr().out
    assert "Selected 1 owned release(s)" in out
    assert "Skipping" not in out


def test_artist_download_with_only_merch_fails(monkeypatch, capsys) -> None:
    items = [
        Item(2, "p", "Slowdive", "Tour Shirt", "package", "https://a.bandcamp.com/merch/x", None),
    ]
    _stub_collection(monkeypatch, items)

    assert main(["download", "--artist", "Slowdive", "--dry-run"]) == 1
    assert "No matching purchases." in capsys.readouterr().err
