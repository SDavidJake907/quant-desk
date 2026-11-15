"""Sell the operator's listed positions at the first scan after the open, once.

    python scripts/sell_at_open.py            # sells only while the market is open

data/monitor/sell_at_open.json is {symbol: book strategy}. While the market is open, each
listed position still held has its working orders cancelled and is closed at market, its
book's exit is logged (so the 30-day re-buy rule applies), and it leaves the list; the
file is removed when empty. With the market shut it reports and does nothing.

Why it exists (2026-11-01): the operator chose to sell five undersized positions at
Monday's open. Cancelling their stops on a Sunday only marks the cancels pending, so a
sale could not be queued until the open. Runs first in scheduled_scan.bat, before
protect.py re-arms a stop under anything naked.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from scanner.config import settings  # noqa: E402
from scanner.execution import bookorders, preclose  # noqa: E402
from scanner.execution.alpaca import Alpaca, BrokerError  # noqa: E402
from scanner.execution.flatten_run import close_when_released  # noqa: E402
from scanner.execution.policy import TradingPolicy  # noqa: E402
from scanner.execution.safety import kill_switch_active  # noqa: E402

LIST = Path("data/monitor/sell_at_open.json")
WHY = "operator 2026-11-01: sold at the open to restart this slot at full size"


async def main() -> int:
    try:
        wanted: dict[str, str] = json.loads(LIST.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    if not wanted:
        LIST.unlink(missing_ok=True)
        return 0
    policy = TradingPolicy()
    async with httpx.AsyncClient() as client:
        broker = Alpaca(settings.alpaca_key_id, settings.alpaca_secret_key, client)
        if not (await broker._get("/v2/clock")).get("is_open"):
            print(f"sell at open: market shut, waiting -- {', '.join(wanted)}")
            return 0
        held = {p["symbol"] for p in await broker._get("/v2/positions")}
        gone = [s for s in wanted if s not in held]
        todo = [s for s in wanted if s in held]
        orders = await broker._get("/v2/orders", status="all", limit=500, nested="true")
        for oid in preclose.working_orders_for(orders, todo):
            try:
                await broker.cancel_order(oid)
            except BrokerError as exc:
                print(f"  cancel {oid}: {exc}")
        failed = await close_when_released(broker, tuple(todo)) if todo else {}
        for s in todo:
            ok = s not in failed
            bookorders.log(f"{wanted[s]}_exit", s, WHY + ("; closed at market" if ok
                                                          else f"; CLOSE FAILED: {failed[s]}"), policy)
            print(f"sell at open: {s} {'closed at market' if ok else 'FAILED ' + failed[s]}")
    left = {s: b for s, b in wanted.items() if s in failed}
    for s in gone:
        print(f"sell at open: {s} no longer held")
    if left:
        LIST.write_text(json.dumps(left, indent=1), encoding="utf-8")
    else:
        LIST.unlink(missing_ok=True)
    if kill_switch_active(settings.kill_switch_path):
        print("  (kill switch is on: it blocks buys, not this sale)")
    # A failed close stays on the list and is tried again next scan. Non-zero so the
    # task goes red: protect.py runs next and would otherwise hide the miss.
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
