"""Command center panels: closed trades, results by book, and a chart per open position.

Pure functions over data the command center already fetched (Alpaca orders and daily bars);
nothing here talks to the broker. Imported by scripts/command_center.py.

Closed trades are rebuilt from filled orders: each symbol's sells are matched to its earlier
buys first in, first out, and a trade belongs to the book of its buy. Order history is read
500 orders deep, so a sell whose buy is older than that is left out rather than guessed at.
"""
from __future__ import annotations

import datetime as dt
import html
from collections import defaultdict, deque

#: The current setup started trading on this day (FREEZE.md 2026-10-26: Gemma stopped picking).
#: `gem-` buys before it were Gemma's picks, not the rules book's.
CURRENT_FROM = "2026-10-26"
PREFIX_BOOK = {"cor": "SPY core", "gem": "Rules book", "sml": "Small caps", "orb": "ORB",
               "pck": "Picks", "man": "Manual", "mom": "Momentum", "smc": "Sweep"}
CURRENT_BOOKS = ("SPY core", "Rules book", "Small caps", "ORB")
STOP_TYPES = ("stop", "stop_limit", "trailing_stop")
WORKING = ("new", "accepted", "held", "pending_new", "partially_filled", "accepted_for_bidding")
HIST_BINS = (-10, -6, -4, -2, -1, 0, 1, 2, 4, 6, 10)


def esc(x) -> str:
    return html.escape("" if x is None else str(x))


def trade_book(coid: str | None, day: str) -> str:
    """The book a buy belongs to; `gem-` before CURRENT_FROM was Gemma's (retired) book."""
    book = PREFIX_BOOK.get((coid or "").split("-", 1)[0].lower(), "Not ours")
    if book == "Rules book" and day < CURRENT_FROM:
        return "Gemma (retired)"
    return book


def exit_kind(order: dict) -> str:
    """How a sell left: its stop, a limit (a bracket's target), or at market (a rule's sale)."""
    if order.get("type") in STOP_TYPES:
        return "stop"
    return "limit" if order.get("type") == "limit" else "sold"


def fills(orders: list[dict]) -> list[dict]:
    """Filled (or partly filled) orders, oldest first, with numeric quantity and price."""
    out = []
    for o in orders:
        qty = float(o.get("filled_qty") or 0)
        px = float(o.get("filled_avg_price") or 0)
        if qty > 0 and px > 0 and o.get("filled_at") and o.get("side") in ("buy", "sell"):
            out.append({**o, "_qty": qty, "_px": px})
    return sorted(out, key=lambda o: o["filled_at"])


def round_trips(orders: list[dict]) -> list[dict]:
    """Closed trades from filled orders: sells matched to earlier buys of the symbol, FIFO."""
    lots: dict[str, deque] = defaultdict(deque)
    trades = []
    for o in fills(orders):
        sym = o.get("symbol")
        if o["side"] == "buy":
            lots[sym].append({"qty": o["_qty"], "px": o["_px"], "day": o["filled_at"][:10],
                              "coid": o.get("client_order_id")})
            continue
        left = o["_qty"]
        while left > 1e-9 and lots[sym]:
            lot = lots[sym][0]
            q = min(left, lot["qty"])
            trades.append({
                "symbol": sym, "book": trade_book(lot["coid"], lot["day"]),
                "opened": lot["day"], "closed": o["filled_at"][:10], "qty": q,
                "entry": lot["px"], "exit": o["_px"], "pnl": (o["_px"] - lot["px"]) * q,
                "pct": (o["_px"] / lot["px"] - 1) * 100, "how": exit_kind(o),
            })
            lot["qty"] -= q
            left -= q
            if lot["qty"] <= 1e-9:
                lots[sym].popleft()
    return merge_partials(trades)


def merge_partials(trades: list[dict]) -> list[dict]:
    """One row per (symbol, book, opened, closed): a lot sold in pieces the same day is one trade."""
    merged: dict[tuple, dict] = {}
    for t in trades:
        k = (t["symbol"], t["book"], t["opened"], t["closed"], t["how"])
        if k not in merged:
            merged[k] = dict(t)
            continue
        m = merged[k]
        cost = m["entry"] * m["qty"] + t["entry"] * t["qty"]
        m["qty"] += t["qty"]
        m["pnl"] += t["pnl"]
        m["entry"] = cost / m["qty"]
        m["exit"] = m["entry"] + m["pnl"] / m["qty"]
        m["pct"] = (m["exit"] / m["entry"] - 1) * 100
    return sorted(merged.values(), key=lambda t: (t["closed"], t["symbol"]), reverse=True)


def book_summary(trades: list[dict]) -> list[dict]:
    """Per book: closed trades, wins, realized $, average and best / worst %."""
    by: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        by[t["book"]].append(t)
    order = [b for b in CURRENT_BOOKS if b in by] + sorted(b for b in by if b not in CURRENT_BOOKS)
    rows = []
    for b in order:
        ts = by[b]
        pcts = [t["pct"] for t in ts]
        won = sum(t["pnl"] > 0 for t in ts)
        rows.append({"book": b, "n": len(ts), "won": won, "win_pct": won / len(ts) * 100,
                     "pnl": sum(t["pnl"] for t in ts), "avg": sum(pcts) / len(pcts),
                     "best": max(pcts), "worst": min(pcts), "current": b in CURRENT_BOOKS})
    return rows


def live_targets(flat_orders: list[dict]) -> dict[str, float]:
    """Working take-profit (limit sell) price per symbol."""
    out = {}
    for o in flat_orders:
        if o.get("side") == "sell" and o.get("type") == "limit" and o.get("status") in WORKING and o.get("limit_price"):
            out[o["symbol"]] = float(o["limit_price"])
    return out


def entry_days(flat_orders: list[dict]) -> dict[str, str]:
    """Day of each symbol's latest filled buy."""
    out = {}
    for o in fills(flat_orders):
        if o["side"] == "buy":
            out[o["symbol"]] = o["filled_at"][:10]
    return out


# ============================================================================
# Render
# ============================================================================
def money(v: float) -> str:
    return f"-${abs(v):,.0f}" if v < 0 else f"+${v:,.0f}"


def tone(v: float) -> str:
    return "up" if v > 0 else "down" if v < 0 else ""


def summary_html(rows: list[dict]) -> str:
    if not rows:
        return "<p class='muted'>No closed trades in the order history read.</p>"
    body = []
    for r in rows:
        cls = "" if r["current"] else " class='muted'"
        body.append(
            f"<tr{cls}><td>{esc(r['book'])}</td><td class='num'>{r['n']}</td>"
            f"<td class='num'>{r['won']} ({r['win_pct']:.0f}%)</td>"
            f"<td class='num {tone(r['pnl'])}'>{money(r['pnl'])}</td>"
            f"<td class='num {tone(r['avg'])}'>{r['avg']:+.2f}%</td>"
            f"<td class='num'>{r['best']:+.1f}% / {r['worst']:+.1f}%</td></tr>")
    return ("<div class='tablewrap'><table><thead><tr><th>Book</th><th class='num'>Closed</th>"
            "<th class='num'>Won</th><th class='num'>Realized</th><th class='num'>Avg trade</th>"
            "<th class='num'>Best / worst</th></tr></thead><tbody>" + "".join(body) + "</tbody></table></div>")


def history_html(trades: list[dict], limit: int = 150) -> str:
    if not trades:
        return ""
    books = [b for b in (*CURRENT_BOOKS, *sorted({t["book"] for t in trades})) if any(t["book"] == b for t in trades)]
    books = list(dict.fromkeys(books))
    buttons = "".join(f"<button type='button' class='tf' data-f='{esc(b)}'>{esc(b)}</button>" for b in books)
    rows = "".join(
        f"<tr data-book='{esc(t['book'])}' data-res='{'win' if t['pnl'] > 0 else 'loss'}'>"
        f"<td class='num'>{esc(t['closed'][5:])}</td><td><b>{esc(t['symbol'])}</b></td>"
        f"<td class='small'>{esc(t['book'])}</td><td class='num muted'>{esc(t['opened'][5:])}</td>"
        f"<td class='num'>{t['entry']:,.2f}</td><td class='num'>{t['exit']:,.2f}</td>"
        f"<td class='num {tone(t['pct'])}'>{t['pct']:+.2f}%</td>"
        f"<td class='num {tone(t['pnl'])}'>{money(t['pnl'])}</td><td class='small muted'>{esc(t['how'])}</td></tr>"
        for t in trades[:limit])
    return (f"<div class='tfbar'><button type='button' class='tf on' data-f=''>All</button>"
            f"<button type='button' class='tf' data-f='win'>Winners</button>"
            f"<button type='button' class='tf' data-f='loss'>Losers</button>{buttons}</div>"
            "<div class='feedwrap tablewrap'><table class='trades'><thead><tr><th class='num'>Closed</th>"
            "<th>Symbol</th><th>Book</th><th class='num'>Opened</th><th class='num'>Entry</th>"
            "<th class='num'>Exit</th><th class='num'>%</th><th class='num'>P/L</th><th>Exit</th>"
            f"</tr></thead><tbody>{rows}</tbody></table></div>")


def histogram_svg(trades: list[dict]) -> str:
    """Count of closed trades by % result; losses red, wins green."""
    if not trades:
        return ""
    edges = (float("-inf"), *HIST_BINS, float("inf"))
    counts = [sum(lo <= t["pct"] < hi for t in trades) for lo, hi in zip(edges, edges[1:])]
    labels = [f"<{HIST_BINS[0]}"] + [f"{lo:+g}" for lo in HIST_BINS[:-1]] + [f"≥{HIST_BINS[-1]}"]
    W, H, L, B, T = 760, 170, 30, 34, 10
    bw = (W - L) / len(counts)
    top = max(counts) or 1
    bars = []
    for i, (c, lab) in enumerate(zip(counts, labels)):
        h = (H - B - T) * c / top
        x = L + i * bw
        col = "var(--down)" if edges[i + 1] <= 0 else "var(--up)"
        bars.append(f"<rect x='{x + 3:.1f}' y='{H - B - h:.1f}' width='{bw - 6:.1f}' height='{h:.1f}' rx='3' fill='{col}'>"
                    f"<title>{c} trade(s)</title></rect>")
        if c:
            bars.append(f"<text class='tick' x='{x + bw / 2:.1f}' y='{H - B - h - 4:.1f}' text-anchor='middle'>{c}</text>")
        bars.append(f"<text class='tick' x='{x + bw / 2:.1f}' y='{H - B + 14:.1f}' text-anchor='middle'>{esc(lab)}</text>")
    bars.append(f"<text class='tick' x='{(W + L) / 2:.0f}' y='{H - 4}' text-anchor='middle'>% result, bins start at the label</text>")
    return (f"<div class='chart'><svg viewBox='0 0 {W} {H}' role='img' aria-label='Closed trades by % result'>"
            f"<line class='base' x1='{L}' x2='{W}' y1='{H - B}' y2='{H - B}'/>{''.join(bars)}</svg></div>")


def position_chart(p: dict, closes: list[tuple[str, float]], target: float | None, opened: str | None) -> str:
    """One card: daily closes with the entry, the live stop and any target drawn across."""
    head = (f"<div class='pc-h'><b>{esc(p['symbol'])}</b> <span class='muted small'>{esc(p['book'])}</span>"
            f"<span class='num {tone(p['upl'])}' style='margin-left:auto'>{p['uplpc']:+.1f}%</span></div>")
    if not p["stop_live"] and not p.get("stopless"):
        head += "<div class='flag critical small'>✕ no live stop</div>"
    if len(closes) < 5:
        return f"<div class='pc card'>{head}<p class='muted small'>No daily bars.</p></div>"
    levels = [("entry", p["entry"], "var(--ink-3)", "5 4")]
    if p.get("stop"):
        levels.append(("stop", p["stop"], "var(--down)", ""))
    if target:
        levels.append(("target", target, "var(--up)", ""))
    vals = [c for _, c in closes] + [v for _, v, _, _ in levels]
    lo, hi = min(vals), max(vals)
    pad = (hi - lo) * 0.06 or 1
    lo, hi = lo - pad, hi + pad
    W, H, L, R, T, B = 360, 150, 6, 74, 8, 8
    x = lambda i: L + (W - L - R) * i / (len(closes) - 1)
    y = lambda v: T + (H - T - B) * (hi - v) / (hi - lo)
    path = " ".join(f"{'M' if i == 0 else 'L'}{x(i):.1f},{y(c):.1f}" for i, (_, c) in enumerate(closes))
    g = []
    if opened:
        idx = next((i for i, (d, _) in enumerate(closes) if d >= opened), None)
        if idx is not None:
            g.append(f"<rect x='{x(idx):.1f}' y='{T}' width='{x(len(closes) - 1) - x(idx):.1f}' height='{H - T - B}' fill='var(--wash)'/>")
    for name, v, col, dash in levels:
        da = f" stroke-dasharray='{dash}'" if dash else ""
        g.append(f"<line x1='{L}' x2='{W - R}' y1='{y(v):.1f}' y2='{y(v):.1f}' stroke='{col}' stroke-width='1.2'{da}/>"
                 f"<text class='tick' x='{W - R + 4}' y='{y(v) + 4:.1f}' fill='{col}'>{name} {v:,.2f}</text>")
    g.append(f"<path d='{path}' fill='none' stroke='var(--s1)' stroke-width='1.8' stroke-linejoin='round'/>")
    g.append(f"<circle cx='{x(len(closes) - 1):.1f}' cy='{y(closes[-1][1]):.1f}' r='3' fill='var(--s1)'/>")
    cap = f"{closes[0][0][5:]} to {closes[-1][0][5:]} · shaded: held"
    return (f"<div class='pc card'>{head}<svg viewBox='0 0 {W} {H}' role='img' aria-label='{esc(p['symbol'])} daily closes'>"
            f"{''.join(g)}</svg><div class='muted small'>{cap}</div></div>")


CSS = """
.tfbar{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px}
.tf.on{background:var(--ink-1);color:var(--page)}
.pcs{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:12px}
.pc{padding:12px;margin:0}
.pc svg{width:100%;height:auto;display:block;overflow:visible}
.pc-h{display:flex;align-items:baseline;gap:8px;margin-bottom:4px}
"""

JS = """
document.querySelectorAll('.tfbar').forEach(bar=>{
  const rows=bar.nextElementSibling.querySelectorAll('tbody tr');
  bar.addEventListener('click',e=>{
    const b=e.target.closest('.tf'); if(!b) return;
    bar.querySelectorAll('.tf').forEach(x=>x.classList.toggle('on',x===b));
    const f=b.dataset.f;
    rows.forEach(r=>{r.style.display=(!f||r.dataset.book===f||r.dataset.res===f)?'':'none';});
  });
});
"""


def daily_closes(bars_json: dict | None) -> dict[str, list[tuple[str, float]]]:
    """Alpaca multi-symbol bars ({"bars": {sym: [...]}}) as {sym: [(day, close)]}."""
    out = {}
    for sym, bars in ((bars_json or {}).get("bars") or {}).items():
        out[sym] = [(b["t"][:10], float(b["c"])) for b in bars or [] if b.get("t") and b.get("c")]
    return out


def bars_start(today: dt.date, days: int = 100) -> str:
    return (today - dt.timedelta(days=days)).isoformat()
