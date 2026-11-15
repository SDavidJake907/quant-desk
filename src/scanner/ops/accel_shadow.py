"""Acceleration shadow: is a name's momentum speeding up or slowing down? Recorded, never traded.

Operator 2026-10-25 asked whether calculus would help; 2026-11-01 "yes" to recording it as a
shadow. Momentum is the first derivative (rate of change); ACCELERATION is the second: the last
month's return minus the average monthly return of the two months before it,

    prior = ((1 + r3m) / (1 + r1m)) ** (1/2) - 1        accel = r1m - prior     (percent points)

At each 9:45 rules pick every name passing the buy checks is recorded with its acceleration and
whether the book picked it (data/shadow/accel.jsonl). The scorecard grades each day's names 21
sessions on: did the speeding-up half (accel > 0) beat the slowing-down half? A candidate
tie-breaker for the rules book only if it does over 30+ pick days, and only after the freeze
(2026-12-31). PURE: values in, values out.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

HORIZON = 21          # sessions graded on
MIN_DAYS = 30         # pick days before the grade means anything


def acceleration(r1m: float | None, r3m: float | None) -> float | None:
    """Percent points: the last month's return minus the prior two months' monthly average."""
    if r1m is None or r3m is None or r1m <= -100:
        return None
    prior = ((1 + r3m / 100) / (1 + r1m / 100)) ** 0.5 - 1
    return r1m - prior * 100


def record(day: str, rows: Iterable[Any], picked: Iterable[str]) -> dict[str, Any]:
    """One pick day: every passing row (PoolRow-like) with its acceleration."""
    chosen = set(picked)
    return {"day": day, "names": [
        {"symbol": r.symbol, "accel": None if (a := acceleration(r.r1m, r.r3m)) is None else round(a, 2),
         "mom_12_1": r.mom_12_1, "picked": r.symbol in chosen}
        for r in rows]}


def forward_return(closes: Sequence[tuple[str, float]], day: str, horizon: int = HORIZON) -> float | None:
    """% from the last close before `day` to the close `horizon` sessions later; None if not yet."""
    before = [i for i, (d, _) in enumerate(closes) if d < day]
    if not before:
        return None
    i = before[-1]
    if i + horizon >= len(closes):
        return None
    return (closes[i + horizon][1] / closes[i][1] - 1) * 100


def grade(records: Iterable[Mapping[str, Any]], closes: Mapping[str, Sequence[tuple[str, float]]],
          horizon: int = HORIZON) -> dict[str, Any]:
    """Per graded day: the speeding-up names' average forward return minus the slowing-down
    names'. Returns days graded, days pending, the average gap and how many days it was positive."""
    gaps, pending = [], 0
    for rec in records:
        up, down = [], []
        for n in rec["names"]:
            if n.get("accel") is None:
                continue
            fr = forward_return(closes.get(n["symbol"], ()), rec["day"], horizon)
            if fr is None:
                continue
            (up if n["accel"] > 0 else down).append(fr)
        if up and down:
            gaps.append(sum(up) / len(up) - sum(down) / len(down))
        else:
            pending += 1
    return {"graded": len(gaps), "pending": pending,
            "avg_gap": sum(gaps) / len(gaps) if gaps else None,
            "positive_days": sum(g > 0 for g in gaps)}
