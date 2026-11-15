"""Watch every open position for profit given back. Report only. Places no order.

    python scripts/position_watch.py

Weekdays 05:33-12:03 Alaska, every 30 minutes (task stock-scanner-position-watch).
Writes logs/position_watch/latest.md and appends that day's file. A Windows
toast fires when a name newly gives back $50 or half of a 5% gain. The same
name stays quiet until the next session. Nothing is sold and no stop moves.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.config import settings
from scanner.execution import ownership
from scanner.execution.alpaca import Alpaca
from scanner.execution.plan_run import entry_dates
from scanner.features.bars import fetch_many
from scanner.ops import notify
from scanner.ops import position_watch as watch

AK = ZoneInfo("America/Anchorage")
OUT = Path("logs/position_watch")
STATE = Path("data/monitor/position_watch.json")


def _load_state(day: str) -> set[str]:
    try:
        saved = json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    if saved.get("day") != day:
        return set()
    return {str(symbol) for symbol in saved.get("flagged") or []}


def _save_state(day: str, flagged: set[str]) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({"day": day, "flagged": sorted(flagged)}, indent=1),
                     encoding="utf-8")


async def _rows(client: httpx.AsyncClient, broker: Alpaca, today) -> list[watch.Watch]:
    positions = await broker._get("/v2/positions") or []
    orders = await broker._get("/v2/orders", status="all", limit=500, nested="true",
                               direction="desc")
    if not isinstance(orders, list):
        orders = []
    owners = ownership.owners(orders)
    entered = entry_dates(orders, ownership.STRATEGIES)
    symbols = tuple(str(p["symbol"]) for p in positions if p.get("symbol"))
    # The daily cache is stamped for the calendar day. A fetch before the high
    # prints would hide that high from every later pass. Twenty minutes is
    # inside the 30-minute schedule, so each run reads the high again.
    bars, errors = await fetch_many(symbols, client, range_="6mo", ttl_seconds=20 * 60)
    for error in errors:
        print(f"  no daily bars: {error}")
    rows: list[watch.Watch] = []
    for position in positions:
        symbol = str(position.get("symbol") or "")
        if not symbol:
            continue
        history = bars.get(symbol)
        highs = tuple(zip(history.dates, history.highs)) if history is not None else ()
        closes = tuple(zip(history.dates, history.closes)) if history is not None else ()
        entry_day = entered.get(symbol)
        entry = float(position["avg_entry_price"])
        last = float(position["current_price"])
        rows.append(watch.Watch(
            symbol=symbol,
            book=owners.get(symbol, ownership.UNKNOWN),
            qty=float(position["qty"]),
            entry=entry,
            last=last,
            prior_close=float(position["lastday_price"]) if position.get("lastday_price") else None,
            best=watch.best_price(entry, last, highs, entry_day),
            best_close=watch.best_close(entry, last, closes, entry_day, today),
            open_pl=float(position.get("unrealized_pl") or 0),
            day_pl=float(position.get("unrealized_intraday_pl") or 0),
            entry_day=entry_day,
        ))
    return rows


async def run() -> int:
    now = datetime.now(AK)
    stamp = now.strftime("%a %Y-%m-%d %H:%M Alaska")
    async with httpx.AsyncClient(timeout=30) as client:
        broker = Alpaca(settings.alpaca_key_id, settings.alpaca_secret_key, client)
        rows = await _rows(client, broker, now.date())
    page = watch.render(rows, stamp, today=now.date())
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "latest.md").write_text(page, encoding="utf-8")
    with (OUT / f"{now.date().isoformat()}.md").open("a", encoding="utf-8") as handle:
        handle.write(page)
        handle.write("\n")
    day = now.date().isoformat()
    previous = _load_state(day)
    fresh = watch.new_flags(previous, rows, today=now.date())
    flagged = {row.symbol for row in rows if watch.flagged(row, today=now.date())}
    _save_state(day, flagged)
    print(page)
    if fresh:
        names = ", ".join(fresh)
        notify.toast("Position watch", f"Profit given back: {names}. Report only. No order.")
        print(f"flagged {names}")
    else:
        print(f"watched {len(rows)} position(s); no new giveback flag")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
