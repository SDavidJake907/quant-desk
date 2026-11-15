from datetime import date, datetime, timedelta

import pytest

from scanner.ops import peak_sell as ps


def _r(pl, **vals):
    base = dict.fromkeys(ps.CHECKS)
    base.update(vals)
    return ps.Readings(symbol="X", pl_pct=pl, values=base)


HOT = {"trend_5m": 1.0, "vwap_5m": 0.5, "crsi_1h": 80, "stc_1h": 99, "stc_1d": 95, "pctb_1d": 0.9}


def test_all_six_in_profit_is_eligible():
    r = _r(2.0, **HOT)
    assert r.score() == 6 and r.may_sell()


def test_red_position_never_eligible():
    assert not _r(-0.5, **HOT).may_sell()


def test_needs_four_of_six():
    three = dict(HOT, stc_1d=10, pctb_1d=0.2, crsi_1h=20)
    four = dict(HOT, stc_1d=10, pctb_1d=0.2)
    assert not _r(2.5, **three).may_sell()
    assert _r(2.5, **four).may_sell()


def test_small_gains_are_held():
    """Under MIN_PROFIT_PCT (2%) a position is never READY, however hot."""
    assert not _r(1.9, **HOT).may_sell()
    assert _r(2.0, **HOT).may_sell()


def test_missing_reading_counts_as_not_met():
    r = _r(1.0, trend_5m=1.0, vwap_5m=0.5, crsi_1h=80)
    assert r.met()["stc_1h"] is None and r.score() == 3 and not r.may_sell()


def test_thresholds_are_inclusive():
    r = _r(1.0, trend_5m=1.0, vwap_5m=0.20, crsi_1h=63, stc_1h=85, stc_1d=80, pctb_1d=0.80)
    assert r.score() == 6


def test_approved_needs_her_sell_and_eligibility():
    ok = ps.Readings("A", 2.5, HOT)
    red = ps.Readings("B", -1.0, HOT)
    ans = {"positions": [{"symbol": "A", "action": "sell", "why": "w"},
                         {"symbol": "B", "action": "sell", "why": "w"},
                         {"symbol": "C", "action": "sell", "why": "w"}]}
    assert ps.approved(ans, [ok, red]) == [("A", "w")]
    assert ps.approved({"positions": [{"symbol": "A", "action": "hold", "why": ""}]}, [ok]) == []
    assert ps.approved(None, [ok]) == []


def test_hourly_aligns_to_the_open():
    t0 = datetime(2026, 10, 21, 9, 30)
    five = [(t0 + timedelta(minutes=5 * i), 1, 2, 0.5, 1 + i, 10) for i in range(14)]
    h = ps.hourly(five)
    assert len(h) == 2 and h[0][4] == 12 and h[1][0] == datetime(2026, 10, 21, 10, 30)


def test_readings_computes_every_check_with_enough_data():
    t0 = datetime(2026, 9, 1, 9, 30)
    five = []
    for d in range(30):
        for i in range(78):
            p = 100 + d + i * 0.01
            five.append((t0 + timedelta(days=d, minutes=5 * i), p, p + 0.1, p - 0.1, p, 1000))
    r = ps.readings("X", 1.0, five, [100 + i * 0.5 for i in range(200)])
    assert all(v is not None for v in r.values.values())


def test_prompt_contains_plan_and_readings():
    text = ps.prompt([ps.Readings("A", 1.5, HOT)], market="up")
    assert "[A]" in text and "6 of 6" in text and "ELIGIBLE" in text and "PLAN" in text


def test_atr_percent_of_uses_the_price_the_card_shows():
    """The stored ATR is a percent of the official close. The card's gap is a
    percent of the live price, so the percent has to be restated on that price
    or the ATR count is the dollar gap over the wrong base."""
    assert ps.atr_percent_of(3.154, 40.38, 40.38) == pytest.approx(3.154)
    assert ps.atr_percent_of(3.154, 40.38, 39.40) == pytest.approx(3.154 * 40.38 / 39.40)
    assert ps.atr_percent_of(None, 40.38, 39.40) is None


def test_readings_name_the_regular_session_they_used():
    t0 = datetime(2026, 9, 1, 9, 30)
    five = []
    for d in range(30):
        for i in range(78):
            p = 100 + d + i * 0.01
            five.append((t0 + timedelta(days=d, minutes=5 * i), p, p + 0.1, p - 0.1, p, 1000))
    r = ps.readings("X", 1.0, five, [100 + i * 0.5 for i in range(200)])
    assert r.session_day == "2026-09-30"


def test_stop_note_shows_distance_in_atr():
    note = ps.stop_note(100.0, 90.0, 4.0)
    assert "$90.00" in note and "11.1%" in note and "2.8 ATR" in note
    assert ps.stop_note(100.0, None, 4.0) == "no live stop found"


def test_table_shows_the_atr_stop():
    r = ps.Readings("A", 1.0, HOT, stop_note="$90.00, 11.1% below")
    assert "ATR stop: $90.00" in ps.table(r)


def test_ready_then_sell_on_trend_turn():
    hot = ps.Readings("A", 2.0, HOT)
    marks, ready, sell = ps.update_marks({}, [hot], now="t1", prices={"A": 10.0})
    assert ready == ["A"] and sell == [] and marks["A"]["ready_price"] == 10.0
    # still climbing: no sale
    _, ready, sell = ps.update_marks(marks, [hot], now="t2", prices={})
    assert ready == [] and sell == []
    # the 5-minute trend turns down while in profit: sell
    turned = ps.Readings("A", 1.5, dict(HOT, trend_5m=0.0, stc_1h=10))
    _, _, sell = ps.update_marks(marks, [turned], now="t3", prices={})
    assert sell == ["A"]


def test_the_10_7_book_sells_a_ready_downturn_and_the_other_books_do_not():
    assert ps.rules_peak_sell("gemma", date(2026, 11, 3)) is True
    assert ps.rules_peak_sell("gemma", date(2026, 11, 10)) is True
    assert ps.rules_peak_sell("gemma", date(2026, 11, 2)) is False
    assert ps.rules_peak_sell("gemma", None) is False
    assert ps.rules_peak_sell("small", date(2026, 11, 11)) is False
    assert ps.rules_peak_sell("smc", date(2026, 11, 10)) is False
    assert ps.rules_peak_sell("core", date(2026, 11, 10)) is False


def test_no_sell_when_red_or_not_marked():
    down = dict(HOT, trend_5m=0.0)
    assert not ps.should_sell(ps.Readings("A", -0.5, down), True)
    assert not ps.should_sell(ps.Readings("A", 2.0, down), False)
    assert not ps.should_sell(ps.Readings("A", 2.0, dict(HOT, trend_5m=None)), True)


def test_marks_dropped_when_position_gone():
    marks, _, _ = ps.update_marks({"OLD": {"ready_at": "x"}}, [ps.Readings("A", 1.0, HOT)], now="t", prices={})
    assert "OLD" not in marks


def test_status_text_shows_stage():
    rs = [ps.Readings("A", 1.0, HOT), ps.Readings("B", -1.0, HOT)]
    text = ps.status_text(rs, {"A": {"ready_at": "10-22 14:00 ET", "ready_price": 10.0}})
    assert "A:" in text and "READY since 10-22 14:00 ET at $10.00" in text and "B:" in text and "not ready" in text


# --- 24/5: the 5-minute trend outside regular hours (operator 2026-10-22) ----

def _candles(end: datetime, n: int, step: float) -> list:
    """n 5-minute candles ending at `end`, closes moving by `step` each bar."""
    out = []
    for i in range(n):
        t = end - timedelta(minutes=5 * (n - 1 - i))
        c = 100 + step * i
        out.append((t, c, c + 0.1, c - 0.1, c, 1000.0))
    return out


def test_extended_trend_reads_a_fresh_after_hours_tape():
    now = datetime(2026, 10, 22, 17, 0)
    up = _candles(now - timedelta(minutes=5), 80, 0.05)
    down = up[:-1] + [(up[-1][0], 90, 90.1, 89.9, 90, 1000.0)]     # the last bar breaks hard
    assert ps.extended_trend(up, now) == 1.0
    assert ps.extended_trend(down, now) == 0.0


def test_extended_trend_refuses_a_stale_tape():
    """Overnight there are no Yahoo candles: the last one is hours old -> no trend, no sell."""
    now = datetime(2026, 10, 22, 23, 0)
    stale = _candles(datetime(2026, 10, 22, 19, 55), 80, 0.05)
    assert ps.extended_trend(stale, now) is None


def test_extended_trend_ignores_the_candle_still_forming():
    now = datetime(2026, 10, 22, 17, 2)
    tape = _candles(datetime(2026, 10, 22, 16, 55), 80, 0.05)
    forming = tape + [(datetime(2026, 10, 22, 17, 0), 50, 50.1, 49.9, 50, 1000.0)]
    assert ps.extended_trend(forming, now) == 1.0


def test_extended_trend_needs_enough_candles():
    now = datetime(2026, 10, 22, 17, 0)
    assert ps.extended_trend(_candles(now - timedelta(minutes=5), 20, 0.05), now) is None
