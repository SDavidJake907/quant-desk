"""The 30-minute position watch reports giveback. It never sells."""

from datetime import date

from scanner.ops import position_watch as watch


def row(**kw) -> watch.Watch:
    base = dict(symbol="VSXY", book="small", qty=24, entry=99.57, last=110.53,
                prior_close=113.52, best=113.52, open_pl=262.99, day_pl=-71.86,
                entry_day=date(2026, 11, 4))
    base.update(kw)
    return watch.Watch(**base)


def test_a_winner_gives_back_dollars_and_keeps_the_rest():
    held = row()
    assert watch.profit_given_back(held) == round((113.52 - 110.53) * 24, 2)
    assert watch.day_given_back(held) == round((113.52 - 110.53) * 24, 2)
    assert watch.half_given_back(held) is False


def test_giving_back_half_of_a_five_percent_gain_is_named():
    """FTRE on 2026-11-10: best close 22.24, sold at 21.48, floor about 21.70."""
    held = row(symbol="FTRE", qty=113, entry=21.16, last=21.48, prior_close=22.24,
               best=22.24, best_close=22.24, open_pl=36.16, day_pl=-85.88,
               entry_day=date(2026, 11, 2))
    assert watch.half_given_back(held) is True
    assert watch.flagged(held) is True
    assert watch.profit_given_back(held) == round((22.24 - 21.48) * 113, 2)


def test_a_name_that_was_never_in_profit_has_nothing_to_give_back():
    held = row(symbol="COHR", book="gemma", qty=8, entry=225.79, last=215.07,
               prior_close=225.19, best=225.79, open_pl=-85.77, day_pl=-85.77,
               entry_day=date(2026, 11, 10))
    assert watch.profit_given_back(held) == 0
    assert watch.day_given_back(held) == 0
    assert watch.half_given_back(held) is False
    assert watch.flagged(held) is False


def test_best_price_ignores_the_days_before_the_buy_and_the_entry_day_high():
    highs = ((date(2026, 11, 3), 200.0), (date(2026, 11, 4), 150.0), (date(2026, 11, 10), 116.0))
    assert watch.best_price(99.57, 110.53, highs, date(2026, 11, 4)) == 116.0
    assert watch.best_price(100.0, 101.0, ((date(2026, 11, 10), 300.0),), date(2026, 11, 10)) == 101.0
    assert watch.best_price(99.57, 110.53, highs, None) is None
    assert watch.best_price(99.57, 110.53, (), date(2026, 11, 4)) is None


def test_a_buy_from_today_does_not_count_yesterdays_close():
    held = row(symbol="VRT", book="gemma", qty=9, entry=198.22, last=197.50,
               prior_close=200.47, best=199.73, open_pl=-6.48, day_pl=-6.48,
               entry_day=date(2026, 11, 10))
    assert watch.day_given_back(held, today=date(2026, 11, 10)) == 0


def test_a_10_7_wick_is_not_labeled_as_the_profit_lock():
    """CNC's 78.08 was a high. Without a completed close through +5%, the banner stays off."""
    held = row(symbol="CNC", book="gemma", qty=57, entry=66.41, last=70.00,
               prior_close=68.54, best=78.08, open_pl=204.63, day_pl=83.22,
               entry_day=date(2026, 11, 3))
    page = watch.render([held], "Tue 2026-11-10 14:28 Alaska", today=date(2026, 11, 10))
    assert "10-7" in page
    assert "profit lock" in page
    assert "Half of a 5%+ gain is gone" not in page


def test_a_10_7_close_that_gave_back_half_is_named():
    held = row(symbol="CNC", book="gemma", qty=57, entry=100.0, last=105.0,
               prior_close=112.0, best=120.0, best_close=112.0, open_pl=285.0,
               day_pl=-399.0, entry_day=date(2026, 11, 10))
    assert watch.lock_applies(held) is True
    assert watch.half_given_back(held) is True
    page = watch.render([held], "Fri 2026-11-13 12:03 Alaska", today=date(2026, 11, 13))
    assert "Half of a 5%+ gain is gone" in page
    assert "profit lock" in page


def test_the_half_lock_uses_the_close_after_the_buy_not_a_wick():
    closes = ((date(2026, 11, 10), 130.0), (date(2026, 11, 11), 110.0))
    assert watch.best_close(100.0, 106.0, closes, date(2026, 11, 10), date(2026, 11, 12)) == 110.0
    assert watch.best_close(100.0, 110.0, closes, date(2026, 11, 10), date(2026, 11, 10)) is None
    held = row(entry=100.0, last=106.0, best=130.0, best_close=110.0, qty=1, open_pl=6.0,
               prior_close=110.0)
    assert watch.half_given_back(held) is False
    page = watch.render([held], "Wed 2026-11-12 10:03 Alaska", today=date(2026, 11, 12))
    assert "Half of a 5%+ gain is gone" not in page


def test_a_missing_best_is_named_instead_of_called_zero():
    page = watch.render([row(best=None, prior_close=99.57, last=110.53, day_pl=0, open_pl=262.99)],
                        "Tue 2026-11-10 10:03 Alaska", today=date(2026, 11, 10))
    assert "unavailable" in page
    assert "No open profit has been given back" not in page


def test_a_missing_best_does_not_invent_a_giveback():
    held = row(best=None)
    assert watch.profit_given_back(held) == 0
    assert watch.half_given_back(held) is False
    assert watch.day_given_back(held) > 0


def test_only_a_new_flag_is_called_out():
    rows = [row(), row(symbol="VIR", book="small", qty=135, entry=13.99, last=13.12,
                       prior_close=13.64, best=14.50, open_pl=-117.45, day_pl=-70.88)]
    assert watch.new_flags({"VSXY"}, rows) == ["VIR"]
    assert watch.new_flags({"VSXY", "VIR"}, rows) == []


def test_the_10_7_book_names_the_close_profit_lock():
    note = watch.exit_note(row(book="gemma", entry_day=date(2026, 11, 10)))
    assert "10-7" in note
    assert "5-minute" in note
    assert "2%" in note
    assert "profit lock" in note
    assert "15" in note
    assert "5%" in note
    assert "8%" in note
    assert "never lowered" in note


def test_the_small_book_keeps_its_close_profit_lock():
    note = watch.exit_note(row())
    assert "profit lock" in note


def test_the_sweep_keeps_its_stop():
    note = watch.exit_note(row(book="smc"))
    assert "stop" in note
    assert "does not sell" in note


def test_the_page_lists_every_name_and_sends_nothing():
    page = watch.render([row(), row(symbol="COHR", book="gemma", qty=8, entry=225.79,
                                    last=215.07, prior_close=225.19, best=225.79,
                                    open_pl=-85.77, day_pl=-85.77,
                                    entry_day=date(2026, 11, 10))],
                        "Tue 2026-11-10 10:03 Alaska")
    assert "VSXY" in page and "COHR" in page
    assert "Report only" in page
    assert "No order" in page
    assert "SELL" not in page
