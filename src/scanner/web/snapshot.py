"""Everything the desk page shows, assembled into one JSON-able dict.

READ-ONLY BY CONSTRUCTION
This module only issues GET requests to the broker and reads local files. It
has no code path that places, cancels or modifies an order, so the page built
on it can be left open all day without being a way to trade.

WHERE EACH PART COMES FROM
    account, equity curve     Alpaca (account, portfolio history)
    positions, protection     Alpaca (positions, open orders and their legs)
    trades, win rate, R       audit-log intents joined to Alpaca orders
    pipeline funnel           the `scan_summary` and refusal records trade.py
                              writes on every run
    Gemma's decisions         `llm_ranking` and `news_checked` records, and
                              the opinion stored on each trade intent
    indicators, charts        30-minute bars, computed the same way the
                              screen computes them
    live headlines            Yahoo RSS, read now; no model is run for them

The pure functions below take plain dicts and are tested without a broker.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from scanner.execution.reconcile import (
    CLOSED_OTHER, CLOSED_STOP, CLOSED_TARGET, EXPIRED, FILLED_OPEN, REJECTED,
    WORKING, LiveTrade, Summary, reconcile,
)

LIVE_LEG = ("new", "accepted", "held", "partially_filled", "pending_new")
CONVICTIONS = ("strong", "neutral", "weak")


# ---- the audit log ---------------------------------------------------------

def read_events(path: str | Path) -> list[dict]:
    """Every audit record, oldest first. Unreadable lines are skipped."""
    target = Path(path)
    if not target.exists():
        return []
    events = []
    for line in target.read_text(encoding="utf-8").splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def intents_by_order(events: Iterable[dict]) -> dict[str, dict]:
    """Latest intent per client_order_id."""
    out: dict[str, dict] = {}
    for event in events:
        intent = event.get("intent") or {}
        if intent.get("client_order_id"):
            out[intent["client_order_id"]] = intent
    return out


def runs(events: Iterable[dict], limit: int = 30) -> list[dict]:
    """One entry per scan, newest first: its counts, refusals and decisions.

    Records written before run ids existed have none and are left out — they
    cannot be grouped into runs after the fact.
    """
    grouped: dict[str, dict] = {}
    for event in events:
        run_id = event.get("run_id")
        if not run_id:
            continue
        run = grouped.setdefault(run_id, {
            "run_id": run_id, "at": event.get("at", ""), "summary": {},
            "refused": Counter(), "by_stage": Counter(), "intents": [],
            "submitted": [], "rejected": [], "news": [], "ranking": [],
            "unprotected": [],
        })
        kind = event.get("event")
        detail = event.get("detail") or {}
        if kind == "scan_summary":
            run["summary"] = detail
        elif kind == "candidate_refused":
            run["refused"][event.get("reason", "")] += 1
            run["by_stage"][event.get("stage", "")] += 1
        elif kind == "trade_intent":
            run["intents"].append(event.get("symbol", ""))
        elif kind == "order_submitted":
            run["submitted"].append(event.get("symbol", ""))
        elif kind == "order_rejected":
            run["rejected"].append({"symbol": event.get("symbol", ""),
                                    "reason": event.get("reason", "")})
        elif kind == "news_checked":
            run["news"].append({"symbol": event.get("symbol", ""),
                                "reason": event.get("reason", ""), **detail})
        elif kind == "llm_ranking":
            run["ranking"] = detail.get("opinions", [])
        elif kind == "position_unprotected":
            run["unprotected"].append(event.get("symbol", ""))
    ordered = sorted(grouped.values(), key=lambda r: r["run_id"], reverse=True)
    for run in ordered:
        run["refused"] = [{"reason": r, "count": n}
                          for r, n in run["refused"].most_common()]
        run["by_stage"] = dict(run["by_stage"])
    return ordered[:limit]


def funnel(run: dict) -> list[dict]:
    """The stages one run passed candidates through, widest first."""
    s = run.get("summary") or {}
    stages = [
        ("Universe", s.get("symbols")),
        ("Bars loaded", s.get("loaded")),
        ("Tradeable", s.get("liquid")),
        ("Setups found", s.get("setups")),
        ("Fresh and live", s.get("fresh")),
        ("Passed every gate", len(run.get("intents", []))),
        ("Orders sent", s.get("sent", len(run.get("submitted", [])))),
    ]
    return [{"label": label, "count": count} for label, count in stages
            if count is not None]


# ---- trades and outcomes ---------------------------------------------------

def _leg_exit(order: dict) -> tuple[float | None, str]:
    for leg in order.get("legs") or []:
        if leg.get("filled_avg_price"):
            return float(leg["filled_avg_price"]), str(leg.get("filled_at") or "")
    return None, ""


def resolve_exits(trades: tuple[LiveTrade, ...], orders: list[dict],
                  held: set[str]) -> tuple[LiveTrade, ...]:
    """Close trades that left the bracket some other way.

    A time exit, or a stop re-armed by `protect.py`, closes the position with
    a separate order, so the original bracket still reads as filled-and-open
    forever. If the symbol is no longer held, the trade is closed: its exit is
    the latest filled sell for that symbol after the entry.
    """
    sells: dict[str, list[dict]] = {}
    for order in orders:
        for leg in [order] + list(order.get("legs") or []):
            if leg.get("side") == "sell" and leg.get("filled_avg_price"):
                sells.setdefault(str(leg.get("symbol")), []).append(leg)
    out = []
    for trade in trades:
        if trade.state == FILLED_OPEN and trade.tracked and trade.symbol not in held:
            later = [leg for leg in sells.get(trade.symbol, [])
                     if str(leg.get("filled_at") or "") >= trade.submitted_at]
            if later:
                last = max(later, key=lambda leg: str(leg.get("filled_at") or ""))
                trade = trade.model_copy(update={
                    "state": CLOSED_OTHER,
                    "exit_fill": float(last["filled_avg_price"]),
                })
        out.append(trade)
    return tuple(out)


def entry_context(events: list[dict], symbol: str, before: str) -> dict:
    """What Gemma saw and said about `symbol` at or before `before`: its
    ranking opinion (with the headlines it read) and the news gate's labels."""
    found: dict = {}
    for event in events:
        at = str(event.get("at", ""))
        if before and at > before:
            continue
        detail = event.get("detail") or {}
        if event.get("event") == "llm_ranking":
            for o in detail.get("opinions", []):
                if o.get("symbol") == symbol:
                    found["opinion"] = o
        elif event.get("event") == "news_checked" and event.get("symbol") == symbol:
            found["news"] = {**detail, "reason": event.get("reason", "")}
    return found


def hindsight_by_order(events: Iterable[dict]) -> dict[str, dict]:
    """Latest Gemma hindsight review per client_order_id."""
    out: dict[str, dict] = {}
    for event in events:
        if event.get("event") == "llm_hindsight":
            detail = event.get("detail") or {}
            if detail.get("client_order_id"):
                out[detail["client_order_id"]] = {**detail, "at": event.get("at", "")}
    return out


def trade_rows(trades: Iterable[LiveTrade], intents: dict[str, dict],
               orders: list[dict], marks: dict[str, float],
               events: list[dict] | None = None) -> list[dict]:
    """One flat row per trade, with what Gemma and the shadow gates said."""
    from scanner.llm.hindsight import call_verdict

    by_coid = {str(o.get("client_order_id") or ""): o for o in orders}
    reviews = hindsight_by_order(events or [])
    rows = []
    for t in trades:
        intent = intents.get(t.client_order_id, {})
        order = by_coid.get(t.client_order_id, {})
        exit_at = _leg_exit(order)[1]
        r = t.r_multiple(marks.get(t.symbol))
        pnl = None
        if t.entry_fill is not None:
            price = t.exit_fill if t.is_closed else marks.get(t.symbol)
            if price is not None:
                pnl = (price - t.entry_fill) * t.shares
        rows.append({
            "symbol": t.symbol, "state": t.state, "tracked": t.tracked,
            "shares": t.shares, "entry": t.entry_fill, "exit": t.exit_fill,
            "intended_entry": t.intended_entry, "stop": t.intended_stop,
            "target": t.intended_target, "r": r, "pnl": pnl,
            "submitted_at": t.submitted_at,
            "filled_at": str(order.get("filled_at") or ""), "exit_at": exit_at,
            "sector": intent.get("sector", ""),
            "conviction": intent.get("llm_conviction", ""),
            "driver": intent.get("llm_driver", ""),
            "concern": intent.get("llm_concern", ""),
            "llm_rank": intent.get("llm_rank", 0),
            "priority_rank": intent.get("priority_rank", 0),
            "slot": intent.get("trigger_slot", ""),
            "weekday": intent.get("trigger_weekday", ""),
            "rsi_gate": intent.get("rsi_gate_passed"),
            "liquidity": intent.get("liquidity_tier", ""),
            "client_order_id": t.client_order_id,
            "verdict": call_verdict(intent.get("llm_conviction", ""), r)
                       if t.is_closed else "",
            "entry_context": entry_context(events or [], t.symbol, t.submitted_at)
                             if events else {},
            "hindsight": reviews.get(t.client_order_id),
        })
    return rows


def performance(rows: list[dict], summary: Summary | None = None) -> dict:
    """Win rate, expectancy and the rest, over closed trades this system placed."""
    closed = [r for r in rows if r["tracked"] and r["state"] in
              (CLOSED_STOP, CLOSED_TARGET, CLOSED_OTHER) and r["r"] is not None]
    wins = [r for r in closed if r["r"] > 0]
    losses = [r for r in closed if r["r"] <= 0]
    gross_win = sum(r["pnl"] or 0 for r in wins)
    gross_loss = -sum(r["pnl"] or 0 for r in losses)
    open_rows = [r for r in rows if r["tracked"] and r["state"] == FILLED_OPEN]
    return {
        "closed": len(closed),
        "wins": len(wins), "losses": len(losses),
        "win_rate": (len(wins) / len(closed) * 100) if closed else None,
        "expectancy_r": statistics.fmean(r["r"] for r in closed) if closed else None,
        "total_r": sum(r["r"] for r in closed),
        "realized_pnl": sum(r["pnl"] or 0 for r in closed),
        "unrealized_pnl": sum(r["pnl"] or 0 for r in open_rows),
        "avg_win_r": statistics.fmean(r["r"] for r in wins) if wins else None,
        "avg_loss_r": statistics.fmean(r["r"] for r in losses) if losses else None,
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else None,
        "best_r": max((r["r"] for r in closed), default=None),
        "worst_r": min((r["r"] for r in closed), default=None),
        "fill_rate": summary.fill_rate if summary else None,
        "sent": summary.sent if summary else None,
        "r_values": [round(r["r"], 3) for r in closed],
        "backtest_expectancy_r": 0.218,
    }


def scoreboard(rows: list[dict]) -> list[dict]:
    """Realized R by Gemma's conviction at entry — the measurement that
    decides whether its ranking ever earns a vote."""
    out = []
    calls = [r for r in rows if r.get("verdict") in ("right", "wrong")]
    for level in CONVICTIONS:
        rs = [r["r"] for r in rows if r["tracked"] and r["conviction"] == level
              and r["state"] in (CLOSED_STOP, CLOSED_TARGET, CLOSED_OTHER)
              and r["r"] is not None]
        out.append({
            "conviction": level, "n": len(rs),
            "mean_r": statistics.fmean(rs) if rs else None,
            "win_rate": (sum(1 for x in rs if x > 0) / len(rs) * 100) if rs else None,
            "right": sum(1 for r in calls if r["conviction"] == level
                         and r["verdict"] == "right"),
            "wrong": sum(1 for r in calls if r["conviction"] == level
                         and r["verdict"] == "wrong"),
        })
    return out


def protection(orders: list[dict]) -> dict[str, dict[str, float]]:
    """Live stop and target per symbol, read from the broker's open legs."""
    out: dict[str, dict[str, float]] = {}
    for order in orders:
        for leg in [order] + list(order.get("legs") or []):
            if leg.get("side") != "sell" or leg.get("status") not in LIVE_LEG:
                continue
            entry = out.setdefault(str(leg.get("symbol")), {})
            if leg.get("stop_price"):
                entry["stop"] = float(leg["stop_price"])
            elif leg.get("limit_price"):
                entry["target"] = float(leg["limit_price"])
    return out


def indicators(ctx: Any) -> dict:
    """The intraday context, as the screen computes it, plus what it means."""
    if ctx is None:
        return {}
    fields = ("price", "ema9", "ema20", "ema21", "ema50", "ema200", "rsi14",
              "macd", "macd_signal", "macd_histogram", "atr_pct", "volume_ratio")
    out = {f: getattr(ctx, f, None) for f in fields}
    out["flags"] = {
        "Above 200 EMA": ctx.above_200,
        "Fast EMA stack": ctx.fast_stack,
        "RSI 40-65": ctx.preferred_pullback,
        "MACD confirmed": ctx.macd_confirmed,
        "Coiled": ctx.coiled,
    }
    return out


def latest_by_symbol(events: Iterable[dict], kind: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for event in events:
        if event.get("event") == kind and event.get("symbol"):
            out[event["symbol"]] = {**(event.get("detail") or {}),
                                    "at": event.get("at", ""),
                                    "reason": event.get("reason", "")}
    return out


def gemma_log(events: Iterable[dict], limit: int = 60) -> list[dict]:
    """Gemma's recent decisions, newest first: rankings and news verdicts."""
    out = []
    for event in events:
        kind = event.get("event")
        detail = event.get("detail") or {}
        if kind == "llm_ranking":
            for o in detail.get("opinions", []):
                out.append({"at": event.get("at", ""), "kind": "ranking", **o})
        elif kind == "news_checked":
            out.append({"at": event.get("at", ""), "kind": "news",
                        "symbol": event.get("symbol", ""),
                        "reason": event.get("reason", ""), **detail})
    out.sort(key=lambda e: e["at"], reverse=True)
    return out[:limit]


# ---- the live part ---------------------------------------------------------

async def build(client, *, audit_path: str, key_id: str, secret: str,
                minutes: int = 30, news: bool = True) -> dict:
    """The whole snapshot. GET requests only."""
    import asyncio
    from datetime import datetime

    from scanner.execution import watchlist
    from scanner.execution.alpaca import Alpaca
    from scanner.execution.policy import TradingPolicy
    from scanner.features import intraday_context
    from scanner.features.intraday import fetch_intraday, max_range
    from scanner.sources import yahoo_rss

    broker = Alpaca(key_id, secret, client)
    raw_account = await broker._get("/v2/account")
    positions = await broker._get("/v2/positions")
    open_orders = await broker._get("/v2/orders", status="open", nested="true")
    orders = await broker._get("/v2/orders", status="all", nested="true",
                               limit=500, direction="desc")
    clock = await broker._get("/v2/clock")
    try:
        history = await broker._get("/v2/account/portfolio/history",
                                    period="1A", timeframe="1D")
    except Exception:
        history = {}

    events = read_events(audit_path)
    intents = intents_by_order(events)
    held = {p["symbol"] for p in positions}
    marks = {p["symbol"]: float(p.get("current_price") or 0) for p in positions}
    trades = resolve_exits(reconcile(intents, orders), orders, held)
    summary = Summary(trades=trades)
    rows = trade_rows(trades, intents, orders, marks, events)
    guards = protection(open_orders)
    news_at_entry = latest_by_symbol(events, "news_checked")

    async def one(position: dict) -> dict:
        symbol = position["symbol"]
        bars = ctx = None
        try:
            bars = await fetch_intraday(symbol, client, minutes=minutes,
                                        range_=max_range(minutes))
            ctx = intraday_context.compute(bars)
        except Exception:
            pass
        headlines = []
        if news:
            try:
                headlines = [{"title": n.title, "link": getattr(n, "link", ""),
                              "published": str(getattr(n, "published", "") or "")}
                             for n in (await yahoo_rss.fetch(symbol, client))[:6]]
            except Exception:
                headlines = []
        row = next((r for r in rows if r["symbol"] == symbol
                    and r["state"] == FILLED_OPEN), {})
        tail = 160
        chart = []
        if bars is not None:
            for i in range(max(0, len(bars) - tail), len(bars)):
                chart.append({"time": int(bars.times[i].timestamp()),
                              "open": bars.opens[i], "high": bars.highs[i],
                              "low": bars.lows[i], "close": bars.closes[i]})
        return {
            "symbol": symbol,
            "qty": float(position.get("qty") or 0),
            "avg_entry": float(position.get("avg_entry_price") or 0),
            "price": float(position.get("current_price") or 0),
            "market_value": float(position.get("market_value") or 0),
            "unrealized_pl": float(position.get("unrealized_pl") or 0),
            "unrealized_plpc": float(position.get("unrealized_plpc") or 0) * 100,
            "change_today_pct": float(position.get("change_today") or 0) * 100,
            "live_stop": guards.get(symbol, {}).get("stop"),
            "live_target": guards.get(symbol, {}).get("target"),
            "trade": row,
            "indicators": indicators(ctx),
            "chart": chart,
            "news_at_entry": news_at_entry.get(symbol),
            "headlines": headlines,
        }

    position_views = await asyncio.gather(*(one(p) for p in positions))
    last_equity = float(raw_account.get("last_equity") or 0)
    equity = float(raw_account.get("equity") or 0)
    curve = [{"time": t, "value": v} for t, v in
             zip(history.get("timestamp") or [], history.get("equity") or [])
             if v]
    recent_runs = runs(events)
    try:
        watching = [w.model_dump() for w in watchlist.load()]
    except Exception:
        watching = []
    return {
        "demo": False,
        "generated_at": datetime.now().astimezone().isoformat(),
        "market_open": bool(clock.get("is_open")),
        "account": {
            "equity": equity, "cash": float(raw_account.get("cash") or 0),
            "buying_power": float(raw_account.get("buying_power") or 0),
            "day_pl": equity - last_equity if last_equity else None,
            "day_pl_pct": ((equity / last_equity - 1) * 100) if last_equity else None,
            "status": raw_account.get("status", ""),
            "start_equity": curve[0]["value"] if curve else None,
        },
        "policy": TradingPolicy().describe(),
        "equity_curve": curve,
        "positions": list(position_views),
        "working": [r for r in rows if r["state"] == WORKING],
        "watchlist": watching,
        "trades": [r for r in rows if r["state"] not in (WORKING,)],
        "performance": performance(rows, summary),
        "scoreboard": scoreboard(rows),
        "runs": [{**run, "funnel": funnel(run)} for run in recent_runs],
        "gemma": gemma_log(events),
        "untracked": sorted({t.symbol for t in summary.untracked}),
    }
