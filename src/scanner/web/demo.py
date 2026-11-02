"""A synthetic snapshot with the same shape as `snapshot.build`.

For previewing the desk without broker keys, and for testing the page. Every
figure is invented and the page shows a DEMO banner whenever `demo` is true.
"""

from __future__ import annotations

import math
import random
from datetime import datetime, timedelta, timezone

SYMBOLS = [("MU", "Information Technology"), ("CAT", "Industrials"),
           ("LLY", "Health Care"), ("XOM", "Energy")]


def _candles(rng, start_price, n=160, minutes=30):
    out, price = [], start_price
    t = datetime(2026, 10, 20, 13, 30, tzinfo=timezone.utc)
    for i in range(n):
        if i and i % 13 == 0:
            t += timedelta(hours=17, minutes=30)
        o = price
        c = max(1.0, o * (1 + rng.gauss(0.0004, 0.006)))
        h = max(o, c) * (1 + abs(rng.gauss(0, 0.002)))
        lo = min(o, c) * (1 - abs(rng.gauss(0, 0.002)))
        out.append({"time": int(t.timestamp()), "open": round(o, 2),
                    "high": round(h, 2), "low": round(lo, 2), "close": round(c, 2)})
        price = c
        t += timedelta(minutes=minutes)
    return out


def _ema(values, n):
    k, e = 2 / (n + 1), values[0]
    for v in values[1:]:
        e = v * k + e * (1 - k)
    return e


def build(seed: int = 11) -> dict:
    rng = random.Random(seed)
    now = datetime(2026, 11, 2, 18, 3, tzinfo=timezone.utc)
    positions = []
    for symbol, sector in SYMBOLS[:3]:
        chart = _candles(rng, rng.uniform(80, 900))
        closes = [c["close"] for c in chart]
        entry = closes[-14]
        stop = round(entry * (1 - rng.uniform(0.016, 0.026)), 2)
        target = round(entry + (entry - stop) * rng.uniform(1.6, 2.8), 2)
        price = closes[-1]
        qty = int(12_000 / entry)
        r = (price - entry) / (entry - stop)
        conviction = rng.choice(["strong", "neutral", "weak"])
        positions.append({
            "symbol": symbol, "qty": qty, "avg_entry": entry, "price": price,
            "market_value": qty * price, "unrealized_pl": qty * (price - entry),
            "unrealized_plpc": (price / entry - 1) * 100,
            "change_today_pct": rng.uniform(-1.5, 1.8),
            "live_stop": stop, "live_target": target if symbol != "LLY" else None,
            "trade": {"symbol": symbol, "sector": sector, "stop": stop,
                      "target": target, "entry": entry, "r": r,
                      "conviction": conviction, "driver": "company",
                      "concern": "none" if conviction != "weak" else
                      "guidance cut is still being digested",
                      "slot": "morning", "weekday": "Tue", "rsi_gate": True,
                      "filled_at": "2026-10-30T15:31:00Z", "priority_rank": 1,
                      "llm_rank": 2},
            "indicators": {
                "price": price, "ema9": _ema(closes, 9), "ema21": _ema(closes, 21),
                "ema50": _ema(closes, 50), "ema200": _ema(closes, 120),
                "rsi14": rng.uniform(38, 66), "macd": rng.uniform(-1, 1.5),
                "macd_signal": rng.uniform(-1, 1), "macd_histogram": rng.uniform(-0.4, 0.6),
                "atr_pct": rng.uniform(0.5, 1.4), "volume_ratio": rng.uniform(0.6, 1.6),
                "flags": {"Above 200 EMA": True, "Fast EMA stack": rng.random() > .5,
                          "RSI 40-65": True, "MACD confirmed": rng.random() > .5,
                          "Coiled": False}},
            "chart": chart,
            "news_at_entry": {"allowed": True, "checked": True, "deal_role": "none",
                              "earnings": "", "reason": "",
                              "items": [{"headline": f"{symbol} raises full-year outlook on demand",
                                         "category": "guidance"},
                                        {"headline": f"Analysts lift {symbol} targets after investor day",
                                         "category": "analyst"}],
                              "at": "2026-10-30T15:03:12-08:00"},
            "headlines": [{"title": f"{symbol} shares extend gains as sector rallies",
                           "link": "", "published": "Mon, 02 Nov 2026 14:10:00 GMT"},
                          {"title": f"What to watch for {symbol} into next quarter",
                           "link": "", "published": "Sun, 01 Nov 2026 12:00:00 GMT"}],
        })

    trades, r_values = [], []
    names = ["AMD", "Q", "SUPN", "NVDA", "DE", "KO", "JPM", "ORCL", "UAL",
             "CF", "QCOM", "HD", "MS", "PANW", "LITE", "MOS", "NOW", "GE"]
    for i, symbol in enumerate(names):
        outcome = rng.random()
        if outcome < 0.42:
            state, r = "target", rng.uniform(1.4, 3.2)
        elif outcome < 0.9:
            state, r = "stop", -rng.uniform(0.92, 1.12) if rng.random() > .1 else -rng.uniform(1.4, 2.6)
        else:
            state, r = "closed", rng.uniform(-0.6, 0.9)
        conviction = rng.choice(["strong", "strong", "neutral", "weak"])
        entry = rng.uniform(40, 600)
        risk = entry * rng.uniform(0.016, 0.03)
        shares = int(12_000 / entry)
        exit_ = entry + r * risk
        r_values.append(round(r, 3))
        day = now - timedelta(days=3 + i * 3)
        while day.weekday() >= 5:
            day -= timedelta(days=1)
        verdict = ("no call" if conviction == "neutral" else
                   ("right" if (conviction == "strong") == (r > 0) else "wrong"))
        trades.append({
            "symbol": symbol, "state": state, "tracked": True, "shares": shares,
            "entry": entry, "exit": exit_, "intended_entry": entry,
            "stop": entry - risk, "target": entry + 2 * risk, "r": r,
            "pnl": (exit_ - entry) * shares, "submitted_at": day.isoformat(),
            "filled_at": day.isoformat(), "exit_at": (day + timedelta(days=1)).isoformat(),
            "sector": rng.choice([s for _, s in SYMBOLS]), "conviction": conviction,
            "driver": rng.choice(["company", "sector", "market"]),
            "concern": "none" if conviction == "strong" else "sector-wide selloff, not company news",
            "llm_rank": rng.randint(1, 5), "priority_rank": rng.randint(1, 5),
            "slot": rng.choice(["open", "morning", "midday", "close"]),
            "weekday": day.strftime("%a"), "rsi_gate": rng.random() > .3,
            "liquidity": rng.choice(["5-20M", "20-50M", "over-50M"]),
            "client_order_id": f"smc-{symbol}-{int(day.timestamp())}",
            "verdict": verdict,
            "entry_context": {"opinion": {
                "symbol": symbol, "conviction": conviction, "driver": "company",
                "concern": "none", "evidence": f"{symbol} beats estimates",
                "headlines": [f"{symbol} beats estimates and raises guidance",
                              f"{symbol} expands buyback program"]}},
            "hindsight": None if i % 3 == 2 else {
                "expected": ("The fresh guidance raise looked like company-specific "
                             "news that would support the reversal." if conviction == "strong"
                             else "Nothing in the news explained the sweep, so the read was neutral."),
                "what_happened": ("The move tracked the sector more than the company; "
                                  "peers fell on the same day." if r < 0 else
                                  "Follow-through came with no new headline, consistent "
                                  "with the earlier guidance raise being absorbed."),
                "missed": "The sector was already weakening the week before entry."
                          if r < 0 else "Nothing identifiable.",
                "driver_in_hindsight": "sector" if r < 0 else "company",
                "evidence_quote": "peers fell" if r < 0 else "",
                "lesson": "Check whether the sweep coincided with a sector-wide drop."
                          if r < 0 else "Company-specific catalysts held up here.",
                "grounded": True, "model": "gemma3:12b", "verdict": verdict,
                "at": (day + timedelta(days=2)).isoformat()},
        })

    wins = [t for t in trades if t["r"] > 0]
    losses = [t for t in trades if t["r"] <= 0]
    gross_win = sum(t["pnl"] for t in wins)
    gross_loss = -sum(t["pnl"] for t in losses)
    performance = {
        "closed": len(trades), "wins": len(wins), "losses": len(losses),
        "win_rate": len(wins) / len(trades) * 100,
        "expectancy_r": sum(r_values) / len(r_values),
        "total_r": sum(r_values),
        "realized_pnl": sum(t["pnl"] for t in trades),
        "unrealized_pnl": sum(p["unrealized_pl"] for p in positions),
        "avg_win_r": sum(t["r"] for t in wins) / len(wins),
        "avg_loss_r": sum(t["r"] for t in losses) / len(losses),
        "profit_factor": gross_win / gross_loss if gross_loss else None,
        "best_r": max(r_values), "worst_r": min(r_values),
        "fill_rate": 71.4, "sent": 28, "r_values": r_values,
        "backtest_expectancy_r": 0.218,
    }
    scoreboard = []
    for level in ("strong", "neutral", "weak"):
        rs = [t["r"] for t in trades if t["conviction"] == level]
        scoreboard.append({
            "conviction": level, "n": len(rs),
            "mean_r": sum(rs) / len(rs) if rs else None,
            "win_rate": sum(1 for x in rs if x > 0) / len(rs) * 100 if rs else None,
            "right": sum(1 for t in trades if t["conviction"] == level and t["verdict"] == "right"),
            "wrong": sum(1 for t in trades if t["conviction"] == level and t["verdict"] == "wrong"),
        })

    equity, curve = 100_000.0, []
    for d in range(70):
        equity *= 1 + rng.gauss(0.0006, 0.004)
        curve.append({"time": int((now - timedelta(days=70 - d)).timestamp()),
                      "value": round(equity, 2)})

    runs = []
    for j in range(8):
        at = now - timedelta(minutes=30 * j)
        fresh = rng.randint(3, 11)
        refused = [{"reason": "already holding", "count": rng.randint(0, 2)},
                   {"reason": "position limit reached", "count": rng.randint(0, 3)},
                   {"reason": "reports earnings Thu 11/05", "count": rng.randint(0, 2)},
                   {"reason": "being acquired — price tracks the deal", "count": rng.randint(0, 1)},
                   {"reason": "ex-dividend 2026-11-04 drops 1.52% = 98% of a 1.55% stop",
                    "count": rng.randint(0, 1)}]
        refused = [r for r in refused if r["count"]]
        sent = max(0, fresh - sum(r["count"] for r in refused)) if j == 0 else rng.randint(0, 1)
        summary = {"symbols": 502, "loaded": 499, "liquid": 471,
                   "setups": rng.randint(30, 70), "fresh": fresh, "sent": min(sent, 2),
                   "minutes": 30, "gate": "" if j < 5 else "market is closed",
                   "dry_run": j >= 5}
        run = {"run_id": at.strftime("%Y%m%dT%H%M%S"), "at": at.isoformat(),
               "summary": summary, "refused": refused, "by_stage": {},
               "intents": ["MU", "CAT"][:summary["sent"]],
               "submitted": ["MU", "CAT"][:summary["sent"]],
               "rejected": [], "news": [], "ranking": [], "unprotected": []}
        run["funnel"] = [
            {"label": "Universe", "count": 502}, {"label": "Bars loaded", "count": 499},
            {"label": "Tradeable", "count": 471},
            {"label": "Setups found", "count": summary["setups"]},
            {"label": "Fresh and live", "count": fresh},
            {"label": "Passed every gate", "count": summary["sent"]},
            {"label": "Orders sent", "count": summary["sent"]}]
        runs.append(run)

    gemma = []
    for j, (symbol, conviction, driver, concern) in enumerate([
        ("MU", "strong", "company", "none"),
        ("CAT", "neutral", "sector", "machinery peers moved together"),
        ("PFE", "weak", "company", "litigation over a discontinued drug"),
        ("WBD", "weak", "company", "pending acquisition"),
    ]):
        gemma.append({"at": (now - timedelta(minutes=4 * j)).isoformat(), "kind": "ranking",
                      "symbol": symbol, "conviction": conviction, "driver": driver,
                      "concern": concern, "evidence": {
                          "MU": "raises full-year outlook on demand",
                          "CAT": "machinery stocks slip as orders cool",
                          "PFE": "lawsuit over discontinued drug moves forward",
                          "WBD": "agreed to be acquired"}[symbol],
                      "grounded": True, "llm_rank": j + 1, "priority_rank": 4 - j,
                      "headlines": [f"{symbol} headline one", f"{symbol} headline two"]})
    gemma.append({"at": (now - timedelta(minutes=20)).isoformat(), "kind": "news",
                  "symbol": "WBD", "reason": "being acquired — price tracks the deal",
                  "allowed": False, "checked": True, "deal_role": "target", "earnings": "",
                  "items": [{"headline": "Paramount Skydance to acquire Warner Bros. Discovery",
                             "category": "mna"}], "filings": ["8-K item 1.01"]})
    gemma.append({"at": (now - timedelta(minutes=22)).isoformat(), "kind": "news",
                  "symbol": "MU", "reason": "", "allowed": True, "checked": True,
                  "deal_role": "none", "earnings": "",
                  "items": [{"headline": "MU raises full-year outlook on demand",
                             "category": "guidance"}], "filings": []})
    gemma.sort(key=lambda e: e["at"], reverse=True)

    return {
        "demo": True, "generated_at": now.isoformat(), "market_open": True,
        "account": {"equity": curve[-1]["value"], "cash": 41_220.18,
                    "buying_power": 82_440.36,
                    "day_pl": curve[-1]["value"] - curve[-2]["value"],
                    "day_pl_pct": (curve[-1]["value"] / curve[-2]["value"] - 1) * 100,
                    "status": "ACTIVE", "start_equity": curve[0]["value"]},
        "policy": "30m k=2 stop>=1.5% rr>=1.5 risk=1.0% max=5 [3f9a1c22b0d4]",
        "equity_curve": curve, "positions": positions,
        "working": [{"symbol": "XOM", "shares": 104, "intended_entry": 114.82,
                     "stop": 112.95, "target": 118.66, "submitted_at": now.isoformat()}],
        "watchlist": [{"symbol": "ORCL", "reason": "reports earnings Thu 11/05",
                       "entry_price": 212.4, "stop_price": 208.1, "target_price": 221.0,
                       "trigger_at": now.isoformat(), "added_at": now.isoformat(),
                       "eligible_on": "2026-11-06"}],
        "trades": trades, "performance": performance, "scoreboard": scoreboard,
        "runs": runs, "gemma": gemma, "untracked": [],
    }
