"""The breakout book's rules (execution/orb.py) and the files it shares."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from scanner.advisor import gemma_pool as gp
from scanner.advisor import operator_notes
from scanner.execution import orb, ownership, profit_lock
from scanner.execution.alpaca import round_tick
from scanner.execution.policy import TradingPolicy

ET = ZoneInfo("America/New_York")
OPEN = datetime(2026, 10, 26, 9, 30, tzinfo=ET)
CLOSE = datetime(2026, 10, 26, 16, 0, tzinfo=ET)


def rng(symbol="AAA", high=101.0, low=99.0, rvol=3.0, range_atr=0.5):
    return orb.Range(symbol=symbol, high=high, low=low, volume=1000, rvol=rvol,
                     atr_pct=4.0, range_atr=range_atr)


def test_phase_follows_the_clock():
    assert orb.phase(OPEN + timedelta(minutes=10), OPEN, CLOSE) == orb.WAIT
    assert orb.phase(OPEN + timedelta(minutes=15), OPEN, CLOSE) == orb.ENTER
    assert orb.phase(CLOSE - timedelta(minutes=30), OPEN, CLOSE) == orb.MANAGE
    assert orb.phase(CLOSE - timedelta(minutes=10), OPEN, CLOSE) == orb.FLATTEN


def test_phase_on_an_early_close():
    early = datetime(2026, 11, 27, 13, 0, tzinfo=ET)
    open_ = datetime(2026, 11, 27, 9, 30, tzinfo=ET)
    assert orb.phase(datetime(2026, 11, 27, 12, 50, tzinfo=ET), open_, early) == orb.FLATTEN
    assert orb.phase(datetime(2026, 11, 27, 12, 35, tzinfo=ET), open_, early) == orb.MANAGE


def test_opening_range_measures_against_the_usual():
    r = orb.opening_range("AAA", high=102, low=100, close=100, volume=600,
                          usual_volume=200, atr_pct=4.0)
    assert r.rvol == 3.0 and r.range_atr == 0.5


def test_opening_range_needs_a_baseline_and_an_atr():
    assert orb.opening_range("AAA", high=102, low=100, close=100, volume=600,
                             usual_volume=None, atr_pct=4.0) is None
    assert orb.opening_range("AAA", high=102, low=100, close=100, volume=600,
                             usual_volume=200, atr_pct=0) is None


def test_usual_volume_needs_five_days():
    assert orb.usual_volume([100, 200, 300, 400]) is None
    assert orb.usual_volume([100, 200, 300, 400, 500, 0]) == 300


def test_shortlist_keeps_movers_and_drops_held_and_wide():
    out = orb.shortlist([rng("LOW", rvol=1.5), rng("WIDE", range_atr=1.4), rng("HELD"),
                         rng("B", rvol=2.5), rng("A", rvol=4.0)], exclude={"HELD"})
    assert [r.symbol for r in out] == ["A", "B"]


def test_breakout_needs_close_above_high_and_ema():
    closes = [100.0] * 9 + [101.5]
    assert orb.breakout(rng(), closes)
    assert not orb.breakout(rng(), [100.0] * 9 + [100.9])        # still inside the range
    assert not orb.breakout(rng(), [100.0] * 5)                  # too few bars for the EMA
    assert not orb.breakout(rng(), [110.0] * 9 + [101.5])        # under its 9-period EMA


def test_breakout_is_not_a_chase():
    assert not orb.breakout(rng(), [100.0] * 9 + [103.5])        # more than a range past the high


def test_levels_use_the_range_low_and_1_5r():
    limit, stop, target = orb.levels(rng(), 101.5, offset_pct=0.0, reward_risk=1.5)
    assert (limit, stop) == (101.5, 99.0)
    assert target == round(101.5 + 1.5 * 2.5, 2)


def test_levels_tight_range_uses_a_2pct_stop():
    limit, stop, _ = orb.levels(rng(high=100.2, low=100.0, range_atr=0.2), 100.3,
                                offset_pct=0.0, reward_risk=1.5)
    assert stop == round(100.3 * 0.98, 2)


def test_levels_never_more_than_3pct_under_the_buy():
    _, stop, _ = orb.levels(rng(high=101, low=90), 101.5, offset_pct=0.0, reward_risk=1.5)
    assert stop == round_tick(profit_lock.loss_floor(101.5)) and stop > 101.5 * 0.97 - 0.01


def test_day_pnl_counts_closed_round_trips_only():
    fills = [{"symbol": "AAA", "side": "buy", "qty": "10", "price": "100"},
             {"symbol": "AAA", "side": "sell", "qty": "10", "price": "102"},
             {"symbol": "BBB", "side": "buy", "qty": "5", "price": "50"},
             {"symbol": "ZBRA", "side": "sell", "qty": "1", "price": "300"}]
    assert orb.day_pnl(fills, ["AAA", "BBB"]) == {"AAA": 20.0}


def test_report_reads_plainly():
    assert "AAA $+20" in orb.report("10-26", {"AAA": 20.0}, running=20.0, days=1, shortlisted=12)
    assert "no trades" in orb.report("10-26", {}, running=0.0, days=1, shortlisted=12)


def test_orb_owns_its_prefix_and_sleeve():
    assert ownership.strategy_of("orb-AAA-1") == ownership.ORB
    assert ownership.ORB in ownership.OWN_SLEEVE
    p = TradingPolicy()
    # ORB's 10% went to the sweep on 2026-11-02. New sweep buys are off from 2026-11-10.
    assert p.orb_allocation_pct == 0.0
    assert (p.gemma_allocation_pct + p.orb_allocation_pct + p.core_allocation_pct
            + p.small_allocation_pct + p.swing_allocation_pct) == 55.0
    assert "orb" in p.flatten_strategies


def test_pool_round_trip(tmp_path):
    rows = [gp.PoolRow(symbol="AAA", sector="Tech", r1m=1.0, r3m=2.0, mom_12_1=3.0, atr_pct=2.5)]
    gp.save(rows, date(2026, 10, 23), directory=tmp_path)
    assert gp.latest(date(2026, 10, 26), directory=tmp_path) == rows       # over a weekend
    assert gp.latest(date(2026, 10, 30), directory=tmp_path) == []        # too old


def test_replace_prefixed_keeps_one_report(tmp_path):
    path = tmp_path / "notes.json"
    path.write_text('[{"id": "keep", "from": "2026-10-01", "until": "2026-12-01", "text": "x"}]')
    for day in ("2026-10-26", "2026-10-27"):
        operator_notes.replace_prefixed("orb-day-", {"id": f"orb-day-{day}", "from": day,
                                                     "until": day, "text": day}, path)
    assert [n.id for n in operator_notes.read(path)] == ["keep", "orb-day-2026-10-27"]
