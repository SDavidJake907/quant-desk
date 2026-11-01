"""The plan's three passes, run by peak watch every 5 minutes (execution/satellite.py has
the rules; this file only fetches, decides with them, and sends).

  exit_pass  every session: raise each Gemma / picks position's broker stop to the plan's
             hard stop (2.5 ATR, never more than 3%, break-even from +1 ATR) and sell at
             or under it; in the last 10 minutes of regular hours, the close-based exits
             (trailing 3 ATR under the highest close, the 10-day time stop, earnings)
  buy_pass   regular hours from 10:00 ET: send Gemma's queued buys (execution/buy_queue.py) that
             pass the 10:00 recheck (fell 2%+ since her decision, a new theme alert, earnings)
  core_pass  regular hours from 10:00 ET, once a day: buy the SPY core up to its target,
             at most half the target a day (two halves), topped up only below 90%

Replaces the profit lock (+3% / +1% / 3% trail) and the 5-minute peak-sell, which the
plan removed (peak-sell's +2% trigger caught bounces, not tops).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from scanner.execution import bookorders, buy_queue, flatten, ownership, picks, profit_lock
from scanner.execution import satellite as sat
from scanner.execution.alpaca import BrokerError, round_tick
from scanner.execution.guards import RiskLimits, check_account
from scanner.execution import small_book
from scanner.execution.ownership import CORE, GEMMA, PICKS, SMALL
from scanner.execution.policy import TradingPolicy
from scanner.execution.sessions import REGULAR
from scanner.features.bars import fetch_bars
from scanner.features.intraday_context import atr_pct
from scanner.ops import shock_alert as sa
from scanner.ops import shock_feed
from scanner.sources import alpaca_bars, composite
from scanner.sources import calendar as event_calendar

CORE_STATE = Path("data/monitor/core_state.json")
#: No new buys in the last 30 minutes (the close-based exits decide then).
LAST_BUY = (15, 30)

Log = Callable[[str], None]


def entry_dates(orders: Iterable[Mapping[str, Any]], books: Iterable[str]) -> dict[str, date]:
    """ET date of the newest filled buy per symbol among `books`."""
    wanted, newest = set(books), {}
    for o in orders:
        for x in (o, *(o.get("legs") or ())):
            if (x.get("side") == "buy" and x.get("filled_at")
                    and ownership.strategy_of(x.get("client_order_id")) in wanted):
                t = datetime.fromisoformat(str(x["filled_at"]).replace("Z", "+00:00"))
                if x["symbol"] not in newest or t > newest[x["symbol"]]:
                    newest[x["symbol"]] = t
    return {s: t.astimezone(sat.ET).date() for s, t in newest.items()}


def atr_dollars(bars) -> float:
    """The daily ATR in dollars, as the bars measured it. Was atr_pct x the LIVE price, so the
    stop (entry - 2.5 ATR) crept up as a stock fell -- RNG's moved 70.75 -> 71.66 in 20+ raises
    on 10-28..30 with no gain (found 2026-10-30)."""
    pct = atr_pct(bars) or 0.0
    return pct / 100 * float(bars.closes[-1]) if bars.closes else 0.0


def _buying_hours(now: datetime) -> bool:
    t = now.astimezone(sat.ET)
    return sat.may_buy_now(t) and (t.hour, t.minute) < LAST_BUY


async def before_closure(broker, today: date, log: Log) -> bool:
    """Whether today is the last session before a weekend-length closure: no core buy then,
    the flatten sells the SPY core at 15:52 ET (operator 2026-10-30: "stop holding for the
    weekend"). An unreadable calendar counts as yes -- a skipped buy risks nothing."""
    try:
        days = await broker._get("/v2/calendar", start=(today + timedelta(days=1)).isoformat(),
                                 end=(today + timedelta(days=7)).isoformat())
        nxt = date.fromisoformat(days[0]["date"]) if days else None
    except Exception as exc:                                  # noqa: BLE001
        log(f"  calendar unreadable ({exc}) -- treated as the last session before a closure")
        nxt = None
    return flatten.closes_for_weekend(today, nxt)


async def _reporting(broker, client, symbols: list[str], today: date) -> set[str]:
    """Held names reporting before the next regular session opens."""
    if not symbols:
        return set()
    days = await broker._get("/v2/calendar", start=(today + timedelta(days=1)).isoformat(),
                             end=(today + timedelta(days=7)).isoformat())
    nxt = date.fromisoformat(days[0]["date"]) if days else today + timedelta(days=1)
    found, failed = await event_calendar.upcoming_earnings(frozenset(symbols), client,
                                                           horizon=(nxt - today).days + 1, today=today)
    if failed:
        raise RuntimeError(f"earnings calendar unreadable for {failed}")
    return {s for s, e in found.items()
            if sat.reports_before_next_session(e.on, e.when, today, nxt)}


async def exit_pass(broker, client, positions, book_of: Mapping[str, str], orders, open_orders,
                    session: str, now: datetime, dry: bool, log: Log,
                    sell: Callable[..., Any]) -> list[str]:
    today = now.astimezone(sat.ET).date()
    entered = entry_dates(orders, (GEMMA, PICKS, SMALL))
    stops = profit_lock.stop_orders(orders)
    closing = session == REGULAR and sat.in_close_window(now)
    reporting: set[str] = set()
    if closing:
        try:
            reporting = await _reporting(broker, client, [p["symbol"] for p in positions], today)
        except Exception as exc:                              # noqa: BLE001
            log(f"  earnings check failed ({exc}) -- trailing and time exits still run")
    alerts: list[str] = []
    for p in positions:
        s, book = p["symbol"], book_of[p["symbol"]]
        entry, price = float(p["avg_entry_price"]), float(p["current_price"])
        try:
            bars = await fetch_bars(s, client, range_="6mo")
        except Exception as exc:                              # noqa: BLE001
            log(f"  {s}: no daily bars ({exc}) -- its broker stop stands")
            continue
        atr = atr_dollars(bars)
        if atr <= 0:
            log(f"  {s}: no ATR -- its broker stop stands")
            continue
        start = entered.get(s)
        after = [c for d, c in zip(bars.dates, bars.closes) if start and start < d < today]
        wide = book == SMALL or (book == GEMMA and sat.wide_stop(start))
        hard = sat.hard_stop(entry=entry, atr=atr, high_close=max([*after, entry]), floor=not wide,
                             max_loss_pct=sat.MAX_STOP_PCT if wide else None)
        why = None
        if price <= hard:
            why = (f"plan stop: {price:,.2f} at/under {hard:,.2f} "
                   + ("(break-even after +1 ATR)" if hard >= entry else f"(buy {entry:,.2f})"))
        elif closing:
            held = sat.trading_days_between(start, today) if start else sat.TIME_STOP_DAYS
            why = sat.close_exit(entry=entry, atr=atr, closes_since_buy=after, price=price,
                                 held_days=held, reports_before_next_session=s in reporting)
        if why:
            if dry:
                log(f"  WOULD SELL {book} {s}: {why}")
                continue
            ok = await sell(broker, client, s, book, why, open_orders, session)
            log(f"  {'SELL SENT' if ok else 'SELL FAILED' if ok is False else 'SELL PENDING'} {book} {s}: {why}")
            if ok:
                alerts.append(f"Sell sent for {s} ({book}) -- {why.split(':')[0]}")
            continue
        order_id, broker_stop = stops.get(s, (None, None))
        if order_id is None or broker_stop is None or hard < broker_stop + profit_lock.MIN_RAISE:
            continue
        if dry:
            log(f"  WOULD RAISE {s} stop {broker_stop:,.2f} -> {hard:,.2f}")
            continue
        try:
            await broker.replace_stop(order_id, round_tick(hard))
            log(f"  RAISED {s} stop {broker_stop:,.2f} -> {hard:,.2f} (plan; entry {entry:,.2f})")
            bookorders.log(f"{book}_stop_raised", s,
                           f"plan stop {broker_stop:,.2f} -> {hard:,.2f}", TradingPolicy())
        except BrokerError as exc:
            log(f"  RAISE FAILED {s} ({exc}) -- this pass still sells at {hard:,.2f}")
    return alerts


async def _rechecks(client, due: Mapping[str, Mapping[str, Any]], today: date, log: Log) -> dict[str, str]:
    """{symbol: why skipped} by the 10:00 recheck (buy_queue.recheck). Each input that cannot be read
    is left out and logged -- her 9:45 checks stand for it."""
    symbols = list(due)
    prices: dict[str, float] = {}
    try:
        snaps = await alpaca_bars.snapshots(symbols, client)
        prices = {s: float(v["latestTrade"]["p"]) for s, v in snaps.items() if (v or {}).get("latestTrade")}
    except Exception as exc:                                  # noqa: BLE001
        log(f"  recheck: prices unavailable ({type(exc).__name__}) -- the 2% drop check skipped")
    kinds = {a.kind for a in sa.active(shock_feed.read(), today)}
    sectors: dict[str, str] = {}
    if any(kinds - set(v.get("alerts") or ()) for v in due.values()):
        try:
            sectors = {m.symbol: m.sector for m in await composite.load_universe(client)}
        except Exception as exc:                              # noqa: BLE001
            log(f"  recheck: sectors unavailable ({type(exc).__name__}) -- the alert check skipped")
    earnings: dict[str, int] = {}
    try:
        found, _ = await event_calendar.upcoming_earnings(frozenset(symbols), client,
                                                          horizon=sat.EARNINGS_BUFFER_DAYS + 1, today=today)
        earnings = {s: (e.on - today).days for s, e in found.items() if e.on}
    except Exception as exc:                                  # noqa: BLE001
        log(f"  recheck: earnings calendar unavailable ({type(exc).__name__}) -- her 9:45 check stands")
    out = {}
    for s, item in due.items():
        why = buy_queue.recheck(item, price=prices.get(s), alert_kinds=kinds, sector=sectors.get(s),
                                earnings_in_days=earnings.get(s), buffer_days=sat.EARNINGS_BUFFER_DAYS)
        if why:
            out[s] = why
    return out


#: The books whose 9:45 picks buy_pass sends: (sleeve % field, slots field, label). The queue
#: file is read at call time (_queue_path). The small-cap book (2026-10-27) buys with cash only --
#: never on margin while the rules book is still above its new 30% after the change.
QUEUED_BOOKS: dict[str, tuple[str, str, str]] = {
    GEMMA: ("gemma_allocation_pct", "gemma_max_positions", "Rules book"),
    SMALL: ("small_allocation_pct", "small_max_positions", "Small caps"),
}


def _queue_path(strategy: str) -> str:
    return small_book.QUEUE_PATH if strategy == SMALL else buy_queue.PATH


async def buy_pass(broker, client, positions, owner_map: Mapping[str, str], open_orders,
                   now: datetime, dry: bool, log: Log, policy: TradingPolicy,
                   strategy: str = GEMMA) -> list[str]:
    if not _buying_hours(now):
        return []
    alloc_field, slots_field, label = QUEUED_BOOKS[strategy]
    max_positions = int(getattr(policy, slots_field))
    path = Path(_queue_path(strategy))
    try:
        queue = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    mine = [p for p in positions if ownership.owner(p["symbol"], owner_map) == strategy]
    pending = [o for o in open_orders if o.get("side") == "buy"
               and ownership.strategy_of(o.get("client_order_id")) == strategy]
    due = buy_queue.due(queue, today=now.astimezone(sat.ET).date(), held=[p["symbol"] for p in mine],
                        pending=[o["symbol"] for o in pending])
    if not due:
        return []
    skipped = await _rechecks(client, due, now.astimezone(sat.ET).date(), log)
    raw = await broker._get("/v2/account")
    equity = float(raw["equity"])
    gate = check_account(equity, float(raw.get("last_equity") or equity), len(mine),
                         RiskLimits(max_positions=max_positions,
                                    max_daily_loss_pct=policy.max_daily_loss_pct, min_equity=policy.min_equity),
                         market_open=True, account_status=str(raw.get("status")),
                         account_blocked=bool(raw.get("trading_blocked")))
    sleeve = equity * float(getattr(policy, alloc_field)) / 100
    deployed = sum(abs(float(p.get("market_value") or 0)) for p in mine) + sum(
        float(o.get("qty") or 0) * float(o.get("limit_price") or 0) for o in pending)
    room = max(0.0, min(float(raw.get("buying_power") or 0), sleeve - deployed))
    if strategy == SMALL:
        room = max(0.0, min(room, float(raw.get("cash") or 0)))
    slots = max_positions - len(mine) - len(pending)
    alerts, done = [], []

    def refused(s: str, reason: str) -> None:
        if not dry:    # a dry run never writes the audit log
            bookorders.log(f"{strategy}_refused", s, reason, policy)

    for s, plan in due.items():
        done.append(s)
        if s in skipped:
            log(f"  QUEUED BUY {s} skipped at 10:00 -- {skipped[s]}")
            refused(s, f"10:00 recheck: {skipped[s]}")
            continue
        if not gate:
            log(f"  QUEUED BUY {s} not sent -- {gate.reason}")
            refused(s, gate.reason)
            continue
        if slots <= 0:
            log(f"  QUEUED BUY {s} not sent -- all {max_positions} slots in use")
            refused(s, "no free slot at 10:00")
            continue
        if dry:
            log(f"  WOULD BUY (queued) {s}")
            continue
        cap = 100.0 / max_positions
        # From satellite.FULL_SLOT_FROM: the full equal slot, the 2.5 ATR stop capped at 8%.
        # Before it, Gemma's B2: the full 2.5 ATR stop, sized to a 3% stop's dollars.
        full = now.astimezone(sat.ET).date() >= sat.FULL_SLOT_FROM
        result = await bookorders.buy_with_stop(
            broker, client, s, strategy=strategy, sleeve=sleeve, room=room,
            cap_pct=cap, policy=policy,
            reason=f"{label} (the plan, 10:00 ET): {plan.get('reason', '')}",
            risk_pct=(sat.full_slot_risk_pct(cap) if full else
                      min(policy.gemma_menu_value("size", plan.get("size", "normal")), sat.same_risk_pct(cap))),
            atr_multiple=policy.gemma_menu_value("stop", plan.get("stop", "normal")),
            reward_risk=policy.gemma_menu_value("target", plan.get("target", "none")), session=REGULAR,
            loss_floor=False, max_loss_pct=sat.MAX_STOP_PCT if full else None)
        log(f"  {'BOUGHT' if result.ok else 'NOT BOUGHT'} (queued) {s}: {result.message}")
        if result.ok:
            slots -= 1
            room -= result.sizing.position_dollars
            alerts.append(f"{label} bought {s} (10:00 queue)")
    if not dry:
        path.write_text(json.dumps(buy_queue.without(queue, done), indent=1), encoding="utf-8")
    # Unfilled queued buys older than the picks rule are cancelled; tomorrow's pick re-chooses.
    for o in picks.stale_entries(open_orders, now, minutes=policy.picks_stale_entry_minutes, strategy=strategy):
        if not dry:
            try:
                await broker.cancel_order(str(o["id"]))
                log(f"  cancelled unfilled buy {o['symbol']} (older than {policy.picks_stale_entry_minutes:g} min)")
            except BrokerError:
                pass
    return alerts


def core_stopped_on(orders: Iterable[Mapping[str, Any]]) -> date | None:
    """ET date the core's stop last filled (status=all history), or None."""
    fills = [str(o["filled_at"]) for o in orders
             if o.get("side") == "sell" and o.get("filled_at")
             and str(o.get("client_order_id") or "").startswith(sat.CORE_STOP_PREFIX)]
    if not fills:
        return None
    return datetime.fromisoformat(max(fills).replace("Z", "+00:00")).astimezone(sat.ET).date()


def core_stop_orders(open_orders: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [o for o in open_orders if o.get("side") == "sell" and o.get("type") == "stop"
            and str(o.get("client_order_id") or "").startswith(sat.CORE_STOP_PREFIX)]


def core_stop_payload(symbol: str, qty: int, stop: float, now: datetime) -> dict[str, Any]:
    """GTC so it survives the close; a plain stop, it acts intraday and at a gap."""
    return {"symbol": symbol, "qty": str(qty), "side": "sell", "type": "stop",
            "stop_price": f"{round_tick(stop):.2f}", "time_in_force": "gtc",
            "client_order_id": f"{sat.CORE_STOP_PREFIX}{symbol}-{int(now.timestamp())}"}


async def core_stop_pass(broker, client, positions, owner_map: Mapping[str, str], orders, open_orders,
                         now: datetime, dry: bool, log: Log, policy: TradingPolicy) -> list[str]:
    """Keep one stop under every SPY core share (operator 2026-10-30): armed when missing,
    re-sent at the new share count after a top-up, raised with the plan, never lowered."""
    s = policy.core_symbol
    core = next((p for p in positions if p["symbol"] == s and ownership.owner(s, owner_map) == CORE), None)
    if core is None:
        return []
    qty = abs(int(float(core["qty"])))
    entry, price = float(core["avg_entry_price"]), float(core["current_price"])
    try:
        bars = await fetch_bars(s, client, range_="6mo")
    except Exception as exc:                                  # noqa: BLE001
        log(f"  CORE STOP: no daily bars for {s} ({exc}) -- any working stop stands")
        return []
    atr = atr_dollars(bars)
    if atr <= 0:
        log(f"  CORE STOP: no ATR for {s} -- any working stop stands")
        return []
    start = entry_dates(orders, (CORE,)).get(s)
    today = now.astimezone(sat.ET).date()
    after = [c for d, c in zip(bars.dates, bars.closes) if start and start < d < today]
    hard = round_tick(sat.hard_stop(entry=entry, atr=atr, high_close=max([*after, entry]), floor=False))
    working = core_stop_orders(open_orders)
    best = max((float(o["stop_price"]) for o in working if o.get("stop_price")), default=None)
    level = hard if best is None else max(hard, best)
    if len(working) == 1 and int(float(working[0]["qty"])) == qty:
        if level < best + profit_lock.MIN_RAISE:
            return []
        if dry:
            log(f"  WOULD RAISE core {s} stop {best:,.2f} -> {level:,.2f}")
            return []
        try:
            await broker.replace_stop(str(working[0]["id"]), level)
            log(f"  RAISED core {s} stop {best:,.2f} -> {level:,.2f} (entry {entry:,.2f})")
            bookorders.log("core_stop_raised", s, f"stop {best:,.2f} -> {level:,.2f}", policy)
        except BrokerError as exc:
            log(f"  CORE STOP RAISE FAILED ({exc}) -- {best:,.2f} still stands")
        return []
    if price <= level:
        # A sell stop above the market is refused; the exit it calls for is taken now
        # (execution/protection.py's rule for a position found past its stop).
        why = f"core {s} {price:,.2f} at/under its stop {level:,.2f} with no stop working"
        if dry:
            log(f"  WOULD SELL {why}")
            return []
        payload = {**core_stop_payload(s, qty, level, now), "type": "market", "time_in_force": "day"}
        payload.pop("stop_price")
        try:
            for o in working:
                await broker.cancel_order(str(o["id"]))
            await broker.submit(payload)
        except BrokerError as exc:
            log(f"  CORE STOP SELL FAILED ({exc})")
            return [f"SPY core has NO stop and the sell failed: {exc}"]
        log(f"  SELL SENT {why}")
        bookorders.log("core_stop_sold", s, why, policy)
        return [f"Sell sent for SPY core -- {why}"]
    why = (f"{qty} sh at {level:,.2f} ({(price - level) / price * 100:.1f}% under {price:,.2f}; "
           f"buy {entry:,.2f}, ATR {atr:,.2f})")
    if dry:
        log(f"  WOULD ARM core {s} stop: {why}")
        return []
    try:
        for o in working:   # wrong share count after a top-up: replace, keeping the higher level
            await broker.cancel_order(str(o["id"]))
        await broker.submit(core_stop_payload(s, qty, level, now))
    except BrokerError as exc:
        log(f"  CORE STOP FAILED ({exc})")
        bookorders.log("core_stop_failed", s, str(exc), policy)
        return [f"SPY core stop could not be armed: {exc}"]
    log(f"  ARMED core {s} stop: {why}")
    bookorders.log("core_stop_armed", s, why, policy)
    return [f"SPY core stop armed: {why}"]


async def core_pass(broker, client, positions, owner_map: Mapping[str, str], open_orders,
                    now: datetime, dry: bool, log: Log, policy: TradingPolicy,
                    state_path: Path = CORE_STATE, orders: Iterable[Mapping[str, Any]] = ()) -> list[str]:
    if not _buying_hours(now) or policy.core_allocation_pct <= 0:
        return []
    today = now.astimezone(sat.ET).date().isoformat()
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    if state.get("day") == today:
        return []
    # A working core BUY waits for its fill; the core's own stop is a sell and does not block.
    if any(ownership.strategy_of(o.get("client_order_id")) == CORE and o.get("side") == "buy"
           for o in open_orders):
        return []
    raw = await broker._get("/v2/account")
    equity = float(raw["equity"])
    held = sum(abs(float(p.get("market_value") or 0)) for p in positions
               if ownership.owner(p["symbol"], owner_map) == CORE)
    dollars = sat.core_buy_dollars(equity=equity, core_value=held, pct=policy.core_allocation_pct)
    if not dry:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps({"day": today, "core_value": held, "buy_dollars": dollars}),
                              encoding="utf-8")
    if dollars <= 0:
        return []
    if await before_closure(broker, date.fromisoformat(today), log):
        log("  CORE BUY skipped: the weekend flatten sells the core this afternoon")
        return []
    stopped_on = core_stopped_on(orders)
    if stopped_on is not None:
        try:
            closes = (await fetch_bars(policy.core_symbol, client, range_="6mo")).closes
        except Exception as exc:                              # noqa: BLE001
            log(f"  CORE BUY skipped: stopped out {stopped_on}, no bars to check a new high ({exc})")
            return []
        if sat.core_rebuy_blocked(stopped_on=stopped_on, today=date.fromisoformat(today), closes=closes):
            log(f"  CORE BUY skipped: stopped out {stopped_on}; re-buy on a new 20-day closing high")
            return []
    last, _ = await bookorders.price_and_atr(policy.core_symbol, client)
    qty = int(dollars // last)
    if qty < 1:
        return []
    limit = round_tick(last * 1.002)
    coid = f"{ownership.prefix_for(CORE)}{policy.core_symbol}-{int(now.timestamp())}"
    why = (f"SPY core: {held:,.0f} held of a {policy.core_allocation_pct:g}% target "
           f"({equity * policy.core_allocation_pct / 100:,.0f}); buying {qty} at <= {limit:,.2f}")
    if dry:
        log(f"  WOULD BUY core {why}")
        return []
    try:
        await broker.submit({"symbol": policy.core_symbol, "qty": str(qty), "side": "buy", "type": "limit",
                             "limit_price": f"{limit:.2f}", "time_in_force": "day",
                             "client_order_id": coid, "extended_hours": False})
    except BrokerError as exc:
        log(f"  CORE BUY FAILED: {exc}")
        bookorders.log("core_refused", policy.core_symbol, str(exc), policy)
        return []
    log(f"  CORE BUY sent: {why}")
    bookorders.log("core_buy", policy.core_symbol, why, policy)
    return [f"SPY core buy: {qty} sh"]
