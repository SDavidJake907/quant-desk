"""The rules book's 9:45 pick (operator 2026-10-26: "stop Gemma from picking, have the code
find the stocks"): buyable S&P 500 names, strongest 12-1 momentum first, into the free slots."""

from scanner.advisor.gemma_pool import PoolRow
from scanner.execution import rules_pick as rp


def _row(symbol: str, mom: float | None, sector: str = "Energy") -> PoolRow:
    return PoolRow(symbol=symbol, sector=sector, r1m=1.0, r3m=5.0, mom_12_1=mom, atr_pct=2.0)


ROWS = [_row("AAA", 40.0), _row("BBB", 90.0), _row("CCC", None), _row("DDD", 60.0)]


def test_ranked_strongest_momentum_first_unknown_last():
    assert [r.symbol for r in rp.ranked(ROWS)] == ["BBB", "DDD", "AAA", "CCC"]


def test_ranked_skips_held_and_other_books_names():
    assert [r.symbol for r in rp.ranked(ROWS, exclude={"BBB", "AAA"})] == ["DDD", "CCC"]


def test_choose_fills_only_the_free_slots_and_skips_refusals():
    picks = rp.choose(rp.ranked(ROWS), refused={"BBB": "already 2 in Energy"}, slots=2)
    assert [r.symbol for r in picks] == ["DDD", "AAA"]


def test_choose_with_no_free_slot_picks_nothing():
    assert rp.choose(rp.ranked(ROWS), refused={}, slots=0) == []


def test_free_slots_counts_holdings_and_working_buys():
    assert rp.free_slots(max_positions=6, held=4, working=1) == 1
    assert rp.free_slots(max_positions=6, held=7, working=0) == 0


def test_queue_item_uses_the_plan_defaults_and_says_why():
    item = rp.queue_item(_row("DDD", 60.0), rank=2, passing=14)
    assert item["stop"] == "normal" and item["target"] == "none" and item["size"] == "normal"
    assert "#2 of 14" in item["reason"] and "+60%" in item["reason"]


def test_the_rules_book_ranks_the_10_month_to_7_month_window_and_skips_a_missing_one():
    scores = {"BBB": 12.0, "AAA": 40.0, "DDD": None, "CCC": 1.0}
    assert [r.symbol for r in rp.momentum_ranked(ROWS, scores)] == ["AAA", "BBB", "CCC"]
    assert [r.symbol for r in rp.momentum_ranked(ROWS, scores, exclude={"AAA"})] == ["BBB", "CCC"]


def test_a_rotation_queue_item_names_the_window_and_carries_no_stop():
    item = rp.rotation_item(_row("AAA", 40.0), rank=1, passing=10, momentum=18.2)
    assert item["rotation"] is True
    assert item["stop"] == "none" and item["target"] == "none"
    assert "10 months ago to 7 months ago" in item["reason"] and "+18%" in item["reason"]
