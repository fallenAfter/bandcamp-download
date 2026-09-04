"""Minimal Bandcamp HTTP helpers used to verify a session."""

from __future__ import annotations

import httpx

from bcdl.config import USER_AGENT

SUMMARY_URL = "https://bandcamp.com/api/fan/2/collection_summary"


class BandcampError(RuntimeError):
    pass


def whoami(identity: str, *, client: httpx.Client | None = None) -> tuple[int, str]:
    """Return (fan_id, username) for the logged-in identity cookie."""

    def _parse(response: httpx.Response) -> tuple[int, str]:
        try:
            data = response.json()
        except ValueError as exc:
            raise BandcampError(
                f"Unexpected response from Bandcamp (HTTP {response.status_code})."
            ) from exc
        fan_id = data.get("fan_id")
        username = (data.get("collection_summary") or {}).get("username")
        if not fan_id or not username:
            raise BandcampError(
                "Not logged in. The identity cookie is missing or expired."
            )
        return int(fan_id), str(username)

    if client is not None:
        return _parse(client.get(SUMMARY_URL))

    with httpx.Client(
        cookies={"identity": identity},
        headers={"User-Agent": USER_AGENT},
        timeout=30.0,
        follow_redirects=True,
    ) as http:
        return _parse(http.get(SUMMARY_URL))
