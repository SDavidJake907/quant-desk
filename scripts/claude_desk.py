"""Claude's research desk (rules and shadow book in scanner/advisor/claude_desk.py). Places no order.

    python scripts/claude_desk.py brief     # build today's brief and print it
    python scripts/claude_desk.py plan      # brief, then Claude researches and writes the plan
    python scripts/claude_desk.py settle    # after the close: play the plans in the shadow book
    python scripts/claude_desk.py trial     # a full research run on any day, saved as plans/trial-*.json
    python scripts/claude_desk.py sunday    # Sunday read: the weekend's news and the week ahead (markdown)
    python scripts/claude_desk.py --desk gemma plan|settle|trial   # Gemma's own shadow book

TWO DESKS, ONE RULEBOOK (operator 2026-10-31: "what would you have Gemma do? and you do?").
`--desk gemma` runs the same brief, the same schema and the same shadow-book rules for Gemma
(local Ollama, no web tools -- the brief's headlines section is her news, and Claude's too, so
they start from the same facts), in data/gemma_desk/. Claude vs Gemma vs the rules book vs SPY,
graded by the same pass rule on 2026-12-31 (FREEZE.md). Neither places an order.

Operator 2026-10-31: "lets have you do the research, News, stocks, and find better entry and
exit per stocks.. cause every stocks is diffrent." The 8:45 ET task runs `plan`: the brief
(account, every book's positions and live stops, indexes and sectors, the rules book's
buyable names with each one's levels and earnings, the shadow book) goes to Claude, run
headless with read and web tools only, which answers in claude_desk.SCHEMA. The 17:15 ET
task runs `settle`. Files: data/claude/briefs/, plans/, book.json; audit events claude_plan
and claude_shadow.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import shutil
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scanner.advisor import claude_desk as cd  # noqa: E402
from scanner.config import settings  # noqa: E402
from scanner.execution import ownership, satellite as sat, satellite_feed  # noqa: E402
from scanner.execution.alpaca import Alpaca  # noqa: E402
from scanner.execution.audit import append_event  # noqa: E402
from scanner.execution.provenance import build_id  # noqa: E402
from scanner.execution.policy import TradingPolicy  # noqa: E402
from scanner.features.bars import Bars, fetch_many  # noqa: E402
from scanner.models import AuditEvent  # noqa: E402
from scanner.sources import alpaca_bars, calendar  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DESKS = {"claude": ROOT / "data" / "claude", "gemma": ROOT / "data" / "gemma_desk"}
DIR = DESKS["claude"]           # set by --desk before anything runs
DESK = "claude"
PROMPT = ROOT / "scripts" / "claude_desk_prompt.md"
GEMMA_PROMPT = ROOT / "scripts" / "gemma_desk_prompt.md"
SUNDAY_PROMPT = ROOT / "scripts" / "claude_sunday_prompt.md"
SUNDAY_LATEST = ROOT / "logs" / "claude" / "sunday.md"
NEWS_URL = "https://data.alpaca.markets/v1beta1/news"
NEWS_DAYS, NEWS_PER_NAME, NEWS_NAMES = 3, 3, 40
INDEXES = ("SPY", "QQQ", "IWM", "MDY", "IJR")
SECTORS = ("XLK", "XLC", "XLY", "XLP", "XLE", "XLF", "XLV", "XLI", "XLB", "XLRE", "XLU", "SMH")
CANDIDATES = 25
RUN_TIMEOUT_S = 40 * 60
TOOLS = "Read,Glob,Grep,WebSearch,WebFetch"
POLICY = TradingPolicy()


def log(event: str, symbol: str, reason: str) -> None:
    append_event(settings.audit_log_path, AuditEvent(
        event=event, at=datetime.now().astimezone(), symbol=symbol, reason=reason,
        stage="advisor", policy=POLICY.fingerprint, build=build_id()))


def book_path() -> Path:
    return DIR / "book.json"


def load_book() -> cd.Book:
    path = book_path()
    return cd.Book.model_validate_json(path.read_text(encoding="utf-8")) if path.exists() else cd.Book()


def save_json(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def rules_pick_module():
    spec = importlib.util.spec_from_file_location("rules_pick_script", ROOT / "scripts" / "rules_pick.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --- the brief ------------------------------------------------------------------------------

def pct(a: float, b: float) -> str:
    return f"{(a / b - 1) * 100:+.1f}%" if b else "n/a"


def levels(b: Bars) -> dict[str, float | str]:
    c, h, lo = b.closes, b.highs, b.lows
    trs = [max(h[i] - lo[i], abs(h[i] - c[i - 1]), abs(lo[i] - c[i - 1])) for i in range(len(c) - 14, len(c))]
    sma = lambda n: sum(c[-n:]) / n if len(c) >= n else float("nan")  # noqa: E731
    return {"close": c[-1], "1d": pct(c[-1], c[-2]), "5d": pct(c[-1], c[-6]), "1m": pct(c[-1], c[-22]),
            "atr14": sum(trs) / 14, "prev_hi": h[-1], "prev_lo": lo[-1],
            "hi20": max(h[-20:]), "lo20": min(lo[-20:]), "sma20": sma(20), "sma50": sma(50), "sma200": sma(200)}


def level_table(rows: dict[str, Bars], extra: dict[str, str] | None = None) -> list[str]:
    head = "| sym | close | 1d | 5d | 1m | ATR14 | prev hi/lo | 20d hi/lo | 20/50/200-day |" + \
        (" notes |" if extra else "")
    out = [head, "|" + "---|" * (head.count("|") - 1)]
    for s, b in rows.items():
        if b is None or len(b.closes) < 30:
            out.append(f"| {s} | no bars |")
            continue
        v = levels(b)
        line = (f"| {s} | {v['close']:.2f} | {v['1d']} | {v['5d']} | {v['1m']} | {v['atr14']:.2f} | "
                f"{v['prev_hi']:.2f}/{v['prev_lo']:.2f} | {v['hi20']:.2f}/{v['lo20']:.2f} | "
                f"{v['sma20']:.2f}/{v['sma50']:.2f}/{v['sma200']:.2f} |")
        out.append(line + (f" {extra.get(s, '')} |" if extra else ""))
    return out


async def live_stops(broker: Alpaca) -> dict[str, float]:
    """The highest live sell stop per symbol (bracket legs show as held, so read status=all)."""
    stops: dict[str, float] = {}
    for o in await broker._get("/v2/orders", status="all", limit=500, nested="true"):
        for leg in (o, *(o.get("legs") or ())):
            if leg.get("side") == "sell" and leg.get("stop_price") and leg.get("status") in (
                    "new", "held", "accepted", "pending_new", "partially_filled"):
                s = leg["symbol"]
                stops[s] = max(stops.get(s, 0.0), float(leg["stop_price"]))
    return stops


def shadow_lines(book: cd.Book) -> list[str]:
    value = book.marks[-1][1] if book.marks else cd.SLEEVE
    out = [f"Sleeve ${cd.SLEEVE:,.0f} in {cd.SLOTS} slots of ${cd.SLOT:,.0f}; value ${value:,.0f} "
           f"({pct(value, cd.SLEEVE)}), cash ${book.cash:,.0f}, played through {book.through or 'nothing yet'}."]
    if book.open:
        out += ["", "| sym | opened | entry | stop | target | days held / max | your reason |", "|---|---|---|---|---|---|---|"]
        out += [f"| {p.symbol} | {p.opened} | {p.entry:.2f} | {p.stop:.2f} | "
                f"{p.target if p.target is not None else '-'} | {p.held_days}/{p.max_days} | {p.why[:120]} |"
                for p in book.open]
    if book.closed:
        won = sum(c.pnl > 0 for c in book.closed)
        out += ["", f"Closed {len(book.closed)}: won {won}, P&L ${sum(c.pnl for c in book.closed):+,.0f}. Last 10:"]
        out += [f"- {c.symbol} {c.opened}->{c.closed} {c.pct:+.1f}% ({c.reason})" for c in book.closed[-10:]]
    return out


async def build_brief(today: date) -> tuple[str, dict[str, float]]:
    """The brief's markdown and the last close of every name it lists (for `accepted`)."""
    async with httpx.AsyncClient(timeout=120) as client:
        broker = Alpaca(settings.alpaca_key_id, settings.alpaca_secret_key, client)
        account = await broker._get("/v2/account")
        positions = await broker._get("/v2/positions")
        owner_map = ownership.owners(await broker._get("/v2/orders", status="all", limit=500, nested="true"))
        stops = await live_stops(broker)
        rp = rules_pick_module()
        rows = await rp.pool_rows(client)
        gates = await satellite_feed.gates(client)
        buyable, _ = await satellite_feed.buyable_pool(client, rows, gates, today)
        ranked = sorted(buyable, key=lambda r: -(r.mom_12_1 or -1e9))[:CANDIDATES]
        book = load_book()
        held = [p["symbol"] for p in positions]
        names = tuple(dict.fromkeys([*INDEXES, *SECTORS, *held, *(p.symbol for p in book.open),
                                     *(r.symbol for r in ranked)]))
        bars, _ = await fetch_many(names, client, range_="1y")
        try:
            found, failed = await calendar.upcoming_earnings(frozenset(held) | {r.symbol for r in ranked},
                                                              client, horizon=21, today=today)
        except Exception as exc:                              # noqa: BLE001 -- the brief says so
            found, failed = {}, (f"calendar unreadable: {type(exc).__name__}",)
        news_names = list(dict.fromkeys([*held, *(p.symbol for p in book.open), *(r.symbol for r in ranked)]))
        heads = await headlines(client, ["SPY", "QQQ", *news_names][:NEWS_NAMES], today)
    equity = float(account["equity"])
    lines = [f"# Claude desk brief -- {today:%A %Y-%m-%d} (built {datetime.now(sat.ET):%H:%M} ET)", "",
             "## Account (Alpaca paper)",
             f"Equity ${equity:,.0f} (started $100,000), cash ${float(account['cash']):,.0f}.", "",
             "| sym | book | qty | avg | last | P&L | live stop | stop under last |", "|---|---|---|---|---|---|---|---|"]
    for p in sorted(positions, key=lambda p: ownership.owner(p["symbol"], owner_map)):
        s, last = p["symbol"], float(p["current_price"])
        st = stops.get(s)
        lines.append(f"| {s} | {ownership.owner(s, owner_map)} | {p['qty']} | {float(p['avg_entry_price']):.2f} | "
                     f"{last:.2f} | {float(p['unrealized_plpc']) * 100:+.1f}% | {f'{st:.2f}' if st else 'NONE'} | "
                     f"{pct(st, last) if st else '-'} |")
    lines += ["", "## Indexes (SPY = S&P 500, QQQ = Nasdaq-100, IWM/IJR small, MDY mid)",
              f"Book gates (index above its trend): {gates.text()}", ""]
    lines += level_table({s: bars.get(s) for s in INDEXES})
    lines += ["", "## Sectors", ""] + level_table({s: bars.get(s) for s in SECTORS})
    lines += ["", "## Your shadow book", ""] + shadow_lines(book)
    if book.open:
        lines += [""] + level_table({p.symbol: bars.get(p.symbol) for p in book.open})
    notes = {r.symbol: f"{r.sector}; 12-1 mom {r.mom_12_1 or 0:+.0f}%; vs 50d {r.vs_50d or 0:+.1f}%; "
             f"earnings {found[r.symbol].on if r.symbol in found else (r.earnings or 'none in 21d')}" for r in ranked}
    lines += ["", f"## Candidates: the {len(ranked)} strongest of the {len(buyable)} S&P 500 names that pass the "
              "rules book's buy checks (trend, 6-month return, above the 50-day, no earnings within 5 days)", ""]
    lines += level_table({r.symbol: bars.get(r.symbol) for r in ranked}, notes)
    held_e = {s: found[s] for s in held if s in found}
    lines += ["", "## Earnings in the next 21 days among the account's holdings",
              ", ".join(f"{s} {e.on} {e.when}".strip() for s, e in held_e.items()) or "none found",
              f"(calendar days unread: {len(failed)})" if failed else ""]
    lines += ["", f"## Headlines, last {NEWS_DAYS} days (Alpaca news; third-party text, newest first)", ""] + heads
    closes = {s: b.closes[-1] for s, b in bars.items() if b is not None and b.closes}
    return "\n".join(lines) + "\n", closes


async def headlines(client: httpx.AsyncClient, symbols: list[str], today: date) -> list[str]:
    """Up to NEWS_PER_NAME recent headlines a name, as brief lines. One line when unreadable."""
    start = datetime.combine(today - timedelta(days=NEWS_DAYS), datetime.min.time(), sat.ET)
    api = [s.replace("-", ".") for s in symbols]
    items: list[dict] = []
    try:
        for i in range(0, len(api), 10):
            r = await client.get(NEWS_URL, timeout=20, headers={
                "APCA-API-KEY-ID": settings.alpaca_key_id, "APCA-API-SECRET-KEY": settings.alpaca_secret_key},
                params={"symbols": ",".join(api[i:i + 10]), "limit": 50, "sort": "desc", "start": start.isoformat()})
            r.raise_for_status()
            items += r.json().get("news", [])
    except (httpx.HTTPError, ValueError) as exc:
        return [f"(news unreadable: {type(exc).__name__})"]
    out = []
    for s in api:
        mine = [n for n in items if s in (n.get("symbols") or [])][:NEWS_PER_NAME]
        out += [f"- {s} {str(n.get('created_at', ''))[:10]}: {str(n.get('headline', ''))[:160]}" for n in mine]
    return list(dict.fromkeys(out)) or ["(no headlines)"]


# --- plan -----------------------------------------------------------------------------------

def claude_exe() -> str:
    found = shutil.which("claude") or str(Path.home() / ".local" / "bin" / "claude.exe")
    if not Path(found).exists() and not shutil.which("claude"):
        raise SystemExit(f"claude CLI not found ({found})")
    return found


def claude_call(prompt: str, schema: dict | None) -> tuple[dict | str, float]:
    """Claude headless, read + web tools only: the structured answer, or text without a schema."""
    cmd = [claude_exe(), "-p", prompt, "--output-format", "json", "--allowedTools", TOOLS,
           "--permission-mode", "dontAsk", "--model", "opus"]
    if schema is not None:
        cmd += ["--json-schema", json.dumps(schema)]
    done = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=RUN_TIMEOUT_S)
    if done.returncode != 0:
        raise RuntimeError(f"claude exited {done.returncode}: {(done.stderr or done.stdout)[-800:]}")
    out = json.loads(done.stdout)
    cost = float(out.get("total_cost_usd") or 0.0)
    if schema is None:
        if out.get("is_error") or not str(out.get("result") or "").strip():
            raise RuntimeError(f"no answer: {str(out.get('result'))[:800]}")
        return str(out["result"]), cost
    if out.get("is_error") or not isinstance(out.get("structured_output"), dict):
        raise RuntimeError(f"no structured plan: {str(out.get('result'))[:800]}")
    return out["structured_output"], cost


def run_claude(brief_path: Path, today: date) -> tuple[dict | str, float]:
    prompt = PROMPT.read_text(encoding="utf-8").replace("{BRIEF}", str(brief_path)).replace("{DAY}", today.isoformat())
    return claude_call(prompt, cd.SCHEMA)


async def run_gemma(brief_path: Path, today: date) -> tuple[dict, float]:
    """Gemma on the local Ollama, the brief inline (no tools), the same schema. Costs nothing."""
    from scanner.llm.ollama import chat_json
    prompt = (GEMMA_PROMPT.read_text(encoding="utf-8").replace("{DAY}", today.isoformat())
              .replace("{BRIEF}", brief_path.read_text(encoding="utf-8")))
    async with httpx.AsyncClient() as client:
        return await chat_json(prompt, cd.SCHEMA, client), 0.0


async def is_session(day: date) -> bool:
    async with httpx.AsyncClient(timeout=30) as client:
        broker = Alpaca(settings.alpaca_key_id, settings.alpaca_secret_key, client)
        return day in await sessions_between(broker, day, day)


async def plan(today: date, *, research: bool, trial: bool = False) -> int:
    if research and not trial and not await is_session(today):
        print(f"{today} is not a session -- no plan")
        return 0
    brief, closes = await build_brief(today)
    brief_path = DIR / "briefs" / f"{today}.md"
    save_json(brief_path, brief)
    if not research:
        print(brief)
        return 0
    plan_path = DIR / "plans" / (f"trial-{today}.json" if trial else f"{today}.json")
    if plan_path.exists() and not trial:
        print(f"plan for {today} already written: {plan_path}")
        return 0
    try:
        raw, cost = await run_gemma(brief_path, today) if DESK == "gemma" else run_claude(brief_path, today)
    except Exception as exc:                                  # noqa: BLE001 -- logged, no plan today
        log(f"{DESK}_plan_error", "", f"{type(exc).__name__}: {exc}"[:500])
        print(f"no plan: {exc}")
        return 1
    calls = []
    skipped = []
    for c in raw.get("calls", []):
        try:
            calls.append(cd.Call.model_validate(c))
        except ValueError as exc:
            skipped.append(f"{c.get('symbol', '?')}: unreadable call ({str(exc)[:80]})")
    out = cd.accepted(cd.Plan(day=today, market=str(raw.get("market", "")), calls=tuple(calls),
                              skipped=tuple(skipped)), closes=closes, book=load_book())
    save_json(plan_path, out.model_dump_json(indent=1))
    buys = [c.symbol for c in out.calls if c.action == "buy"]
    log(f"{DESK}_trial" if trial else f"{DESK}_plan", ",".join(buys), f"{len(out.calls)} calls, {len(out.skipped)} refused, "
        f"list cost ${cost:.2f}; {out.market[:300]}")
    print(f"plan {today}: {len(out.calls)} calls (buys {buys}), refused {list(out.skipped)}; list cost ${cost:.2f}")
    return 0


# --- settle ---------------------------------------------------------------------------------

async def sessions_between(broker: Alpaca, start: date, end: date) -> list[date]:
    rows = await broker._get("/v2/calendar", start=start.isoformat(), end=end.isoformat())
    return [date.fromisoformat(r["date"]) for r in rows]


def load_plan(day: date) -> cd.Plan | None:
    path = DIR / "plans" / f"{day}.json"
    return cd.Plan.model_validate_json(path.read_text(encoding="utf-8")) if path.exists() else None


async def daily_bars(client: httpx.AsyncClient, symbols: list[str], start: date, end: date) -> dict[str, dict[date, cd.Bar]]:
    raw = await alpaca_bars.bars(symbols, client, timeframe="1Day", feed="sip",
                                 start=datetime.combine(start, datetime.min.time(), sat.ET),
                                 end=datetime.combine(end, datetime.max.time().replace(microsecond=0), sat.ET))
    out: dict[str, dict[date, cd.Bar]] = {}
    for s, rows in raw.items():
        for r in rows:
            day = datetime.fromisoformat(r["t"].replace("Z", "+00:00")).astimezone(sat.ET).date()
            out.setdefault(s, {})[day] = cd.Bar(day=day, o=r["o"], h=r["h"], l=r["l"], c=r["c"])
    return out


async def settle(now: datetime) -> int:
    book = load_book()
    last_done = now.date() if now.hour * 60 + now.minute >= 16 * 60 + 15 else now.date() - timedelta(days=1)
    start = max(cd.FIRST_DAY, (book.through + timedelta(days=1)) if book.through else cd.FIRST_DAY)
    async with httpx.AsyncClient(timeout=60) as client:
        broker = Alpaca(settings.alpaca_key_id, settings.alpaca_secret_key, client)
        days = await sessions_between(broker, start, last_done) if start <= last_done else []
        plans = {d: load_plan(d) for d in days}
        symbols = sorted({p.symbol for p in book.open}
                         | {c.symbol for p in plans.values() if p for c in p.calls} | {"SPY", "QQQ"})
        bars = await daily_bars(client, symbols, cd.FIRST_DAY - timedelta(days=7), last_done) if days or book.marks else {}
    for d in days:
        book = cd.play(book, plans[d], {s: v[d] for s, v in bars.items() if d in v}, d)
    if days:
        save_json(book_path(), book.model_dump_json(indent=1))
    print(report(book, bars))
    if days:
        log(f"{DESK}_shadow", ",".join(p.symbol for p in book.open),
            f"through {book.through}: value {book.marks[-1][1]:.0f}, {len(book.closed)} closed")
    return 0


def report(book: cd.Book, bars: dict[str, dict[date, cd.Bar]]) -> str:
    if not book.marks:
        return f"{DESK} shadow book: nothing played yet (first plan day {cd.FIRST_DAY})"
    value = book.marks[-1][1]
    lines = [f"{DESK} shadow book through {book.through}: ${value:,.0f} ({pct(value, cd.SLEEVE)}), "
             f"max drawdown {cd.max_drawdown_pct(book.marks):.1f}%"]
    for s in ("SPY", "QQQ"):
        series = bars.get(s, {})
        before = [d for d in series if d < cd.FIRST_DAY]
        if before and book.through in series:
            lines.append(f"  {s} over the same days: {pct(series[book.through].c, series[max(before)].c)}")
    won = sum(c.pnl > 0 for c in book.closed)
    lines.append(f"  closed {len(book.closed)} (won {won}), open {[p.symbol for p in book.open]}")
    lines += [f"  {c.symbol:<6} {c.opened} -> {c.closed} {c.pct:+6.1f}%  {c.reason}" for c in book.closed[-10:]]
    return "\n".join(lines)


# --- sunday ---------------------------------------------------------------------------------

async def sunday(today: date) -> int:
    """The weekend's news and the week ahead, written by Claude for the operator. No calls."""
    brief, _ = await build_brief(today)
    brief_path = DIR / "briefs" / f"sunday-{today}.md"
    save_json(brief_path, brief)
    prompt = (SUNDAY_PROMPT.read_text(encoding="utf-8").replace("{BRIEF}", str(brief_path))
              .replace("{DAY}", today.isoformat()))
    try:
        text, cost = claude_call(prompt, None)
    except Exception as exc:                                  # noqa: BLE001 -- logged, no read this week
        log("claude_sunday_error", "", f"{type(exc).__name__}: {exc}"[:500])
        print(f"no Sunday read: {exc}")
        return 1
    page = f"*Written {datetime.now(sat.ET):%a %Y-%m-%d %H:%M} ET by Claude (research only, no orders).*\n\n{text}\n"
    save_json(DIR / "sunday" / f"{today}.md", page)
    save_json(SUNDAY_LATEST, page)
    log("claude_sunday", "", f"list cost ${cost:.2f}; {str(text)[:300]}")
    print(page)
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("mode", choices=("brief", "plan", "settle", "trial", "sunday"))
    p.add_argument("--desk", choices=tuple(DESKS), default="claude")
    a = p.parse_args()
    mode, DESK, DIR = a.mode, a.desk, DESKS[a.desk]
    if mode == "sunday" and DESK != "claude":
        raise SystemExit("the Sunday read is Claude's")
    now = datetime.now(sat.ET)
    if mode == "sunday":
        raise SystemExit(asyncio.run(sunday(now.date())))
    if mode == "settle":
        raise SystemExit(asyncio.run(settle(now)))
    raise SystemExit(asyncio.run(plan(now.date(), research=mode != "brief", trial=mode == "trial")))
