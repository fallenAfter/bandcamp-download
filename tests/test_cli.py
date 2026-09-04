import pytest

from bcdl.cli import build_parser, main


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


def test_download_requires_exactly_one_selection(capsys) -> None:
    assert main(["download"]) == 2
    err = capsys.readouterr().err
    assert "exactly one" in err
