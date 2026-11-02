"""Post-earnings drift study: the arithmetic that decides what it can claim."""

from __future__ import annotations

import datetime as dt
import random

import pytest

from scanner.backtest.pead import (
    BOTTOM, MIDDLE, TOP, Window, align, bucket, control_windows, cutoffs,
    earnings_windows, quarter, quarter_edges, split_quarters, window_at,
)
from scanner.sources.earnings_history import parse_earnings_dates

DAYS = [dt.date(2026, 1, 1) + dt.timedelta(days=i) for i in range(40)]


def test_only_8k_item_202_counts_as_earnings():
    recent = {
        "form": ["8-K", "8-K", "10-Q", "8-K"],
        "items": ["2.02,9.01", "5.02", "", "2.02"],
        "filingDate": ["2026-02-01", "2026-03-01", "2026-04-01", "2025-11-01"],
    }
    assert parse_earnings_dates(recent) == (dt.date(2025, 11, 1), dt.date(2026, 2, 1))


def test_alignment_drops_days_the_index_did_not_trade():
    d, c, b = align(DAYS[:3], [10, 11, 12], [DAYS[0], DAYS[2]], [100, 102])
    assert d == [DAYS[0], DAYS[2]] and c == [10, 12] and b == [100, 102]


def test_reaction_is_abnormal_and_entry_is_the_close_it_ends_on():
    closes = [100.0] * 10
    bench = [1000.0] * 10
    closes[6] = 110.0                       # a+1 for a=5: the stock jumps 10%
    bench[6] = 1010.0                       # the index rose 1%
    closes[8] = 121.0                       # two bars after entry: +10% more
    w = window_at("X", DAYS[:10], closes, bench, 5, (2,))
    assert w.reaction == pytest.approx(9.0)          # 10% - 1%
    assert w.excess[2] == pytest.approx(10.0 - (-0.990099), rel=1e-4)


def test_the_hold_never_reads_past_the_series():
    w = window_at("X", DAYS[:8], [100.0] * 8, [1000.0] * 8, 5, (1, 5))
    assert 1 in w.excess and 5 not in w.excess


def test_a_window_that_cannot_see_its_reaction_is_dropped():
    assert window_at("X", DAYS[:5], [1.0] * 5, [1.0] * 5, 1, (1,)) is None
    assert window_at("X", DAYS[:5], [1.0] * 5, [1.0] * 5, 4, (1,)) is None


def test_a_filing_on_a_holiday_anchors_to_the_bar_before():
    dates = [DAYS[0], DAYS[1], DAYS[2], DAYS[4], DAYS[5], DAYS[6]]
    out = earnings_windows("X", dates, [1.0] * 6, [1.0] * 6, (DAYS[3],), (1,))
    assert out[0].anchor == DAYS[2]


def test_controls_stay_away_from_every_report():
    dates = DAYS
    closes = [100.0] * 40
    ws = control_windows("X", dates, closes, closes, (DAYS[20],), (1,),
                         random.Random(1), per_event=5, gap=10)
    assert ws and all(abs((w.anchor - DAYS[20]).days) > 10 for w in ws)
    assert not any(w.is_earnings for w in ws)


def test_cutoffs_split_off_the_tails():
    low, high = cutoffs(list(range(100)), 0.2)
    assert low == 19 and high == 80
    assert bucket(Window("X", DAYS[0], 85), low, high) == TOP
    assert bucket(Window("X", DAYS[0], 5), low, high) == BOTTOM
    assert bucket(Window("X", DAYS[0], 50), low, high) == MIDDLE


def test_the_holdout_is_the_most_recent_quarters():
    ws = [Window("X", dt.date(2024 + i // 4, 3 * (i % 4) + 1, 15), 0.0) for i in range(9)]
    train, hold = split_quarters(ws, 1 / 3)
    assert max(train) < min(hold)
    assert hold == {"2025Q3", "2025Q4", "2026Q1"}


def test_the_edge_is_earnings_minus_the_same_bucket_on_ordinary_days():
    day = dt.date(2025, 2, 1)
    events = [Window("X", day, 10.0, {20: 3.0}) for _ in range(10)]
    controls = [Window("Y", day, 10.0, {20: 1.0}, is_earnings=False) for _ in range(10)]
    edges = quarter_edges(events, controls, 20, TOP, low=-5.0, high=5.0)
    assert edges == {quarter(day): pytest.approx(2.0)}
