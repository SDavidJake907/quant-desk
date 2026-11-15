"""Write the position card. Peak watch calls this; it can also be run on its own.

    python scripts/position_card.py            # rewrite logs/positions/latest.md
    python scripts/position_card.py --force    # rewrite even if the card is fresh

Report only. Places no order.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, time, timedelta, timezone

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import peak_watch as pw  # scripts/ is on the path when this file runs

from scanner.config import settings
from scanner.entity import build_entity, for_symbol
from scanner.execution.alpaca import Alpaca
from scanner.execution.ownership import owner, owners
from scanner.ops import position_card as card
from scanner.sources import president, x_posts
from scanner.sources.president import Post
from scanner.sources.sec_tickers import EntityMasterError, load_universe

NEWS_URL = "https://data.alpaca.markets/v1beta1/news"
MARKET = ("SPY", "QQQ")
SOURCE_LABEL = {"truth": "Truth Social"}


def _api(symbol: str) -> str:
    return symbol.replace("-", ".")


def _source(name: str) -> str:
    if name in SOURCE_LABEL:
        return SOURCE_LABEL[name]
    if name.startswith("white_house:"):
        return "White House " + name.split(":", 1)[1].replace("_", " ")
    return name


def _when(moment: datetime) -> str:
    try:
        return moment.astimezone().strftime("%m-%d %H:%M")
    except (ValueError, OSError):
        return ""


async def alpaca_news(symbols: list[str], client: httpx.AsyncClient) -> tuple[list[dict], str | None]:
    """Headlines since 04:00 ET. ([], reason) when the wire cannot be read."""
    from scanner.execution import satellite as sat
    now = datetime.now(sat.ET)
    start = min(datetime.combine(now.date(), time(4), tzinfo=sat.ET), now - timedelta(minutes=1))
    try:
        response = await client.get(NEWS_URL, timeout=20, headers={
            "APCA-API-KEY-ID": settings.alpaca_key_id,
            "APCA-API-SECRET-KEY": settings.alpaca_secret_key,
        }, params={
            "symbols": ",".join(_api(s) for s in symbols), "limit": 50, "sort": "desc",
            "start": start.astimezone(timezone.utc).isoformat(),
        })
        response.raise_for_status()
        return list(response.json().get("news") or []), None
    except (httpx.HTTPError, ValueError) as exc:
        return [], f"{type(exc).__name__}"


async def _entities(client: httpx.AsyncClient, symbols: list[str]) -> dict:
    """SEC name per stock. Empty when the file cannot be read; the card still builds."""
    try:
        universe = await load_universe(client)
    except EntityMasterError:
        return {}
    found = {}
    for symbol in symbols:
        row = universe.get(symbol.upper())
        if row:
            found[symbol] = build_entity(symbol, row[0], row[1])
    return found


def by_symbol(items: list[dict], symbol: str, entity=None) -> list[str]:
    """Headlines for one symbol. A stock with a known name keeps only stories that use that name."""
    return [str(item.get("headline") or "") for item in for_symbol(items, _api(symbol), entity)]


def _posts(found: tuple[Post, ...]) -> list[tuple[str, str, str]]:
    return [(_source(p.source), _when(p.when), p.text) for p in found]


def _holding(position: dict, book: str, stop: float | None, reading, sector: str) -> card.Holding:
    values = dict(reading.values) if reading is not None else None
    atr = reading.atr_pct if reading is not None else None
    session_day = reading.session_day if reading is not None else ""
    return card.Holding(
        symbol=position["symbol"], book=book, qty=float(position.get("qty") or 0),
        entry=float(position.get("avg_entry_price") or 0),
        last=float(position.get("current_price") or 0),
        upl=float(position.get("unrealized_pl") or 0),
        upl_pct=float(position.get("unrealized_plpc") or 0) * 100,
        today=float(position.get("unrealized_intraday_pl") or 0),
        stop=stop, atr_pct=atr, sector=sector, values=values, session_day=session_day,
    )


async def refresh(client: httpx.AsyncClient, positions: list[dict], readings: list,
                  stops: dict, book_of: dict, *, session: str, force: bool = False) -> bool:
    """Rewrite the card when it is due. False when the current card is still fresh."""
    now = datetime.now(timezone.utc)
    if not force and not card.due(card.PATH, now):
        return False
    symbols = [p["symbol"] for p in positions]
    have = {r.symbol: r for r in readings}
    missing = [p for p in positions if p["symbol"] not in have]
    if missing:
        extra = await pw.readings_for(client, missing, stops, lambda _line: None, session)
        have.update({r.symbol: r for r in extra})
    wanted = list(dict.fromkeys([*symbols, *MARKET]))
    items, news_error = await alpaca_news(wanted, client)
    names = await _entities(client, symbols)
    found, failed, tried = await president.collect(client)
    x_found, x_error = await x_posts.recent(symbols, client, settings.x_bearer_token)
    sectors = card.sector_map()
    holdings = [
        _holding(p, book_of.get(p["symbol"], ""), stops.get(p["symbol"]),
                 have.get(p["symbol"]), sectors.get(p["symbol"].upper(), ""))
        for p in positions
    ]
    market = list(dict.fromkeys([*by_symbol(items, "SPY"), *by_symbol(items, "QQQ")]))
    stored = card.load_labels(card.LABELS)
    held_at, held_labels = stored if stored else ("", None)
    text = card.render(
        written=datetime.now().astimezone(), holdings=holdings, market=market,
        headlines={s: by_symbol(items, s, None if s in MARKET else names.get(s)) for s in symbols},
        news_error=news_error,
        posts=_posts(found), feed_failed=failed, feed_tried=tried,
        held_labels=held_labels, held_at=held_at,
        x_posts=[(_when(when) if when else "", text) for when, text in x_found],
        x_error=x_error,
    )
    card.save(card.PATH, text)
    return True


async def run(force: bool) -> int:
    import gemma_review as grv
    async with httpx.AsyncClient(timeout=120) as client:
        broker = Alpaca(settings.alpaca_key_id, settings.alpaca_secret_key, client)
        from scanner.execution import sessions
        session = await sessions.current(broker)
        positions = await broker._get("/v2/positions") or []
        orders = await broker._get("/v2/orders", status="all", limit=500, nested="true") or []
        stops = grv.live_stops(orders)
        owner_map = owners(orders)
        book_of = {p["symbol"]: owner(p["symbol"], owner_map) for p in positions}
        wrote = await refresh(client, positions, [], stops, book_of, session=session, force=force)
    print("position card written" if wrote else "position card still fresh")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="rewrite even if the card is under 25 minutes old")
    raise SystemExit(asyncio.run(run(parser.parse_args().force)))
