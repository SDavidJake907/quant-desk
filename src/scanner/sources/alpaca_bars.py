"""Alpaca stock bars for many symbols at once (the breakout book, execution/orb.py).

One request carries up to CHUNK symbols and is paged with next_page_token.
FEEDS: this account's plan gives SIP (all exchanges) only for data older than
15 minutes ("subscription does not permit querying recent SIP data", checked
2026-10-23) and IEX (one exchange, ~2-3% of volume) in real time. The book
compares IEX with IEX -- today's opening volume against IEX's usual -- so the
ratio holds even though the absolute numbers are small.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

import httpx

from scanner.config import settings

URL = "https://data.alpaca.markets/v2/stocks/bars"
CHUNK = 200
Bar = dict   # {"t", "o", "h", "l", "c", "v"} as Alpaca sends it


def _headers() -> dict[str, str]:
    return {"APCA-API-KEY-ID": settings.alpaca_key_id,
            "APCA-API-SECRET-KEY": settings.alpaca_secret_key}


def _rfc3339(moment: datetime) -> str:
    return moment.astimezone().isoformat(timespec="seconds")


async def bars(symbols: Sequence[str], client: httpx.AsyncClient, *, timeframe: str,
               start: datetime, end: datetime, feed: str = "iex") -> dict[str, list[Bar]]:
    """{symbol: bars oldest first} between `start` and `end`. Raises on an HTTP error:
    a half-read market is not a smaller market."""
    out: dict[str, list[Bar]] = {}
    for i in range(0, len(symbols), CHUNK):
        token = None
        while True:
            params = {"symbols": ",".join(symbols[i:i + CHUNK]), "timeframe": timeframe,
                      "start": _rfc3339(start), "end": _rfc3339(end), "feed": feed,
                      "limit": 10000, "adjustment": "raw"}
            if token:
                params["page_token"] = token
            r = await client.get(URL, headers=_headers(), params=params, timeout=30.0)
            r.raise_for_status()
            body = r.json()
            for s, rows in (body.get("bars") or {}).items():
                out.setdefault(s, []).extend(rows)
            token = body.get("next_page_token")
            if not token:
                break
    return out


LATEST_QUOTE = "https://data.alpaca.markets/v2/stocks/{symbol}/quotes/latest"


def recent_sip_verdict(status_code: int, body: str = "") -> str:
    """Whether this account just read a SIP quote.

    'ok' is HTTP 200. 'refused' is the free-plan block on recent SIP. 'unchecked'
    is any other failure. A deposit waits until this is 'ok'. Paper scans stay
    on the feed that already works while it is not.
    """
    if status_code == 200:
        return "ok"
    if status_code == 403 or "recent sip" in body.lower():
        return "refused"
    return "unchecked"


async def latest_quote_status(symbol: str, client: httpx.AsyncClient, *,
                               feed: str = "sip") -> tuple[int, str]:
    """HTTP status and a short body for one latest quote. Read-only. Does not raise."""
    try:
        response = await client.get(
            LATEST_QUOTE.format(symbol=symbol), headers=_headers(),
            params={"feed": feed}, timeout=20.0)
    except httpx.HTTPError as exc:
        return 0, str(exc)
    return response.status_code, response.text[:400]


SNAPSHOTS = "https://data.alpaca.markets/v2/stocks/snapshots"


async def snapshots(symbols: Sequence[str], client: httpx.AsyncClient, *, feed: str = "iex") -> dict[str, dict]:
    """{symbol: Alpaca snapshot} (latestTrade, dailyBar, prevDailyBar), CHUNK symbols a request."""
    out: dict[str, dict] = {}
    for i in range(0, len(symbols), CHUNK):
        r = await client.get(SNAPSHOTS, headers=_headers(), timeout=30.0,
                             params={"symbols": ",".join(symbols[i:i + CHUNK]), "feed": feed})
        r.raise_for_status()
        out.update(r.json() or {})
    return out
