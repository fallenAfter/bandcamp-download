"""Store and load the Bandcamp session cookie.

Bandcamp has no fan API tokens. A logged-in `identity` cookie is the credential.
This tool never asks for a password — login on bandcamp.com is behind reCAPTCHA.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from bcdl.config import config_dir, ensure_config_dir

IDENTITY_ENV = "BANDCAMP_IDENTITY"
COOKIE_FILE = "cookies.json"


class AuthError(RuntimeError):
    pass


def cookie_path() -> Path:
    return config_dir() / COOKIE_FILE


def parse_identity(value: str) -> str:
    """Accept a raw cookie value, `identity=...`, or a full Cookie header."""
    value = value.strip().strip('"').strip("'")
    if not value:
        raise AuthError("No identity cookie value given.")
    if "identity=" in value:
        for part in value.replace("Cookie:", "", 1).split(";"):
            part = part.strip()
            if part.startswith("identity="):
                value = part[len("identity=") :].strip().strip('"')
                break
    if not value:
        raise AuthError("Could not find an identity cookie in the given value.")
    return value


def parse_cookies_txt(path: Path) -> str:
    """Read a Netscape cookies.txt export and return the identity value."""
    identity = None
    for line in path.read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        domain, _flag, _path, _secure, _expiry, name, value = parts[:7]
        if domain.lstrip(".").endswith("bandcamp.com") and name == "identity":
            identity = value
            break
    if not identity:
        raise AuthError(f"{path}: no identity cookie for bandcamp.com found.")
    return identity


def save_identity(identity: str) -> Path:
    path = ensure_config_dir() / COOKIE_FILE
    payload = {"identity": identity}
    path.write_text(json.dumps(payload, indent=2) + "\n")
    path.chmod(0o600)
    return path


def load_identity() -> str:
    path = cookie_path()
    if not path.exists():
        raise AuthError(
            f"No saved session at {path}. Run `bcdl login` first "
            f"(or set {IDENTITY_ENV})."
        )
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise AuthError(f"Could not parse {path}") from exc
    identity = data.get("identity")
    if not identity:
        raise AuthError(f"{path} has no identity cookie.")
    return identity


def identity_from_env() -> str | None:
    value = os.environ.get(IDENTITY_ENV)
    if not value:
        return None
    return parse_identity(value)


def cookies_dict(identity: str | None = None) -> dict[str, str]:
    if identity is None:
        identity = load_identity()
    return {"identity": identity}
