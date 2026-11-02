"""Close positions the backtest would already have closed.

    python scripts/manage_positions.py                  # report only
    python scripts/manage_positions.py --execute        # actually close
    python scripts/manage_positions.py --max-bars 60

THE MISMATCH THIS FIXES
The backtest closed every trade after 60 bars — that is how +0.218R was
measured, and 5.6% of holdout trades exited on that rule rather than at a stop
or a target. A bracket order has no such limit. Without this, a filled position
rides indefinitely while the evidence justifying it assumed a ~4.6 session
maximum. Live results would then be measuring a strategy nobody tested.

Age is counted in BARS from the symbol's own series, not in calendar time,
because that is how the limit was defined. Weekends and holidays therefore
cost nothing, exactly as in the backtest.

It also cancels working orders whose trigger bar has aged out: a limit resting
at a price the setup no longer justifies is not the trade that was validated.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime

import httpx

from scanner.config import settings
from scanner.execution.alpaca import Alpaca, BrokerError
from scanner.execution.policy import TradingPolicy
from scanner.features.intraday import fetch_intraday, max_range

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"
GREEN, RED, YELLOW = "\033[32m", "\033[31m", "\033[33m"


def bars_since(bars, moment: datetime) -> int:
    """How many completed bars have printed since `moment`."""
    return sum(1 for t in bars.times if t > moment)


def _parsed(stamp: str) -> datetime | None:
    if not stamp:
        return None
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None


async def main(max_bars: int, minutes: int, execute: bool) -> None:
    async with httpx.AsyncClient() as client:
        broker = Alpaca(settings.alpaca_key_id, settings.alpaca_secret_key, client)
        positions = await broker._get("/v2/positions")
        orders = await broker._get("/v2/orders", status="open", nested="true")

        # A market close sent while the market is shut queues for the next
        # open and fills wherever it gaps to. The scheduled run fires every
        # half hour of the session, so waiting costs at most 30 minutes.
        if execute and not await broker.market_open():
            print(f"{YELLOW}market is closed — reporting only{RESET}")
            execute = False

        if not execute:
            print(f"{YELLOW}report only — nothing will be closed or cancelled{RESET}")
        print(f"{DIM}limit: {max_bars} bars of {minutes}m "
              f"(~{max_bars * minutes / 60 / 6.5:.1f} sessions){RESET}\n")

        stale_positions: list[str] = []
        stale_orders: list[dict] = []

        for position in positions:
            symbol = position["symbol"]
            recent = await broker._get("/v2/orders", status="all",
                                       symbols=symbol, limit=20, direction="desc")
            filled_at = next(
                (_parsed(o["filled_at"]) for o in recent if o.get("filled_at")), None
            )
            if filled_at is None:
                print(f"  {DIM}{symbol:<6} no fill timestamp — skipped{RESET}")
                continue
            bars = await fetch_intraday(symbol, client, minutes=minutes,
                                        range_=max_range(minutes))
            age = bars_since(bars, filled_at)
            stale = age >= max_bars
            print(f"  {BOLD}{symbol:<6}{RESET} held {age:>3} bars "
                  f"{DIM}(filled {filled_at:%Y-%m-%d %H:%M}){RESET}"
                  f"{f'   {YELLOW}STALE{RESET}' if stale else ''}")
            if stale:
                stale_positions.append(symbol)

        for order in orders:
            submitted = _parsed(order.get("submitted_at", ""))
            if submitted is None or order.get("filled_at"):
                continue
            bars = await fetch_intraday(order["symbol"], client, minutes=minutes,
                                        range_=max_range(minutes))
            age = bars_since(bars, submitted)
            if age >= max_bars:
                stale_orders.append(order)
                print(f"  {BOLD}{order['symbol']:<6}{RESET} order resting "
                      f"{YELLOW}{age} bars{RESET} — setup has aged out")

        if not stale_positions and not stale_orders:
            print(f"\n{GREEN}nothing has aged out{RESET}")
            return

        print(f"\n{BOLD}{len(stale_positions)} position(s), "
              f"{len(stale_orders)} order(s) past the limit{RESET}")
        if not execute:
            print(f"{DIM}re-run with --execute to close them{RESET}")
            return

        for symbol in stale_positions:
            try:
                await broker.close_position_and_legs(symbol)
                print(f"  {GREEN}closed{RESET} {symbol}")
            except BrokerError as exc:
                print(f"  {RED}could not close {symbol}: {exc}{RESET}")
        for order in stale_orders:
            try:
                await broker.cancel_order(order["id"])
                print(f"  {GREEN}cancelled{RESET} {order['symbol']}")
            except BrokerError as exc:
                print(f"  {RED}could not cancel {order['symbol']}: {exc}{RESET}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    policy = TradingPolicy()
    p.add_argument("--max-bars", type=int, default=policy.max_hold_bars)
    p.add_argument("--minutes", type=int, default=policy.interval_minutes)
    p.add_argument("--execute", action="store_true",
                   help="actually close and cancel; default reports only")
    a = p.parse_args()
    try:
        asyncio.run(main(a.max_bars, a.minutes, a.execute))
    except BrokerError as exc:
        raise SystemExit(f"broker unavailable: {exc}")
