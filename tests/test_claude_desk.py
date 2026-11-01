from datetime import date

from scanner.advisor import claude_desk as cd
from scanner.advisor.claude_desk import Bar, Book, Call, Plan, Position

D1, D2 = date(2026, 11, 2), date(2026, 11, 3)


def bar(o, h, l, c, day=D1):  # noqa: E741
    return Bar(day=day, o=o, h=h, l=l, c=c)


def buy(sym="ABC", kind="limit", entry=100.0, stop=95.0, target=None, max_days=None):
    return Call(symbol=sym, action="buy", entry_type=kind, entry=entry, stop=stop, target=target,
                max_days=max_days, why="test")


def held(sym="ABC", stop=95.0, target=None, max_days=30, held_days=0):
    return Position(symbol=sym, opened=D1, entry=100.0, shares=60.0, stop=stop, target=target,
                    max_days=max_days, held_days=held_days, last=100.0)


# --- check ---------------------------------------------------------------------------------

def test_a_buy_needs_a_stop_under_the_entry_and_within_the_cap():
    assert cd.check(buy(stop=None), last_close=100.0, held=None)
    assert "not under" in cd.check(buy(stop=101.0), last_close=100.0, held=None)
    assert "8%" in cd.check(buy(stop=91.0), last_close=100.0, held=None)
    assert cd.check(buy(stop=93.0), last_close=100.0, held=None) is None


def test_an_entry_far_from_the_last_close_is_refused():
    assert "5%" in cd.check(buy(entry=106.0, stop=100.0), last_close=100.0, held=None)


def test_sell_and_hold_need_a_shadow_position_and_a_stop_never_drops():
    assert "not held" in cd.check(Call(symbol="ABC", action="sell"), last_close=100.0, held=None)
    lower = Call(symbol="ABC", action="hold", stop=90.0)
    assert "never lowered" in cd.check(lower, last_close=100.0, held=held())
    assert cd.check(Call(symbol="ABC", action="hold", stop=97.0), last_close=100.0, held=held()) is None


def test_buys_past_the_free_slots_are_skipped():
    book = Book(open=tuple(held(sym=f"H{i}") for i in range(4)))
    plan = Plan(day=D1, calls=(buy("A"), buy("B")))
    out = cd.accepted(plan, closes={"A": 100.0, "B": 100.0}, book=book)
    assert [c.symbol for c in out.calls] == ["A"]
    assert any("no free slot" in s for s in out.skipped)


# --- play: entries --------------------------------------------------------------------------

def test_limit_fills_only_when_the_low_reaches_it():
    plan = Plan(day=D1, calls=(buy(entry=100.0, stop=95.0),))
    assert cd.play(Book(), plan, {"ABC": bar(102, 103, 100.5, 102)}, D1).open == ()
    out = cd.play(Book(), plan, {"ABC": bar(102, 103, 99.0, 101)}, D1)
    assert len(out.open) == 1 and abs(out.open[0].entry - 100.0 * (1 + cd.COST)) < 1e-9


def test_a_limit_gapped_under_fills_at_the_open():
    out = cd.play(Book(), Plan(day=D1, calls=(buy(entry=100.0, stop=95.0),)),
                  {"ABC": bar(98, 99, 97, 98.5)}, D1)
    assert abs(out.open[0].entry - 98.0 * (1 + cd.COST)) < 1e-9


def test_breakout_fills_when_the_high_reaches_it():
    plan = Plan(day=D1, calls=(buy(kind="breakout", entry=105.0, stop=100.0),))
    assert cd.play(Book(), plan, {"ABC": bar(102, 104, 101, 103)}, D1).open == ()
    assert len(cd.play(Book(), plan, {"ABC": bar(102, 106, 101, 105.5)}, D1).open) == 1


def test_a_buy_stopped_on_its_fill_bar_is_closed_that_bar():
    out = cd.play(Book(), Plan(day=D1, calls=(buy(entry=100.0, stop=97.0),)),
                  {"ABC": bar(101, 101, 96, 98)}, D1)
    assert out.open == () and out.closed[0].reason == "stop (fill day)"
    assert out.cash < cd.SLEEVE


def test_a_slot_is_one_fifth_of_the_sleeve():
    out = cd.play(Book(), Plan(day=D1, calls=(buy(kind="open", entry=None, stop=95.0),)),
                  {"ABC": bar(100, 101, 99, 100)}, D1)
    pos = out.open[0]
    assert abs(pos.shares * pos.entry - cd.SLOT) < 1e-6


# --- play: exits ----------------------------------------------------------------------------

def test_stop_gap_target_and_both_in_one_bar():
    book = Book(cash=24_000.0, open=(held(stop=95.0, target=110.0),))
    gap = cd.play(book, None, {"ABC": bar(93, 94, 92, 93, D2)}, D2).closed[0]
    assert gap.reason == "stop (gap)" and abs(gap.exit - 93 * (1 - cd.COST)) < 1e-9
    hit = cd.play(book, None, {"ABC": bar(99, 111, 98, 108, D2)}, D2).closed[0]
    assert hit.reason == "target"
    both = cd.play(book, None, {"ABC": bar(100, 111, 94, 100, D2)}, D2).closed[0]
    assert both.reason == "stop"


def test_time_exit_and_sell_call():
    book = Book(cash=24_000.0, open=(held(max_days=3, held_days=2),))
    assert cd.play(book, None, {"ABC": bar(101, 102, 100, 101.5, D2)}, D2).closed[0].reason == "time"
    sold = cd.play(Book(open=(held(),)), Plan(day=D2, calls=(Call(symbol="ABC", action="sell"),)),
                   {"ABC": bar(103, 104, 102, 103, D2)}, D2).closed[0]
    assert sold.reason == "sell call" and abs(sold.exit - 103 * (1 - cd.COST)) < 1e-9


def test_a_hold_call_raises_the_stop():
    plan = Plan(day=D2, calls=(Call(symbol="ABC", action="hold", stop=99.0),))
    out = cd.play(Book(open=(held(stop=95.0),)), plan, {"ABC": bar(100, 101, 98.5, 100, D2)}, D2)
    assert out.closed[0].reason == "stop" and out.closed[0].exit < 99.0


def test_no_bar_holds_the_position_at_its_last_close():
    out = cd.play(Book(cash=24_000.0, open=(held(),)), None, {}, D2)
    assert out.open[0].held_days == 0 and out.marks[-1] == (D2, 30_000.0)


def test_drawdown():
    marks = ((D1, 31_000.0), (D2, 27_900.0))
    assert abs(cd.max_drawdown_pct(marks) - 10.0) < 1e-9
