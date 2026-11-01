"""Peak watch: every 5 minutes, 24/5, mark READY and sell before it is too late.

    python scripts/peak_watch.py            # one pass (task stock-scanner-peakwatch, every 5 min)
    python scripts/peak_watch.py --dry-run  # show what it would do, send nothing

Operator 2026-10-22: "the frist one should be mark ready to sell, and befor it to late sell it";
chose "5-min check, auto-sell". For every position in Gemma's book and the operator's picks:
  READY  in profit and >= 4 of the six peak checks (scanner/ops/peak_sell.py) -> marked
  SELL   a marked position whose 5-minute SuperTrend has turned DOWN while still in profit
Gemma's sells go through bookorders.sell (audit log, her memory sees the reason); picks through
picks.txt + picks_sync. Marks: data/monitor/peak_marks.json. Log: logs/peak_watch/DATE.md.
An operator rule, not a tested edge (FREEZE.md).

24/5 (operator 2026-10-22): runs in every session the broker's clock says is open -- premarket,
regular, after-hours, overnight. Outside regular hours the 5-minute trend reads the pre/post-market
candles and must be fresh (ps.extended_trend); overnight Yahoo has none, so nothing sells on trend
then. Sells outside regular hours are limit orders (bookorders.sell with the session).

FROM 2026-10-26, THE CORE-AND-SATELLITE PLAN (execution/satellite.py, plan_run.py): the
peak-sell and the profit lock are off; each pass runs the plan's exits (hard stop, and in
the last 10 minutes the trailing / time / earnings exits), and in regular hours from 10:00
ET sends Gemma's queued buys and the SPY core. READY marks are still shown, as information.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import gemma_review as grv  # scripts/ is on the path when this script runs
import gemma_trade as gtr

from scanner.config import settings
from scanner.execution import bookorders, ownership, plan_run, profit_lock, sell_queue, sessions
from scanner.execution.alpaca import Alpaca, BrokerError
from scanner.execution.ownership import GEMMA, PICKS, SMALL
from scanner.features.bars import fetch_many
from scanner.features.intraday import EXCHANGE_TZ
from scanner.features.technicals import average_true_range_pct
from scanner.ops import notify
from scanner.ops import peak_sell as ps
from scanner.signals.candles24 import yahoo_5m

MARKS = Path("data/monitor/peak_marks.json")
#: Off from 2026-10-26 (the core-and-satellite plan, execution/satellite.py): its +2%
#: trigger caught bounces, not tops; the plan's trailing exit (execution/plan_run.py) sells.
PEAK_SELL = False
SELL_QUEUE = Path(sell_queue.PATH)
OUT = Path("logs/peak_watch")


async def readings_for(client, positions, stops: dict, log,
                       session: str = sessions.REGULAR) -> list[ps.Readings]:
    """Live peak readings (with the ATR stop note) for the given Alpaca positions. Outside
    regular hours the 5-minute trend comes from the extended-hours candles (None if stale)."""
    if not positions:
        return []
    symbols = tuple(p["symbol"] for p in positions)
    daily, _ = await fetch_many(symbols, client, range_="1y")
    now = datetime.now(EXCHANGE_TZ).replace(tzinfo=None, second=0, microsecond=0)
    out = []
    for p in positions:
        s = p["symbol"]
        try:
            raw = await yahoo_5m(s, client, range_="1mo")
        except (httpx.HTTPError, KeyError, IndexError, TypeError) as exc:
            log(f"  no 5m candles for {s} ({exc})")
            raw = []
        every = [(t.astimezone(EXCHANGE_TZ).replace(tzinfo=None), *rest) for t, *rest in raw]
        five = [x for x in ps.session(every) if x[0] <= now - timedelta(minutes=5)]    # completed candles only
        price = float(p["current_price"])
        closes = list(daily[s].closes) if s in daily else []
        if closes and daily[s].dates[-1] == now.date():
            closes[-1] = price
        elif closes:
            closes.append(price)
        r = ps.readings(s, float(p["unrealized_plpc"]) * 100, five, closes)
        if session != sessions.REGULAR:
            r = dataclasses.replace(r, values={**r.values, "trend_5m": ps.extended_trend(every, now)})
        atr = average_true_range_pct(daily[s]) if s in daily else None
        out.append(dataclasses.replace(r, stop_note=ps.stop_note(price, stops.get(s), atr)))
    return out


def load_marks() -> dict:
    try:
        return json.loads(MARKS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def remove_picks(symbols: list[str], why: str) -> list[str]:
    import market_monitor  # same picks.txt edit + commit the monitor uses
    return market_monitor.remove_picks(symbols, why)


async def sell_position(broker, client, symbol: str, book: str, why: str, open_orders,
                        session: str) -> bool | None:
    """Gemma's through bookorders.sell (limit orders outside regular hours); picks
    through picks.txt + picks_sync, so the list stays the record. None: the pick is
    already off the list, so picks_sync (extsync hourly, scan at the open) is selling
    it -- not run again here every 5 min (2026-10-22: BHVN was re-sold 23 times)."""
    if book == PICKS:
        if not remove_picks([symbol], why):
            return None
        subprocess.run([sys.executable, "scripts/picks_sync.py", "--submit"], check=False)
        return True
    return await bookorders.sell(broker, symbol, open_orders, strategy=book, reason=why,
                                 policy=gtr.POLICY, session=session, client=client)


def status(ok: bool | None) -> str:
    return {True: "SELL SENT", False: "SELL FAILED", None: "SELL PENDING (off the picks list; picks sync sells it)"}[ok]


async def queue_pass(broker, client, positions, book_of, open_orders, session: str,
                     dry: bool, log) -> list[str]:
    """Queued sells (execution/sell_queue.py) -- the operator's, and the market monitor's
    (Gemma's met sell-ifs, the picks alarm): retried each pass until sold."""
    try:
        queue = json.loads(SELL_QUEUE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    held = {p["symbol"] for p in positions}
    alerts: list[str] = []
    for s in sell_queue.due(queue, held=held, open_orders=open_orders):
        if dry:
            log(f"  WOULD SELL (queued) {book_of[s]} {s}: {queue[s]}")
            continue
        ok = await sell_position(broker, client, s, book_of[s], queue[s], open_orders, session)
        log(f"  {'SELL SENT' if ok else 'SELL WAITING'} (queued) {book_of[s]} {s}: {queue[s]}"
            + ("" if ok else " -- no fresh price yet, stop kept; retried in 5 min"))
        if ok:
            alerts.append(f"Sell sent for {s} ({book_of[s]}): {queue[s]}")
    if not dry:
        # Re-read: the market monitor may have queued a sell during this pass.
        try:
            latest = json.loads(SELL_QUEUE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            latest = queue
        SELL_QUEUE.write_text(json.dumps(sell_queue.prune(latest, held=held), indent=1), encoding="utf-8")
    return alerts


async def main(dry: bool) -> int:
    now = datetime.now(EXCHANGE_TZ)
    async with httpx.AsyncClient(timeout=60) as client:
        session = await sessions.current(Alpaca(settings.alpaca_key_id, settings.alpaca_secret_key, client))
    if session == sessions.CLOSED:
        print(f"{now:%H:%M} ET: market closed -- nothing to watch")
        return 0
    lines: list[str] = []

    def log(text: str) -> None:
        print(text, flush=True)
        lines.append(text)

    log(f"\n## {now:%H:%M} ET [{session}]{' DRY RUN' if dry else ''}")
    alerts = []
    async with httpx.AsyncClient(timeout=120) as client:
        broker = Alpaca(settings.alpaca_key_id, settings.alpaca_secret_key, client)
        positions = await broker._get("/v2/positions")
        orders = await broker._get("/v2/orders", status="all", limit=500, nested="true")
        open_orders = await broker._get("/v2/orders", status="open", nested="true")
        owner_map = ownership.owners(orders)
        stops = grv.live_stops(orders)
        book_of = {p["symbol"]: ownership.owner(p["symbol"], owner_map) for p in positions}
        watched = [p for p in positions if book_of[p["symbol"]] in (GEMMA, PICKS, SMALL)]
        rs = await readings_for(client, watched, stops, log, session)
        prices = {p["symbol"]: float(p["current_price"]) for p in watched}
        marks, ready, sell = ps.update_marks(load_marks(), rs, now=f"{now:%m-%d %H:%M} ET", prices=prices)
        for r in rs:
            stage = "SELL" if r.symbol in sell else "READY" if r.symbol in marks else "-"
            trend = r.values.get("trend_5m")
            log(f"  {r.symbol:<6} {book_of[r.symbol]:<5} P/L {r.pl_pct:+6.2f}%  {r.score()}/6  5m trend "
                f"{'UP' if trend == 1.0 else 'DOWN' if trend == 0.0 else 'n/a'}  {stage}")
        for s in ready:
            log(f"  READY {s}: in profit with >= {ps.MIN_MET} of 6 peak checks -- sells when the 5m trend turns down")
            alerts.append(f"{s} READY to sell (peak checks)")
        # The plan (2026-10-26) removed the 5-minute peak-sell: READY is shown, nothing sells on it.
        for s in (sell if PEAK_SELL else ()):
            m = marks.get(s, {})
            why = (f"peak-sell: READY since {m.get('ready_at')} at {m.get('ready_price')}, "
                   f"the 5-minute trend turned down while in profit")
            if dry:
                log(f"  WOULD SELL {book_of[s]} {s}: {why}")
                continue
            ok = await sell_position(broker, client, s, book_of[s], why, open_orders, session)
            log(f"  {status(ok)} {book_of[s]} {s}: {why}")
            if ok:
                alerts.append(f"Sell sent for {s} ({book_of[s]}) -- took profit, trend turned after READY")
                marks.pop(s, None)
        gone = set(sell) if not dry else set()
        alerts += await queue_pass(broker, client, positions, book_of, open_orders, session, dry, log)
        # The plan's exits replace the profit lock; its buys and the SPY core go out from 10:00 ET.
        alerts += await plan_run.exit_pass(broker, client, [p for p in watched if p["symbol"] not in gone],
                                           book_of, orders, open_orders, session, now, dry, log, sell_position)
        alerts += await plan_run.core_stop_pass(broker, client, positions, owner_map, orders, open_orders,
                                                now, dry, log, gtr.POLICY)
        if session == sessions.REGULAR:
            alerts += await plan_run.buy_pass(broker, client, positions, owner_map, open_orders, now,
                                              dry, log, gtr.POLICY)
            alerts += await plan_run.buy_pass(broker, client, positions, owner_map, open_orders, now,
                                              dry, log, gtr.POLICY, strategy=SMALL)
            alerts += await plan_run.core_pass(broker, client, positions, owner_map, open_orders, now,
                                               dry, log, gtr.POLICY, orders=orders)
        if alerts and not dry:
            body = "; ".join(alerts)
            await notify.send(client, server=settings.ntfy_server, topic=settings.ntfy_topic,
                              title="Quant-desk peak watch", message=body, priority="high", tags=("moneybag",))
            notify.toast("Quant-desk peak watch", body)
    if not dry:
        MARKS.parent.mkdir(parents=True, exist_ok=True)
        MARKS.write_text(json.dumps(marks, indent=1), encoding="utf-8")
        OUT.mkdir(parents=True, exist_ok=True)
        with (OUT / f"{now.date().isoformat()}.md").open("a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry-run", action="store_true", help="show what it would do; send nothing")
    raise SystemExit(asyncio.run(main(p.parse_args().dry_run)))
