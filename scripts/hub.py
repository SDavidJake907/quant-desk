"""One page: what this system did, and what needs a human.

    python scripts/hub.py                 # the briefing
    python scripts/hub.py --narrate       # + Gemma's read of it
    python scripts/hub.py --days 5        # widen the window

READ-ONLY, and outside the frozen trading path. It reads logs and asks the
broker for state. It places no order and changes no policy.

WHY THIS EXISTS
The state of this system is spread across a 300KB log, a JSONL audit trail, a
broker account, four scheduled tasks, a policy object and two markdown files.
Answering "is it working?" has repeatedly meant reading funnel lines by hand —
and twice this week it meant writing throwaway scripts, because a scan running
on stale bars and a market with nothing in it print the same line.

`report.py` assembles the EVIDENCE — every measurement the project has made.
`monitor.py` watches the account. This is the third thing: the OPERATIONAL
picture, today, in the order a human needs it.

THE MODEL NARRATES, IT DOES NOT COMPUTE
Every number here is arithmetic over logs, done in Python and printed whether
Ollama is up or not. `--narrate` adds a short prose read on top. The rule is
the same one the news layer runs under and for the same reason: the model's
output schema has no numeric fields, so a figure it invents cannot reach the
page. If the model is unreachable the briefing is unchanged — it simply has
no commentary.

IT DEGRADES RATHER THAN FAILS
A missing log, an unreachable broker and a dead model each cost their own
section and nothing else. A status tool that cannot run when something is
broken is a status tool that is useless exactly when it is needed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.execution import ownership, plan_run
from scanner.execution import satellite as sat
from scanner.ops import runlog
from scanner.config import settings
from scanner.execution import continuity
from scanner.execution.alpaca import Alpaca, BrokerError
from scanner.execution.policy import TradingPolicy
from scanner.execution.provenance import build_id

DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"
GREEN, RED, YELLOW, CYAN = "\033[32m", "\033[31m", "\033[33m", "\033[36m"

POLICY = TradingPolicy()
SCHEDULED_LOG = Path("logs/scheduled.log")
ANSI = re.compile(r"\x1b\[[0-9;]*m")

#: Pulled out of the scan line, which is the only place these exist.
FUNNEL = re.compile(
    r"funnel: (\d+) symbols\s+setups (\d+)\s+stale (\d+).*?candidates (\d+)")
BARS_LINE = re.compile(r"bars: newest (\d{2}:\d{2}) \w+.*?\((-?[\d.]+) bars\)")
RUN_HEADER = re.compile(
    r"^===== (?:([A-Z]+) )?\w{3} (\d{2}/\d{2}/\d{4})\s+([\d:]+)")


def _model_reachable() -> bool:
    """Is Ollama answering right now? A past outage matters less than a
    present one, and the difference decides whether this needs a human."""
    try:
        r = httpx.get(f"{settings.ollama_host}/api/tags", timeout=3.0)
        return r.status_code == 200
    except Exception:                                   # noqa: BLE001
        return False


def _audit(path: str) -> list[dict]:
    rows = []
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except OSError:
        return []
    return rows


def _scheduled_tail(days: int) -> list[str]:
    """Every scheduled run in the last `days`, merged from the per-task logs
    (scanner.ops.runlog) in start-time order."""
    cutoff = date.today() - timedelta(days=days)
    keep, taking = [], False
    for line in runlog.read_logs(SCHEDULED_LOG.parent):
        when = runlog.header_time(line)
        if when is not None:
            taking = when.date() >= cutoff
        if taking:
            keep.append(line)
    return keep


def window_section() -> list[str]:
    out = [f"{BOLD}WINDOW{RESET}"]
    out.append(f"  policy      {POLICY.fingerprint}")
    out.append(f"  build       {build_id()}")
    out.append(f"  deployment  {POLICY.weekday_deployment_pct}  "
               f"max {POLICY.max_positions} positions")
    try:
        freeze = Path("FREEZE.md").read_text(encoding="utf-8")
        for key in ("window ", "started ", "ends "):
            for line in freeze.splitlines()[:16]:
                if line.strip().startswith(key.strip()):
                    out.append(f"  {' '.join(line.split())}")
                    break
    except OSError:
        out.append(f"  {DIM}FREEZE.md unreadable{RESET}")
    return out


def running_section(days: int, market_open: bool | None) -> tuple[list[str], list[str]]:
    out, attention = [f"{BOLD}RUNNING{RESET}"], []
    lines = _scheduled_tail(days)
    headers = [RUN_HEADER.match(l) for l in lines]
    runs = [h for h in headers if h]
    if not runs:
        out.append(f"  {YELLOW}no scheduled runs in the last {days} day(s)"
                   f"{RESET}")
        attention.append("no scheduled runs found in the log")
    else:
        last = runs[-1]
        out.append(f"  runs (last {days}d)   {len(runs)}")
        out.append(f"  most recent      {last.group(2)} {last.group(3)}"
                   f"{'  ' + last.group(1) if last.group(1) else ''}")

    gap = continuity.check(settings.audit_log_path)
    if gap is None:
        out.append(f"  coverage         {DIM}no history yet{RESET}")
    elif gap.is_alerting:
        out.append(f"  coverage         {RED}{gap.describe()}{RESET}")
        attention.append(f"GAP: {gap.describe()}")
    else:
        out.append(f"  coverage         {GREEN}no gap{RESET} "
                   f"{DIM}({gap.missed_sessions} sessions missed){RESET}")

    freshness = [BARS_LINE.search(l) for l in lines]
    seen = [m for m in freshness if m]
    if seen:
        newest, age = seen[-1].group(1), float(seen[-1].group(2))
        # Stale bars only matter while the market is OPEN. Outside session
        # hours the newest bar is legitimately hours old, and a status tool
        # that cries wolf every evening is one nobody reads by Wednesday.
        stale = age > 1.5 and market_open is True
        tint = YELLOW if stale else (GREEN if age <= 1.5 else DIM)
        suffix = "" if market_open is not False else "  (market shut — expected)"
        out.append(f"  bars last scan   {tint}newest {newest}, "
                   f"{age:.1f} bars old{RESET}{DIM}{suffix}{RESET}")
        if stale:
            attention.append(f"bars were {age:.1f} bars old with the market "
                             f"OPEN — check the cache")
    else:
        out.append(f"  bars last scan   {DIM}not reported "
                   f"(build predates the freshness line){RESET}")
    return out, attention


def today_section(rows: list[dict], days: int) -> tuple[list[str], list[str]]:
    out, attention = [f"{BOLD}ACTIVITY (last {days}d){RESET}"], []
    lines = _scheduled_tail(days)
    funnels = [FUNNEL.search(l) for l in lines]
    hits = [m for m in funnels if m]
    if hits:
        setups = sum(int(m.group(2)) for m in hits)
        cands = sum(int(m.group(4)) for m in hits)
        out.append(f"  scans            {len(hits)}")
        out.append(f"  setups seen      {setups} {DIM}(re-counted each scan)"
                   f"{RESET}")
        tint = GREEN if cands else DIM
        out.append(f"  candidates       {tint}{cands}{RESET}")
    else:
        out.append(f"  {DIM}no funnel lines in range{RESET}")

    cutoff = (date.today() - timedelta(days=days)).isoformat()
    recent = [r for r in rows if str(r.get("at", ""))[:10] >= cutoff]
    events = Counter(r.get("event") for r in recent)
    if events:
        out.append("  audit events     " + ", ".join(
            f"{k} {v}" for k, v in events.most_common()))
    slates = sum(1 for r in recent if r.get("event") == "candidate_slate")
    out.append(f"  slates recorded  {slates} "
               f"{DIM}(needed to score the model's ranking){RESET}")
    degraded = [r for r in recent if r.get("event") == "news_gate_degraded"]
    if degraded:
        last = max(str(r.get("at", "")) for r in degraded)[:10]
        still = _model_reachable()
        if still:
            out.append(f"  news gate        {DIM}ran degraded "
                       f"{len(degraded)}x, last {last}; model reachable now"
                       f"{RESET}")
        else:
            attention.append(f"news gate degraded {len(degraded)}x (last "
                             f"{last}) and the model is STILL unreachable — "
                             f"every name passes unrefused")
    return out, attention


async def account_section() -> tuple[list[str], list[str], bool | None]:
    out, attention = [f"{BOLD}ACCOUNT{RESET}"], []
    try:
        async with httpx.AsyncClient() as client:
            broker = Alpaca(settings.alpaca_key_id, settings.alpaca_secret_key,
                            client)
            account = await broker.account()
            positions = await broker._get("/v2/positions")
            orders = await broker._get("/v2/orders", status="open", limit=200)
            naked = await broker.unprotected_positions()
            # Momentum holds carry no stop by design (WORKLIST §40): listing
            # them here daily would bury a real naked position. A rules-book
            # position opened on or after the 10-7 book also has no stop.
            history = await broker._get("/v2/orders", status="all", limit=500, nested="true")
            owner_map = ownership.owners(history)
            entered = plan_run.entry_dates(history, (ownership.GEMMA,))
            naked = tuple(p for p in naked
                          if ownership.owner(p.symbol, owner_map) not in POLICY.stopless_strategies
                          and not sat.rules_book_has_no_stop(
                              ownership.owner(p.symbol, owner_map), entered.get(p.symbol)))
            market_open = await broker.market_open()
    except BrokerError as exc:
        out.append(f"  {YELLOW}broker unreachable: {exc}{RESET}")
        attention.append("broker unreachable — account state unknown")
        return out, attention, None

    out.append(f"  equity           {account.equity:,.2f}")
    out.append(f"  positions        {len(positions)}   "
               f"working orders {len(orders)}")
    for p in positions:
        out.append(f"    {p.get('symbol'):<6} {p.get('qty'):>6} @ "
                   f"{float(p.get('avg_entry_price', 0)):.2f}  "
                   f"P/L {float(p.get('unrealized_pl', 0)):+,.2f}")
    if naked:
        out.append(f"  {RED}UNPROTECTED      {len(naked)}{RESET}")
        attention.append(f"{len(naked)} position(s) with NO LIVE STOP — "
                         f"run scripts/protect.py --submit")
    elif positions:
        out.append(f"  protection       {GREEN}every position has a stop"
                   f"{RESET}")
    out.append(f"  market           {'open' if market_open else 'closed'}")
    return out, attention, market_open


def outcomes_section(rows: list[dict]) -> list[str]:
    import statistics
    outs = [r for r in rows if r.get("event") == "trade_outcome"]
    out = [f"{BOLD}RESULTS{RESET}"]
    if not outs:
        out.append(f"  {DIM}no completed trades yet{RESET}")
        return out
    here = [r for r in outs if r.get("policy") == POLICY.fingerprint]
    values = [r["outcome"]["realized_r"] for r in outs]
    mean = statistics.fmean(values)
    out.append(f"  completed        {len(outs)} all-time, "
               f"{len(here)} under this policy")
    if len(values) > 1:
        se = statistics.stdev(values) / len(values) ** 0.5
        lo, hi = mean - 1.96 * se, mean + 1.96 * se
        crosses = lo < 0 < hi
        out.append(f"  expectancy       {mean:+.3f}R  "
                   f"{DIM}95% CI {lo:+.3f} to {hi:+.3f}{RESET}")
        if crosses:
            out.append(f"  {YELLOW}the interval contains zero — this sample "
                       f"cannot tell profit from noise{RESET}")
    return out


async def narrate(sections: list[str], attention: list[str]) -> str:
    """Gemma's read. Advisory, and structurally unable to emit a number."""
    from scanner.llm.ollama import LLMError, chat_json

    facts = ANSI.sub("", "\n".join(sections))
    schema = {
        "type": "object",
        "properties": {
            "state": {"type": "string",
                      "enum": ["healthy", "degraded", "needs_attention"]},
            "summary": {"type": "string"},
            "watch": {"type": "string"},
        },
        "required": ["state", "summary", "watch"],
    }
    prompt = (
        "You are reading the operational status of an automated paper-trading "
        "system. Summarise it for its operator.\n\n"
        f"{facts}\n\n"
        f"Flagged for attention: {'; '.join(attention) or 'nothing'}\n\n"
        "state: healthy if it is running and nothing needs a human; degraded "
        "if a component is down but trading is unaffected; needs_attention if "
        "a human must act.\n"
        "summary: two sentences on what this system did and whether that is "
        "normal. Scarcity of trades is EXPECTED and is not a fault.\n"
        "watch: the single thing most worth watching next, or 'nothing'.\n\n"
        "Report no numbers, prices, percentages or counts of any kind — the "
        "figures are already on the page above. Describe only."
    )
    async with httpx.AsyncClient() as client:
        try:
            raw = await chat_json(prompt, schema, client)
        except (LLMError, ValueError) as exc:
            return f"{DIM}no commentary — model unreachable ({exc}){RESET}"
    state = str(raw.get("state", "")).lower()
    tint = {"healthy": GREEN, "degraded": YELLOW}.get(state, RED)
    return (f"{BOLD}GEMMA'S READ{RESET} {DIM}(advisory){RESET}\n"
            f"  state   {tint}{state}{RESET}\n"
            f"  {raw.get('summary', '')}\n"
            f"  {CYAN}watch:{RESET} {raw.get('watch', '')}")


async def main(days: int, want_narration: bool) -> int:
    rows = _audit(settings.audit_log_path)
    attention: list[str] = []
    blocks: list[list[str]] = []

    blocks.append(window_section())
    # Account first: it answers whether the market is open, which decides
    # whether stale bars are a fault or just the evening.
    acct, note, market_open = await account_section()
    running, note2 = running_section(days, market_open)
    blocks.append(running)
    blocks.append(acct)
    attention += note2 + note
    activity, note = today_section(rows, days)
    blocks.append(activity)
    attention += note
    blocks.append(outcomes_section(rows))

    flat: list[str] = []
    for block in blocks:
        flat += block + [""]
    print("\n".join(flat).rstrip())

    print(f"\n{BOLD}NEEDS A HUMAN{RESET}")
    if attention:
        for item in attention:
            print(f"  {RED}• {item}{RESET}")
    else:
        print(f"  {GREEN}nothing{RESET}")

    if want_narration:
        print()
        print(await narrate(flat, attention))
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--days", type=int, default=1)
    p.add_argument("--narrate", action="store_true",
                   help="add Gemma's prose read of the page")
    a = p.parse_args()
    raise SystemExit(asyncio.run(main(a.days, a.narrate)))
