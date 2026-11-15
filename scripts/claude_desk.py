"""Research desk (rules and shadow book in scanner/advisor/claude_desk.py). Places no order.

The filename is historical. Claude is retired (operator 2026-11-03). This is a Grok project.

    python scripts/claude_desk.py brief     # build today's brief and print it
    python scripts/claude_desk.py --desk gemma plan|settle|trial   # Gemma's shadow book
    python scripts/claude_desk.py verify          # Grok approves or disapproves Gemma's plan
    python scripts/claude_desk.py verify --check  # report only; exit 1 if her plan is unsigned
    python scripts/claude_desk.py sunday    # Grok writes the Sunday read. No calls.

Gemma, on local Ollama, writes the only shadow plan. Grok checks it and keeps no book.
`--desk claude` and `--desk grok` do not write a plan. The Sunday read is Grok's.
None of this places an order.
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
from scanner.entity import build_entity, for_symbol  # noqa: E402
from scanner.features.bars import Bars, fetch_many  # noqa: E402
from scanner.models import AuditEvent  # noqa: E402
from scanner.sources import alpaca_bars, calendar  # noqa: E402
try:
    from scanner.sources.moomoo_quote import brief_quote_order, holdings_note, quotes_for_brief  # noqa: E402
except ImportError:  # the local quote reader is not part of this tree
    brief_quote_order = holdings_note = quotes_for_brief = None
from scanner.sources.sec_tickers import EntityMasterError, load_universe  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DESKS = {"claude": ROOT / "data" / "claude", "gemma": ROOT / "data" / "gemma_desk",
         "grok": ROOT / "data" / "grok_desk"}
DIR = DESKS["claude"]           # set by --desk before anything runs
DESK = "claude"
GEMMA_PROMPT = ROOT / "scripts" / "gemma_desk_prompt.md"
GROK_PROMPT = ROOT / "scripts" / "grok_desk_prompt.md"
VERIFY_PROMPT = ROOT / "scripts" / "grok_verify_prompt.md"
#: Read and web only. This CLI has no open_page tool; naming it made the checker
#: warn and then run out of turns on 2026-11-03. Shell and edits stay denied.
GROK_TOOLS = "read_file,grep,list_dir,web_search,web_fetch"
GROK_DENY = "run_terminal_cmd,write,search_replace,task"
GROK_MAX_TURNS = "24"
VERIFY_TURNS = "12"
SUNDAY_PROMPT = ROOT / "scripts" / "grok_sunday_prompt.md"
SUNDAY_DIR = DESKS["grok"]
SUNDAY_LATEST = ROOT / "logs" / "grok" / "sunday.md"
#: Claude is retired. Grok checks this desk only.
CHECKED = ("gemma",)
NEWS_URL = "https://data.alpaca.markets/v1beta1/news"
NEWS_DAYS, NEWS_PER_NAME, NEWS_NAMES = 3, 3, 40
INDEXES = ("SPY", "QQQ", "IWM", "MDY", "IJR")
SECTORS = ("XLK", "XLC", "XLY", "XLP", "XLE", "XLF", "XLV", "XLI", "XLB", "XLRE", "XLU", "SMH")
#: Funds. A headline is about the tape, not one company, so the symbol tag is enough.
ETFS = frozenset({*INDEXES, *SECTORS})
CANDIDATES = 25
RUN_TIMEOUT_S = 40 * 60
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


#: Shown in the brief. The live rules book is still ownership "gemma"; that word made Gemma
#: write holds on the real account (trial 2026-10-31).
BRIEF_BOOK = {"gemma": "rules book", "small": "small caps", "core": "index core", "smc": "sweep",
              "orb": "ORB", "momentum": "momentum", "picks": "picks", "manual": "manual"}


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
        rows, _ = await rp.pool_rows(client)   # pool_rows returns (rows, scores) since 2026-11-03
        gates = await satellite_feed.gates(client)
        buyable, _ = await satellite_feed.buyable_pool(client, rows, gates, today)
        ranked = sorted(buyable, key=lambda r: -(r.mom_12_1 or -1e9))[:CANDIDATES]
        book = load_book()
        held = [p["symbol"] for p in positions]
        names = tuple(dict.fromkeys([*INDEXES, *SECTORS, *held, *(p.symbol for p in book.open),
                                     *(r.symbol for r in ranked)]))
        bars, _ = await fetch_many(names, client, range_="1y")
        try:
            found, failed = await calendar.with_moomoo(frozenset(held) | {r.symbol for r in ranked},
                                                         client, horizon=21, today=today)
        except Exception as exc:                              # noqa: BLE001 -- the brief says so
            found, failed = {}, (f"calendar unreadable: {type(exc).__name__}",)
        news_names = list(dict.fromkeys([*held, *(p.symbol for p in book.open), *(r.symbol for r in ranked)]))
        heads = await headlines(client, ["SPY", "QQQ", *news_names][:NEWS_NAMES], today)
        try:
            from scanner.advisor.tradingview_tool import morning_block
            tv_text = await morning_block(client)
        except Exception as exc:                          # noqa: BLE001 -- the brief still builds
            tv_text = f"TradingView unavailable ({type(exc).__name__})."
    equity = float(account["equity"])
    lines = [f"# {DESK} desk brief -- {today:%A %Y-%m-%d} (built {datetime.now(sat.ET):%H:%M} ET)", "",
             "## Account (Alpaca paper)",
             "This table is the live account. It is not your shadow book.",
             f"Equity ${equity:,.0f} (started $100,000), cash ${float(account['cash']):,.0f}.", "",
             "| sym | book | qty | avg | last | P&L | live stop | stop under last |", "|---|---|---|---|---|---|---|---|"]
    for p in sorted(positions, key=lambda p: ownership.owner(p["symbol"], owner_map)):
        s, last = p["symbol"], float(p["current_price"])
        st = stops.get(s)
        book_name = BRIEF_BOOK.get(ownership.owner(s, owner_map), ownership.owner(s, owner_map))
        lines.append(f"| {s} | {book_name} | {p['qty']} | {float(p['avg_entry_price']):.2f} | "
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
             f"earnings {_earn_note(r.symbol, found, r.earnings)}" for r in ranked}
    lines += ["", f"## Candidates: the {len(ranked)} strongest of the {len(buyable)} S&P 500 names that pass the "
              "rules book's buy checks (trend, 6-month return, above the 50-day, no earnings within 5 days)", ""]
    lines += level_table({r.symbol: bars.get(r.symbol) for r in ranked}, notes)
    held_e = {s: found[s] for s in held if s in found}
    lines += ["", "## Earnings in the next 21 days among the account's holdings",
              ", ".join(f"{s} {e.on} {e.when}".strip() for s, e in held_e.items()) or "none found",
              f"(calendar days unread: {len(failed)})" if failed else ""]
    lines += ["", f"## Headlines, last {NEWS_DAYS} days (Alpaca news; third-party text, newest first)", ""] + heads
    lines += ["", "## TradingView (headlines and market cap; not our prices, not an order)",
              "An entry uses the candidates table close. Do not take a price from this section.",
              "", tv_text]
    if brief_quote_order is not None and quotes_for_brief is not None:
        quote_names = brief_quote_order(
            held,
            [p.symbol for p in book.open],
            [r.symbol for r in ranked],
            INDEXES,
            SECTORS,
        )
        lines += ["", quotes_for_brief(quote_names)]
    if holdings_note is not None:
        lines += ["", holdings_note([*held, *(p.symbol for p in book.open)])]
    # Yahoo bars only. accepted() measures the 5% entry rule against this close.
    closes = {s: b.closes[-1] for s, b in bars.items() if b is not None and b.closes}
    return "\n".join(lines) + "\n", closes


def _earn_note(symbol: str, found: dict, reported: str | None) -> str:
    """The next report. `none` on the pool row means none inside 30 days, not that the company never reports."""
    if symbol in found:
        return str(found[symbol].on)
    if reported == "none":
        return "none in 30 days"
    return reported or "none in 21 days"


async def _entities(client: httpx.AsyncClient, symbols: list[str]) -> dict:
    """SEC name for each stock. Empty when the file cannot be read, and the brief still builds."""
    try:
        universe = await load_universe(client)
    except EntityMasterError:
        return {}
    found = {}
    for symbol in symbols:
        row = universe.get(symbol.upper())
        if row:
            found[symbol] = build_entity(symbol, row[0], row[1])
    return found


async def _news_for(client: httpx.AsyncClient, symbol: str, start: datetime) -> list[dict]:
    """One symbol's own page. A shared page of 50 lets the noisy names crowd the rest out."""
    response = await client.get(NEWS_URL, timeout=20, headers={
        "APCA-API-KEY-ID": settings.alpaca_key_id, "APCA-API-SECRET-KEY": settings.alpaca_secret_key},
        params={"symbols": symbol, "limit": 10, "sort": "desc", "start": start.isoformat()})
    response.raise_for_status()
    return list(response.json().get("news") or [])


async def headlines(client: httpx.AsyncClient, symbols: list[str], today: date) -> list[str]:
    """Up to NEWS_PER_NAME headlines that name the company. One line when unreadable."""
    start = datetime.combine(today - timedelta(days=NEWS_DAYS), datetime.min.time(), sat.ET)
    api = [s.replace("-", ".") for s in symbols]
    gate = asyncio.Semaphore(5)

    async def _pull(symbol: str) -> list[dict] | Exception:
        try:
            async with gate:
                return await _news_for(client, symbol, start)
        except (httpx.HTTPError, ValueError) as exc:
            return exc

    pulled = await asyncio.gather(*(_pull(s) for s in api))
    if api and all(isinstance(page, Exception) for page in pulled):
        return [f"(news unreadable: {type(pulled[0]).__name__})"]
    try:
        entities = await _entities(client, api)
    except (httpx.HTTPError, ValueError) as exc:
        return [f"(news unreadable: {type(exc).__name__})"]
    out = []
    for symbol, page in zip(api, pulled):
        if isinstance(page, Exception):
            out.append(f"- {symbol}: news unreadable ({type(page).__name__})")
            continue
        entity = None if symbol in ETFS else entities.get(symbol)
        mine = for_symbol(page, symbol, entity, limit=NEWS_PER_NAME)
        if mine:
            out += [f"- {symbol} {str(n.get('created_at', ''))[:10]}: {str(n.get('headline', ''))[:160]}"
                    for n in mine]
        elif page and entity is not None:
            out.append(f"- {symbol}: no headline names this company")
        else:
            out.append(f"- {symbol}: no headline in the last {NEWS_DAYS} days")
    return list(dict.fromkeys(out)) or ["(no headlines)"]


# --- plan -----------------------------------------------------------------------------------

def grok_exe() -> str:
    """The installed CLI. A scheduled task often has a thinner PATH than a login shell."""
    home = Path.home() / ".grok" / "bin" / "grok.exe"
    if home.exists():
        return str(home)
    found = shutil.which("grok")
    if found:
        return found
    raise RuntimeError(f"grok CLI not found ({home})")


def grok_call(prompt: str, schema: dict | None, *, turns: str = GROK_MAX_TURNS) -> tuple[dict | str, float]:
    """Grok headless, read and web tools only. A schema returns the structured object."""
    cmd = [grok_exe(), "-p", prompt, "--output-format", "json",
           "--permission-mode", "bypassPermissions", "--tools", GROK_TOOLS,
           "--disallowed-tools", GROK_DENY, "--max-turns", turns]
    if schema is not None:
        cmd += ["--json-schema", json.dumps(schema)]
    done = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=RUN_TIMEOUT_S)
    if done.returncode != 0:
        raise RuntimeError(f"grok exited {done.returncode}: {(done.stderr or done.stdout)[-800:]}")
    return cd.grok_answer(cd.grok_object(done.stdout), structured=schema is not None)


def run_grok(brief_path: Path, today: date) -> tuple[dict | str, float]:
    """Retired picker. The schedule does not call this. The checker uses VERIFY_PROMPT."""
    prompt = GROK_PROMPT.read_text(encoding="utf-8").replace("{BRIEF}", str(brief_path)).replace("{DAY}", today.isoformat())
    return grok_call(prompt, cd.SCHEMA)


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
        if DESK == "gemma":
            raw, cost = await run_gemma(brief_path, today)
        else:
            raise RuntimeError("only Gemma writes a shadow plan; Grok verifies it")
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
    # This account's SIP feed refuses bars newer than 15 minutes (alpaca_bars).
    # Settle runs on the session that just closed, so the request uses IEX.
    raw = await alpaca_bars.bars(symbols, client, timeframe="1Day", feed="iex",
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
        book = cd.play(book, cd.for_settle(plans[d]), {s: v[d] for s, v in bars.items() if d in v}, d)
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
    """The weekend's news and the week ahead, written by Grok. No calls and no orders."""
    brief, _ = await build_brief(today)
    brief_path = SUNDAY_DIR / "briefs" / f"sunday-{today}.md"
    save_json(brief_path, brief)
    prompt = (SUNDAY_PROMPT.read_text(encoding="utf-8").replace("{BRIEF}", str(brief_path))
              .replace("{DAY}", today.isoformat()))
    try:
        text, cost = grok_call(prompt, None, turns=VERIFY_TURNS)
    except (Exception, SystemExit) as exc:                   # noqa: BLE001 -- logged, no read this week
        log("grok_sunday_error", "", f"{type(exc).__name__}: {exc}"[:500])
        print(f"no Sunday read: {exc}")
        return 1
    if not isinstance(text, str) or not text.strip():
        log("grok_sunday_error", "", "empty Sunday read")
        print("no Sunday read: empty")
        return 1
    written = f"{datetime.now(sat.ET):%a %Y-%m-%d %H:%M}"
    page = f"*Written {written} ET by Grok. Research only, no orders.*\n\n{text}\n"
    save_json(SUNDAY_DIR / "sunday" / f"{today}.md", page)
    save_json(SUNDAY_LATEST, page)
    log("grok_sunday", "", f"list cost ${cost:.2f}; {text[:240]}")
    print(page)
    return 0


# --- verify ---------------------------------------------------------------------------------

def _verdicts(raw: dict) -> list[cd.Verdict]:
    found = []
    for row in raw.get("calls") or []:
        if not isinstance(row, dict):
            continue
        try:
            found.append(cd.Verdict.model_validate(row))
        except ValueError:
            continue
    return found


def verify_one(name: str, folder: Path, today: date) -> str:
    """Approve or disapprove one desk's plan. Returns a one-line result. Raises if Grok does not answer."""
    path = folder / "plans" / f"{today}.json"
    if not path.exists():
        return f"{name}: no plan for {today}"
    plan = cd.Plan.model_validate_json(path.read_text(encoding="utf-8"))
    if plan.verified:
        return f"{name}: already verified, {len(plan.calls)} calls kept"
    brief = folder / "briefs" / f"{today}.md"
    prompt = (VERIFY_PROMPT.read_text(encoding="utf-8").replace("{DAY}", today.isoformat())
              .replace("{DESK}", name)
              .replace("{BRIEF}", str(brief))
              .replace("{PLAN}", plan.model_dump_json()))
    raw, cost = grok_call(prompt, cd.VERIFY_SCHEMA, turns=VERIFY_TURNS)
    if not isinstance(raw, dict):
        raise RuntimeError("verify did not return a plan")
    out = cd.apply_verdicts(plan, _verdicts(raw), market_ok=bool(raw.get("approve_market")),
                            market_why=str(raw.get("market_why") or ""))
    save_json(path, out.model_dump_json(indent=1))
    kept = [c.symbol for c in out.calls]
    log(f"grok_verify_{name}", ",".join(kept),
        f"{len(kept)} approved, {len(out.skipped)} refused, list cost ${cost:.2f}")
    return f"{name}: verified, kept {kept}, refused {len(out.skipped)}, list cost ${cost:.2f}"


def verify_status(today: date) -> int:
    """Exit 1 when a written plan has not been verified. Does not call Grok."""
    bad = 0
    for name, folder in DESKS.items():
        if name not in CHECKED:
            continue
        path = folder / "plans" / f"{today}.json"
        if not path.exists():
            print(f"{name}: no plan for {today}")
            continue
        plan = cd.Plan.model_validate_json(path.read_text(encoding="utf-8"))
        if plan.verified:
            print(f"{name}: verified, {len(plan.calls)} calls will play")
        else:
            print(f"{name}: NOT verified -- settle will not play these calls")
            bad = 1
    return bad


def verify(today: date) -> int:
    """Grok signs Gemma's plan. A desk he cannot reach stays unsigned. Claude is retired."""
    failed = 0
    for name, folder in DESKS.items():
        if name not in CHECKED:
            continue
        try:
            print(verify_one(name, folder, today))
        except (Exception, SystemExit) as exc:               # noqa: BLE001 -- one desk must not block the other
            failed = 1
            message = f"{type(exc).__name__}: {exc}"[:400]
            log(f"grok_verify_{name}_error", "", message)
            print(f"{name}: Grok could not verify ({message})")
    return failed


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("mode", choices=("brief", "plan", "settle", "trial", "sunday", "verify"))
    p.add_argument("--desk", choices=tuple(DESKS), default="claude")
    p.add_argument("--check", action="store_true", help="verify: report only, do not call Grok")
    a = p.parse_args()
    mode, DESK, DIR = a.mode, a.desk, DESKS[a.desk]
    now = datetime.now(sat.ET)
    if mode == "verify":
        raise SystemExit(verify_status(now.date()) if a.check else verify(now.date()))
    if mode == "sunday":
        raise SystemExit(asyncio.run(sunday(now.date())))
    if DESK == "claude":
        print("Claude is retired. Grok verifies Gemma's plan: python scripts/claude_desk.py verify")
        raise SystemExit(0)
    if DESK == "grok":
        print("Grok does not run a shadow book. He verifies Gemma's plan: "
              "python scripts/claude_desk.py verify")
        raise SystemExit(0)
    if mode == "settle":
        raise SystemExit(asyncio.run(settle(now)))
    raise SystemExit(asyncio.run(plan(now.date(), research=mode != "brief", trial=mode == "trial")))
