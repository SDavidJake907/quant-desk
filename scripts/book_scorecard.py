"""The book scorecard, written after the close (rules in scanner/ops/book_scorecard.py).

    python scripts/book_scorecard.py

Each book's dollars and percent since the fresh start (the 2026-10-30 close) beside its
benchmark, the live sweep beside its ghost, and the acceleration shadow's grade. Writes
logs/scorecard/latest.md (the command center shows it) and one row a day to
data/scorecard/daily.jsonl. READ ONLY: GET requests and local files; it never orders.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.config import settings  # noqa: E402
from scanner.execution import ownership  # noqa: E402
from scanner.execution.alpaca import Alpaca  # noqa: E402
from scanner.execution.policy import TradingPolicy  # noqa: E402
from scanner.ops import accel_shadow, book_scorecard as bs  # noqa: E402

DATA_URL = "https://data.alpaca.markets/v2/stocks/bars"
GHOST = Path("data/competition/ghost_scanner.json")
ACCEL = Path("data/shadow/accel.jsonl")
REPORT = Path("logs/scorecard/latest.md")
DAILY = Path("data/scorecard/daily.jsonl")
BENCH = ("SPY", "QQQ", "IWM")
POLICY = TradingPolicy()


async def daily_closes(client: httpx.AsyncClient, symbols: set[str], start: date) -> dict[str, list[tuple[str, float]]]:
    """{symbol: [(day, close)]} from `start`, SIP, unadjusted (to match the fills)."""
    headers = {"APCA-API-KEY-ID": settings.alpaca_key_id, "APCA-API-SECRET-KEY": settings.alpaca_secret_key}
    end = (datetime.now(timezone.utc) - timedelta(minutes=16)).strftime("%Y-%m-%dT%H:%M:%SZ")
    out: dict[str, list[tuple[str, float]]] = {}
    names = sorted(symbols)
    for i in range(0, len(names), 100):
        token = None
        while True:
            params = {"symbols": ",".join(names[i:i + 100]), "timeframe": "1Day", "start": start.isoformat(),
                      "end": end, "limit": 10000, "feed": "sip", "adjustment": "raw"}
            if token:
                params["page_token"] = token
            r = await client.get(DATA_URL, params=params, headers=headers, timeout=60)
            r.raise_for_status()
            j = r.json()
            for s, bars in (j.get("bars") or {}).items():
                out.setdefault(s, []).extend((b["t"][:10], float(b["c"])) for b in bars)
            token = j.get("next_page_token")
            if not token:
                break
    return out


def close_on(series: list[tuple[str, float]], day: date) -> float | None:
    vals = [c for d, c in series if d <= day.isoformat()]
    return vals[-1] if vals else None


def equity_on(history: dict, day: date) -> float | None:
    from zoneinfo import ZoneInfo
    for ts, eq in zip(history.get("timestamp") or [], history.get("equity") or []):
        if eq and datetime.fromtimestamp(ts, ZoneInfo("America/New_York")).date() == day:
            return float(eq)
    return None


def accel_records() -> list[dict]:
    try:
        return [json.loads(line) for line in ACCEL.read_text(encoding="utf-8").splitlines() if line.strip()]
    except OSError:
        return []


async def main() -> int:
    today = datetime.now(timezone.utc).astimezone().date()
    async with httpx.AsyncClient() as client:
        broker = Alpaca(settings.alpaca_key_id, settings.alpaca_secret_key, client)
        account = await broker._get("/v2/account")
        positions = await broker._get("/v2/positions")
        history = await broker._get("/v2/account/portfolio/history", period="1M", timeframe="1D")
        all_orders = await broker._get("/v2/orders", status="all", limit=500, nested="true")
        owner = ownership.owners(all_orders)
        fills = bs.fills_since(all_orders, bs.START)
        held = {p["symbol"]: (float(p["qty"]), float(p["market_value"])) for p in positions}
        records = accel_records()
        accel_names = {n["symbol"] for r in records for n in r["names"]}
        first = min([bs.START_BASE] + [date.fromisoformat(r["day"]) for r in records]) - timedelta(days=7)
        closes = await daily_closes(client, set(held) | {f["symbol"] for f in fills} | set(BENCH) | accel_names,
                                    first)

    base = {s: c for s, ser in closes.items() if (c := close_on(ser, bs.START_BASE)) is not None}
    last = {s: ser[-1][1] for s, ser in closes.items() if ser}
    # Every value at the last daily close, as the benchmarks are: Alpaca's market value uses the
    # latest after-hours trade, which would put a gap between a book and its benchmark.
    held = {s: (q, q * last[s] if s in last else v) for s, (q, v) in held.items()}
    equity_base = equity_on(history, bs.START_BASE)
    equity_now = float(account["equity"])
    rows = []
    for label, strategy, field, weights in bs.BOOKS:
        alloc = bs.allocation_pct(field, float(getattr(POLICY, field)))
        sleeve = (equity_base or equity_now) * alloc / 100
        res = bs.book_pnl(book=strategy, owner=owner, held=held, fills=fills, base_close=base)
        pct = res["pnl"] / sleeve * 100 if res and sleeve else None
        bench = bs.benchmark_pct(weights, base, last)
        rows.append({"book": label, "pnl": res and round(res["pnl"], 2), "pct": pct and round(pct, 3),
                     "bench": "/".join(weights), "bench_pct": bench and round(bench, 3),
                     "verdict": bs.verdict(pct, bench)})
    acct_pnl = sum(r["pnl"] or 0.0 for r in rows)
    acct_pct = acct_pnl / equity_base * 100 if equity_base else None
    spy_pct = bs.benchmark_pct({"SPY": 1.0}, base, last)
    try:
        ghost = bs.ghost_since(json.loads(GHOST.read_text(encoding="utf-8"))["trades"], bs.START)
    except (OSError, ValueError, KeyError):
        ghost = None
    sweep = next(r for r in rows if r["book"] == "Sweep")
    sweep_alloc = bs.allocation_pct("swing_allocation_pct", POLICY.swing_allocation_pct)
    risk_dollars = (equity_base or equity_now) * sweep_alloc / 100 * POLICY.risk_pct / 100
    sweep_r = (sweep["pnl"] / risk_dollars) if sweep["pnl"] is not None and risk_dollars else None
    graded = accel_shadow.grade(records, closes)

    day = {"day": today.isoformat(), "equity": equity_now, "account_pnl": acct_pnl, "account_pct": acct_pct,
           "spy_pct": spy_pct,
           "books": rows, "ghost": ghost, "sweep_r": sweep_r, "accel": graded}
    write_daily(day)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(render(day, equity_base), encoding="utf-8")
    print(render(day, equity_base))
    return 0


def write_daily(day: dict) -> None:
    DAILY.parent.mkdir(parents=True, exist_ok=True)
    try:
        rows = [json.loads(x) for x in DAILY.read_text(encoding="utf-8").splitlines() if x.strip()]
    except OSError:
        rows = []
    rows = [r for r in rows if r.get("day") != day["day"]] + [day]
    DAILY.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def fmt(x: float | None, unit: str = "%", money: bool = False) -> str:
    if x is None:
        return "–"
    if money:
        return f"-${abs(x):,.0f}" if x < 0 else f"+${x:,.0f}"
    return f"{x:+.2f}{unit}"


def render(day: dict, equity_base: float | None) -> str:
    lines = [f"Since the fresh start (the {bs.START_BASE:%m-%d} close), updated {day['day']}. "
             f"Each book against what it is judged by, over the same days.", "",
             "| Book | P&L | % of its sleeve | Benchmark | Benchmark % | Verdict |",
             "|---|---:|---:|---|---:|---|"]
    for r in day["books"]:
        lines.append(f"| {r['book']} | {fmt(r['pnl'], money=True)} | {fmt(r['pct'])} | {r['bench']} | "
                     f"{fmt(r['bench_pct'])} | {r['verdict']} |")
    lines.append(f"| **All books** | {fmt(day['account_pnl'], money=True)} | "
                 f"{fmt(day['account_pct'])} | SPY | {fmt(day['spy_pct'])} | "
                 f"{bs.verdict(day['account_pct'], day['spy_pct'])} |")
    g = day["ghost"]
    lines += ["", "**Sweep vs its ghost** (the same rules on paper, no news filter, ideal fills): "
              + (f"live {fmt(day['sweep_r'], 'R')}; ghost {g['closed']} closed, {g['won']} won, "
                 f"{g['r']:+.2f}R, {g['open']} open." if g else "ghost file unreadable.")]
    a = day["accel"]
    gap = "–" if a["avg_gap"] is None else f"{a['avg_gap']:+.2f} pts"
    lines += ["", f"**Acceleration shadow** (speeding-up vs slowing-down names, "
                  f"{accel_shadow.HORIZON} sessions on): {a['graded']} pick days graded, {a['pending']} pending; "
                  f"average gap {gap}, positive on {a['positive_days']} days. "
                  f"Means something after {accel_shadow.MIN_DAYS} graded days; never trades."]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
