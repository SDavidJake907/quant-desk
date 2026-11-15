"""The book scorecard and the acceleration shadow (operator 2026-11-01). Neither ever trades."""

from datetime import date
from types import SimpleNamespace

from scanner.ops import accel_shadow, book_scorecard as bs


def _order(sym, side, qty, px, at, legs=()):
    return {"symbol": sym, "side": side, "filled_qty": str(qty), "filled_avg_price": str(px),
            "filled_at": at, "legs": list(legs)}


def test_fills_since_reads_bracket_legs_and_skips_older_fills():
    stop_leg = _order("APA", "sell", 10, 49.0, "2026-11-03T15:00:00Z")
    orders = [_order("APA", "buy", 10, 50.0, "2026-10-27T14:00:00Z", legs=[stop_leg]),
              _order("TXN", "buy", 6, 300.0, "2026-11-02T14:00:00Z")]
    got = {(f["symbol"], f["side"]) for f in bs.fills_since(orders, date(2026, 11, 2))}
    assert got == {("APA", "sell"), ("TXN", "buy")}


def test_book_pnl_counts_start_holdings_buys_and_sales():
    owner = {"SPY": "core", "QQQ": "core", "APA": "gemma"}
    # Start: 64 SPY at the 10-30 close of 750. Since: sold 32 at 760, bought 17 QQQ at 600.
    fills = [{"symbol": "SPY", "side": "sell", "qty": 32, "px": 760.0},
             {"symbol": "QQQ", "side": "buy", "qty": 17, "px": 600.0}]
    held = {"SPY": (32, 32 * 770.0), "QQQ": (17, 17 * 610.0), "APA": (10, 500.0)}
    res = bs.book_pnl(book="core", owner=owner, held=held, fills=fills, base_close={"SPY": 750.0})
    # 32 x 760 - 17 x 600 + (32 x 770 + 17 x 610) - 64 x 750 = 1,130
    assert abs(res["pnl"] - 1130.0) < 1e-6 and res["start_value"] == 48000.0


def test_a_position_sold_out_still_counts_its_sale():
    owner = {"VSTS": "small"}
    fills = [{"symbol": "VSTS", "side": "sell", "qty": 3, "px": 15.0}]
    res = bs.book_pnl(book="small", owner=owner, held={}, fills=fills, base_close={"VSTS": 14.8})
    assert abs(res["pnl"] - 0.6) < 1e-9 and res["value_now"] == 0


def test_missing_start_price_is_no_data_not_a_guess():
    res = bs.book_pnl(book="core", owner={"SPY": "core"}, held={"SPY": (1, 750.0)}, fills=[], base_close={})
    assert res is None and bs.verdict(None, 1.0) == "no data"


def test_a_zero_sweep_sleeve_still_measures_the_window_it_traded():
    assert bs.allocation_pct("swing_allocation_pct", 0.0) == bs.SWEEP_MEASURED_PCT == 20.0
    assert bs.allocation_pct("swing_allocation_pct", 20.0) == 20.0
    assert bs.allocation_pct("gemma_allocation_pct", 0.0) == 0.0


def test_benchmark_blend_and_verdict():
    base, last = {"SPY": 100.0, "QQQ": 100.0}, {"SPY": 102.0, "QQQ": 104.0}
    assert abs(bs.benchmark_pct({"SPY": 0.5, "QQQ": 0.5}, base, last) - 3.0) < 1e-9
    assert bs.verdict(4.0, 3.0) == "ahead by 1.00 pts" and bs.verdict(3.0, 3.0) == "even"


def test_ghost_counts_only_trades_entered_since_the_start():
    trades = [{"entry_at": "2026-10-30T10:00", "entry": 100, "stop": 99, "exit_at": "x", "exit_price": 103},
              {"entry_at": "2026-11-02T10:00", "entry": 100, "stop": 99, "exit_at": "x", "exit_price": 102},
              {"entry_at": "2026-11-02T11:00", "entry": 100, "stop": 98, "exit_at": "x", "exit_price": 98},
              {"entry_at": "2026-11-03T10:00", "entry": 100, "stop": 99, "exit_at": None, "exit_price": None}]
    assert bs.ghost_since(trades, date(2026, 11, 2)) == {"closed": 2, "won": 1, "r": 1.0, "open": 1}


def test_acceleration_is_last_month_minus_the_prior_months_average():
    # +10% last month after a flat two months: speeding up by 10 points
    assert abs(accel_shadow.acceleration(10.0, 10.0) - 10.0) < 1e-9
    # 3 months +21% made of +10% a month: no acceleration
    assert abs(accel_shadow.acceleration(10.0, 33.1)) < 1e-6
    assert accel_shadow.acceleration(None, 5.0) is None


def test_record_marks_the_picked_names():
    rows = [SimpleNamespace(symbol="TXN", r1m=10.0, r3m=10.0, mom_12_1=88.0),
            SimpleNamespace(symbol="ADI", r1m=None, r3m=5.0, mom_12_1=70.0)]
    rec = accel_shadow.record("2026-11-02", rows, ["TXN"])
    assert rec["names"][0] == {"symbol": "TXN", "accel": 10.0, "mom_12_1": 88.0, "picked": True}
    assert rec["names"][1]["accel"] is None and not rec["names"][1]["picked"]


def test_grade_waits_for_the_horizon_then_compares_the_halves():
    days = [f"2026-11-{d:02d}" for d in range(1, 31)]
    up = [(d, 100.0 + i) for i, d in enumerate(days)]       # rises
    down = [(d, 100.0) for d in days]                        # flat
    rec = {"day": "2026-11-03", "names": [{"symbol": "UP", "accel": 2.0}, {"symbol": "DN", "accel": -2.0}]}
    g = accel_shadow.grade([rec], {"UP": up, "DN": down}, horizon=5)
    assert g["graded"] == 1 and g["avg_gap"] > 0 and g["positive_days"] == 1
    late = {"day": "2026-11-29", "names": rec["names"]}
    assert accel_shadow.grade([late], {"UP": up, "DN": down}, horizon=5)["pending"] == 1
