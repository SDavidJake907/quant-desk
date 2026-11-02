"""Have Gemma review its own calls on trades that have closed.

    python scripts/hindsight.py              # review closed trades not yet reviewed
    python scripts/hindsight.py --show       # print the prompts, call nothing
    python scripts/hindsight.py --redo       # review every closed trade again

For each closed trade this system placed, it gathers what Gemma said at entry
(conviction, driver, concern, the quote and headlines it used), today's
headlines for the symbol, and how the trade ended. Gemma writes what it
expected, what seems to have happened, and what it missed. The review is
appended to the audit log as an `llm_hindsight` record and shown on the desk.

Read-only toward the broker. Needs Ollama running.

THESE REVIEWS ARE NOT EVIDENCE. A model that knows the ending will always
find a story that fits it. Whether Gemma's calls are worth anything is decided
by the desk's scoreboard — realized R grouped by the conviction it gave before
the outcome — not by anything written here.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime

import httpx

from scanner.config import settings
from scanner.execution.alpaca import Alpaca, BrokerError
from scanner.execution.audit import append_event
from scanner.execution.reconcile import CLOSED_OTHER, CLOSED_STOP, CLOSED_TARGET, reconcile
from scanner.llm import hindsight
from scanner.models import AuditEvent
from scanner.sources import yahoo_rss
from scanner.web.snapshot import (
    hindsight_by_order, intents_by_order, read_events, resolve_exits, trade_rows,
)

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"
GREEN, RED, YELLOW, VIOLET = "\033[32m", "\033[31m", "\033[33m", "\033[35m"
CLOSED = (CLOSED_STOP, CLOSED_TARGET, CLOSED_OTHER)


async def main(show: bool, redo: bool, limit: int) -> None:
    events = read_events(settings.audit_log_path)
    reviewed = set() if redo else set(hindsight_by_order(events))
    async with httpx.AsyncClient() as client:
        broker = Alpaca(settings.alpaca_key_id, settings.alpaca_secret_key, client)
        orders = await broker._get("/v2/orders", status="all", nested="true",
                                   limit=500, direction="desc")
        held = {p["symbol"] for p in await broker._get("/v2/positions")}
        intents = intents_by_order(events)
        trades = resolve_exits(reconcile(intents, orders), orders, held)
        rows = [r for r in trade_rows(trades, intents, orders, {}, events)
                if r["tracked"] and r["state"] in CLOSED
                and r["client_order_id"] not in reviewed][:limit]
        if not rows:
            print(f"{DIM}no closed trades waiting for a review{RESET}")
            return
        print(f"{DIM}{len(rows)} closed trade(s) to review{RESET}\n")

        for row in rows:
            opinion = row["entry_context"].get("opinion", {})
            entry = {"evidence": opinion.get("evidence", ""),
                     "headlines": opinion.get("headlines", [])}
            try:
                later = [n.title for n in await yahoo_rss.fetch(row["symbol"], client)]
            except Exception:
                later = []
            verdict = row["verdict"]
            colour = GREEN if verdict == "right" else (RED if verdict == "wrong" else DIM)
            print(f"{BOLD}{row['symbol']:<6}{RESET} {row['state']:<7} "
                  f"{(row['r'] or 0):+.2f}R   Gemma said "
                  f"{VIOLET}{row['conviction'] or 'nothing'}{RESET}   "
                  f"call {colour}{verdict}{RESET}")
            if show:
                print(f"{DIM}{hindsight.build_prompt(row, entry, later)}{RESET}\n")
                continue
            result = await hindsight.review(row, entry, later, client)
            if result is None:
                print(f"  {YELLOW}model unavailable — skipped; run again later{RESET}")
                continue
            append_event(settings.audit_log_path, AuditEvent(
                event="llm_hindsight", at=datetime.now().astimezone(),
                symbol=row["symbol"], detail={
                    "client_order_id": row["client_order_id"],
                    "model": settings.triage_model, "verdict": verdict,
                    "r": row["r"], "state": row["state"], **result,
                }))
            print(f"  expected   {result['expected']}")
            print(f"  happened   {result['what_happened']}")
            print(f"  missed     {result['missed']}")
            print(f"  {DIM}lesson     {result['lesson']}{RESET}\n")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--show", action="store_true", help="print prompts, call nothing")
    p.add_argument("--redo", action="store_true", help="review already-reviewed trades too")
    p.add_argument("--limit", type=int, default=25)
    a = p.parse_args()
    try:
        asyncio.run(main(a.show, a.redo, a.limit))
    except BrokerError as exc:
        raise SystemExit(f"broker unavailable: {exc}")
