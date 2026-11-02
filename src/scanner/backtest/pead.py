"""Post-earnings announcement drift: does a strong earnings reaction keep going?

THE CLAIM BEING TESTED
After a company reports, the stock moves. The academic finding (Ball & Brown,
1968, and decades since) is that it tends to KEEP moving the same way for
weeks — the market under-reacts on the day. If that holds here, buying the
strongest reactions and holding them should beat the index.

HOW "SURPRISE" IS MEASURED WITHOUT ANALYST ESTIMATES
The classic version needs consensus EPS, which this project has no source for.
The well-known substitute is the market's own verdict: the stock's return
around the announcement minus the index's over the same days. A stock that
jumps 9% while the market is flat was surprised, whatever the EPS said.

    reaction window   close[a-2] -> close[a+1]    (a = filing date's bar)
    entry             close[a+1]                  (reaction fully known)
    exit              close[a+1+hold]

The window starts a day before the filing because 8-Ks are sometimes filed
the day AFTER the release (see `sources.earnings_history`). Entry is at the
close the window ends on, so nothing in the trade uses a price from after
entry. Returns are EXCESS over the S&P 500 index, so market drift is removed.

THE CONTROL THAT MATTERS MOST
"Stocks that just jumped keep going" might be plain short-term momentum and
have nothing to do with earnings. So the same reaction is measured on RANDOM
non-earnings days in the same stocks, bucketed by the same cutoffs. If the
non-earnings jumps drift as much as the earnings jumps, there is no earnings
effect — only momentum, which would be a different strategy.

WHAT THIS DELIBERATELY DOES NOT DO
No beta adjustment: a high-beta stock in a rising market beats the index for
free. The random-day control in the same stocks absorbs much of that, which is
one more reason it is the comparison to read.
"""

from __future__ import annotations

import datetime as dt
import math
import random
import statistics
from dataclasses import dataclass, field

TOP, BOTTOM, MIDDLE = "top", "bottom", "middle"


@dataclass(frozen=True)
class Window:
    """One announcement (or one random control day) and what followed."""

    symbol: str
    anchor: dt.date
    reaction: float                     # abnormal %, stock minus index
    excess: dict[int, float] = field(default_factory=dict)  # hold -> excess %
    is_earnings: bool = True


def quarter(day: dt.date) -> str:
    return f"{day.year}Q{(day.month - 1) // 3 + 1}"


def align(dates, closes, bench_dates, bench_closes):
    """Keep only the days both series traded, so every return is measured over
    the same calendar span for the stock and the index."""
    bench = dict(zip(bench_dates, bench_closes))
    out_d, out_c, out_b = [], [], []
    for day, close in zip(dates, closes):
        if day in bench and close > 0 and bench[day] > 0:
            out_d.append(day)
            out_c.append(close)
            out_b.append(bench[day])
    return out_d, out_c, out_b


def _pct(a: float, b: float) -> float:
    return (b / a - 1.0) * 100.0


def window_at(symbol, dates, closes, bench, a: int, holds, *, is_earnings=True):
    """Measure one window anchored at bar `a`. None if the reaction window or
    the entry does not fit inside the series."""
    if a - 2 < 0 or a + 1 >= len(closes):
        return None
    reaction = _pct(closes[a - 2], closes[a + 1]) - _pct(bench[a - 2], bench[a + 1])
    entry = a + 1
    excess = {}
    for hold in holds:
        exit_ = entry + hold
        if exit_ < len(closes):
            excess[hold] = (_pct(closes[entry], closes[exit_])
                            - _pct(bench[entry], bench[exit_]))
    return Window(symbol=symbol, anchor=dates[a], reaction=reaction,
                  excess=excess, is_earnings=is_earnings)


def _index_on_or_before(dates, when: dt.date) -> int | None:
    lo, hi, found = 0, len(dates) - 1, None
    while lo <= hi:
        mid = (lo + hi) // 2
        if dates[mid] <= when:
            found, lo = mid, mid + 1
        else:
            hi = mid - 1
    return found


def earnings_windows(symbol, dates, closes, bench, announced, holds):
    out = []
    first = dates[0] if dates else None
    for day in announced:
        if first is None or day < first:
            continue
        a = _index_on_or_before(dates, day)
        if a is None:
            continue
        w = window_at(symbol, dates, closes, bench, a, holds)
        if w is not None:
            out.append(w)
    return out


def control_windows(symbol, dates, closes, bench, announced, holds,
                    rng: random.Random, *, per_event: int = 2, gap: int = 10):
    """Random days in the same stock, at least `gap` bars from any report.

    Same count per stock as its earnings sample times `per_event`, so the
    control is not dominated by whichever stocks happen to have long series.
    """
    near: set[int] = set()
    for day in announced:
        a = _index_on_or_before(dates, day)
        if a is not None:
            near.update(range(a - gap, a + gap + 1))
    eligible = [i for i in range(2, len(closes) - 1) if i not in near]
    wanted = per_event * sum(1 for d in announced if dates and d >= dates[0])
    if not eligible or not wanted:
        return []
    picks = rng.sample(eligible, min(wanted, len(eligible)))
    out = []
    for a in sorted(picks):
        w = window_at(symbol, dates, closes, bench, a, holds, is_earnings=False)
        if w is not None:
            out.append(w)
    return out


def cutoffs(reactions, fraction: float = 0.2) -> tuple[float, float]:
    """Bottom and top `fraction` boundaries. Computed on TRAIN ONLY and then
    applied unchanged to the holdout, like the stop floor was."""
    ordered = sorted(reactions)
    if len(ordered) < 5:
        raise ValueError("too few reactions to set cutoffs")
    n = len(ordered)
    low = ordered[max(0, int(n * fraction) - 1)]
    high = ordered[min(n - 1, int(n * (1 - fraction)))]
    return low, high


def bucket(w: Window, low: float, high: float) -> str:
    if w.reaction >= high:
        return TOP
    if w.reaction <= low:
        return BOTTOM
    return MIDDLE


def stats(values) -> tuple[float, float, float]:
    values = list(values)
    if len(values) < 2:
        return (values[0] if values else 0.0), 0.0, 0.0
    mean = statistics.fmean(values)
    se = statistics.stdev(values) / math.sqrt(len(values))
    return mean, se, (mean / se if se else 0.0)


def by_quarter(windows, hold: int, which: str, low: float, high: float,
               *, min_n: int = 10) -> dict[str, float]:
    """Mean excess per calendar quarter for one bucket.

    Hundreds of companies report inside the same few weeks, so their trades
    are one correlated cross-section, not hundreds of independent bets. One
    number per quarter is the honest unit for a t-statistic.
    """
    groups: dict[str, list[float]] = {}
    for w in windows:
        if hold in w.excess and (which == "all" or bucket(w, low, high) == which):
            groups.setdefault(quarter(w.anchor), []).append(w.excess[hold])
    return {q: statistics.fmean(v) for q, v in groups.items() if len(v) >= min_n}


def quarter_edges(events, controls, hold, which, low, high, *, min_n=10):
    """Per quarter: earnings bucket minus the same bucket on non-earnings days.
    Only quarters where both sides have enough trades count."""
    real = by_quarter(events, hold, which, low, high, min_n=min_n)
    ctrl = by_quarter(controls, hold, which, low, high, min_n=min_n)
    return {q: real[q] - ctrl[q] for q in sorted(real) if q in ctrl}


def split_quarters(windows, holdout: float) -> tuple[set[str], set[str]]:
    """Chronological: the most recent `holdout` share of quarters is held back."""
    quarters = sorted({quarter(w.anchor) for w in windows})
    cut = int(round(len(quarters) * (1 - holdout)))
    return set(quarters[:cut]), set(quarters[cut:])
