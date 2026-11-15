"""Arm a GTC stop under each open 10-7 position that does not have one.

The stop is 2.5 ATR under the entry and never more than 8% under it. A close
one ATR above the entry raises it to the entry, the same rule as the other
full-slot books. A level that is not under the last price is printed and not
sent. This script does not sell.

    python scripts/arm_rules_stops.py
    python scripts/arm_rules_stops.py --submit
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime

import httpx

from scanner.config import settings
from scanner.execution import ownership, plan_run
from scanner.execution import satellite as sat
from scanner.execution.alpaca import Alpaca, BrokerError, round_tick, stop_payload, unprotected
from scanner.execution.bookorders import log
from scanner.execution.ownership import GEMMA
from scanner.execution.policy import TradingPolicy
from scanner.features.bars import fetch_bars
from scanner.models import TradeIntent


async def planned(broker: Alpaca, client: httpx.AsyncClient):
    positions = await broker._get("/v2/positions")
    history = await broker._get("/v2/orders", status="all", limit=500, nested="true")
    owners = ownership.owners(history)
    entered = plan_run.entry_dates(history, (GEMMA,))
    naked = {p.symbol: p.qty for p in unprotected(positions, history)}
    today = datetime.now().astimezone(sat.ET).date()
    rows = []
    for position in positions:
        symbol = str(position["symbol"])
        if ownership.owner(symbol, owners) != GEMMA or not sat.rules_rotation(entered.get(symbol)):
            continue
        qty = naked.get(symbol, 0)
        if qty <= 0:
            continue
        entry = float(position["avg_entry_price"])
        last = float(position["current_price"])
        bars = await fetch_bars(symbol, client, range_="6mo")
        atr = plan_run.atr_dollars(bars)
        start = entered[symbol]
        after = [close for day, close in zip(bars.dates, bars.closes) if start < day < today]
        high = max([*after, entry])
        if atr <= 0:
            stop = entry * (1 - sat.MAX_STOP_PCT / 100)
        else:
            stop = sat.hard_stop(
                entry=entry, atr=atr, high_close=high, floor=False, max_loss_pct=sat.MAX_STOP_PCT)
        rows.append((symbol, qty, entry, last, round_tick(stop), atr))
    return rows


async def main(submit: bool) -> None:
    policy = TradingPolicy()
    async with httpx.AsyncClient() as client:
        broker = Alpaca(settings.alpaca_key_id, settings.alpaca_secret_key, client, paper=True)
        rows = await planned(broker, client)
        if not rows:
            print("every open 10-7 position already has a live stop")
            return
        print(f"{len(rows)} open 10-7 position(s) without a stop")
        for symbol, qty, entry, last, stop, atr in rows:
            room = (last - stop) / last * 100 if last else 0
            state = "ARM" if last > stop else "SKIP"
            print(f"  {state} {symbol:<6} {qty:>4} sh  entry {entry:,.2f}  last {last:,.2f}  "
                  f"stop {stop:,.2f}  atr {atr:,.2f}  {room:.1f}% of room")
        if not submit:
            print("report only — nothing sent")
            return
        now = datetime.now().astimezone()
        for symbol, qty, entry, last, stop, _atr in rows:
            if last <= stop:
                print(f"  not sent {symbol}: last {last:,.2f} is not above the stop {stop:,.2f}")
                continue
            coid = f"gem-stop-{symbol}-{int(now.timestamp())}"
            payload = stop_payload(symbol, qty, stop, now, coid)
            intent = TradeIntent(
                symbol=symbol, action="sell", strategy=GEMMA, trigger_at=now,
                entry_price=entry, stop_price=stop, target_price=0.0,
                shares=qty, risk_dollars=qty * (entry - stop), position_dollars=qty * entry,
                reward_risk=0.0, paper=True, client_order_id=payload["client_order_id"],
                llm_concern="operator 2026-11-10: stop on the open 10-7 book")
            try:
                order = await broker.submit(payload)
            except BrokerError as exc:
                print(f"  FAILED {symbol}: {exc}")
                continue
            log("order_submitted", symbol, intent.llm_concern, policy, intent=intent,
                order_id=str(order.get("id", "")))
            print(f"  armed {symbol} stop {stop:,.2f} id {order.get('id', '?')}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--submit", action="store_true")
    asyncio.run(main(parser.parse_args().submit))
