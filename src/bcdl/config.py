"""Paths and defaults. Secrets live under the config dir, never in the repo."""

from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "bcdl"
DEFAULT_FORMAT = "flac"
# First available format wins. wav/aiff omitted — huge files, no gain over FLAC.
FORMAT_PREFERENCE = ("flac", "alac", "mp3-320")
KNOWN_FORMATS = (
    "flac",
    "alac",
    "wav",
    "aiff-lossless",
    "mp3-320",
    "mp3-v0",
    "aac-hi",
    "vorbis",
)
DEFAULT_DELAY_SECONDS = 3.0
USER_AGENT = f"{APP_NAME}/0.1 (+https://github.com/fallenAfter/bandcamp-download)"


def config_dir() -> Path:
    """Directory for cookies, collection cache, and download manifest.

    Override with BCDL_HOME (useful on a headless server).
    """
    override = os.environ.get("BCDL_HOME")
    if override:
        return Path(override).expanduser().resolve()
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg).expanduser() / APP_NAME
    return Path.home() / ".config" / APP_NAME


def ensure_config_dir() -> Path:
    path = config_dir()
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    return path
