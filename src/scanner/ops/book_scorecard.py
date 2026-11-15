"""The book scorecard: each book's result since the fresh start, against what it is judged by.

Operator 2026-11-01 ("start fresh on monday", then "yes" to a daily scorecard): from the close
of START_BASE (Friday 2026-10-30) every book is measured the same way --

    P&L = sells since the start - buys since the start + value now - value at the start

where "value at the start" is the book's shares on START_BASE priced at that day's close. The
percent is that P&L over the book's sleeve at the start (equity x its allocation). Each book is
set beside its benchmark over the same days:

    Index core   SPY / QQQ half and half      Rules book   SPY
    Small caps   IWM                          Sweep        its ghost (the same rules on paper)

and the acceleration shadow (accel_shadow) is graded underneath. PURE: values in, values out.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from typing import Any

START_BASE = date(2026, 10, 30)      # the last close before the fresh start
START = date(2026, 11, 2)            # the first session measured
#: (book label, ownership strategy, policy allocation field, benchmark: {symbol: weight})
BOOKS: tuple[tuple[str, str, str, dict[str, float]], ...] = (
    ("Index core", "core", "core_allocation_pct", {"SPY": 0.5, "QQQ": 0.5}),
    ("Rules book", "gemma", "gemma_allocation_pct", {"SPY": 1.0}),
    ("Small caps", "small", "small_allocation_pct", {"IWM": 1.0}),
    ("Sweep", "smc", "swing_allocation_pct", {"SPY": 1.0}),
)

#: New sweep buys draw on 0% from 2026-11-10. This scorecard still divides the sweep's
#: dollars by the 20% sleeve that book traded from 2026-11-02, so a zero live allocation
#: does not blank the row or divide by zero. It does not size an order.
SWEEP_MEASURED_PCT = 20.0


def allocation_pct(field: str, live_pct: float) -> float:
    """Sleeve percent the scorecard divides by.

    Live policy, except a sweep sleeve at or below zero keeps SWEEP_MEASURED_PCT
    for the window that book actually traded.
    """
    if field == "swing_allocation_pct" and live_pct <= 0:
        return SWEEP_MEASURED_PCT
    return live_pct


def fills_since(orders: Iterable[Mapping[str, Any]], since: date) -> list[dict[str, Any]]:
    """Filled orders (bracket legs included) on or after `since` (UTC date of the fill)."""
    out = []
    for o in orders:
        for leg in (o, *(o.get("legs") or ())):
            qty, px, at = float(leg.get("filled_qty") or 0), float(leg.get("filled_avg_price") or 0), leg.get("filled_at")
            if qty > 0 and px > 0 and at and str(at)[:10] >= since.isoformat() \
                    and leg.get("side") in ("buy", "sell"):
                out.append({"symbol": leg["symbol"], "side": leg["side"], "qty": qty, "px": px, "at": str(at)})
    return out


def book_pnl(*, book: str, owner: Mapping[str, str], held: Mapping[str, tuple[float, float]],
             fills: Sequence[Mapping[str, Any]], base_close: Mapping[str, float]) -> dict[str, float] | None:
    """One book's P&L since the start. `held` is {symbol: (qty, market value)} now; `owner` maps
    a symbol to its book (newest filled buy). None when a start price is missing."""
    syms = {s for s, b in owner.items() if b == book and (s in held or any(f["symbol"] == s for f in fills))}
    net_qty: dict[str, float] = defaultdict(float)
    cash = 0.0
    for f in fills:
        if f["symbol"] not in syms:
            continue
        sign = 1.0 if f["side"] == "buy" else -1.0
        net_qty[f["symbol"]] += sign * f["qty"]
        cash -= sign * f["qty"] * f["px"]
    start_value = 0.0
    for s in syms:
        start_qty = held.get(s, (0.0, 0.0))[0] - net_qty[s]
        if abs(start_qty) > 1e-9:
            if s not in base_close:
                return None
            start_value += start_qty * base_close[s]
    value_now = sum(held[s][1] for s in syms if s in held)
    return {"pnl": cash + value_now - start_value, "start_value": start_value, "value_now": value_now}


def benchmark_pct(weights: Mapping[str, float], base: Mapping[str, float], last: Mapping[str, float]) -> float | None:
    if any(s not in base or s not in last for s in weights):
        return None
    return sum(w * (last[s] / base[s] - 1) * 100 for s, w in weights.items())


def ghost_since(trades: Iterable[Mapping[str, Any]], since: date) -> dict[str, float]:
    """The ghost's trades entered on or after `since`: closed count, wins, R, open count."""
    closed = won = 0
    total_r = 0.0
    opened = 0
    for t in trades:
        if str(t.get("entry_at", ""))[:10] < since.isoformat():
            continue
        risk = float(t["entry"]) - float(t["stop"])
        if not t.get("exit_at"):
            opened += 1
            continue
        r = (float(t["exit_price"]) - float(t["entry"])) / risk if risk > 0 else 0.0
        closed += 1
        won += r > 0
        total_r += r
    return {"closed": closed, "won": won, "r": total_r, "open": opened}


def verdict(book_pct: float | None, bench_pct: float | None) -> str:
    if book_pct is None or bench_pct is None:
        return "no data"
    gap = book_pct - bench_pct
    if abs(gap) < 0.005:
        return "even"
    return f"{'ahead' if gap > 0 else 'behind'} by {abs(gap):.2f} pts"
