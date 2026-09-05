"""Command-line entry point."""

from __future__ import annotations

import argparse
import getpass
import json
import sys
import time
from collections.abc import Sequence
from pathlib import Path

import httpx

from bcdl import __version__
from bcdl.auth import (
    IDENTITY_ENV,
    AuthError,
    identity_from_env,
    load_identity,
    parse_cookies_txt,
    parse_identity,
    save_identity,
)
from bcdl.collection import (
    fetch_collection,
    filter_items,
    parse_targets_file,
    resolve_artists,
    resolve_targets,
    save_collection,
)
from bcdl.config import DEFAULT_DELAY_SECONDS, DEFAULT_FORMAT, KNOWN_FORMATS
from bcdl.download import download_item, existing_download
from bcdl.manifest import record_download
from bcdl.session import BandcampError, Client, whoami

CLI_ERRORS = (AuthError, BandcampError, OSError, httpx.HTTPError, json.JSONDecodeError)


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
        "--artist",
        dest="artists",
        action="append",
        default=[],
        metavar="NAME",
        help="Show only purchases by this artist (repeatable, case-insensitive)",
    )
    list_cmd.add_argument(
        "--json",
        action="store_true",
        help="Print JSON instead of a table",
    )
    list_cmd.add_argument(
        "--include-hidden",
        action="store_true",
        help="Include items hidden in your Bandcamp collection",
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
    download.add_argument(
        "--artist",
        dest="artists",
        action="append",
        default=[],
        metavar="NAME",
        help="Download every owned album/track by this artist (repeatable)",
    )
    download.add_argument(
        "--file",
        dest="from_file",
        metavar="FILE",
        type=Path,
        help="Text file of album URLs or item ids (one per line)",
    )
    download.add_argument(
        "--dry-run",
        action="store_true",
        help="List what would be downloaded without fetching files",
    )
    download.add_argument(
        "--include-preorders",
        action="store_true",
        help="Include unreleased preorders when selecting by --artist",
    )
    download.add_argument(
        "--force",
        action="store_true",
        help="Re-download even if the album is in the manifest or already on disk",
    )
    download.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY_SECONDS,
        metavar="SECONDS",
        help=f"Wait between albums (default: {DEFAULT_DELAY_SECONDS:g})",
    )
    download.add_argument(
        "--retries",
        type=int,
        default=5,
        help="Total attempts per album (default: 5)",
    )
    download.add_argument(
        "--retry-wait",
        type=float,
        default=5.0,
        metavar="SECONDS",
        help="Wait after a failed attempt (default: 5)",
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


def _report_artist_errors(missing: list[str], ambiguous: dict[str, list[str]]) -> bool:
    ok = True
    for query, names in ambiguous.items():
        print(
            f"--artist {query!r} matches more than one artist: {', '.join(names)}",
            file=sys.stderr,
        )
        ok = False
    for query in missing:
        print(f"No owned albums by {query!r}. Try `bcdl list --artist NAME`.", file=sys.stderr)
        ok = False
    return ok


def cmd_login(args: argparse.Namespace) -> int:
    try:
        identity = _read_identity(args)
        if args.no_verify:
            path = save_identity(identity)
            print(f"Saved session to {path}")
            return 0
        fan_id, username = whoami(identity)
        path = save_identity(identity)
        print(f"Logged in as {username} (fan_id {fan_id})")
        print(f"Saved session to {path}")
        return 0
    except CLI_ERRORS as exc:
        print(exc, file=sys.stderr)
        return 1


def cmd_list(args: argparse.Namespace) -> int:
    try:
        identity = load_identity()
        with Client(identity) as client:
            items = fetch_collection(client, include_hidden=True)
        save_collection(items)
        if not args.include_hidden:
            items = [item for item in items if not item.hidden]
        if args.artists:
            artist_items, missing, ambiguous = resolve_artists(items, args.artists)
            if not _report_artist_errors(missing, ambiguous):
                return 1
            items = artist_items
        items = filter_items(items, args.search)
        if args.json:
            print(json.dumps([item.to_dict() for item in items], indent=2))
            return 0
        if not items:
            print("No matching purchases.")
            return 0
        print(f"{'KEY':<12} {'TYPE':<8} ARTIST — TITLE")
        for item in items:
            flag = ""
            if not item.downloadable:
                flag = " (no download)"
            elif item.is_preorder:
                flag = " (preorder)"
            print(
                f"{item.key:<12} {item.item_type:<8} "
                f"{item.band_name} — {item.item_title}{flag}"
            )
            if item.item_url:
                print(f"{'':12} {item.item_url}")
        print(f"\n{len(items)} item(s)")
        return 0
    except CLI_ERRORS as exc:
        print(exc, file=sys.stderr)
        return 1


def cmd_download(args: argparse.Namespace) -> int:
    if args.delay < 0:
        print("--delay must be >= 0", file=sys.stderr)
        return 2
    if args.retries < 1:
        print("--retries must be >= 1", file=sys.stderr)
        return 2

    urls = list(args.urls)
    ids = list(args.ids)
    artists = list(args.artists)
    if args.from_file is not None:
        try:
            file_urls, file_ids = parse_targets_file(args.from_file)
        except OSError as exc:
            print(exc, file=sys.stderr)
            return 1
        urls.extend(file_urls)
        ids.extend(file_ids)
    if not urls and not ids and not artists:
        print(
            "Specify album URL(s), --id, --artist, and/or --file with one target per line.",
            file=sys.stderr,
        )
        return 2

    try:
        identity = load_identity()
        dest_dir = Path(args.output).expanduser() if args.output else Path.cwd()
        failures = 0
        downloaded = 0
        with Client(identity) as client:
            catalog = fetch_collection(client, include_hidden=True, page_delay=0.5)
            save_collection(catalog)
            selected, missing = resolve_targets(catalog, urls, ids)
            artist_items, artist_missing, ambiguous = resolve_artists(catalog, artists)
            if not _report_artist_errors(artist_missing, ambiguous):
                failures += 1
            seen = {item.key for item in selected}
            for item in artist_items:
                if item.key in seen:
                    continue
                # An artist's purchases can include merch with no digital download,
                # and preorders whose ZIP only holds the tracks released so far.
                if not item.downloadable:
                    print(f"Skipping {item.item_title} (no digital download)")
                    continue
                if item.is_preorder and not args.include_preorders:
                    print(
                        f"Skipping {item.item_title} (unreleased preorder; "
                        "use --include-preorders)"
                    )
                    continue
                seen.add(item.key)
                selected.append(item)
            for label in missing:
                print(f"Not in your collection: {label}", file=sys.stderr)
                failures += 1
            if not selected:
                if not failures:
                    print("No matching purchases.", file=sys.stderr)
                return 1
            if args.delay == 0 and len(selected) > 1:
                print(
                    "Warning: --delay 0 with multiple albums can trigger Bandcamp rate limits.",
                    file=sys.stderr,
                )
            print(
                f"Selected {len(selected)} owned release(s). "
                f"Downloads are sequential with a {args.delay:g}s pause between albums."
            )
            if args.dry_run:
                for item in selected:
                    print(f"  {item.key:<12} {item.band_name} — {item.item_title}")
                print("Dry run: nothing downloaded.")
                return 1 if failures else 0
            pending = len(selected)
            for index, item in enumerate(selected):
                label = f"{item.band_name} — {item.item_title}"
                existing = None if args.force else existing_download(
                    item, dest_dir, args.format
                )
                if existing is not None:
                    print(f"Skipping {label} (already downloaded: {existing})")
                    continue
                print(f"Downloading {label} ({args.format})")
                try:
                    path, fmt = download_item(
                        client,
                        item,
                        dest_dir,
                        preferred_format=args.format,
                        retries=args.retries,
                        retry_wait=args.retry_wait,
                    )
                except (BandcampError, OSError, httpx.HTTPError) as exc:
                    print(f"Failed {label}: {exc}", file=sys.stderr)
                    failures += 1
                    remaining = pending - index - 1
                    if remaining and args.delay:
                        time.sleep(args.delay)
                    continue
                record_download(
                    item.key,
                    artist=item.band_name,
                    title=item.item_title,
                    fmt=fmt,
                    path=str(path),
                )
                print(f"Saved {path} [{fmt}]")
                downloaded += 1
                remaining = pending - index - 1
                if remaining and args.delay:
                    time.sleep(args.delay)
        if failures:
            print(f"Finished with {failures} failure(s), {downloaded} downloaded.")
            return 1
        return 0
    except CLI_ERRORS as exc:
        print(exc, file=sys.stderr)
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
