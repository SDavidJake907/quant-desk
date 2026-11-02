"""Pre-trade smoke test: verify the environment before any scan runs.

    python scripts/smoke_test.py              # paper mode: warn on problems
    python scripts/smoke_test.py --live       # live mode: hard-block on problems

Checks:
  1. Ollama is reachable and triage + embed models are installed
  2. Alpaca keys are configured and point to the PAPER endpoint
  3. Account is reachable and ACTIVE
  4. Session start equity is captured (daily-loss guard baseline)
  5. Kill switch is not active

Exit non-zero on any failure. Safe to run before every scheduled scan; it
places no order and changes no position.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone

import httpx

from scanner.config import settings
from scanner.execution.preflight import run, banner
from scanner.execution.safety import kill_switch_active, load_session_equity


def _fail(message: str) -> int:
    print(message)
    return 1


async def main(live: bool) -> int:
    problems: list[str] = []

    # Kill switch must not be active for a smoke test that could lead to trading.
    if kill_switch_active(settings.kill_switch_path):
        problems.append(
            f"kill switch is active ({settings.kill_switch_path}) — remove it first"
        )

    async with httpx.AsyncClient() as client:
        report = await run(client, live=live)

    print(banner(report))

    if not report.model.complete:
        problems.append(
            f"{report.model.triage_model} / {report.model.embed_model}"
        )
    if report.broker is None or not report.broker.reachable:
        problems.append("broker unreachable")
    if report.broker is not None and not report.broker.paper:
        problems.append("broker endpoint is LIVE, not paper")
    if report.broker is not None and report.broker.account_status != "ACTIVE":
        problems.append(f"account status is {report.broker.account_status}")
    session_equity = settings.session_start_equity or load_session_equity()
    if not session_equity or session_equity <= 0:
        problems.append(
            "SCANNER_SESSION_START_EQUITY is 0 and no session equity captured — "
            "the daily-loss guard cannot detect a drawdown that happened before "
            "this process started. Run scripts/capture_session_equity.py or set "
            "SCANNER_SESSION_START_EQUITY in .env"
        )

    if problems:
        print(f"\nSMOKE TEST FAILED ({len(problems)} problem(s)):")
        for p in problems:
            print(f"  - {p}")
        print("\nDo not trade until these are resolved.")
        return 1

    print("\nSMOKE TEST OK — environment ready for scanning.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live", action="store_true",
        help="hard-block on model environment or broker problems (paper mode warns only)",
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.live)))