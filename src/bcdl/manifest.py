"""Record completed downloads so later runs can skip them."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bcdl.config import config_dir, ensure_config_dir

MANIFEST_FILE = "manifest.json"


def manifest_path() -> Path:
    return config_dir() / MANIFEST_FILE


def load_manifest() -> dict[str, Any]:
    path = manifest_path()
    if not path.exists():
        return {"items": {}}
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return {"items": {}}
    if "items" not in data or not isinstance(data["items"], dict):
        return {"items": {}}
    return data


def save_manifest(manifest: dict[str, Any]) -> Path:
    path = ensure_config_dir() / MANIFEST_FILE
    path.write_text(json.dumps(manifest, indent=2) + "\n")
    return path


def record_download(
    key: str,
    *,
    artist: str,
    title: str,
    fmt: str,
    path: str,
) -> None:
    manifest = load_manifest()
    manifest["items"][key] = {
        "key": key,
        "artist": artist,
        "title": title,
        "format": fmt,
        "path": path,
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
    }
    save_manifest(manifest)


def is_downloaded(key: str) -> bool:
    entry = load_manifest().get("items", {}).get(key)
    if not entry:
        return False
    recorded = entry.get("path")
    return bool(recorded) and Path(recorded).exists()
