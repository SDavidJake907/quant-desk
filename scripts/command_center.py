"""Command center: one HTML page with the whole desk on it.

    python scripts/command_center.py            # write logs/dashboard/command_center.html
    python scripts/command_center.py --open     # ...and open it in the browser
    python scripts/command_center.py --no-broker  # files only, skip Alpaca

READ ONLY. It reads the paper account (GET requests only), the audit log, the
per-task logs, the competition files and FREEZE.md, and writes one
self-contained HTML file. It never places, changes or cancels anything, and it
imports nothing from the trading path, so it cannot change a window's
fingerprint or build behaviour.

Every panel says where its numbers came from. A source that is missing or
unreadable shows as missing on the page instead of being guessed at.

Panels
  status strip   kill switch, live stops (every book but Momentum; the SPY core's from 2026-10-30), daily loss vs 3%, tasks
  KPI tiles      equity, today, each book's open P/L, slots used (limits read from policy.py)
  vs the index   the account against SPY and IWM, last month (Alpaca portfolio history + daily bars)
  standings      logs/competition/standings.md as written by competition.py
  positions      every open position by book, its live stop and distance to it
  penny shadow   logs/shadow/penny.md: the would-buys and their grade vs IWM (never trades)
  tasks          each running scheduled task: last run, age vs schedule, errors
  audit feed     the last audit records, and today's refusals by reason

Updated 2026-10-27 for the current account (SPY core 50 / rules book 30 / small caps 10 /
ORB 10, penny shadow); the operator's draft described the Gemma-era books and tasks.
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import sys
import urllib.error
import urllib.request
import webbrowser
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAPER_URL = "https://paper-api.alpaca.markets"
DATA_URL = "https://data.alpaca.markets"
INDEXES = ("SPY", "IWM")
AK = dt.timezone(dt.timedelta(hours=-8))  # display only; AKDT/AKST handled below

# ----------------------------------------------------------------------------
# Book identity -- the same prefixes execution/ownership.py uses
# ----------------------------------------------------------------------------
BOOKS = {
    "cor": "SPY core",
    "gem": "Rules book",          # the `gem-` ids are kept from Gemma's book (2026-10-26)
    "sml": "Small caps",
    "orb": "ORB",
    "pck": "Picks",
    "man": "Manual",
    "mom": "Momentum",
    "smc": "Sweep (paused)",
}
#: Books held without a stop by design (policy.stopless_strategies): never flagged as naked.
#: "SPY core" removed 2026-10-30: it carries a stop now (plan_run.core_stop_pass).
STOPLESS = {"Momentum"}
#: Tiles shown even when the book is empty.
MAIN_BOOKS = ("SPY core", "Rules book", "Small caps", "ORB")


def slot_limits(root: Path) -> dict:
    """{label: (prefixes, cap)} from the project's own policy, so the page cannot drift from it."""
    try:
        sys.path.insert(0, str(root / "src"))
        from scanner.execution.policy import TradingPolicy  # type: ignore

        p = TradingPolicy()
        caps = (p.gemma_max_positions, p.small_max_positions, p.orb_max_positions)
    except Exception:
        caps = (5, 4, 3)
    return {"Rules book": (("gem",), caps[0]), "Small caps": (("sml",), caps[1]), "ORB": (("orb",), caps[2])}

# The running scheduled tasks and how stale each may get before it is flagged.
# (log name, label, max age in hours on a weekday, runs on weekends?, judged only in session?)
TASKS = [
    ("scan", "Stop protection every 30 min", 1.0, False, True),
    ("rules_pick", "9:45 picks: rules book + small caps", 26, False, False),
    ("orb", "ORB day book every 5 min", 0.5, False, True),
    ("peak_watch", "Peak watch: exits + 10:00 buys, every 5 min 24/5", 0.5, False, False),
    ("health", "Health check 06:00 / 14:15", 17, False, False),
    ("flatten", "Flatten before closures", 26, False, False),
    ("preclose", "Pre-close four-sign check 15:40 ET", 26, False, False),
    ("gemma_desk_plan", "Gemma desk plan 08:30 ET (shadow)", 26, False, False),
    ("claude_plan", "Claude desk plan 08:45 ET (shadow)", 26, False, False),
    ("claude_settle", "Claude desk settle 17:20 ET", 26, False, False),
    ("gemma_desk_settle", "Gemma desk settle 17:25 ET", 26, False, False),
    ("claude_sunday", "Claude Sunday read 18:30 ET Sun", 170, True, False),
    ("data", "Intraday bars 13:00", 26, False, False),
    ("outcomes", "Outcomes, standings, penny shadow 13:30", 26, False, False),
]
SESSION_HOURS_AK = (5.5, 12.1)  # regular session: in-session tasks are only judged inside it

#: Case-sensitive on purpose: "0 failed" in a summary line is not an error.
ERROR_RE = re.compile(r"Traceback|\bERROR\b|Exception:|\bFAILED\b")
#: Where a task's latest run starts. rules_pick writes two blocks a run (RULES PICK, SMALL PICK).
RUN_HEADERS = {"rules_pick": "===== RULES PICK"}
HEALTH_VERDICT = re.compile(r"^health \S+ \S+\s+(ALL OK|\d+ PROBLEMS?)")


def last_run(lines: list[str], header: str = "=====") -> list[str]:
    """The lines of a task log's latest run: from its last run header to the end."""
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].startswith(header):
            return lines[i:]
    return lines[-80:]
REFUSAL_HINTS = ("refus", "reject", "skip", "blocked")


# ============================================================================
# Small helpers
# ============================================================================
def now_ak() -> dt.datetime:
    try:
        from zoneinfo import ZoneInfo

        return dt.datetime.now(ZoneInfo("America/Anchorage"))
    except Exception:
        return dt.datetime.now(AK)


def esc(x) -> str:
    return html.escape("" if x is None else str(x))


def fnum(x, digits=2, sign=False, pct=False, money=False) -> str:
    if x is None:
        return "–"
    try:
        v = float(x)
    except (TypeError, ValueError):
        return esc(x)
    s = f"{v:+,.{digits}f}" if sign else f"{v:,.{digits}f}"
    if money:
        s = ("-$" + s[1:].lstrip("+")) if s.startswith("-") else ("+$" if sign else "$") + s.lstrip("+")
    if pct:
        s += "%"
    return s


def tone(v) -> str:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return ""
    return "up" if v > 0 else "down" if v < 0 else ""


def ago(t: dt.datetime | None, now: dt.datetime) -> str:
    if t is None:
        return "never"
    s = (now - t).total_seconds()
    if s < 90:
        return "just now"
    if s < 5400:
        return f"{int(s // 60)} min ago"
    if s < 172800:
        return f"{s / 3600:.1f} h ago"
    return f"{s / 86400:.1f} days ago"


def load_env(root: Path) -> dict:
    env = dict(os.environ)
    p = root / ".env"
    if p.exists():
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return env


def tail_lines(path: Path, n: int = 400) -> list[str]:
    try:
        with path.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 256 * 1024))
            data = f.read().decode("utf-8", errors="replace")
        return data.splitlines()[-n:]
    except OSError:
        return []


# ============================================================================
# Sources
# ============================================================================
class Broker:
    """GET-only Alpaca paper client. There is no method that writes."""

    def __init__(self, env: dict, fixture: Path | None = None):
        self.fixture = json.loads(fixture.read_text()) if fixture else None
        self.key = env.get("SCANNER_ALPACA_KEY_ID") or env.get("APCA_API_KEY_ID")
        self.secret = env.get("SCANNER_ALPACA_SECRET_KEY") or env.get("APCA_API_SECRET_KEY")
        self.error = None
        if not self.fixture and not (self.key and self.secret):
            self.error = "no Alpaca paper keys in .env"

    def get(self, path: str, base: str = PAPER_URL):
        if self.fixture is not None:
            return self.fixture.get(path.split("?")[0])
        if self.error:
            return None
        req = urllib.request.Request(
            base + path,
            headers={"APCA-API-KEY-ID": self.key, "APCA-API-SECRET-KEY": self.secret},
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read().decode())
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            if base == PAPER_URL:
                self.error = f"Alpaca: {e}"
            return None


def session_day(ts: int) -> str:
    """Alpaca's daily portfolio-history stamp as the New York session date it belongs to."""
    try:
        from zoneinfo import ZoneInfo

        return dt.datetime.fromtimestamp(ts, tz=ZoneInfo("America/New_York")).date().isoformat()
    except Exception:
        return (dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc) - dt.timedelta(hours=4)).date().isoformat()


def vs_index(history: list[tuple[str, float]], closes: dict[str, dict[str, float]]) -> dict[str, list[tuple[str, float]]]:
    """% change from the first day shown, for the account and each index on the same days."""
    if not history:
        return {}
    base_day = history[0][0]
    out = {"Account": [(d, (v / history[0][1] - 1) * 100) for d, v in history]}
    for sym, by_day in closes.items():
        if base_day in by_day:
            out[sym] = [(d, (by_day[d] / by_day[base_day] - 1) * 100) for d, _ in history if d in by_day]
    return {k: v for k, v in out.items() if len(v) >= 2}


def read_audit(root: Path, n: int = 4000) -> list[dict]:
    out = []
    for line in tail_lines(root / "logs" / "trading_audit.jsonl", n):
        try:
            rec = json.loads(line)
            if isinstance(rec, dict):
                out.append(rec)
        except ValueError:
            continue
    return out


def rec_time(rec: dict) -> dt.datetime | None:
    for k in ("ts", "timestamp", "time", "at", "created_at", "logged_at"):
        v = rec.get(k)
        if isinstance(v, str):
            try:
                t = dt.datetime.fromisoformat(v.replace("Z", "+00:00"))
                return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)
            except ValueError:
                continue
    return None


def rec_kind(rec: dict) -> str:
    for k in ("event", "kind", "type", "record", "stage"):
        if isinstance(rec.get(k), str):
            return rec[k]
    return "record"


def rec_reason(rec: dict) -> str | None:
    for k in ("reason", "refusal", "rule", "why", "gate"):
        v = rec.get(k)
        if isinstance(v, str) and v:
            return v
    return None


def book_of(coid: str | None) -> str:
    if not coid:
        return "Not ours"
    return BOOKS.get(coid.split("-", 1)[0].lower(), "Not ours")


def kill_switch(root: Path) -> tuple[bool | None, str]:
    """Ask the project's own settings where the kill switch file is."""
    path = None
    try:
        sys.path.insert(0, str(root / "src"))
        from scanner.config import settings  # type: ignore

        path = Path(getattr(settings, "kill_switch_path"))
    except Exception:
        pass
    if path is None:
        for guess in ("STOP_TRADING", "KILL_SWITCH", "data/KILL_SWITCH"):
            if (root / guess).exists():
                return True, guess
        return None, "settings.kill_switch_path not found"
    if not path.is_absolute():
        path = root / path
    return path.exists(), str(path.relative_to(root) if path.is_relative_to(root) else path)


def read_freeze(root: Path) -> str | None:
    p = root / "FREEZE.md"
    if not p.exists():
        return None
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines()[:40]:
        s = line.strip().lstrip("#").strip()
        if re.search(r"\bwindow\b", s, re.I) and len(s) > 6:
            return re.sub(r"[*`_]", "", s)[:140]
    return None


def desk_rows(root: Path) -> str:
    """One table row per research desk from its book.json (read only)."""
    rows = []
    for name, path in (("Claude", root / "data" / "claude" / "book.json"),
                       ("Gemma", root / "data" / "gemma_desk" / "book.json")):
        try:
            b = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            rows.append(f"<tr><td>{name}</td><td colspan='5' class='muted'>no shadow trades played yet</td></tr>")
            continue
        marks = b.get("marks") or []
        value = float(marks[-1][1]) if marks else 30000.0
        closed = b.get("closed") or []
        won = sum(1 for c in closed if float(c.get("exit", 0)) > float(c.get("entry", 0)))
        held = ", ".join(p.get("symbol", "") for p in b.get("open") or []) or "-"
        rows.append(f"<tr><td>{name}</td><td class='num'>{fnum(value, money=True)}</td>"
                    f"<td class='num {tone(value - 30000)}'>{fnum((value / 30000 - 1) * 100, sign=True, pct=True)}</td>"
                    f"<td class='num'>{len(closed)} ({won} won)</td><td>{esc(held)}</td><td>{esc(b.get('through') or '-')}</td></tr>")
    return ("<div class='tablewrap'><table><thead><tr><th>Desk</th><th class='num'>Value</th><th class='num'>Return</th>"
            "<th class='num'>Closed</th><th>Open</th><th>Through</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>")


def task_health(root: Path, now: dt.datetime) -> list[dict]:
    rows = []
    weekend = now.weekday() >= 5
    hour = now.hour + now.minute / 60
    for name, label, max_h, on_weekend, in_session in TASKS:
        p = root / "logs" / f"scheduled_{name}.log"
        if not p.exists():
            rows.append(dict(name=name, label=label, status="missing", last=None, errors=0, note="no log file"))
            continue
        last = dt.datetime.fromtimestamp(p.stat().st_mtime, tz=dt.timezone.utc).astimezone(now.tzinfo)
        age_h = (now - last).total_seconds() / 3600
        run = last_run(tail_lines(p, 2000), RUN_HEADERS.get(name, "====="))
        errs = [l for l in run if ERROR_RE.search(l)]
        if name == "health":       # its report quotes other logs' errors: use its own verdict
            verdict = next((l for l in reversed(run) if HEALTH_VERDICT.match(l)), "")
            errs = [verdict] if verdict and "ALL OK" not in verdict else []
        allowance = max_h
        if weekend and not on_weekend:
            allowance = max_h + 48
        if in_session and not (SESSION_HOURS_AK[0] <= hour <= SESSION_HOURS_AK[1]) and not weekend:
            allowance = 20  # outside the session the last run was at the close
        if weekend and max_h < 26:
            allowance = 70  # frequent tasks idle from Friday's close to Sunday night
        status = "good"
        note = ""
        if age_h > allowance:
            status, note = "critical", f"older than {allowance:g} h"
        elif errs:
            status, note = "serious", errs[-1].strip()[:160]
        rows.append(dict(name=name, label=label, status=status, last=last, errors=len(errs), note=note))
    return rows


def md_to_html(md: str) -> str:
    """Enough markdown for standings.md: headings, tables, lists, bold, code."""
    out, table, inlist = [], [], False

    def inline(s: str) -> str:
        s = esc(s)
        s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
        s = re.sub(r"`(.+?)`", r"<code>\1</code>", s)
        return s

    def flush_table():
        if not table:
            return
        rows = [r for r in table if not re.fullmatch(r"\|?[\s:|-]+\|?", r)]
        cells = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows]
        h = "".join(f"<th>{inline(c)}</th>" for c in cells[0])
        b = "".join(
            "<tr>" + "".join(f"<td class='{'num' if re.fullmatch(r'[-+−–$0-9.,%R ]+', c) else ''}'>{inline(c)}</td>" for c in row) + "</tr>"
            for row in cells[1:]
        )
        out.append(f"<div class='tablewrap'><table><thead><tr>{h}</tr></thead><tbody>{b}</tbody></table></div>")
        table.clear()

    for line in md.splitlines():
        if line.strip().startswith("|"):
            table.append(line)
            continue
        flush_table()
        if inlist and not line.lstrip().startswith(("- ", "* ")):
            out.append("</ul>")
            inlist = False
        m = re.match(r"(#{1,4})\s+(.*)", line)
        if m:
            out.append(f"<h4>{inline(m.group(2))}</h4>")
        elif line.lstrip().startswith(("- ", "* ")):
            if not inlist:
                out.append("<ul>")
                inlist = True
            out.append(f"<li>{inline(line.lstrip()[2:])}</li>")
        elif line.strip():
            out.append(f"<p>{inline(line)}</p>")
    flush_table()
    if inlist:
        out.append("</ul>")
    return "\n".join(out)


# ============================================================================
# Gather
# ============================================================================
def gather(root: Path, broker: Broker | None) -> dict:
    now = now_ak()
    d: dict = {"now": now, "root": root}

    # --- broker ---------------------------------------------------------------
    acct = pos = orders = hist = clock = None
    if broker is not None:
        acct = broker.get("/v2/account")
        pos = broker.get("/v2/positions") or []
        orders = broker.get("/v2/orders?status=all&limit=500&nested=true&direction=desc") or []
        hist = broker.get("/v2/account/portfolio/history?period=1M&timeframe=1D")
        clock = broker.get("/v2/clock")
    d["broker_error"] = broker.error if broker else "skipped (--no-broker)"
    d["account"] = acct
    d["clock"] = clock

    # flatten nested bracket legs
    flat = []
    for o in orders or []:
        flat.append(o)
        flat.extend(o.get("legs") or [])

    # owner of each held symbol = prefix of its most recent filled buy
    owner = {}
    for o in sorted(flat, key=lambda o: o.get("filled_at") or o.get("created_at") or "", reverse=True):
        sym = o.get("symbol")
        if sym and sym not in owner and o.get("side") == "buy" and o.get("status") in ("filled", "partially_filled"):
            owner[sym] = book_of(o.get("client_order_id"))
    # also pending buys (so an unfilled order shows its book)
    live_stops = defaultdict(list)
    for o in flat:
        if o.get("side") == "sell" and o.get("type") in ("stop", "stop_limit", "trailing_stop") and o.get("status") in (
            "new", "accepted", "held", "pending_new", "partially_filled", "accepted_for_bidding",
        ):
            if o.get("stop_price"):
                live_stops[o["symbol"]].append(float(o["stop_price"]))

    # stops logged for extended hours but not yet armed (audit intent)
    audit = read_audit(root)
    intent_stop = {}
    for r in audit:
        sym = r.get("symbol")
        sp = r.get("stop") or r.get("stop_price")
        if sym and isinstance(sp, (int, float)):
            intent_stop[sym] = float(sp)

    positions = []
    for p in pos or []:
        sym = p.get("symbol")
        price = float(p.get("current_price") or 0)
        stops = live_stops.get(sym, [])
        stop = max(stops) if stops else None
        positions.append(dict(
            symbol=sym,
            book=owner.get(sym, "Not ours"),
            qty=float(p.get("qty") or 0),
            entry=float(p.get("avg_entry_price") or 0),
            price=price,
            value=float(p.get("market_value") or 0),
            upl=float(p.get("unrealized_pl") or 0),
            uplpc=float(p.get("unrealized_plpc") or 0) * 100,
            today=float(p.get("unrealized_intraday_pl") or 0),
            stop=stop,
            stop_live=stop is not None,
            logged_stop=intent_stop.get(sym),
            to_stop=((price - stop) / price * 100) if (stop and price) else None,
        ))
    positions.sort(key=lambda r: (r["book"], -r["value"]))
    d["positions"] = positions

    # --- account figures ------------------------------------------------------
    if acct:
        eq = float(acct.get("equity") or 0)
        last = float(acct.get("last_equity") or eq)
        d["equity"] = eq
        d["day_pl"] = eq - last
        d["day_pc"] = (eq - last) / last * 100 if last else 0
        d["cash"] = float(acct.get("cash") or 0)
        d["acct_status"] = acct.get("status")
    d["history"] = None
    d["index_closes"] = {}
    if hist and hist.get("timestamp"):
        pts = [(session_day(t), float(e)) for t, e in zip(hist["timestamp"], hist["equity"]) if e]
        d["history"] = pts or None
        if pts and broker is not None and broker.fixture is None:
            for sym in INDEXES:
                bars = broker.get(f"/v2/stocks/{sym}/bars?timeframe=1Day&start={pts[0][0]}&feed=iex", base=DATA_URL)
                d["index_closes"][sym] = {b["t"][:10]: float(b["c"]) for b in (bars or {}).get("bars") or []}

    # per-book open P/L and slots
    by_book = defaultdict(lambda: dict(n=0, value=0.0, upl=0.0, today=0.0))
    for p in positions:
        b = by_book[p["book"]]
        b["n"] += 1
        b["value"] += p["value"]
        b["upl"] += p["upl"]
        b["today"] += p["today"]
    d["by_book"] = dict(by_book)
    slots = []
    for label, (prefixes, cap) in slot_limits(root).items():
        names = {BOOKS[x] for x in prefixes}
        used = sum(v["n"] for k, v in by_book.items() if k in names)
        slots.append((label, used, cap))
    d["slots"] = slots

    # --- safety ----------------------------------------------------------------
    d["kill"], d["kill_path"] = kill_switch(root)
    d["naked"] = [p["symbol"] for p in positions if not p["stop_live"] and p["book"] not in STOPLESS]
    d["freeze"] = read_freeze(root)
    d["tasks"] = task_health(root, now)

    # --- standings and the penny shadow -------------------------------------------
    d["series_src"] = "Alpaca portfolio history + SPY / IWM daily bars"
    for name, p in (("standings", root / "logs" / "competition" / "standings.md"),
                    ("penny", root / "logs" / "shadow" / "penny.md"),
                    ("preclose", root / "logs" / "preclose" / "latest.md"),
                    ("sunday", root / "logs" / "claude" / "sunday.md")):
        d[name] = md_to_html(p.read_text(encoding="utf-8", errors="replace")) if p.exists() else None
        d[name + "_time"] = (
            dt.datetime.fromtimestamp(p.stat().st_mtime, tz=dt.timezone.utc).astimezone(now.tzinfo) if p.exists() else None
        )

    # --- audit feed --------------------------------------------------------------
    today = now.date()
    feed, refusals = [], Counter()
    for r in audit[-400:]:
        t = rec_time(r)
        kind = rec_kind(r)
        reason = rec_reason(r)
        if t and t.astimezone(now.tzinfo).date() == today and (
            any(h in kind.lower() for h in REFUSAL_HINTS) or (r.get("refused") is True)
        ):
            key = reason or kind
            if r.get("symbol"):
                key = key.replace(str(r["symbol"]), "(symbol)")
            refusals[key] += 1
        feed.append(dict(
            t=t, kind=kind, symbol=r.get("symbol") or "", reason=reason or "",
            book=book_of(r.get("client_order_id")) if r.get("client_order_id") else "",
        ))
    d["feed"] = list(reversed(feed[-40:]))
    d["refusals"] = refusals.most_common(8)
    d["audit_n"] = len(audit)
    return d


# ============================================================================
# Render
# ============================================================================
SERIES_STYLE = {  # slot order of the validated palette; the indexes dashed
    "Account": ("--s1", ""),
    "SPY": ("--ink-3", "6 4"),
    "IWM": ("--s3", "2 4"),
}


def line_chart(cid: str, series: dict[str, list[tuple[str, float]]], unit: str, height=260) -> str:
    """Inline SVG line chart. Hover data is embedded as JSON for the crosshair."""
    dates = sorted({d for s in series.values() for d, _ in s})
    if len(dates) < 2:
        return "<p class='muted'>Not enough points yet.</p>"
    vals = [v for s in series.values() for _, v in s]
    lo, hi = min(vals + [0]), max(vals + [0])
    pad = (hi - lo) * 0.08 or 1
    lo, hi = lo - pad, hi + pad
    W, H, L, R, T, B = 760, height, 52, 92, 12, 26
    x = lambda i: L + (W - L - R) * i / (len(dates) - 1)
    y = lambda v: T + (H - T - B) * (hi - v) / (hi - lo)
    idx = {d: i for i, d in enumerate(dates)}

    # ticks
    span = hi - lo
    step = 10 ** int(f"{span / 4:e}".split("e")[1])
    for m in (1, 2, 2.5, 5, 10):
        if span / (step * m) <= 5:
            step *= m
            break
    ticks, t = [], (lo // step + 1) * step
    while t < hi:
        ticks.append(round(t, 10))
        t += step
    g = []
    for t in ticks:
        cls = "base" if abs(t) < 1e-9 else "grid"
        g.append(f"<line class='{cls}' x1='{L}' x2='{W - R}' y1='{y(t):.1f}' y2='{y(t):.1f}'/>")
        g.append(f"<text class='tick' x='{L - 8}' y='{y(t) + 4:.1f}' text-anchor='end'>{t:g}{unit}</text>")
    n = len(dates)
    for i in sorted({0, n // 2, n - 1}):
        g.append(f"<text class='tick' x='{x(i):.1f}' y='{H - 6}' text-anchor='middle'>{dates[i][5:]}</text>")

    lines, labels = [], []
    ends = []
    for name, pts in series.items():
        var, dash = SERIES_STYLE.get(name, ("--ink-3", ""))
        path = " ".join(f"{'M' if k == 0 else 'L'}{x(idx[d]):.1f},{y(v):.1f}" for k, (d, v) in enumerate(pts))
        da = f" stroke-dasharray='{dash}'" if dash else ""
        lines.append(f"<path d='{path}' fill='none' stroke='var({var})' stroke-width='2' stroke-linejoin='round' stroke-linecap='round'{da}/>")
        d_last, v_last = pts[-1]
        ends.append([y(v_last), name, v_last, var, x(idx[d_last])])
    # direct labels at line ends, nudged apart
    ends.sort()
    for i in range(1, len(ends)):
        if ends[i][0] - ends[i - 1][0] < 15:
            ends[i][0] = ends[i - 1][0] + 15
    for yy, name, v, var, xx in ends:
        labels.append(
            f"<circle cx='{xx:.1f}' cy='{y(v):.1f}' r='3.5' fill='var({var})' stroke='var(--surface)' stroke-width='2'/>"
            f"<text class='endlbl' x='{W - R + 8}' y='{yy + 4:.1f}'>{esc(name)} <tspan class='endval'>{v:+.2f}{unit}</tspan></text>"
        )

    data = {
        "dates": dates,
        "unit": unit,
        "series": [
            {"name": n, "var": SERIES_STYLE.get(n, ("--ink-3", ""))[0], "dash": bool(SERIES_STYLE.get(n, ("", ""))[1]),
             "pts": {d: v for d, v in s}}
            for n, s in series.items()
        ],
        "geom": [L, R, T, B, W, H, lo, hi],
    }
    legend = "" if len(series) < 2 else "".join(
        f"<span class='lg'><svg width='22' height='8'><line x1='1' x2='21' y1='4' y2='4' stroke='var({SERIES_STYLE.get(n, ('--ink-3',''))[0]})' "
        f"stroke-width='2' {('stroke-dasharray=' + chr(39) + SERIES_STYLE[n][1] + chr(39)) if SERIES_STYLE.get(n, ('', ''))[1] else ''}/></svg>{esc(n)}</span>"
        for n in series
    )
    return f"""
<div class="legend">{legend}</div>
<div class="chart" id="{cid}" data-chart='{esc(json.dumps(data))}'>
  <svg viewBox="0 0 {W} {H}" preserveAspectRatio="xMidYMid meet" role="img" aria-label="Cumulative return by book">
    {''.join(g)}{''.join(lines)}{''.join(labels)}
    <line class="xhair" x1="0" x2="0" y1="{T}" y2="{H - B}" style="display:none"/>
    <rect class="hit" x="{L}" y="{T}" width="{W - L - R}" height="{H - T - B}" fill="transparent"/>
  </svg>
  <div class="tip" style="display:none"></div>
</div>"""


def status_pill(state: str, label: str, detail: str = "") -> str:
    icon = {"good": "✓", "warning": "!", "serious": "!", "critical": "✕", "unknown": "?", "missing": "?"}[state]
    return f"<div class='pill {state}' title='{esc(detail)}'><span class='ic'>{icon}</span><span>{esc(label)}</span></div>"


def render(d: dict) -> str:
    now = d["now"]
    pills = []

    # kill switch
    if d["kill"] is None:
        pills.append(status_pill("unknown", "Kill switch: unknown", d["kill_path"]))
    elif d["kill"]:
        pills.append(status_pill("critical", "Kill switch ON — no new entries", d["kill_path"]))
    else:
        pills.append(status_pill("good", "Kill switch off", d["kill_path"]))

    # stops
    if d.get("account") is None:
        pills.append(status_pill("unknown", "Stops: no broker data", d["broker_error"] or ""))
    elif d["naked"]:
        pills.append(status_pill("critical", f"No live stop: {', '.join(d['naked'])}", "protect.py arms these every 30 min in the session"))
    else:
        held = [p for p in d["positions"] if p["book"] not in STOPLESS]
        pills.append(status_pill("good", f"Every position has a live stop ({len(held)})"))

    # daily loss
    if "day_pc" in d:
        dp = d["day_pc"]
        st = "critical" if dp <= -3 else "warning" if dp <= -2 else "good"
        pills.append(status_pill(st, f"Day {dp:+.2f}% (stop at −3%)"))

    # tasks
    bad = [t for t in d["tasks"] if t["status"] in ("critical", "serious")]
    miss = [t for t in d["tasks"] if t["status"] == "missing"]
    if bad:
        pills.append(status_pill("serious", f"{len(bad)} task(s) need you", ", ".join(t["name"] for t in bad)))
    else:
        pills.append(status_pill("good", f"Tasks on time ({len(d['tasks']) - len(miss)})"))

    # market clock
    clock = d.get("clock")
    if clock:
        pills.append(status_pill("good" if clock.get("is_open") else "unknown",
                                 "Market open" if clock.get("is_open") else "Market closed (regular hours)"))

    # ---- KPI tiles ----------------------------------------------------------
    tiles = []
    if "equity" in d:
        tiles.append(("Account equity", fnum(d["equity"], 0, money=True),
                      f"<span class='{tone(d['day_pl'])}'>{fnum(d['day_pl'], 0, sign=True, money=True)} today</span>"))
    extra = [k for k in d.get("by_book", {}) if k not in MAIN_BOOKS]
    for bk in (*MAIN_BOOKS, *extra):
        b = d.get("by_book", {}).get(bk)
        if b or bk in MAIN_BOOKS:
            b = b or dict(n=0, value=0, upl=0, today=0)
            tiles.append((f"{bk} — open P/L",
                          f"<span class='{tone(b['upl'])}'>{fnum(b['upl'], 0, sign=True, money=True)}</span>",
                          f"{b['n']} position(s) · {fnum(b['value'], 0, money=True)} · "
                          f"<span class='{tone(b['today'])}'>{fnum(b['today'], 0, sign=True, money=True)} today</span>"))
    for label, used, cap in d.get("slots", []):
        pct = min(100, used / cap * 100) if cap else 0
        tiles.append((f"Slots — {label}", f"{used} <span class='of'>/ {cap}</span>",
                      f"<div class='meter'><div style='width:{pct:.0f}%'></div></div>"))
    tiles_html = "".join(
        f"<div class='tile'><div class='tl'>{esc(a)}</div><div class='tv'>{b}</div><div class='ts'>{c}</div></div>"
        for a, b, c in tiles
    ) or "<p class='muted'>No account data.</p>"

    # ---- the account against the index ---------------------------------------------
    series = vs_index(d.get("history") or [], d.get("index_closes") or {})
    if series:
        comp_chart = line_chart("comp", series, "%")
    else:
        comp_chart = f"<p class='muted'>No portfolio history ({esc(d['broker_error'] or 'empty')}).</p>"
    standings = d["standings"] or "<p class='muted'>logs/competition/standings.md not found — run <code>python scripts/competition.py standings</code>.</p>"
    penny = d["penny"] or "<p class='muted'>logs/shadow/penny.md not found — run <code>python scripts/penny_shadow.py</code>.</p>"
    sunday = d["sunday"] or "<p class='muted'>No Sunday read yet — Claude writes it Sundays 18:30 ET.</p>"
    desks = desk_rows(d["root"])
    preclose = d["preclose"] or "<p class='muted'>No pre-close check yet — it runs 15:40 ET on session days.</p>"

    # ---- positions ---------------------------------------------------------------
    if d["positions"]:
        rows = []
        for p in d["positions"]:
            if p["stop_live"]:
                dist = p["to_stop"] or 0
                w = max(2, min(100, dist / 10 * 100))  # bar: 0..10% from stop
                cls = "warn" if dist < 1.5 else ""
                stop_cell = (f"<td class='num'>{fnum(p['stop'])}</td>"
                             f"<td><div class='dist {cls}' title='{dist:.2f}% above the stop'><div style='width:{w:.0f}%'></div></div>"
                             f"<span class='num small'>{dist:.1f}%</span></td>")
            elif p["book"] in STOPLESS:
                stop_cell = "<td colspan='2'><span class='muted small'>no stop by design</span></td>"
            else:
                note = f"logged {fnum(p['logged_stop'])}, not armed" if p["logged_stop"] else "none"
                stop_cell = f"<td colspan='2'><span class='flag critical'>✕ no live stop</span> <span class='muted small'>{esc(note)}</span></td>"
            rows.append(
                f"<tr><td><span class='book b-{esc(p['book'].split()[0].lower())}'>{esc(p['book'])}</span></td>"
                f"<td><b>{esc(p['symbol'])}</b></td><td class='num'>{fnum(p['qty'], 0)}</td>"
                f"<td class='num'>{fnum(p['entry'])}</td><td class='num'>{fnum(p['price'])}</td>"
                f"<td class='num'>{fnum(p['value'], 0, money=True)}</td>"
                f"<td class='num {tone(p['upl'])}'>{fnum(p['upl'], 0, sign=True, money=True)}</td>"
                f"<td class='num {tone(p['uplpc'])}'>{fnum(p['uplpc'], 1, sign=True, pct=True)}</td>"
                f"<td class='num {tone(p['today'])}'>{fnum(p['today'], 0, sign=True, money=True)}</td>"
                f"{stop_cell}</tr>"
            )
        pos_html = (
            "<div class='tablewrap'><table class='pos'><thead><tr><th>Book</th><th>Symbol</th><th class='num'>Qty</th>"
            "<th class='num'>Entry</th><th class='num'>Last</th><th class='num'>Value</th><th class='num'>Open P/L</th>"
            "<th class='num'>%</th><th class='num'>Today</th><th class='num'>Stop</th><th>Room to stop</th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table></div>"
        )
    else:
        pos_html = f"<p class='muted'>No open positions{(' — ' + esc(d['broker_error'])) if d['broker_error'] else ''}.</p>"

    # ---- tasks ---------------------------------------------------------------------
    trows = "".join(
        f"<tr><td><span class='dot {t['status']}'></span>{esc(t['label'])}</td>"
        f"<td class='muted small'>{esc(t['name'])}</td>"
        f"<td class='num'>{esc(t['last'].strftime('%a %H:%M') if t['last'] else '–')}</td>"
        f"<td class='muted'>{esc(ago(t['last'], now))}</td>"
        f"<td class='small {'err' if t['status'] in ('critical', 'serious') else 'muted'}'>{esc(t['note'] or ('ok' if t['status'] == 'good' else ''))}</td></tr>"
        for t in d["tasks"]
    )

    # ---- audit -----------------------------------------------------------------------
    feed = "".join(
        f"<tr><td class='num muted'>{esc(r['t'].astimezone(now.tzinfo).strftime('%m-%d %H:%M') if r['t'] else '')}</td>"
        f"<td>{esc(r['kind'])}</td><td><b>{esc(r['symbol'])}</b></td><td class='muted small'>{esc(r['book'])}</td>"
        f"<td class='muted small'>{esc(r['reason'][:90])}</td></tr>"
        for r in d["feed"]
    ) or "<tr><td class='muted'>No audit records.</td></tr>"
    refusals = "".join(
        f"<div class='rf'><span>{esc(k[:60])}</span><span class='num'>{n}</span>"
        f"<div class='rfbar'><div style='width:{n / d['refusals'][0][1] * 100:.0f}%'></div></div></div>"
        for k, n in d["refusals"]
    ) or "<p class='muted small'>No refusals logged today.</p>"

    def src(s):
        return f"<div class='src'>Source: {s}</div>"

    stand_t = f"written {ago(d['standings_time'], now)}" if d.get("standings_time") else ""
    return TEMPLATE.format(
        css=CSS, js=JS,
        when=esc(now.strftime("%a %b %d, %H:%M AK")),
        window=esc(d["freeze"] or "FREEZE.md: no window line found"),
        pills="".join(pills),
        tiles=tiles_html,
        comp_chart=comp_chart,
        comp_src=src(esc(d["series_src"])),
        standings=standings, stand_t=esc(stand_t), penny=penny, preclose=preclose, sunday=sunday, desks=desks,
        positions=pos_html,
        pos_src=src("Alpaca paper · positions + open stop orders · book from the latest buy's client_order_id prefix"),
        tasks=trows,
        refusals=refusals,
        feed=feed,
        audit_src=src(f"logs/trading_audit.jsonl · last {d['audit_n']} records read"),
    )


CSS = r"""
:root{
  color-scheme:light;
  --page:#f9f9f7;--surface:#fcfcfb;--ink-1:#0b0b0b;--ink-2:#52514e;--ink-3:#898781;
  --grid:#e1e0d9;--axis:#c3c2b7;--ring:rgba(11,11,11,.10);
  --s1:#2a78d6;--s2:#eb6834;--s3:#1baf7a;
  --good:#0ca30c;--warning:#fab219;--serious:#ec835a;--critical:#d03b3b;
  --up:#006300;--down:#d03b3b;--wash:rgba(11,11,11,.04);
}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])){
  color-scheme:dark;
  --page:#0d0d0d;--surface:#1a1a19;--ink-1:#f0efec;--ink-2:#c3c2b7;--ink-3:#898781;
  --grid:#2c2c2a;--axis:#383835;--ring:rgba(255,255,255,.10);
  --s1:#3987e5;--s2:#d95926;--s3:#199e70;--up:#0ca30c;--down:#e66767;--wash:rgba(255,255,255,.05);
}}
:root[data-theme="dark"]{
  color-scheme:dark;
  --page:#0d0d0d;--surface:#1a1a19;--ink-1:#f0efec;--ink-2:#c3c2b7;--ink-3:#898781;
  --grid:#2c2c2a;--axis:#383835;--ring:rgba(255,255,255,.10);
  --s1:#3987e5;--s2:#d95926;--s3:#199e70;--up:#0ca30c;--down:#e66767;--wash:rgba(255,255,255,.05);
}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--ink-1);font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
.wrap{max-width:1280px;margin:0 auto;padding:20px 16px 48px}
header{display:flex;flex-wrap:wrap;align-items:baseline;gap:6px 16px;margin-bottom:14px}
h1{font-size:20px;margin:0;letter-spacing:-.01em}
.sub{color:var(--ink-2);font-size:13px}
.right{margin-left:auto;display:flex;gap:8px;align-items:center}
button{font:inherit;font-size:12px;color:var(--ink-2);background:var(--surface);border:1px solid var(--ring);border-radius:8px;padding:4px 10px;cursor:pointer}
.pills{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:16px}
.pill{display:flex;align-items:center;gap:8px;background:var(--surface);border:1px solid var(--ring);border-radius:999px;padding:5px 12px 5px 6px;font-size:13px}
.pill .ic{width:20px;height:20px;border-radius:50%;display:grid;place-items:center;font-size:11px;font-weight:700;color:#fff}
.pill.good .ic{background:var(--good)}.pill.warning .ic{background:var(--warning);color:#0b0b0b}
.pill.serious .ic{background:var(--serious)}.pill.critical .ic{background:var(--critical)}
.pill.unknown .ic,.pill.missing .ic{background:var(--ink-3)}
.pill.critical{border-color:var(--critical);font-weight:600}
.tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:12px;margin-bottom:16px}
.tile,.card{background:var(--surface);border:1px solid var(--ring);border-radius:12px}
.tile{padding:14px 16px}
.tl{font-size:12px;color:var(--ink-2)}
.tv{font-size:26px;font-weight:600;letter-spacing:-.02em;margin:2px 0}
.tv .of{font-size:15px;color:var(--ink-3);font-weight:400}
.ts{font-size:12px;color:var(--ink-2)}
.up{color:var(--up)}.down{color:var(--down)}
.meter{height:6px;background:var(--grid);border-radius:3px;margin-top:8px;overflow:hidden}
.meter div{height:100%;background:var(--s1);border-radius:3px}
.grid2{display:grid;grid-template-columns:minmax(0,1.6fr) minmax(0,1fr);gap:16px;margin-bottom:16px}
@media(max-width:900px){.grid2{grid-template-columns:1fr}}
.card{padding:16px;margin-bottom:16px;min-width:0}
.grid2 .card{margin-bottom:0}
h2{font-size:15px;margin:0 0 2px}
.cap{color:var(--ink-2);font-size:12px;margin-bottom:10px}
h4{font-size:13px;margin:12px 0 6px;color:var(--ink-1)}
.src{color:var(--ink-3);font-size:11px;margin-top:10px}
.muted{color:var(--ink-3)}.small{font-size:12px}
.tablewrap{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:13px}
th{text-align:left;font-weight:500;color:var(--ink-2);font-size:12px;border-bottom:1px solid var(--axis);padding:6px 8px;white-space:nowrap}
td{padding:6px 8px;border-bottom:1px solid var(--grid);vertical-align:middle}
tr:hover td{background:var(--wash)}
.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
th.num{text-align:right}
code{font-size:12px;background:var(--wash);padding:1px 4px;border-radius:4px}
.book{font-size:11px;font-weight:600;border-radius:6px;padding:2px 7px;border:1px solid var(--ring);white-space:nowrap}
.book::before{content:"";display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:5px;background:currentColor;vertical-align:0}
.book{color:var(--ink-1)} .b-spy::before{background:var(--ink-3)} .b-rules::before{background:var(--s1)}
.b-small::before{background:var(--s3)} .b-orb::before{background:var(--s2)}
.b-not::before,.b-sweep::before,.b-picks::before,.b-manual::before,.b-momentum::before{background:var(--ink-3)}
.dist{display:inline-block;width:70px;height:6px;background:var(--grid);border-radius:3px;overflow:hidden;vertical-align:middle;margin-right:6px}
.dist div{height:100%;background:var(--good);border-radius:3px}
.dist.warn div{background:var(--warning)}
.flag{font-size:12px;font-weight:600}.flag.critical{color:var(--critical)}
.dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:8px;vertical-align:0}
.dot.good{background:var(--good)}.dot.serious{background:var(--serious)}.dot.critical{background:var(--critical)}.dot.missing{background:var(--ink-3)}
.err{color:var(--critical)}
.legend{display:flex;flex-wrap:wrap;gap:4px 14px;font-size:12px;color:var(--ink-2);margin-bottom:6px}
.lg{display:flex;align-items:center;gap:6px}
.chart{position:relative}
.chart svg{width:100%;height:auto;display:block;overflow:visible}
.grid{stroke:var(--grid);stroke-width:1}.base{stroke:var(--axis);stroke-width:1}
.tick{fill:var(--ink-3);font-size:11px;font-variant-numeric:tabular-nums}
.endlbl{fill:var(--ink-1);font-size:12px;font-weight:500}.endval{fill:var(--ink-2);font-weight:400}
.xhair{stroke:var(--axis);stroke-width:1}
.tip{position:absolute;pointer-events:none;background:var(--surface);border:1px solid var(--ring);border-radius:8px;
     padding:8px 10px;font-size:12px;box-shadow:0 4px 16px rgba(0,0,0,.12);min-width:150px;z-index:2}
.tip .d{color:var(--ink-2);margin-bottom:4px}
.tip .r{display:flex;align-items:center;gap:8px}.tip .r span:last-child{margin-left:auto;font-variant-numeric:tabular-nums}
.tip .sw{width:10px;height:3px;border-radius:2px}
.rf{display:grid;grid-template-columns:1fr auto;gap:2px 8px;margin-bottom:8px;font-size:12px}
.rfbar{grid-column:1/3;height:5px;background:var(--grid);border-radius:3px;overflow:hidden}
.rfbar div{height:100%;background:var(--ink-3)}
.standings p{margin:4px 0}
.standings table{margin-bottom:6px}
.feedwrap{max-height:340px;overflow:auto}
footer{color:var(--ink-3);font-size:12px;margin-top:8px}
"""

JS = r"""
(function(){
  const root=document.documentElement;
  try{const t=localStorage.getItem('cc-theme'); if(t) root.setAttribute('data-theme',t);}catch(e){}
  document.getElementById('theme').addEventListener('click',()=>{
    const dark=getComputedStyle(root).colorScheme.includes('dark');
    const t=dark?'light':'dark'; root.setAttribute('data-theme',t);
    try{localStorage.setItem('cc-theme',t);}catch(e){}
  });
  document.querySelectorAll('.chart[data-chart]').forEach(el=>{
    const c=JSON.parse(el.dataset.chart), svg=el.querySelector('svg'), tip=el.querySelector('.tip'),
          xh=svg.querySelector('.xhair'), hit=svg.querySelector('.hit');
    const [L,R,T,B,W,H]=c.geom, n=c.dates.length;
    const xAt=i=>L+(W-L-R)*i/(n-1);
    function move(ev){
      const r=svg.getBoundingClientRect(), sx=(ev.clientX-r.left)*W/r.width;
      const i=Math.max(0,Math.min(n-1,Math.round((sx-L)/(W-L-R)*(n-1))));
      const d=c.dates[i], x=xAt(i);
      xh.setAttribute('x1',x);xh.setAttribute('x2',x);xh.style.display='';
      const rows=c.series.filter(s=>d in s.pts).sort((a,b)=>b.pts[d]-a.pts[d]).map(s=>{
        const v=s.pts[d];
        return `<div class="r"><span class="sw" style="background:var(${s.var});${s.dash?'opacity:.6':''}"></span><span>${s.name}</span><span>${(v>0?'+':'')+v.toFixed(2)}${c.unit}</span></div>`;}).join('');
      tip.innerHTML=`<div class="d">${d}</div>${rows}`;
      tip.style.display='';
      const px=x*r.width/W, left=px+14+tip.offsetWidth>r.width?px-14-tip.offsetWidth:px+14;
      tip.style.left=left+'px';tip.style.top='8px';
    }
    hit.addEventListener('mousemove',move);
    hit.addEventListener('touchstart',e=>move(e.touches[0]),{passive:true});
    hit.addEventListener('mouseleave',()=>{tip.style.display='none';xh.style.display='none';});
  });
})();
"""

TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Desk Command Center</title>
<style>{css}</style></head>
<body><div class="wrap">
<header>
  <h1>Desk command center</h1>
  <span class="sub">Paper account · {when}</span>
  <div class="right"><span class="sub">{window}</span><button id="theme" type="button">Light / dark</button></div>
</header>

<div class="pills">{pills}</div>
<div class="tiles">{tiles}</div>

<div class="grid2">
  <section class="card">
    <h2>The account vs the index, last month</h2>
    <div class="cap">% change from the first day shown. Dashed: SPY and IWM. Each book is judged over months against its index. Hover for any day.</div>
    {comp_chart}
    {comp_src}
  </section>
  <section class="card standings">
    <h2>Standings</h2>
    <div class="cap">As written by competition.py {stand_t}</div>
    {standings}
    <div class="src">Source: logs/competition/standings.md</div>
  </section>
</div>

<section class="card">
  <h2>Open positions</h2>
  <div class="cap">Room to stop: how far price sits above the live stop (bar full at 10%; amber under 1.5%).</div>
  {positions}
  {pos_src}
</section>

<section class="card standings">
  <h2>Sunday read (Claude, research only)</h2>
  {sunday}
  <div class="src">Source: logs/claude/sunday.md</div>
</section>

<section class="card">
  <h2>Research desks (shadow books, never trade)</h2>
  <div class="cap">Claude and Gemma get the same brief each morning; their calls play in $30,000 paper sleeves. Judged 2026-12-31 vs SPY and the rules book (FREEZE.md pass rule).</div>
  {desks}
  <div class="src">Source: data/claude/book.json, data/gemma_desk/book.json</div>
</section>

<section class="card standings">
  <h2>Pre-close check: four signs to sell</h2>
  <div class="cap">15:40 ET. Chart (under the 50-day and the 20-day low), market (2 of 3: VIXY spike, 8+ sectors down, HYG down), a catalyst headline, MACD under its signal. Only the SPY core sells, when losing and all four confirm; the rest is reported.</div>
  {preclose}
  <div class="src">Source: logs/preclose/latest.md</div>
</section>

<section class="card standings">
  <h2>Penny watch-list (shadow, never trades)</h2>
  <div class="cap">$1-$5 listed, $5M+/day, trending, profitable, no reverse split, IWM gate. Graded vs IWM at 5 and 21 sessions.</div>
  {penny}
  <div class="src">Source: logs/shadow/penny.md</div>
</section>

<section class="card">
  <h2>Scheduled tasks</h2>
  <div class="cap">From each task's own log: last write, and error lines in its latest output.</div>
  <div class="tablewrap"><table><thead><tr><th>Task</th><th>Log</th><th class="num">Last run</th><th>Age</th><th>Note</th></tr></thead>
  <tbody>{tasks}</tbody></table></div>
  <div class="src">Source: logs/scheduled_&lt;task&gt;.log</div>
</section>

<div class="grid2">
  <section class="card">
    <h2>Audit log</h2>
    <div class="cap">Newest first</div>
    <div class="feedwrap tablewrap"><table><thead><tr><th class="num">AK time</th><th>Event</th><th>Symbol</th><th>Book</th><th>Reason</th></tr></thead>
    <tbody>{feed}</tbody></table></div>
    {audit_src}
  </section>
  <section class="card">
    <h2>Refusals today</h2>
    <div class="cap">Why orders were not sent, by rule</div>
    {refusals}
  </section>
</div>

<footer>Read-only. Generated by scripts/command_center.py — nothing on this page can place or change an order.</footer>
</div>
<script>{js}</script>
</body></html>"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, default=ROOT)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--no-broker", action="store_true", help="skip Alpaca; files only")
    ap.add_argument("--broker-fixture", type=Path, help=argparse.SUPPRESS)  # tests/demo
    ap.add_argument("--open", action="store_true", help="open the page when written")
    a = ap.parse_args(argv)

    root = a.root.resolve()
    broker = None if a.no_broker else Broker(load_env(root), a.broker_fixture)
    data = gather(root, broker)
    out = a.out or root / "logs" / "dashboard" / "command_center.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(data), encoding="utf-8")
    print(f"wrote {out}")
    if data.get("broker_error"):
        print(f"  broker: {data['broker_error']}")
    print(f"  competition: {data['series_src']}")
    if a.open:
        webbrowser.open(out.as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())
