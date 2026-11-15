"""Opening-range breakout day book (rules in scanner/execution/orb.py).

    python scripts/orb_trade.py            # dry run: what it would do right now
    python scripts/orb_trade.py --submit   # scheduled every 5 min, 9:46-15:56 ET

Each pass asks the broker's calendar where the session is:
  before 9:45      nothing
  9:45 - close-30  build today's shortlist once (from Gemma's saved pool), then buy breaks
  close-30 - -10   no new buys; stale entries cancelled
  close-10 on      sell everything this book holds, then tell Gemma what it did

Its own 10% (policy.orb_allocation_pct), its own order prefix ("orb-"), so peak
watch, the market monitor and Gemma's book never touch its positions, and it
never touches theirs. State for the day: data/orb/<date>.json.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.advisor import gemma_pool as gp
from scanner.advisor import operator_notes
from scanner.config import settings
from scanner.execution import bookorders, manual, orb, ownership
from scanner.execution.alpaca import Alpaca, BrokerError
from scanner.execution.guards import RiskLimits, check_account
from scanner.execution.ownership import ORB
from scanner.execution.policy import TradingPolicy
from scanner.execution.safety import kill_switch_active
from scanner.execution.sizing import size_position
from scanner.models import TradeIntent
from scanner.sources import alpaca_bars
from scanner.sources import calendar as event_calendar

ET = ZoneInfo("America/New_York")
POLICY = TradingPolicy()
STATE_DIR = Path("data/orb")
LEDGER = STATE_DIR / "ledger.json"
NOTE_PREFIX = "orb-day-"


def say(text: str) -> None:
    print(f"  {text}", flush=True)


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=1), encoding="utf-8")


def at_et(day: date, hhmm: str) -> datetime:
    h, m = (int(x) for x in hhmm.split(":"))
    return datetime.combine(day, time(h, m), ET)


async def sessions_between(broker, start: date, end: date) -> list[dict]:
    return await broker._get("/v2/calendar", start=start.isoformat(), end=end.isoformat())


async def baseline(client, symbols: list[str], days: list[date]) -> dict[str, float]:
    """Usual first-15-minute IEX volume per symbol over `days`."""
    volumes: dict[str, list[float]] = {s: [] for s in symbols}
    for day in days:
        start = at_et(day, "09:30")
        got = await alpaca_bars.bars(symbols, client, timeframe="15Min", start=start,
                                     end=start + timedelta(minutes=14, seconds=59))
        for s, rows in got.items():
            if rows:
                volumes.setdefault(s, []).append(float(rows[0]["v"]))
    return {s: u for s, v in volumes.items() if (u := orb.usual_volume(v)) is not None}


async def build_shortlist(broker, client, today: date, held: set[str]) -> dict:
    """Today's watched names, or a dict with a "skip" reason."""
    pool = gp.latest(today)
    if not pool:
        return {"skip": "no saved Gemma pool in the last 4 days"}
    atr = {r.symbol: r.atr_pct for r in pool if r.atr_pct}
    symbols = sorted(atr)
    past = [date.fromisoformat(r["date"]) for r in
            await sessions_between(broker, today - timedelta(days=21), today - timedelta(days=1))]
    usual = await baseline(client, symbols, past[-orb.BASELINE_DAYS:])
    start = at_et(today, "09:30")
    now_bars = await alpaca_bars.bars(symbols, client, timeframe="15Min", start=start,
                                      end=start + timedelta(minutes=14, seconds=59))
    ranges = []
    for s, rows in now_bars.items():
        if rows:
            b = rows[0]
            r = orb.opening_range(s, high=float(b["h"]), low=float(b["l"]), close=float(b["c"]),
                                  volume=float(b["v"]), usual_volume=usual.get(s), atr_pct=atr.get(s))
            if r:
                ranges.append(r)
    picked = orb.shortlist(ranges, exclude=held)
    found, failed = await event_calendar.upcoming_earnings(
        frozenset(r.symbol for r in picked), client, horizon=1, today=today)
    if failed:
        return {"skip": f"earnings calendar unreadable for {failed} -- no trading today (fails closed)"}
    reporting = set(found)
    kept = [r for r in picked if r.symbol not in reporting]
    return {"pool": len(pool), "measured": len(ranges), "earnings_today": sorted(reporting & {r.symbol for r in picked}),
            "names": [r.model_dump(mode="json") for r in kept], "bought": []}


def completed_closes(rows: list[dict], now: datetime) -> tuple[list[float], datetime | None]:
    """Regular-hours 5-minute closes whose bar has ended, and the last bar's start."""
    closes, last = [], None
    for b in rows:
        t = datetime.fromisoformat(b["t"].replace("Z", "+00:00")).astimezone(ET)
        if not time(9, 30) <= t.time() < time(16, 0) or t + timedelta(minutes=5) > now:
            continue
        closes.append(float(b["c"]))
        last = t
    return closes, last


async def buy(broker, s: str, rng: orb.Range, last: float, *, sleeve: float,
              room: float) -> bool:
    limit, stop, target = orb.levels(rng, last, offset_pct=POLICY.entry_limit_offset_pct,
                                     reward_risk=POLICY.orb_reward_risk)
    sizing = size_position(sleeve, limit, stop, risk_pct=POLICY.orb_risk_pct,
                           max_position_pct=100.0 / POLICY.orb_max_positions, buying_power=room)
    why = (f"opening-range break: close {last:,.2f} > range high {rng.high:,.2f}, "
           f"volume {rng.rvol:.1f}x usual, range {rng.range_atr:.2f} ATR")
    if not sizing.is_tradeable:
        bookorders.log("orb_refused", s, sizing.limited_by, POLICY)
        say(f"{s:<6} not bought -- {sizing.limited_by}")
        return False
    now = datetime.now().astimezone()
    payload = manual.bracket(s, sizing.shares, limit=limit, stop=stop, target=target, at=now)
    payload["client_order_id"] = f"{ownership.prefix_for(ORB)}{s}-{int(now.timestamp())}"
    payload["time_in_force"] = "day"            # a day book: nothing it sends outlives the session
    intent = TradeIntent(
        symbol=s, action="buy", strategy=ORB, trigger_at=now, entry_price=limit,
        stop_price=stop, target_price=target, shares=sizing.shares,
        risk_dollars=sizing.risk_dollars, position_dollars=sizing.position_dollars,
        reward_risk=POLICY.orb_reward_risk, paper=True,
        client_order_id=payload["client_order_id"], llm_concern=why[:300])
    bookorders.log("trade_intent", s, why, POLICY, intent=intent)
    try:
        order = await broker.submit(payload)
    except BrokerError as exc:
        bookorders.log("order_rejected", s, str(exc), POLICY, intent=intent)
        say(f"{s:<6} REJECTED -- {exc}")
        return False
    bookorders.log("order_submitted", s, why, POLICY, intent=intent, order_id=str(order.get("id", "")))
    say(f"BOUGHT {s} {sizing.shares} sh limit {limit:,.2f} stop {stop:,.2f} target {target:,.2f} "
        f"(risk ${sizing.risk_dollars:,.0f}) -- {why}")
    return True


async def cancel_stale(broker, open_orders: list[dict], now: datetime, dry: bool) -> None:
    for o in open_orders:
        if (ownership.strategy_of(o.get("client_order_id")) != ORB or o.get("side") != "buy"
                or o.get("filled_at")):
            continue
        made = datetime.fromisoformat(str(o["created_at"]).replace("Z", "+00:00"))
        if now - made > timedelta(minutes=orb.STALE_ENTRY_MINUTES):
            say(f"{o['symbol']:<6} entry unfilled after {orb.STALE_ENTRY_MINUTES:.0f} min -- "
                + ("would cancel" if dry else "cancelled"))
            if not dry:
                try:
                    await broker.cancel_order(str(o["id"]))
                except BrokerError as exc:
                    say(f"{o['symbol']:<6} cancel failed: {exc}")


async def flatten(broker, positions, open_orders, owner_map, dry: bool) -> bool:
    """Sell everything the book holds and cancel its entries. True when flat."""
    mine = ownership.positions_for(positions, owner_map, (ORB,))
    for o in open_orders:
        if ownership.strategy_of(o.get("client_order_id")) == ORB and o.get("side") == "buy" \
                and not o.get("filled_at") and not dry:
            try:
                await broker.cancel_order(str(o["id"]))
            except BrokerError:
                pass
    flat = True
    for p in mine:
        s = p["symbol"]
        if dry:
            say(f"would sell {s} (end of day)")
            continue
        ok = await bookorders.sell(broker, s, open_orders, strategy=ORB, policy=POLICY,
                                   reason="end of day: the breakout book is flat every night")
        say(f"{'SOLD' if ok else 'SELL FAILED'} {s} (end of day)")
        flat = flat and ok
    return flat


async def tell_gemma(broker, today: date, state: dict) -> None:
    fills = await broker._get("/v2/account/activities/FILL", date=today.isoformat())
    pnl = orb.day_pnl(fills, state.get("bought", []))
    ledger = read_json(LEDGER, {})
    ledger[today.isoformat()] = {"pnl": pnl, "watched": len(state.get("names", []))}
    write_json(LEDGER, ledger)
    running = sum(sum(d["pnl"].values()) for d in ledger.values())
    text = orb.report(f"{today:%m-%d}", pnl, running=running, days=len(ledger),
                      shortlisted=len(state.get("names", [])))
    operator_notes.replace_prefixed(NOTE_PREFIX, {
        "id": f"{NOTE_PREFIX}{today.isoformat()}", "from": today.isoformat(),
        "until": (today + timedelta(days=4)).isoformat(), "text": text})
    say(f"told Gemma: {text}")


async def main(dry: bool) -> int:
    now = datetime.now(ET)
    today = now.date()
    print(f"\n## {now:%H:%M} ET{' (dry run)' if dry else ''}", flush=True)
    async with httpx.AsyncClient(timeout=60) as client:
        broker = Alpaca(settings.alpaca_key_id, settings.alpaca_secret_key, client)
        cal = await sessions_between(broker, today, today)
        if not cal:
            say("no session today")
            return 0
        step = orb.phase(now, at_et(today, cal[0]["open"]), at_et(today, cal[0]["close"]))
        path = STATE_DIR / f"{today.isoformat()}.json"
        state = read_json(path, {})
        positions = await broker._get("/v2/positions")
        orders = await broker._get("/v2/orders", status="all", limit=500, nested="true")
        open_orders = await broker._get("/v2/orders", status="open", nested="true")
        owner_map = ownership.owners(orders)
        if step == orb.WAIT:
            say("before 9:45 ET -- the opening range is still forming")
            return 0
        if step == orb.FLATTEN:
            flat = await flatten(broker, positions, open_orders, owner_map, dry)
            if flat and not dry and state and not state.get("reported"):
                await tell_gemma(broker, today, state)
                write_json(path, {**state, "reported": True})
            return 0 if flat else 1
        await cancel_stale(broker, open_orders, now, dry)
        if step == orb.MANAGE:
            say("last 30 minutes -- no new buys")
            return 0
        held = {p["symbol"] for p in positions} | {o["symbol"] for o in open_orders
                                                    if o.get("side") == "buy"}
        if "names" not in state and "skip" not in state:
            state = await build_shortlist(broker, client, today, held)
            if not dry:
                write_json(path, state)
            if "skip" in state:
                say(f"no shortlist today: {state['skip']}")
            else:
                say(f"shortlist: {len(state['names'])} of {state['measured']} measured "
                    f"({state['pool']} in Gemma's pool); earnings today, left out: "
                    f"{state['earnings_today'] or 'none'}")
                for r in state["names"]:
                    say(f"  {r['symbol']:<6} range {r['low']:,.2f}-{r['high']:,.2f}  "
                        f"volume {r['rvol']:.1f}x  {r['range_atr']:.2f} ATR")
        if "skip" in state:
            return 0
        if kill_switch_active(settings.kill_switch_path):
            say("kill switch active -- no buys")
            return 0
        mine = ownership.positions_for(positions, owner_map, (ORB,))
        pending = [o for o in open_orders if ownership.strategy_of(o.get("client_order_id")) == ORB
                   and o.get("side") == "buy"]
        slots = POLICY.orb_max_positions - len(mine) - len(pending)
        bought = set(state.get("bought", []))
        watch = [orb.Range(**r) for r in state["names"]
                 if r["symbol"] not in bought and r["symbol"] not in held]
        if slots <= 0 or not watch:
            say(f"{len(mine)} held, {len(pending)} pending, {len(watch)} still watched -- nothing to do")
            return 0
        raw = await broker._get("/v2/account")
        equity = float(raw["equity"])
        gate = check_account(equity, float(raw.get("last_equity") or equity), 0,
                             RiskLimits(max_positions=POLICY.orb_max_positions,
                                        max_daily_loss_pct=POLICY.max_daily_loss_pct,
                                        min_equity=POLICY.min_equity),
                             market_open=True, account_status=str(raw.get("status")),
                             account_blocked=bool(raw.get("trading_blocked")))
        if not gate:
            say(f"no buys -- {gate.reason}")
            return 0
        sleeve = equity * POLICY.orb_allocation_pct / 100
        if sleeve <= 0:
            say("ORB is off (0% of equity from 2026-11-02, its 10% to the sweep scanner): no buys")
            return 0
        deployed =sum(abs(float(p.get("market_value") or 0)) for p in mine) + sum(
            float(o.get("qty") or 0) * float(o.get("limit_price") or 0) for o in pending)
        room = max(0.0, min(float(raw.get("buying_power") or 0), sleeve - deployed))
        five = await alpaca_bars.bars([r.symbol for r in watch], client, timeframe="5Min",
                                      start=now - timedelta(days=4), end=now)
        for rng in watch:                               # strongest volume first
            closes, last_at = completed_closes(five.get(rng.symbol, []), now)
            if last_at is None or last_at.date() != today or last_at.time() < time(9, 45):
                continue
            if not orb.breakout(rng, closes):
                continue
            if dry:
                say(f"WOULD BUY {rng.symbol}: close {closes[-1]:,.2f} > high {rng.high:,.2f}")
                continue
            if await buy(broker, rng.symbol, rng, closes[-1], sleeve=sleeve, room=room):
                bought.add(rng.symbol)
                write_json(path, {**state, "bought": sorted(bought)})
                slots -= 1
                room = max(0.0, room - sleeve / POLICY.orb_max_positions)
            if slots <= 0:
                break
        say(f"{len(mine)} held, {len(pending)} pending, {len(watch)} watched, bought today: "
            f"{sorted(bought) or 'none'}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--submit", action="store_true", help="send orders (default: dry run)")
    sys.exit(asyncio.run(main(dry=not p.parse_args().submit)))
