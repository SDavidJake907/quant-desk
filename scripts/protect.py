"""Re-arm open positions that have lost their protective stop.

    python scripts/protect.py              # report only, send nothing
    python scripts/protect.py --submit     # send the OCO orders

WHY THIS SCRIPT EXISTS
On 2026-08-07 every bracket was sent with `time_in_force="day"`. That applies
to the protective legs as well as the entry, so at 20:01 UTC — one minute
after the close — the broker cancelled all three stops and all three targets.
Nothing errored, because the orders had done exactly what `day` means.

Three positions worth 58,701 dollars then sat with no floor under them
through the weekend and all of Monday's session. A power outage had stopped
the scheduler, so nothing was even running to notice.

`bracket_payload` now sends `gtc`, which stops this happening to any NEW
order. It cannot help a position that is already naked — a bracket attaches
protection to an entry, and these entries filled days ago. That is what this
script is for: it reads the original stop and target back out of the audit
log and re-arms each position with a standalone OCO.

THE LEVELS COME FROM THE AUDIT LOG, NOT FROM TODAY'S PRICE
Re-deriving a stop from the current chart would silently move the exit, and
every R-multiple this project has recorded assumes the stop the setup was
sized against. `size_position` chose the share count FROM that stop distance,
so a different stop means the position is no longer risking what it was
built to risk. The log is the only record of the original number, so the log
is what this reads.

A BREACHED STOP IS CLOSED, NOT LEFT FOR A HUMAN (changed 2026-10-06)
It still never re-arms a stop the price has already traded through -- that
would sit above the market and fill instantly at a worse price. But it no
longer leaves such a position naked "for a human": on 2026-10-05 INTC sat
unprotected through the open that way. A breached stop or a reached target is
now sold at market (queued for the open if the market is shut -- what a stop
does on a gap), only for the naked shares, never twice, and an alert file is
written to the Desktop and logs/ for every action. See execution/protection.py.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
from pathlib import Path

import httpx

from scanner.config import settings
from scanner.execution.alpaca import Alpaca, BrokerError, protective_payload
from scanner.execution.audit import append_event, submitted_levels
from scanner.execution import ownership, protection
from scanner.execution.policy import TradingPolicy
from scanner.models import AuditEvent

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"
GREEN, RED, YELLOW, CYAN = "\033[32m", "\033[31m", "\033[33m", "\033[36m"




def _note_gone() -> bool:
    """True when every old protection note is gone. A locked file stays."""
    stuck = protection.clear_alert((Path.home() / "Desktop", Path("logs")))
    for line in stuck:
        print(f"{YELLOW}could not remove protection note: {line}{RESET}")
    return not stuck


def write_alert(rows) -> None:
    """Leave a note where it will be seen: the Desktop, and logs/."""
    from pathlib import Path
    text = protection.alert_text(
        rows, when=datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z"))
    for folder in (Path.home() / "Desktop", Path("logs")):
        try:
            (folder / "PROTECTION_ALERT.txt").write_text(text, encoding="utf-8")
        except OSError:
            pass


async def main(submit: bool) -> None:
    async with httpx.AsyncClient() as client:
        broker = Alpaca(settings.alpaca_key_id, settings.alpaca_secret_key, client)

        naked = await broker.unprotected_positions()
        # Momentum holds have no stop BY DESIGN (WORKLIST §37): its backtest
        # had none, so arming one would trade a strategy nobody tested.
        stopless = TradingPolicy().stopless_strategies
        if naked and stopless:
            history = await broker._get("/v2/orders", status="all", limit=500, nested="true")
            owner_map = ownership.owners(history)
            skipped = [p.symbol for p in naked
                       if ownership.owner(p.symbol, owner_map) in stopless]
            if skipped:
                print(f"{DIM}no stop by design ({'/'.join(stopless)}): {', '.join(skipped)}{RESET}")
            naked = tuple(p for p in naked if p.symbol not in skipped)
        if not naked:
            if _note_gone():
                print(f"{GREEN}every open position has a live stop.{RESET}")
            return

        levels = submitted_levels(settings.audit_log_path)
        quotes = {
            p["symbol"]: float(p["current_price"])
            for p in await broker._get("/v2/positions")
        }

        print(f"\n{BOLD}{len(naked)} unprotected position(s){RESET}\n")
        closing = protection.closing_shares(
            await broker._get("/v2/orders", status="open", nested="true"))
        armable: list[tuple[str, int, float, float]] = []
        to_close: list[tuple[str, int, str]] = []
        alerts: list[tuple[str, int, str, str]] = []
        leaving: set[str] = set()
        for position in naked:
            symbol, qty = position.symbol, position.qty
            stop, target = levels.get(symbol, (None, None))
            last = quotes.get(symbol, 0.0)
            decision = protection.decide(last=last, stop=stop, target=target)
            exposed = protection.uncovered(qty, closing.get(symbol, 0))
            if exposed <= 0:
                print(f"  {YELLOW}{symbol:<6} a market sell is already working{RESET}")
                leaving.add(symbol)
                continue
            if decision == "unknown":
                print(f"  {RED}{symbol:<6} no submission in the audit log — "
                      f"cannot recover its stop; close or set one by hand{RESET}")
                alerts.append((symbol, exposed, decision, "no stop on record -- act by hand"))
                continue
            # A stop already below the market cannot be re-armed honestly, so
            # the exit it called for is taken now instead of left for a human.
            if decision in ("close_stop", "close_target"):
                edge, level = (("stop", stop) if decision == "close_stop"
                               else ("target", target))
                print(f"  {RED}{symbol:<6} last {last:,.2f} is past its {edge} "
                      f"{level:,.2f} — exit called: sell {exposed} at market{RESET}")
                to_close.append((symbol, exposed, decision))
                continue

            room = (last - stop) / last * 100 if last else 0.0
            print(f"  {BOLD}{symbol:<6}{RESET} {exposed:>6} sh   "
                  f"stop {RED}{stop:>9,.2f}{RESET}   "
                  f"target {GREEN}{target:>9,.2f}{RESET}   "
                  f"{DIM}last {last:,.2f}, {room:.2f}% above stop{RESET}")
            armable.append((symbol, exposed, stop, target))

        if not submit:
            print(f"\n{YELLOW}report only — nothing sent. "
                  f"Re-run with --submit to arm and close these.{RESET}")
            return

        for symbol, qty, decision in to_close:
            now = datetime.now().astimezone()
            try:
                order = await broker.submit(protection.exit_payload(symbol, qty, now))
                append_event(settings.audit_log_path, AuditEvent(
                    event="protection_closed", at=now, symbol=symbol,
                    order_id=str(order.get("id", "")), request_id=broker.last_request_id,
                    stage="trade", reason=f"{decision}: market sell {qty} sh, no live stop",
                ))
                alerts.append((symbol, qty, decision, f"market sell sent, id {order.get('id', '?')}"))
                leaving.add(symbol)
                print(f"  {YELLOW}sold{RESET}  {symbol:<6} {qty} sh at market "
                      f"{DIM}id {order.get('id', '?')}{RESET}")
            except BrokerError as exc:
                append_event(settings.audit_log_path, AuditEvent(
                    event="protection_failed", at=now, symbol=symbol,
                    request_id=broker.last_request_id, reason=f"{decision}: {exc}",
                    stage="trade",
                ))
                alerts.append((symbol, qty, decision, f"SELL FAILED: {exc}"))
                print(f"  {RED}FAILED{RESET} {symbol:<6} {exc}")

        print()
        for symbol, qty, stop, target in armable:
            payload = protective_payload(symbol, qty, stop=stop, target=target)
            try:
                order = await broker.submit(payload)
                append_event(settings.audit_log_path, AuditEvent(
                    event="protection_restored",
                    at=datetime.now().astimezone(),
                    symbol=symbol, order_id=str(order.get("id", "")),
                    request_id=broker.last_request_id, stage="trade",
                    reason=f"re-armed {qty} sh: stop {stop:.2f} target {target:.2f}",
                ))
                print(f"  {GREEN}armed{RESET} {symbol:<6} "
                      f"{DIM}id {order.get('id', '?')}{RESET}")
            except BrokerError as exc:
                append_event(settings.audit_log_path, AuditEvent(
                    event="protection_failed",
                    at=datetime.now().astimezone(),
                    symbol=symbol, request_id=broker.last_request_id,
                    reason=str(exc), stage="trade",
                ))
                print(f"  {RED}FAILED{RESET} {symbol:<6} {exc}")

        if alerts:
            write_alert(alerts)

        remaining = await broker.unprotected_positions()
        still = [p for p in remaining if p.symbol not in leaving]
        if still:
            print(f"\n{RED}{BOLD}still unprotected: "
                  f"{', '.join(p.symbol for p in still)}{RESET}")
        elif alerts:
            # The note just written is the record of this pass. A later clean
            # pass removes it. Deleting it here hid the 2026-10-05 style exit.
            print(f"\n{YELLOW}protection note written; it stays until the book is covered.{RESET}")
        elif _note_gone():
            print(f"\n{GREEN}{BOLD}every open position now has a live stop.{RESET}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--submit", action="store_true",
                   help="actually send the OCO orders (default: report only)")
    asyncio.run(main(p.parse_args().submit))
