"""Buy-with-a-safety-stop and sell, shared by every list-driven book.

The picks list (scripts/picks_sync.py) and Gemma's book (scripts/gemma_trade.py)
place the same kind of order: a limit just above the last price, a stop
`atr_multiple` daily ATRs below, a target ceiling, a GTC bracket. Kept in one
place so the two books cannot drift into different order logic.

Every order is logged as a TradeIntent + order_submitted, which is what
protect.py reads to re-arm a lost stop and what the scorecard grades.
"""

from __future__ import annotations

from datetime import datetime

from scanner.config import settings
import asyncio

from scanner.execution import extended, manual, picks, profit_lock
from scanner.execution.alpaca import BrokerError, round_tick
from scanner.execution.audit import NO_TARGET_MULTIPLE, append_event
from scanner.execution.flatten_run import close_when_released
from scanner.execution.ownership import prefix_for
from scanner.execution.policy import TradingPolicy
from scanner.execution.provenance import build_id
from scanner.execution.sessions import REGULAR
from scanner.execution.sizing import Sizing, size_position
from scanner.features.bars import fetch_bars
from scanner.features.intraday import fetch_intraday, max_range
from scanner.features.intraday_context import atr_pct
from scanner.models import AuditEvent, Frozen, TradeIntent


class Bought(Frozen):
    symbol: str
    ok: bool
    message: str
    sizing: Sizing | None = None


def log(event: str, symbol: str, reason: str, policy: TradingPolicy, **kw) -> None:
    append_event(settings.audit_log_path, AuditEvent(
        event=event, at=datetime.now().astimezone(), symbol=symbol, reason=reason,
        stage="trade", policy=policy.fingerprint, build=build_id(), **kw))


def target_price(*, limit: float, stop: float, reward_risk: float,
                 ceiling: float | None = None) -> float | None:
    """`reward_risk` x the risk above the entry; 0 means no target at all."""
    if reward_risk > 0:
        return round_tick(limit + reward_risk * (limit - stop))
    if ceiling is None:
        return None   # Gemma chose "none": ride it, no ceiling target
    return round_tick(limit * ceiling)


async def price_and_atr(symbol: str, client) -> tuple[float, float]:
    intraday = await fetch_intraday(symbol, client, minutes=30, range_=max_range(30))
    daily = await fetch_bars(symbol, client, range_="6mo")
    last = float(intraday.closes[-1]) if intraday.closes else float(daily.closes[-1])
    return last, atr_pct(daily) or 0.0


async def buy_with_stop(broker, client, symbol: str, *, strategy: str, sleeve: float,
                        room: float, cap_pct: float, policy: TradingPolicy,
                        reason: str, risk_pct: float | None = None,
                        atr_multiple: float | None = None,
                        reward_risk: float | None = None,
                        session: str = REGULAR, loss_floor: bool = True,
                        max_loss_pct: float | None = None) -> Bought:
    """Price, size, send and log one bracket buy. Never raises on a broker or
    pricing failure; the refusal is logged and returned. `atr_multiple` and
    `reward_risk` are Gemma's menu choices; left out, the picks defaults.
    `loss_floor` off keeps the full ATR stop (satellite.wide_stop; the caller
    lowers `risk_pct` so the size shrinks instead)."""
    try:
        last, atr = await price_and_atr(symbol, client)
        if session != REGULAR:
            # Outside regular hours: today's extended-session price, fresh or nothing.
            live = await extended.price(symbol, client, session)
            if live is None:
                raise ValueError(f"no fresh {session} price")
            last = live
        limit, stop, _ = picks.levels(
            last=last, atr_pct=atr,
            atr_multiple=policy.picks_stop_atr_multiple if atr_multiple is None else atr_multiple,
            offset_pct=policy.entry_limit_offset_pct, ceiling=policy.picks_target_ceiling)
        # Never more than MAX_LOSS_PCT under the buy (operator 2026-10-23), unless the
        # caller sizes for the wide stop instead (Gemma's B2, 2026-10-26).
        if loss_floor:
            stop = round_tick(max(stop, profit_lock.loss_floor(limit)))
        if max_loss_pct is not None:   # full slots (satellite.MAX_STOP_PCT)
            stop = round_tick(max(stop, limit * (1 - max_loss_pct / 100)))
        # reward_risk is None when the picks list calls without specifying
        # (picks always uses picks_target_ceiling as its ceiling target).
        # reward_risk == 0.0 means Gemma explicitly chose "none" (ride it).
        # reward_risk > 0 means Gemma chose 2R/3R/5R.
        if reward_risk is None:
            effective_rr = 0.0
            target_ceiling = policy.picks_target_ceiling
        else:
            effective_rr = reward_risk
            target_ceiling = None if effective_rr == 0.0 else policy.picks_target_ceiling
        target = target_price(limit=limit, stop=stop, reward_risk=effective_rr,
                              ceiling=target_ceiling)
    except Exception as exc:                               # noqa: BLE001
        log(f"{strategy}_refused", symbol, f"cannot price: {exc}", policy)
        return Bought(symbol=symbol, ok=False, message=f"cannot price: {exc}")
    sizing = size_position(sleeve, limit, stop,
                           risk_pct=policy.risk_pct if risk_pct is None else risk_pct,
                           max_position_pct=cap_pct, buying_power=room)
    if not sizing.is_tradeable:
        log(f"{strategy}_refused", symbol, sizing.limited_by, policy)
        return Bought(symbol=symbol, ok=False, message=sizing.limited_by)
    now = datetime.now().astimezone()
    coid = f"{prefix_for(strategy)}{symbol}-{int(now.timestamp())}"
    # Gemma may choose target "none" (reward_risk == 0): ride it, no ceiling
    # target. The bracket still needs a take_profit leg, so use a ceiling so
    # high it is effectively never hit (NO_TARGET_MULTIPLE x the buy) — the
    # plan's exits sell. Record the true choice (0.0 target, 0.0 R:R) in the audit intent.
    if effective_rr > 0:
        # Gemma chose 2R/3R/5R: target is limit + R:R * risk
        order_target = target
        intent_target = target
        intent_rr = round((target - limit) / (limit - stop), 3)
    elif target_ceiling is not None:
        # Picks list: ceiling target (2.0x entry), reward_risk not specified
        order_target = target  # which is limit * ceiling
        intent_target = target
        intent_rr = round((target - limit) / (limit - stop), 3)
    else:
        # Gemma chose "none": effectively no target
        order_target = round_tick(limit * NO_TARGET_MULTIPLE)
        intent_target = 0.0
        intent_rr = 0.0
    if session == REGULAR:
        payload = manual.bracket(symbol, sizing.shares, limit=limit, stop=stop,
                                 target=order_target, at=now)
        payload["client_order_id"] = coid
    else:
        # No stop legs exist outside regular hours: the stop and target ride in
        # the logged intent and protect.py arms them at the open (WORKLIST §59).
        payload = extended.limit_payload(symbol, sizing.shares, "buy", limit, coid)
    intent = TradeIntent(
        symbol=symbol, action="buy", strategy=strategy, trigger_at=now,
        entry_price=limit, stop_price=stop, target_price=intent_target,
        shares=sizing.shares, risk_dollars=sizing.risk_dollars,
        position_dollars=sizing.position_dollars,
        # limit > entry > stop (offset_pct >= 0, and sizing guarantees
        # stop < entry), so the denominator is strictly positive.
        reward_risk=intent_rr,
        paper=True, client_order_id=payload["client_order_id"], llm_concern=reason[:300])
    log("trade_intent", symbol, reason, policy, intent=intent)
    try:
        order = await broker.submit(payload)
    except BrokerError as exc:
        log("order_rejected", symbol, str(exc), policy, intent=intent)
        return Bought(symbol=symbol, ok=False, message=f"rejected: {exc}")
    log("order_submitted", symbol, reason, policy, intent=intent,
        order_id=str(order.get("id", "")))
    return Bought(symbol=symbol, ok=True, sizing=sizing,
                  message=(f"{sizing.shares} sh limit {limit:,.2f} stop {stop:,.2f} "
                           f"({(limit - stop) / limit * 100:.1f}% below), "
                           f"risk {sizing.risk_dollars:,.0f}, limited by {sizing.limited_by}"
                           + ("" if session == REGULAR
                              else f" [{session}: stop arms at the open]")))


async def sell(broker, symbol: str, open_orders, *, strategy: str, reason: str,
               policy: TradingPolicy, session: str = REGULAR, client=None) -> bool:
    """Cancel the symbol's working orders (its bracket legs), then close the
    position once the shares are released. Logged either way. Outside regular
    hours the close is a limit order a little under the live price (market
    orders are not accepted then). Outside regular hours the price is checked
    BEFORE the stop is cancelled: with no fresh price the sale cannot be sent,
    and the position keeps its stop (2026-10-22 -- it used to be left naked)."""
    live = None
    if session != REGULAR:
        live = await extended.price(symbol, client, session) if client is not None else None
        if live is None:
            log(f"{strategy}_exit", symbol, f"{reason}; {session} CLOSE FAILED: no fresh price "
                "(stop kept)", policy)
            return False
    for order in open_orders:
        if order.get("symbol") == symbol:
            try:
                await broker.cancel_order(str(order["id"]))
            except BrokerError:
                pass
    if session != REGULAR:
        return await _sell_extended(broker, symbol, strategy=strategy, reason=reason,
                                    policy=policy, session=session, live=live)
    failed = await close_when_released(broker, (symbol,))
    ok = symbol not in failed
    log(f"{strategy}_exit", symbol,
        reason + ("; closed at market" if ok else f"; CLOSE FAILED: {failed.get(symbol)}"),
        policy)
    return ok


async def _sell_extended(broker, symbol: str, *, strategy: str, reason: str,
                         policy: TradingPolicy, session: str, live: float) -> bool:
    qty = 0
    for _ in range(10):                              # wait for cancelled legs to release
        held = {p["symbol"]: p for p in await broker._get("/v2/positions")}
        if symbol not in held:
            log(f"{strategy}_exit", symbol, reason + "; already closed", policy)
            return True
        qty = int(float(held[symbol].get("qty_available") or 0))
        if qty > 0:
            break
        await asyncio.sleep(2)
    if live is None or qty < 1:
        why = "no fresh price" if live is None else "shares not released"
        log(f"{strategy}_exit", symbol, f"{reason}; {session} CLOSE FAILED: {why}", policy)
        return False
    limit = live * (1 - policy.extended_sell_offset_pct / 100)
    coid = f"{prefix_for(strategy)}{symbol}-x{int(datetime.now().timestamp())}"
    try:
        await broker.submit(extended.limit_payload(symbol, qty, "sell", limit, coid))
    except BrokerError as exc:
        log(f"{strategy}_exit", symbol, f"{reason}; {session} CLOSE FAILED: {exc}", policy)
        return False
    log(f"{strategy}_exit", symbol, f"{reason}; {session} limit sell {qty} sh at {limit:,.2f}", policy)
    return True
