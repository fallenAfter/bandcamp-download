"""Bandcamp HTTP session.

Uses the same undocumented endpoints the website calls. They can change
without notice.
"""

from __future__ import annotations

import html
import json
import re
import time
from html.parser import HTMLParser
from typing import Any

import httpx

from bcdl.config import USER_AGENT

SUMMARY_URL = "https://bandcamp.com/api/fan/2/collection_summary"
COLLECTION_ITEMS_URL = "https://bandcamp.com/api/fancollection/1/collection_items"
HIDDEN_ITEMS_URL = "https://bandcamp.com/api/fancollection/1/hidden_items"


class BandcampError(RuntimeError):
    pass


class SchemaChanged(BandcampError):
    """A field we depend on moved or vanished."""


class _PagedataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blob: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.blob is not None:
            return
        attrd = dict(attrs)
        if attrd.get("id") == "pagedata" and attrd.get("data-blob"):
            self.blob = attrd["data-blob"]


def parse_pagedata(text: str) -> dict[str, Any]:
    parser = _PagedataParser()
    parser.feed(text)
    if parser.blob:
        return json.loads(parser.blob)
    match = re.search(r'id="pagedata"[^>]*data-blob="([^"]+)"', text)
    if match:
        return json.loads(html.unescape(match.group(1)))
    raise SchemaChanged("No #pagedata blob found — are we logged in?")


def dotted(data: dict[str, Any], path: str, default: Any = None, *, required: bool = False) -> Any:
    node: Any = data
    for part in path.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            if required:
                raise SchemaChanged(
                    f"Bandcamp's response no longer has {path!r} (stopped at {part!r})."
                )
            return default
    return node


class Client:
    def __init__(
        self,
        identity: str,
        *,
        timeout: float = 60.0,
        http: httpx.Client | None = None,
    ) -> None:
        self._owns_http = http is None
        self.http = http or httpx.Client(
            cookies={"identity": identity},
            headers={"User-Agent": USER_AGENT},
            timeout=timeout,
            follow_redirects=True,
        )

    def close(self) -> None:
        if self._owns_http:
            self.http.close()

    def __enter__(self) -> Client:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def whoami(self) -> tuple[int, str]:
        data = self.get_json(SUMMARY_URL)
        fan_id = dotted(data, "fan_id")
        username = dotted(data, "collection_summary.username")
        if not fan_id or not username:
            raise BandcampError(
                "Not logged in. The identity cookie is missing or expired."
            )
        return int(fan_id), str(username)

    def get(self, url: str) -> httpx.Response:
        return self._request("GET", url)

    def get_json(self, url: str) -> dict[str, Any]:
        response = self.get(url)
        try:
            return response.json()
        except ValueError as exc:
            raise SchemaChanged(f"{url} returned non-JSON (HTTP {response.status_code})") from exc

    def post_json(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = self._request("POST", url, json=payload)
        try:
            return response.json()
        except ValueError as exc:
            raise SchemaChanged(f"{url} returned non-JSON (HTTP {response.status_code})") from exc

    def pagedata(self, url: str) -> dict[str, Any]:
        return parse_pagedata(self.get(url).text)

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        backoff = 2.0
        last_error: Exception | None = None
        for attempt in range(5):
            try:
                response = self.http.request(method, url, **kwargs)
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt == 4:
                    raise BandcampError(f"{method} {url} failed: {exc}") from exc
                time.sleep(backoff)
                backoff *= 2
                continue
            if response.status_code == 429 or response.status_code >= 500:
                retry_after = response.headers.get("retry-after")
                wait = float(retry_after) if (retry_after or "").isdigit() else backoff
                if attempt == 4:
                    raise BandcampError(
                        f"{method} {url} still failing after retries "
                        f"(HTTP {response.status_code})"
                    )
                time.sleep(wait)
                backoff *= 2
                continue
            if response.status_code >= 400:
                raise BandcampError(f"{method} {url} -> HTTP {response.status_code}")
            return response
        raise BandcampError(f"{method} {url} failed: {last_error}")


def whoami(identity: str, *, client: httpx.Client | None = None) -> tuple[int, str]:
    """Return (fan_id, username) for the logged-in identity cookie."""
    if client is not None:
        return Client(identity, http=client).whoami()
    with Client(identity) as session:
        return session.whoami()
