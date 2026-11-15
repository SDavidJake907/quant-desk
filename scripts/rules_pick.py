"""The rules book's 9:45 ET pick (rules in scanner/execution/rules_pick.py). No model is asked.

    python scripts/rules_pick.py            # show what it would queue
    python scripts/rules_pick.py --submit   # queue it for the 10:00 ET buy (peak watch sends it)

From 2026-11-03: the S&P 500 through the scanner's floor, ranked by the return from 10 months
ago to 7 months ago. The strongest names fill the free slots of 10, on every 5th weekday.
The buy order has no stop leg. The intent records the 2.5 ATR stop, never more than 8%
under the limit, and protect.py arms it once the shares are held. The close exits are in
execution/satellite.py. Saves the day's pool for the breakout book (execution/orb.py).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date, datetime
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.advisor import gemma_pool as gp  # noqa: E402
from scanner.config import settings  # noqa: E402
from scanner.execution import bookorders, buy_queue, ownership, rules_pick as rp  # noqa: E402
from scanner.execution import satellite as sat, sessions  # noqa: E402
from scanner.execution.alpaca import Alpaca  # noqa: E402
from scanner.execution.ownership import GEMMA  # noqa: E402
from scanner.execution.policy import TradingPolicy  # noqa: E402
from scanner.execution.safety import kill_switch_active  # noqa: E402
from scanner.ops import accel_shadow  # noqa: E402
from scanner.features.bars import fetch_many  # noqa: E402
from scanner.features.intraday_context import atr_pct  # noqa: E402
from scanner.features.technicals import compute  # noqa: E402
from scanner.sources import alpaca_bars, composite, market_caps  # noqa: E402
try:
    from scanner.sources.moomoo_quote import caps_for, overlay_caps  # noqa: E402
except ImportError:  # the local quote reader is not part of this tree
    caps_for = overlay_caps = None
from scanner.sources.index_history import SP500  # noqa: E402

POLICY = TradingPolicy()


async def pool_rows(client: httpx.AsyncClient) -> tuple[list[gp.PoolRow], dict[str, float | None]]:
    """The S&P 500 through the scanner's floor and the $2B cap floor, with each name's
    10-month to 7-month return. Two years of bars cover that window."""
    members = await composite.load_universe(client, (SP500,))
    sectors = {m.symbol: m.sector for m in members}
    caps = await market_caps.load(client)
    missing = tuple(s for s in sectors if s not in caps)
    if missing and caps_for is not None and overlay_caps is not None:
        try:
            filled = caps_for(missing)
            caps = overlay_caps(caps, filled)
            if filled:
                print(f"  market cap filled {len(filled)} names Nasdaq did not price")
        except Exception as exc:                              # noqa: BLE001 -- unknown cap stays kept
            print(f"  market cap fill unavailable ({type(exc).__name__}) -- unknown caps stay in")
    names = tuple(s for s in sectors if gp.large_enough(s, caps))
    daily, _ = await fetch_many(names, client, range_="2y")
    bars = {s: b for s, b in daily.items()
            if b is not None and len(b.closes) >= 30 and gp.tradeable(
                b.closes, b.highs, b.lows, b.volumes, min_price=POLICY.min_price,
                min_range_pct=POLICY.min_range_pct, min_dollar_volume=POLICY.min_dollar_volume)}
    scores = {s: sat.momentum_window(b.closes) for s, b in bars.items()}
    rows = gp.rows_for(bars, sectors, momentum={s: compute(b).momentum_12_1 for s, b in bars.items()},
                       atr={s: atr_pct(b) for s, b in bars.items()}, momentum_rank={})
    return rows, scores


async def decision_prices(client: httpx.AsyncClient, symbols: list[str]) -> dict[str, float]:
    """Each pick's price now, for the 10:00 recheck (buy_queue.recheck skips a 2% drop)."""
    try:
        snaps = await alpaca_bars.snapshots(symbols, client)
    except Exception as exc:                                  # noqa: BLE001 -- the recheck skips the drop test
        print(f"  no decision prices for the 10:00 recheck ({type(exc).__name__})")
        return {}
    return {s: float(v["latestTrade"]["p"]) for s, v in snaps.items() if (v or {}).get("latestTrade")}


ACCEL_LOG = Path("data/shadow/accel.jsonl")


def save_accel(day: date, rows, picked) -> None:
    """The acceleration shadow (ops/accel_shadow.py): every name passing the buy checks today,
    with its acceleration and whether it was picked. Recorded only; nothing reads it to trade."""
    ACCEL_LOG.parent.mkdir(parents=True, exist_ok=True)
    with ACCEL_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(accel_shadow.record(day.isoformat(), rows, picked)) + "\n")


def write_queue(today: date, items: dict[str, dict]) -> None:
    path = Path(buy_queue.PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(buy_queue.make(today, items), indent=1), encoding="utf-8")


async def main(submit: bool) -> int:
    async with httpx.AsyncClient(timeout=120) as client:
        broker = Alpaca(settings.alpaca_key_id, settings.alpaca_secret_key, client)
        if submit and await sessions.current(broker) != sessions.REGULAR:
            print("not regular hours -- the rules book buys in regular hours only")
            return 0
        if kill_switch_active(settings.kill_switch_path):
            print("kill switch active -- nothing queued")
            return 0
        positions = await broker._get("/v2/positions")
        open_orders = await broker._get("/v2/orders", status="open", nested="true")
        owner_map = ownership.owners(await broker._get("/v2/orders", status="all", limit=500, nested="true"))
        mine = [p["symbol"] for p in positions if ownership.owner(p["symbol"], owner_map) == GEMMA]
        working = {o["symbol"] for o in open_orders if o.get("side") == "buy"
                   and ownership.strategy_of(o.get("client_order_id")) == GEMMA}
        taken = {p["symbol"] for p in positions} | {o["symbol"] for o in open_orders}
        slots = rp.free_slots(max_positions=sat.RULES_TOP_N, held=len(mine), working=len(working))
        today_et = datetime.now(sat.ET).date()
        print(f"rules book: holds {sorted(mine) or 'nothing'}; {slots} free of {sat.RULES_TOP_N}")
        if not sat.rules_rebalance_due(today_et):
            print(f"  not a refill day (every {sat.RULES_REBALANCE_DAYS} weekdays from {sat.RULES_BOOK_FROM})")
            return 0

        rows, scores = await pool_rows(client)
        if submit:
            gp.save(rows, today_et)                           # the breakout book trades from it
        eligible = [r for r in rows if scores.get(r.symbol) is not None]
        candidates = rp.momentum_ranked(rows, scores, exclude=taken)
        print(f"  {len(eligible)} of {len(rows)} S&P 500 names have a 10-month to 7-month return; "
              f"strongest: {[r.symbol for r in candidates[:10]]}")
        picks = candidates[:slots]
        if slots == 0 or not picks:
            print("  nothing to queue")
            if submit:
                save_accel(today_et, eligible, ())
            return 0
        rank = {r.symbol: i for i, r in enumerate(candidates, 1)}
        items = {r.symbol: rp.rotation_item(r, rank=rank[r.symbol], passing=len(candidates),
                                            momentum=float(scores[r.symbol])) for r in picks}
        prices = await decision_prices(client, list(items))
        items = {s: {**v, "alerts": [], **({"price": prices[s]} if s in prices else {})} for s, v in items.items()}
        for s, v in items.items():
            print(f"  QUEUE {s:<6} {v['reason']}")
        if not submit:
            print("  (dry run -- nothing queued)")
            return 0
        write_queue(today_et, items)
        save_accel(today_et, eligible, items)
        for s, v in items.items():
            bookorders.log("rules_pick", s, v["reason"], POLICY)
        print(f"  queued for 10:00 ET (peak watch sends them, sized then): {list(items)}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--submit", action="store_true", help="write the 10:00 ET buy queue")
    raise SystemExit(asyncio.run(main(p.parse_args().submit)))
