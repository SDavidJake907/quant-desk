"""Label news on the stocks already held. Report only. Places no order.

    python scripts/held_news.py

Writes logs/positions/held_news.json. The position card reads that file on its
next refresh. Peak watch does not call this, so a slow model cannot delay an exit.
Gemma labels the headline. The lines name a lawsuit, an earnings date, or a
takeover. A miss does not sell.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.config import settings
from scanner.execution.alpaca import Alpaca
from scanner.execution.news_gate import NewsFlag, check
from scanner.ops import position_card as card

#: One name. The task limit is 20 minutes, and the book is about ten names.
PER_NAME_S = 60


async def label_one(symbol: str, client: httpx.AsyncClient) -> list[str]:
    try:
        flag = await asyncio.wait_for(check(symbol, client), timeout=PER_NAME_S)
    except Exception as exc:  # noqa: BLE001 -- a missed label is written, and nothing is sold
        print(f"  {symbol}: not labeled ({type(exc).__name__})")
        flag = NewsFlag(symbol=symbol, checked=False)
    lines = card.label_lines(flag)
    print(f"  {symbol}: {lines[0]}")
    return lines


async def run() -> int:
    async with httpx.AsyncClient(timeout=30) as client:
        broker = Alpaca(settings.alpaca_key_id, settings.alpaca_secret_key, client)
        positions = await broker._get("/v2/positions") or []
        symbols = [str(p.get("symbol") or "") for p in positions if p.get("symbol")]
        labels = {symbol: await label_one(symbol, client) for symbol in symbols}
    written = datetime.now().astimezone().strftime("%a %Y-%m-%d %H:%M %Z")
    card.save_labels(card.LABELS, written, labels)
    print(f"labeled {len(labels)} position(s) at {written}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
