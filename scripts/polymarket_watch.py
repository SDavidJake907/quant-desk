"""Read Polymarket odds for the open book. Places no bet.

    python scripts/polymarket_watch.py

Writes logs/positions/polymarket.md. A price on that page does not buy and
does not sell. Gamma is a public catalog. This script does not open a wallet.
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
from scanner.sources import polymarket as pm


def _save(text: str) -> None:
    from pathlib import Path
    path = Path("logs/positions/polymarket.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


async def _search(client: httpx.AsyncClient, query: str) -> tuple[tuple[pm.Market, ...], str | None]:
    try:
        return await pm.search(client, query), None
    except (httpx.HTTPError, ValueError) as exc:
        return (), type(exc).__name__


async def run() -> int:
    written = datetime.now().astimezone().strftime("%a %Y-%m-%d %H:%M %Z")
    by_symbol: dict[str, tuple[pm.Market, ...]] = {}
    macro: list[pm.Market] = []
    seen: set[str] = set()
    failed = 0
    tried = 0
    symbols: list[str] = []
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            broker = Alpaca(settings.alpaca_key_id, settings.alpaca_secret_key, client)
            positions = await broker._get("/v2/positions") or []
            symbols = [str(p.get("symbol") or "") for p in positions if p.get("symbol")]
            for symbol in symbols:
                tried += 1
                rows, why = await _search(client, symbol)
                if why:
                    failed += 1
                    print(f"  {symbol}: search failed ({why})")
                by_symbol[symbol] = pm.for_symbol(rows, symbol)
            for query in pm.MACRO:
                tried += 1
                rows, why = await _search(client, query)
                if why:
                    failed += 1
                    print(f"  {query}: search failed ({why})")
                    continue
                for market in rows[:1]:
                    if market.question in seen:
                        continue
                    seen.add(market.question)
                    macro.append(market)
    except Exception as exc:  # noqa: BLE001 -- the page says the feed died, and nothing is bet
        print(f"polymarket watch failed ({type(exc).__name__})")
        _save(pm.format_report(
            written=written, symbols=symbols, by_symbol=by_symbol, macro=macro,
            error=type(exc).__name__,
        ))
        return 1
    error = "search failed" if tried and failed == tried else None
    _save(pm.format_report(
        written=written, symbols=symbols, by_symbol=by_symbol, macro=macro, error=error,
    ))
    named = sum(1 for rows in by_symbol.values() if rows)
    print(f"polymarket page written for {len(symbols)} name(s), {named} with a question, {len(macro)} macro")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
