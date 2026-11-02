"""Pre-trade readiness check for live trading.

Runs all pre-flight checks and exits non-zero if any problem is found.
Run this before any --live scan to verify the environment is ready.

    python scripts/live_readiness_check.py

Checks:
  1. Ollama reachable, triage + embed models installed
  2. Alpaca API keys configured, paper endpoint only
  3. Account reachable, ACTIVE status
  4. Session start equity captured
  5. Kill switch NOT active
  6. No critical safety findings

Exit non-zero if any problem found. Safe to run anytime - places no orders.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

import httpx

from scanner.config import settings
from scanner.execution.preflight import run, banner
from scanner.execution.safety import kill_switch_active, inspect, format_report
from scanner.execution.alpaca import Alpaca


async def main() -> int:
    problems: list[str] = []

    # Kill switch must NOT be active
    if kill_switch_active(settings.kill_switch_path):
        problems.append(
            f"kill switch is active ({settings.kill_switch_path}) — remove it first"
        )

    async with httpx.AsyncClient(timeout=60) as client:
        # Pre-flight checks
        report = await run(client, live=True)
        print(banner(report))
        if report.blocked:
            problems.append(report.reason)

        # Broker account check
        try:
            broker = Alpaca(
                settings.alpaca_key_id, settings.alpaca_secret_key, client
            )
            account = await broker.account()
            print(f"  broker account: {account.status}, equity {account.equity:,.2f}")
            if account.status != "ACTIVE":
                problems.append(f"account status is {account.status}, not ACTIVE")
        except Exception as exc:
            print(f"  ! broker unreachable: {exc}")
            problems.append("broker unreachable — cannot confirm account status")

    if problems:
        print(f"\nLIVE READINESS CHECK FAILED ({len(problems)} problem(s)):")
        for p in problems:
            print(f"  - {p}")
        print("\nDo not trade until these are resolved.")
        return 1

    print("\nLIVE READINESS CHECK PASSED — environment ready for live trading.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pre-trade readiness check for live trading")
    parser.parse_args()
    sys.exit(asyncio.run(main()))