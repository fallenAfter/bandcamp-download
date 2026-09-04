"""Command-line entry point."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from bcdl import __version__
from bcdl.config import DEFAULT_FORMAT, KNOWN_FORMATS


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bcdl",
        description="Download albums you have purchased on Bandcamp.",
    )
    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"bcdl {__version__}",
    )
    sub = parser.add_subparsers(dest="command")

    login = sub.add_parser(
        "login",
        help="Store your Bandcamp identity cookie",
    )
    login.set_defaults(func=cmd_login)

    list_cmd = sub.add_parser(
        "list",
        help="List purchased albums in your collection",
    )
    list_cmd.add_argument(
        "--search",
        metavar="QUERY",
        help="Filter by artist or album title",
    )
    list_cmd.add_argument(
        "--json",
        action="store_true",
        help="Print JSON instead of a table",
    )
    list_cmd.set_defaults(func=cmd_list)

    download = sub.add_parser(
        "download",
        help="Download selected purchased albums as ZIP files",
    )
    download.add_argument(
        "urls",
        nargs="*",
        metavar="URL",
        help="Bandcamp album URL(s) from your collection",
    )
    download.add_argument(
        "--id",
        dest="ids",
        action="append",
        default=[],
        metavar="ID",
        help="Collection item id (repeatable). From `bcdl list`.",
    )
    download.add_argument(
        "-o",
        "--output",
        metavar="DIR",
        help="Directory to write ZIP files into (default: current directory)",
    )
    download.add_argument(
        "-f",
        "--format",
        default=DEFAULT_FORMAT,
        choices=KNOWN_FORMATS,
        help="Preferred audio format (default: flac)",
    )
    download.set_defaults(func=cmd_download)

    return parser


def cmd_login(_args: argparse.Namespace) -> int:
    print("login is not implemented yet", file=sys.stderr)
    return 1


def cmd_list(_args: argparse.Namespace) -> int:
    print("list is not implemented yet", file=sys.stderr)
    return 1


def cmd_download(_args: argparse.Namespace) -> int:
    print("download is not implemented yet", file=sys.stderr)
    return 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return 0
    return func(args)


if __name__ == "__main__":
    raise SystemExit(main())
