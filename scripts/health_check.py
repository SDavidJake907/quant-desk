"""The health check, 06:00 and 14:15 AK weekdays (advisor: scanner/ops/health.py).

READ-ONLY: it sends no order and changes nothing. Checks every position has a
live stop, every enabled task's last run, Ollama for orphan runners and this
morning's task logs for errors (the 9:45 rules pick and the scan). Writes
logs/health/YYYY-MM-DD.md; on a problem it pushes to the phone (ntfy, when
SCANNER_NTFY_TOPIC is set) and shows a Windows notification on this PC.

    .venv/Scripts/python.exe scripts/health_check.py
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.config import settings  # noqa: E402
from scanner.execution import ownership  # noqa: E402
from scanner.execution.alpaca import Alpaca  # noqa: E402
from scanner.execution.policy import TradingPolicy  # noqa: E402
from scanner.ops import deskcheck, notify  # noqa: E402
from scanner.ops import health as h  # noqa: E402

OUT = Path("logs/health")
#: Morning tasks whose logs are scanned for errors in today's runs.
#: Gemma's decision, review, Hindsight, ideas, leaders and week-ahead retired 2026-10-26.
MORNING_LOGS = ("scan", "rules_pick")

TASKS_PS = (
    "Get-ScheduledTask -TaskName 'stock-scanner*' | ForEach-Object { $i = $_ | Get-ScheduledTaskInfo; "
    "[pscustomobject]@{name=$_.TaskName; state=[string]$_.State; result=[int64]$i.LastTaskResult; "
    "last=$i.LastRunTime.ToString('yyyy-MM-dd HH:mm')} } | ConvertTo-Json")
PROCS_PS = (
    "Get-CimInstance Win32_Process -Filter \"Name='ollama.exe' or Name='llama-server.exe'\" | "
    "ForEach-Object { [pscustomobject]@{pid=[int]$_.ProcessId; ppid=[int]$_.ParentProcessId; "
    "name=$_.Name; cmd=[string]$_.CommandLine} } | ConvertTo-Json")


def powershell_json(script: str) -> list[dict]:
    out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                         capture_output=True, text=True, timeout=60).stdout.strip()
    if not out:
        return []
    data = json.loads(out)
    return data if isinstance(data, list) else [data]




def log_texts() -> dict[str, str]:
    out = {}
    for task in MORNING_LOGS:
        path = Path("logs") / f"scheduled_{task}.log"
        if path.exists():
            out[task] = path.read_text(encoding="utf-8", errors="replace")
    return out


async def broker_findings(c: httpx.AsyncClient, today: date) -> tuple[list[h.Finding], list[date]]:
    b = Alpaca(settings.alpaca_key_id, settings.alpaca_secret_key, c)
    positions = await b._get("/v2/positions")
    after = (today - timedelta(days=120)).isoformat() + "T00:00:00Z"
    orders = await b._get("/v2/orders", status="all", limit=500, nested="true", after=after)
    # Momentum carries no stop by design (policy.stopless_strategies; the SPY core has one from 10-30),
    # as in protect.py / hub.py / desk_journal.py; flagging them daily buries a real gap.
    owner_map = ownership.owners(orders)
    stopless = TradingPolicy().stopless_strategies
    positions = [p for p in positions if ownership.owner(p["symbol"], owner_map) not in stopless]
    found = h.stop_gaps(positions, orders)
    if len(orders) >= 500:
        found.append(h.Finding(level="watch", text="stops: 500+ orders in 120 days -- older stops not read"))
    cal = await b._get("/v2/calendar", start=(today - timedelta(days=10)).isoformat(), end=today.isoformat())
    sessions = [date.fromisoformat(d["date"]) for d in cal]
    return found, sessions


def guarded(name: str, fn) -> list[h.Finding]:
    """A check that cannot run is itself a finding, never a crash of the whole check."""
    try:
        out = fn()
        return out if isinstance(out, list) else [out]
    except Exception as exc:                               # noqa: BLE001
        return [h.Finding(level="problem", text=f"{name}: check could not run ({exc})"[:200])]


async def main() -> int:
    now = datetime.now().astimezone()
    today = now.date()
    findings: list[h.Finding] = []
    sessions: list[date] = []
    async with httpx.AsyncClient(timeout=60) as c:
        try:
            broker, sessions = await broker_findings(c, today)
            findings += broker
        except Exception as exc:                           # noqa: BLE001
            findings.append(h.Finding(level="problem", text=f"broker: could not read ({exc})"[:200]))
        findings += guarded("tasks", lambda: h.task_problems(powershell_json(TASKS_PS)))
        findings += guarded("ollama", lambda: h.orphan_runners(powershell_json(PROCS_PS)))
        findings += guarded("logs", lambda: deskcheck.trouble_lines(today, log_texts()))

        text = h.render(findings, now=now)
        print(text)
        OUT.mkdir(parents=True, exist_ok=True)
        with (OUT / f"{today.isoformat()}.md").open("a", encoding="utf-8") as f:
            f.write(text + "\n\n")
        line = h.summary(findings)
        if line != "ALL OK":
            title = "Quant-desk: health check"
            await notify.send(c, server=settings.ntfy_server, topic=settings.ntfy_topic,
                              title=title, message=line, priority="high", tags=("warning",))
            notify.toast(title, line)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
