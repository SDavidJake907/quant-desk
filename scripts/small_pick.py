"""The small-cap book's 9:45 ET pick (rules in scanner/execution/small_book.py). No model is asked.

    python scripts/small_pick.py            # show what it would queue
    python scripts/small_pick.py --submit   # queue it for the 10:00 ET buy (peak watch sends it)

Operator 2026-10-27: 10% of the account in up to 4 S&P SmallCap 600 names, $5+, $2M+ traded a
day, only while IWM is above its 200-day. The rules book's checks, strongest 12-1 momentum first;
sizing, stops and exits are the plan's (execution/plan_run.py). Runs after rules_pick.py in the
05:45 AK task (scheduled_gemma.bat).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.advisor import gemma_pool as gp  # noqa: E402
from scanner.config import settings  # noqa: E402
from scanner.execution import bookorders, buy_queue, ownership, rules_pick, small_book as sb  # noqa: E402
from scanner.execution import satellite as sat, sessions  # noqa: E402
from scanner.execution.alpaca import Alpaca  # noqa: E402
from scanner.execution.ownership import SMALL  # noqa: E402
from scanner.execution.policy import TradingPolicy  # noqa: E402
from scanner.execution.safety import kill_switch_active  # noqa: E402
from scanner.execution.scorecard import read_rows  # noqa: E402
from scanner.features.bars import fetch_bars, fetch_many  # noqa: E402
from scanner.features.intraday_context import atr_pct  # noqa: E402
from scanner.features.technicals import compute  # noqa: E402
from scanner.sources import alpaca_bars, composite  # noqa: E402
from scanner.sources import calendar as event_calendar  # noqa: E402
from scanner.sources.index_history import SP600  # noqa: E402

POLICY = TradingPolicy()


def sold_recently(rows, since: datetime) -> set[str]:
    """Names this book closed since `since` (audit small_exit events)."""
    out = set()
    for r in rows:
        if r.get("event") == "small_exit" and r.get("symbol"):
            try:
                if datetime.fromisoformat(str(r["at"])) >= since:
                    out.add(str(r["symbol"]))
            except ValueError:
                continue
    return out


async def main(submit: bool) -> int:
    async with httpx.AsyncClient(timeout=120) as client:
        broker = Alpaca(settings.alpaca_key_id, settings.alpaca_secret_key, client)
        if submit and await sessions.current(broker) != sessions.REGULAR:
            print("not regular hours -- the small-cap book buys in regular hours only")
            return 0
        if kill_switch_active(settings.kill_switch_path):
            print("kill switch active -- nothing queued")
            return 0
        gate = sat.gate_open((await fetch_bars(POLICY.small_gate_symbol, client, range_="2y")).closes)
        if gate is not True:
            print(f"small caps: {POLICY.small_gate_symbol} gate closed (not above its {sat.GATE_DAYS}-day "
                  "average) -- no new buys; holdings exit by their own rules")
            return 0
        positions = await broker._get("/v2/positions")
        open_orders = await broker._get("/v2/orders", status="open", nested="true")
        owner_map = ownership.owners(await broker._get("/v2/orders", status="all", limit=500, nested="true"))
        mine = [p["symbol"] for p in positions if ownership.owner(p["symbol"], owner_map) == SMALL]
        working = {o["symbol"] for o in open_orders if o.get("side") == "buy"
                   and ownership.strategy_of(o.get("client_order_id")) == SMALL}
        taken = {p["symbol"] for p in positions} | {o["symbol"] for o in open_orders}
        slots = rules_pick.free_slots(max_positions=POLICY.small_max_positions, held=len(mine),
                                      working=len(working))
        print(f"small caps: {POLICY.small_gate_symbol} gate open; holds {sorted(mine) or 'nothing'}; "
              f"{slots} free of {POLICY.small_max_positions}")
        if slots == 0:
            return 0

        members = await composite.load_universe(client, (SP600,))
        sectors = {m.symbol: m.sector for m in members}
        daily, _ = await fetch_many(tuple(sectors), client, range_="2y")
        bars = {s: b for s, b in daily.items() if b is not None and len(b.closes) >= 130 and sb.liquid(
            b.closes, b.volumes, min_price=POLICY.small_min_price,
            min_dollar_volume=POLICY.small_min_dollar_volume)}
        rows = gp.rows_for(bars, sectors, momentum={s: compute(b).momentum_12_1 for s, b in bars.items()},
                           atr={s: atr_pct(b) for s, b in bars.items()}, momentum_rank={})
        mom = {r.symbol: r.mom_12_1 for r in rows}

        def price_ok(s: str) -> bool:
            c = bars[s].closes
            month = sat.month_return(c)
            return (sat.momentum_top(s, mom) and c[-1] / c[-127] > 1
                    and c[-1] > sum(c[-50:]) / 50 and month is not None and month > 0)

        candidates = [r for r in rules_pick.ranked(rows, exclude=taken) if price_ok(r.symbol)]
        print(f"  {len(rows)} of {len(sectors)} S&P 600 names pass ${POLICY.small_min_price:g}+ and "
              f"${POLICY.small_min_dollar_volume / 1e6:g}M a day; {len(candidates)} pass momentum / "
              f"6-month / 50-day / last month: {[r.symbol for r in candidates[:10]]}")
        if not candidates:
            return 0
        today = date.today()
        found, failed = await event_calendar.with_moomoo(
            frozenset(r.symbol for r in candidates), client, horizon=sat.EARNINGS_BUFFER_DAYS + 3, today=today)
        sold = sold_recently(read_rows(settings.audit_log_path),
                             datetime.now().astimezone() - timedelta(days=sat.REBUY_LOOKBACK_DAYS))

        def check(row: gp.PoolRow, held_sectors) -> str | None:
            if failed:
                return "earnings calendar unreadable -- refused (fails closed)"
            c, e = bars[row.symbol].closes, found.get(row.symbol)
            return sat.buy_refusal(
                gate=gate, gate_name=POLICY.small_gate_symbol, top_momentum=sat.momentum_top(row.symbol, mom),
                ret_6m=(c[-1] / c[-127] - 1) * 100, above_50d=c[-1] > sum(c[-50:]) / 50,
                ret_1m=sat.month_return(c),
                earnings_in_days=(e.on - today).days if e else None, sector=row.sector,
                held_sectors=held_sectors, sold_recently=row.symbol in sold,
                new_20d_high=sat.new_closing_high(c))

        picks, refused = sb.pick(candidates, check=check,
                                 held_sectors=sat.sector_counts(mine, sectors), slots=slots)
        for s, why in refused.items():
            print(f"  {s:<6} skipped -- {why}")
        if not picks:
            print("  every candidate refused -- nothing queued")
            return 0
        rank = {r.symbol: i for i, r in enumerate(candidates, 1)}
        items = {r.symbol: sb.queue_item(r, rank=rank[r.symbol], passing=len(candidates)) for r in picks}
        try:
            snaps = await alpaca_bars.snapshots(list(items), client)
        except Exception as exc:                              # noqa: BLE001 -- the recheck skips the drop test
            print(f"  no decision prices for the 10:00 recheck ({type(exc).__name__})")
            snaps = {}
        items = {s: {**v, "alerts": [], **({"price": float(snaps[s]["latestTrade"]["p"])}
                                           if (snaps.get(s) or {}).get("latestTrade") else {})}
                 for s, v in items.items()}
        for s, v in items.items():
            print(f"  QUEUE {s:<6} {v['reason']}")
        if not submit:
            print("  (dry run -- nothing queued)")
            return 0
        path = Path(sb.QUEUE_PATH)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(buy_queue.make(datetime.now(sat.ET).date(), items), indent=1), encoding="utf-8")
        for s, v in items.items():
            bookorders.log("small_pick", s, v["reason"], POLICY)
        print(f"  queued for 10:00 ET (peak watch sends them, sized then): {list(items)}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--submit", action="store_true", help="write the 10:00 ET buy queue")
    raise SystemExit(asyncio.run(main(p.parse_args().submit)))
