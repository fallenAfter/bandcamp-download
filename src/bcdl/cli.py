"""Command-line entry point."""

from __future__ import annotations

import argparse
import getpass
import sys
from collections.abc import Sequence
from pathlib import Path

from bcdl import __version__
from bcdl.auth import (
    IDENTITY_ENV,
    AuthError,
    identity_from_env,
    parse_cookies_txt,
    parse_identity,
    save_identity,
)
from bcdl.config import DEFAULT_FORMAT, KNOWN_FORMATS
from bcdl.session import BandcampError, whoami


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
    login.add_argument(
        "--identity",
        metavar="VALUE",
        help="identity cookie value (or set BANDCAMP_IDENTITY)",
    )
    login.add_argument(
        "--cookies-txt",
        metavar="FILE",
        type=Path,
        help="Netscape cookies.txt export from a browser extension",
    )
    login.add_argument(
        "--no-verify",
        action="store_true",
        help="Save the cookie without checking it against Bandcamp",
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


def _read_identity(args: argparse.Namespace) -> str:
    if args.cookies_txt is not None:
        return parse_cookies_txt(args.cookies_txt)
    if args.identity:
        return parse_identity(args.identity)
    env_value = identity_from_env()
    if env_value:
        return env_value
    print(
        "Log in at https://bandcamp.com in your browser, then copy the "
        "`identity` cookie:\n"
        "  DevTools → Application/Storage → Cookies → https://bandcamp.com\n"
        f"You can also set {IDENTITY_ENV} or pass --identity / --cookies-txt.\n"
    )
    try:
        value = getpass.getpass("identity cookie: ")
    except (EOFError, KeyboardInterrupt) as exc:
        raise AuthError("No identity cookie provided.") from exc
    return parse_identity(value)


def cmd_login(args: argparse.Namespace) -> int:
    try:
        identity = _read_identity(args)
        path = save_identity(identity)
        if args.no_verify:
            print(f"Saved session to {path}")
            return 0
        fan_id, username = whoami(identity)
        print(f"Logged in as {username} (fan_id {fan_id})")
        print(f"Saved session to {path}")
        return 0
    except (AuthError, BandcampError, OSError) as exc:
        print(exc, file=sys.stderr)
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
