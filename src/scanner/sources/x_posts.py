"""Recent X posts that name a held symbol. Report only. Sends no order.

The recent-search call needs SCANNER_X_BEARER_TOKEN. An empty token is
reported as not configured. The position card decides which post belongs
on which name. A post that names no holding is left off.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

import httpx

SEARCH_URL = "https://api.x.com/2/tweets/search/recent"
MAX_RESULTS = 10


def search_query(symbols: Sequence[str]) -> str:
    """Cashtags only, so a two-letter ticker is not a plain English word."""
    tags = []
    seen = set()
    for symbol in symbols:
        text = str(symbol).strip().upper()
        if not text or text in seen:
            continue
        seen.add(text)
        tags.append(f"${text}")
    if not tags:
        return ""
    return "(" + " OR ".join(tags) + ") -is:retweet"


def parse_posts(payload: object) -> tuple[tuple[datetime | None, str], ...]:
    """(when, text) from a recent-search body. A bad body is empty."""
    if not isinstance(payload, dict):
        return ()
    rows = payload.get("data")
    if not isinstance(rows, list):
        return ()
    found = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        text = " ".join(str(row.get("text") or "").split())
        if not text:
            continue
        when = _when(row.get("created_at"))
        found.append((when, text))
    return tuple(found)


def _when(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


async def recent(
    symbols: Sequence[str],
    client: httpx.AsyncClient,
    token: str,
) -> tuple[tuple[tuple[datetime | None, str], ...], str | None]:
    """Posts for these cashtags. ((), reason) when the feed cannot be read."""
    if not str(token or "").strip():
        return (), "not configured"
    query = search_query(symbols)
    if not query:
        return (), None
    try:
        response = await client.get(
            SEARCH_URL,
            headers={"Authorization": f"Bearer {token}"},
            params={"query": query, "max_results": MAX_RESULTS, "tweet.fields": "created_at"},
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        return (), type(exc).__name__
    return parse_posts(payload), None
