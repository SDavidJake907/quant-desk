"""The plan's three passes, run by peak watch every 5 minutes (execution/satellite.py has
the rules; this file only fetches, decides with them, and sends).

  exit_pass  every session: raise each Gemma / picks position's broker stop to the plan's
             hard stop (2.5 ATR, never more than 3%, break-even from +1 ATR) and sell at
             or under it; in the last 10 minutes of regular hours, the close-based exits
             (trailing 3 ATR under the highest close, the 10-day time stop, earnings)
  buy_pass   regular hours from 10:00 ET: send Gemma's queued buys (execution/buy_queue.py) that
             pass the 10:00 recheck (fell 2%+ since her decision, a new theme alert, earnings).
             A buy still due after the close goes out 16:00-20:00 ET as a day limit.
  core_pass  regular hours from 10:00 ET, once a day: buy the SPY core up to its target,
             at most half the target a day (two halves), topped up only below 90%.
             The same due buy, trim, or stop sale can go out after hours as a limit.

Replaces the profit lock (+3% / +1% / 3% trail) and the 5-minute peak-sell, which the
plan removed (peak-sell's +2% trigger caught bounces, not tops).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Iterable, Mapping
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from scanner.execution import bookorders, buy_queue, extended, flatten, ownership, picks, profit_lock
from scanner.execution import satellite as sat
from scanner.execution.alpaca import BrokerError, round_tick
from scanner.execution.guards import RiskLimits, check_account
from scanner.execution import small_book
from scanner.execution.ownership import CORE, GEMMA, PICKS, SMALL
from scanner.execution.policy import TradingPolicy
from scanner.execution.sessions import AFTER, REGULAR
from scanner.features.bars import fetch_bars
from scanner.features.intraday_context import atr_pct
from scanner.ops import shock_alert as sa
from scanner.ops import shock_feed
from scanner.sources import alpaca_bars, composite
from scanner.sources import calendar as event_calendar

CORE_STATE = Path("data/monitor/core_state.json")
#: A core sale that is a rebalance, not a stop-out (core_stopped_on reads only cor-stop-).
CORE_TRIM_PREFIX = "cor-trim-"
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


def _order_window(now: datetime, session: str) -> bool:
    """The 10:00 regular clock, or the after-hours session for an order already due.

    Premarket and overnight are not an order window."""
    if session == AFTER:
        return True
    if session != REGULAR:
        return False
    return _buying_hours(now)


def _retry_when_priced(session: str, result: bookorders.Bought) -> bool:
    """An after-hours buy with no fresh print stays queued for the next pass."""
    return session == AFTER and not result.ok and "no fresh" in result.message


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
    found, failed = await event_calendar.with_moomoo(frozenset(symbols), client,
                                                      horizon=(nxt - today).days + 1, today=today)
    if failed:
        raise RuntimeError(f"earnings calendar unreadable for {failed}")
    return {s for s, e in found.items()
            if sat.reports_before_next_session(e.on, e.when, today, nxt)}


async def _spx_extended(client, today: date, log: Log) -> bool | None:
    """Whether today's S&P 500 close is more than 3% above its 20-day average.

    A missing bar, or a series that does not include today, does not sell. The
    range is 1 month so this read is not the morning's longer cache."""
    try:
        bars = await fetch_bars("^GSPC", client, range_="1mo")
    except Exception as exc:                                  # noqa: BLE001
        log(f"  S&P extension unreadable ({exc}) -- the rules book is not sold for it")
        return None
    if not bars.dates or bars.dates[-1] != today:
        log("  S&P daily bar is not today's -- the extension exit waits")
        return None
    return sat.market_extended(bars.closes)


async def exit_pass(broker, client, positions, book_of: Mapping[str, str], orders, open_orders,
                    session: str, now: datetime, dry: bool, log: Log,
                    sell: Callable[..., Any]) -> list[str]:
    today = now.astimezone(sat.ET).date()
    entered = entry_dates(orders, (GEMMA, PICKS, SMALL))
    stops = profit_lock.stop_orders(orders)
    closing = session == REGULAR and sat.in_close_window(now)
    # The 10-7 extension and the 15-session hold are decided on the regular close.
    # After 16:00 that same decision can still go out as a limit. The half-gain
    # profit lock is a close, like the small-cap lock, and stays in `closing`.
    # The 10-7 broker stop rises on any regular-session pass, and only upward.
    close_due = closing or session == AFTER
    reporting: set[str] = set()
    if closing:
        try:
            reporting = await _reporting(broker, client, [p["symbol"] for p in positions], today)
        except Exception as exc:                              # noqa: BLE001
            log(f"  earnings check failed ({exc}) -- trailing and time exits still run")
    extended: bool | None = None
    extended_known = False
    alerts: list[str] = []
    for p in positions:
        s, book = p["symbol"], book_of[p["symbol"]]
        entry, price = float(p["avg_entry_price"]), float(p["current_price"])
        start = entered.get(s)
        if book == GEMMA and sat.rules_rotation(start):
            why = None
            after: list[float] = []
            if close_due:
                if not extended_known:
                    extended = await _spx_extended(client, today, log)
                    extended_known = True
                held = sat.trading_days_between(start, today)
                why = sat.rules_close_exit(held_days=held, extended=extended)
            if why is None and (closing or session == REGULAR):
                try:
                    bars = await fetch_bars(s, client, range_="6mo")
                except Exception as exc:                      # noqa: BLE001
                    log(f"  {s}: no daily bars ({exc}) -- its broker stop stands")
                else:
                    # The entry day and today stay out. A wick is not a close.
                    after = [c for d, c in zip(bars.dates, bars.closes)
                             if start and start < d < today]
                    if closing:
                        why = sat.profit_lock_exit(
                            entry=entry, closes_since_buy=after, price=price)
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
            if session == REGULAR and after and order_id is not None and broker_stop is not None:
                level = round_tick(sat.raised_stop(entry, max(after), broker_stop))
                # A stop at or above the live price is refused, and it is not a sale.
                if price > level >= broker_stop + profit_lock.MIN_RAISE:
                    if dry:
                        log(f"  WOULD RAISE {s} stop {broker_stop:,.2f} -> {level:,.2f}")
                    else:
                        try:
                            await broker.replace_stop(order_id, level)
                            log(f"  RAISED {s} stop {broker_stop:,.2f} -> {level:,.2f} "
                                f"(10-7; entry {entry:,.2f})")
                            bookorders.log(f"{book}_stop_raised", s,
                                           f"plan stop {broker_stop:,.2f} -> {level:,.2f}",
                                           TradingPolicy())
                        except BrokerError as exc:
                            log(f"  RAISE FAILED {s} ({exc}) -- the old stop stands")
            continue
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
        found, _ = await event_calendar.with_moomoo(frozenset(symbols), client,
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
                   strategy: str = GEMMA, session: str = REGULAR) -> list[str]:
    if not _order_window(now, session):
        return []
    alloc_field, slots_field, label = QUEUED_BOOKS[strategy]
    # From RULES_BOOK_FROM the rules book holds 10 names. The policy field stays
    # at 5 so the open book's sizing record and the policy fingerprint do not move.
    rotation_book = (strategy == GEMMA
                     and now.astimezone(sat.ET).date() >= sat.RULES_BOOK_FROM)
    max_positions = sat.RULES_TOP_N if rotation_book else int(getattr(policy, slots_field))
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
        if s in skipped:
            log(f"  QUEUED BUY {s} skipped at 10:00 -- {skipped[s]}")
            refused(s, f"10:00 recheck: {skipped[s]}")
            done.append(s)
            continue
        if not gate:
            log(f"  QUEUED BUY {s} not sent -- {gate.reason}")
            refused(s, gate.reason)
            done.append(s)
            continue
        if slots <= 0:
            log(f"  QUEUED BUY {s} not sent -- all {max_positions} slots in use")
            refused(s, "no free slot at 10:00")
            done.append(s)
            continue
        if rotation_book and not plan.get("rotation"):
            log(f"  QUEUED BUY {s} not sent -- queued under the old rules book")
            refused(s, "queued under the old rules book")
            done.append(s)
            continue
        if dry:
            log(f"  WOULD BUY (queued) {s}")
            done.append(s)
            continue
        if rotation_book:
            result = await bookorders.buy_rotation(
                broker, client, s, strategy=strategy, sleeve=sleeve, room=room,
                slots=max_positions, policy=policy,
                reason=f"{label} (10-7 rotation, 10:00 ET): {plan.get('reason', '')}",
                session=session)
            if _retry_when_priced(session, result):
                log(f"  QUEUED BUY {s} waits: no fresh after-hours price")
                continue
            log(f"  {'BOUGHT' if result.ok else 'NOT BOUGHT'} (queued) {s}: {result.message}")
            done.append(s)
            if result.ok and result.sizing is not None:
                slots -= 1
                room -= result.sizing.position_dollars
                alerts.append(f"{label} bought {s} (10:00 queue)")
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
            reward_risk=policy.gemma_menu_value("target", plan.get("target", "none")), session=session,
            loss_floor=False, max_loss_pct=sat.MAX_STOP_PCT if full else None)
        if _retry_when_priced(session, result):
            log(f"  QUEUED BUY {s} waits: no fresh after-hours price")
            continue
        log(f"  {'BOUGHT' if result.ok else 'NOT BOUGHT'} (queued) {s}: {result.message}")
        done.append(s)
        if result.ok:
            slots -= 1
            room -= result.sizing.position_dollars
            alerts.append(f"{label} bought {s} (10:00 queue)")
    if not dry:
        path.write_text(json.dumps(buy_queue.without(queue, done), indent=1), encoding="utf-8")
    # Unfilled regular-session buys older than the picks rule are cancelled.
    # An after-hours day limit is left to work until 20:00 ET.
    if session != REGULAR:
        return alerts
    for o in picks.stale_entries(open_orders, now, minutes=policy.picks_stale_entry_minutes, strategy=strategy):
        if not dry:
            try:
                await broker.cancel_order(str(o["id"]))
                log(f"  cancelled unfilled buy {o['symbol']} (older than {policy.picks_stale_entry_minutes:g} min)")
            except BrokerError:
                pass
    return alerts


def core_stopped_on(orders: Iterable[Mapping[str, Any]], symbol: str | None = None) -> date | None:
    """ET date `symbol`'s core stop last filled (status=all history), or None."""
    fills = [str(o["filled_at"]) for o in orders
             if o.get("side") == "sell" and o.get("filled_at")
             and (symbol is None or o.get("symbol") == symbol)
             and str(o.get("client_order_id") or "").startswith(sat.CORE_STOP_PREFIX)]
    if not fills:
        return None
    return datetime.fromisoformat(max(fills).replace("Z", "+00:00")).astimezone(sat.ET).date()


def core_stop_orders(open_orders: Iterable[Mapping[str, Any]], symbol: str | None = None) -> list[Mapping[str, Any]]:
    return [o for o in open_orders if o.get("side") == "sell" and o.get("type") == "stop"
            and (symbol is None or o.get("symbol") == symbol)
            and str(o.get("client_order_id") or "").startswith(sat.CORE_STOP_PREFIX)]


def core_stop_payload(symbol: str, qty: int, stop: float, now: datetime) -> dict[str, Any]:
    """GTC so it survives the close; a plain stop, it acts intraday and at a gap."""
    return {"symbol": symbol, "qty": str(qty), "side": "sell", "type": "stop",
            "stop_price": f"{round_tick(stop):.2f}", "time_in_force": "gtc",
            "client_order_id": f"{sat.CORE_STOP_PREFIX}{symbol}-{int(now.timestamp())}"}


async def core_stop_pass(broker, client, positions, owner_map: Mapping[str, str], orders, open_orders,
                         now: datetime, dry: bool, log: Log, policy: TradingPolicy,
                         session: str = REGULAR) -> list[str]:
    """Keep one stop under every core share of each index (operator 2026-10-30): armed when
    missing, re-sent at the new share count after a top-up, raised with the plan, never lowered."""
    alerts: list[str] = []
    for s in policy.core_symbols:
        alerts += await _core_stop_for(s, broker, client, positions, owner_map, orders, open_orders,
                                       now, dry, log, policy, session)
    return alerts


async def _core_stop_for(s: str, broker, client, positions, owner_map: Mapping[str, str], orders,
                         open_orders, now: datetime, dry: bool, log: Log, policy: TradingPolicy,
                         session: str = REGULAR) -> list[str]:
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
    working = core_stop_orders(open_orders, s)
    best = max((float(o["stop_price"]) for o in working if o.get("stop_price")), default=None)
    level = hard if best is None else max(hard, best)
    # After hours a GTC stop does not fill. A price already through it is a
    # limit sale, not a raise. The raise stays the regular-session path.
    matched = len(working) == 1 and int(float(working[0]["qty"])) == qty
    if matched and not (session == AFTER and price <= level):
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
        if session == AFTER:
            live = await extended.price(s, client, session) if client is not None else None
            if live is None:
                log(f"  CORE STOP {s} waits: no fresh after-hours price (stop kept)")
                return []
            limit = round_tick(live * (1 - policy.extended_sell_offset_pct / 100))
            payload = extended.limit_payload(
                s, qty, "sell", limit, f"{sat.CORE_STOP_PREFIX}{s}-x{int(now.timestamp())}")
        elif session != REGULAR:
            log(f"  CORE STOP {s} waits: the through-stop sell goes out in regular hours or after hours")
            return []
        else:
            payload = {**core_stop_payload(s, qty, level, now), "type": "market", "time_in_force": "day"}
            payload.pop("stop_price")
        try:
            for o in working:
                await broker.cancel_order(str(o["id"]))
            await broker.submit(payload)
        except BrokerError as exc:
            log(f"  CORE STOP SELL FAILED ({exc})")
            return [f"{s} core has NO stop and the sell failed: {exc}"]
        log(f"  SELL SENT {why}")
        bookorders.log("core_stop_sold", s, why, policy)
        return [f"Sell sent for {s} core -- {why}"]
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
        return [f"{s} core stop could not be armed: {exc}"]
    log(f"  ARMED core {s} stop: {why}")
    bookorders.log("core_stop_armed", s, why, policy)
    return [f"{s} core stop armed: {why}"]


async def core_pass(broker, client, positions, owner_map: Mapping[str, str], open_orders,
                    now: datetime, dry: bool, log: Log, policy: TradingPolicy,
                    state_path: Path = CORE_STATE, orders: Iterable[Mapping[str, Any]] = (),
                    session: str = REGULAR) -> list[str]:
    """Once a day from 10:00 ET: each core index (policy.core_symbols, equal shares of the
    core) is bought toward its target in two halves, or sold down when far over it.
    After hours, a buy or trim that is still due goes out as a limit."""
    if not _order_window(now, session) or policy.core_allocation_pct <= 0 or not policy.core_symbols:
        return []
    today = now.astimezone(sat.ET).date().isoformat()
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    symbols = set(policy.core_symbols)
    # A file from before this pass recorded a symbol list has no "done". That
    # file means the day already ran, including this morning's refused QQQ add.
    # Deleting it would send the order on the next five-minute pass.
    if state.get("day") == today and ("done" not in state or symbols <= set(state.get("done") or [])):
        return []
    done = set(state.get("done") or []) if state.get("day") == today else set()
    raw = await broker._get("/v2/account")
    equity = float(raw["equity"])
    pct = policy.core_allocation_pct / len(policy.core_symbols)
    held = {s: sum(abs(float(p.get("market_value") or 0)) for p in positions
                   if p["symbol"] == s and ownership.owner(s, owner_map) == CORE)
            for s in policy.core_symbols}

    def stamp() -> None:
        if dry:
            return
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps({
            "day": today, "core_value": held, "pct_each": pct, "done": sorted(done),
        }), encoding="utf-8")

    alerts: list[str] = []
    for s in policy.core_symbols:
        if s in done:
            continue
        target = equity * pct / 100
        if held[s] > target * policy.core_trim_above:
            extra, finished = await core_trim(
                broker, s, positions, open_orders, held[s], target, now, dry, log, policy,
                session=session, client=client)
        else:
            extra, finished = await _core_buy_for(
                s, broker, client, open_orders, orders, equity, held[s], pct,
                today, now, dry, log, policy, session=session)
        alerts += extra
        # A missing after-hours price is not finished: the next pass retries.
        # A crash inside the call still leaves the symbol out of "done".
        if not finished:
            continue
        done.add(s)
        stamp()
    return alerts


async def _core_buy_for(s: str, broker, client, open_orders, orders, equity: float, held: float,
                        pct: float, today: str, now: datetime, dry: bool, log: Log,
                        policy: TradingPolicy, session: str = REGULAR) -> tuple[list[str], bool]:
    """Returns the alerts and whether this symbol is finished for the day.

    A missing after-hours price is not finished, so the next pass can price it."""
    # A working core BUY waits for its fill. A working core stop is a sell. The
    # broker can still refuse the buy as a wash trade against that stop.
    if any(ownership.strategy_of(o.get("client_order_id")) == CORE and o.get("side") == "buy"
           and o.get("symbol") == s for o in open_orders):
        return [], True
    dollars = sat.core_buy_dollars(equity=equity, core_value=held, pct=pct)
    if dollars <= 0:
        return [], True
    if await before_closure(broker, date.fromisoformat(today), log):
        log(f"  CORE BUY {s} skipped: the weekend flatten sells the core this afternoon")
        return [], True
    stopped_on = core_stopped_on(orders, s)
    if stopped_on is not None:
        try:
            closes = (await fetch_bars(s, client, range_="6mo")).closes
        except Exception as exc:                              # noqa: BLE001
            log(f"  CORE BUY {s} skipped: stopped out {stopped_on}, no bars to check a new high ({exc})")
            return [], True
        if sat.core_rebuy_blocked(stopped_on=stopped_on, today=date.fromisoformat(today), closes=closes):
            log(f"  CORE BUY {s} skipped: stopped out {stopped_on}; re-buy on a new 20-day closing high")
            return [], True
    live = None
    if session == AFTER:
        live = await extended.price(s, client, session)
        if live is None:
            log(f"  CORE BUY {s} waits: no fresh after-hours price")
            return [], False
    last, _ = await bookorders.price_and_atr(s, client)
    if live is not None:
        last = live
    qty = int(dollars // last)
    if qty < 1:
        return [], True
    limit = round_tick(last * 1.002)
    coid = f"{ownership.prefix_for(CORE)}{s}-{int(now.timestamp())}"
    why = (f"{s} core: {held:,.0f} held of a {pct:g}% target ({equity * pct / 100:,.0f}); "
           f"buying {qty} at <= {limit:,.2f}")
    if dry:
        log(f"  WOULD BUY core {why}")
        return [], True
    if session == AFTER:
        payload = extended.limit_payload(s, qty, "buy", limit, coid)
    else:
        payload = {"symbol": s, "qty": str(qty), "side": "buy", "type": "limit",
                   "limit_price": f"{limit:.2f}", "time_in_force": "day",
                   "client_order_id": coid, "extended_hours": False}
    armed = True
    try:
        await broker.submit(payload)
    except BrokerError as exc:
        sent, armed = await _retry_wash(s, broker, open_orders, payload, coid, now, log, policy, exc)
        if not sent:
            return ([], True) if armed else ([f"{s} core stop could not be put back"], True)
    log(f"  CORE BUY sent: {why}")
    bookorders.log("core_buy", s, why, policy)
    alerts = [f"{s} core buy: {qty} sh"]
    if not armed:
        alerts.append(f"{s} core stop could not be put back")
    return alerts, True


def _is_wash(exc: BrokerError) -> bool:
    return "wash" in str(exc).lower()


def _saved_core_stops(open_orders, symbol: str) -> list[tuple[str, int, float]] | None:
    """Id, shares, and price of each working core stop. None when one cannot be put back."""
    saved: list[tuple[str, int, float]] = []
    for order in core_stop_orders(open_orders, symbol):
        try:
            qty = int(float(order["qty"]))
            price = float(order["stop_price"])
        except (KeyError, TypeError, ValueError):
            return None
        if qty < 1 or price <= 0 or not order.get("id"):
            return None
        saved.append((str(order["id"]), qty, price))
    return saved


async def _shares_free(broker, symbol: str, qty: int) -> bool:
    try:
        live = await broker._get(f"/v2/positions/{symbol}")
        return int(float(live.get("qty_available") or 0)) >= qty
    except (BrokerError, TypeError, ValueError):
        return False


async def _rearm_core_stops(broker, saved: list[tuple[str, int, float]], symbol: str,
                            now: datetime, log: Log, policy: TradingPolicy) -> bool:
    """Put each cancelled stop back. Wait until its shares are free, then retry the stop only.

    A cancel does not free the shares in the same instant. Sending the stop
    into that window is a 422, and sending it under the new buy can be a 403.
    Either one used to be swallowed, the buy was called done, and the shares
    stayed naked. This does not send another buy.
    """
    ok = True
    for i, (_order_id, qty, price) in enumerate(saved):
        placed = False
        last = "shares never released"
        for attempt in range(TRIM_RELEASE_TRIES):
            free = await _shares_free(broker, symbol, qty)
            if not free and attempt < TRIM_RELEASE_TRIES - 1:
                await asyncio.sleep(TRIM_RELEASE_WAIT)
                continue
            try:
                when = now + timedelta(seconds=i * TRIM_RELEASE_TRIES + attempt)
                await broker.submit(core_stop_payload(symbol, qty, price, when))
                placed = True
                break
            except BrokerError as exc:
                last = str(exc)
                if attempt < TRIM_RELEASE_TRIES - 1:
                    await asyncio.sleep(TRIM_RELEASE_WAIT)
        if not placed:
            ok = False
            log(f"  CORE STOP {symbol} could not be put back ({last})")
            bookorders.log("core_stop_failed", symbol, last, policy)
    return ok


async def _retry_wash(s: str, broker, open_orders, payload: dict[str, Any], coid: str,
                      now: datetime, log: Log, policy: TradingPolicy, exc: BrokerError) -> tuple[bool, bool]:
    """One more buy when the broker calls it a wash trade against the core stop.

    Returns (buy sent, stop back on). The stop is cancelled, the buy is sent
    once, and the old stop goes back on the old share count in this same pass.
    The next core stop pass resizes it after a fill. Any other refusal leaves
    the stop where it is. A cancel that fails stops the retry: a stop already
    cancelled in this attempt is put back, and the buy is not sent.
    """
    if not _is_wash(exc):
        log(f"  CORE BUY {s} FAILED: {exc}")
        bookorders.log("core_refused", s, str(exc), policy)
        return False, True
    saved = _saved_core_stops(open_orders, s)
    if saved is None:
        log(f"  CORE BUY {s} FAILED: {exc}")
        bookorders.log("core_refused", s, str(exc), policy)
        return False, True
    log(f"  CORE BUY {s} blocked by its stop; cancelling it and trying once")
    released: list[tuple[str, int, float]] = []
    try:
        for order_id, qty, price in saved:
            await broker.cancel_order(order_id)
            released.append((order_id, qty, price))
    except BrokerError as again:
        armed = await _rearm_core_stops(broker, released, s, now, log, policy)
        log(f"  CORE BUY {s} FAILED: the stop is still working ({again})")
        bookorders.log("core_refused", s, str(again), policy)
        return False, armed
    try:
        await broker.submit({**payload, "client_order_id": f"{coid}-r"})
    except BrokerError as again:
        armed = await _rearm_core_stops(broker, saved, s, now, log, policy)
        log(f"  CORE BUY {s} FAILED: {again}")
        bookorders.log("core_refused", s, str(again), policy)
        return False, armed
    armed = await _rearm_core_stops(broker, saved, s, now, log, policy)
    return True, armed


#: Waiting for a cancelled stop to release its shares before the trim sells them.
TRIM_RELEASE_TRIES, TRIM_RELEASE_WAIT = 6, 2.0


async def core_trim(broker, s: str, positions, open_orders, held: float, target: float,
                    now: datetime, dry: bool, log: Log, policy: TradingPolicy,
                    session: str = REGULAR, client=None) -> tuple[list[str], bool]:
    """Sell a core index down to its target. Regular hours use a market order.
    After hours use a limit, and the stop stays until that price is fresh.
    The sale's `cor-trim-` id is not a stop-out, so it never triggers the re-buy rule.
    The bool is False only when after hours has no fresh price yet."""
    pos = next((p for p in positions if p["symbol"] == s), None)
    price = float(pos["current_price"]) if pos else 0.0
    if session == AFTER:
        live = await extended.price(s, client, session) if client is not None else None
        if live is None:
            log(f"  CORE TRIM {s} waits: no fresh after-hours price (stop kept)")
            return [], False
        price = live
    sell_qty = int((held - target) // price) if price > 0 else 0
    if sell_qty < 1:
        return [], True
    how = "at market" if session != AFTER else f"limit {price * (1 - policy.extended_sell_offset_pct / 100):,.2f}"
    why = (f"{s} core {held:,.0f} is over {policy.core_trim_above:g}x its {target:,.0f} target; "
           f"selling {sell_qty} of {int(float(pos['qty']))} {how}")
    if dry:
        log(f"  WOULD TRIM {why}")
        return [], True
    coid = f"{CORE_TRIM_PREFIX}{s}-{int(now.timestamp())}"
    if session == AFTER:
        payload = extended.limit_payload(
            s, sell_qty, "sell", price * (1 - policy.extended_sell_offset_pct / 100), coid)
    else:
        payload = {"symbol": s, "qty": str(sell_qty), "side": "sell", "type": "market",
                   "time_in_force": "day", "client_order_id": coid}
    try:
        for o in core_stop_orders(open_orders, s):
            await broker.cancel_order(str(o["id"]))
        for _ in range(TRIM_RELEASE_TRIES):
            live = await broker._get(f"/v2/positions/{s}")
            if int(float(live.get("qty_available") or 0)) >= sell_qty:
                break
            await asyncio.sleep(TRIM_RELEASE_WAIT)
        else:
            log(f"  CORE TRIM {s}: shares never released -- the stop is re-armed next pass")
            return [f"{s} core trim not sent: shares never released"], True
        await broker.submit(payload)
    except BrokerError as exc:
        log(f"  CORE TRIM {s} FAILED ({exc}) -- the stop is re-armed next pass")
        bookorders.log("core_trim_failed", s, str(exc), policy)
        return [f"{s} core trim failed: {exc}"], True
    log(f"  CORE TRIM sent: {why}")
    bookorders.log("core_trim", s, why, policy)
    return [f"{s} core trimmed: sold {sell_qty} sh"], True
