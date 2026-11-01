from scanner.execution import preclose as pc

UP = [100.0 + i * 0.5 for i in range(60)]        # a steady uptrend, last close 129.5
LOWS = [c - 1 for c in UP]
BROAD = {"VIXY": 8.0, "HYG": -0.5, **{s: -1.0 for s in pc.SECTORS}}
ROTATION = {"VIXY": 10.5, "HYG": -0.19, **{s: (-1.0 if i < 5 else 0.5) for i, s in enumerate(pc.SECTORS)}}
NEWS = ["Oil jumps after missile strike on tanker"]


def run(price, *, book="core", entry=130.0, day=BROAD, heads=NEWS, closes=UP, lows=LOWS):
    return pc.score(symbol="SPY", book=book, price=price, entry=entry, closes=closes, lows=lows,
                    day_pct=day, headlines=heads, sell_books=("core",))


def test_chart_needs_both_the_50_day_and_the_20_day_low():
    assert not pc.chart_sign(UP, LOWS, 125.0).ok          # under the 50-day? no: 50d ~117
    assert pc.chart_sign(UP, LOWS, 110.0).ok             # under both
    assert not pc.chart_sign(UP[:10], LOWS[:10], 1.0).ok  # too few bars never confirms


def test_market_needs_two_of_three():
    assert pc.market_sign(BROAD).ok
    rot = pc.market_sign(ROTATION)                     # 2026-10-30: VIXY only
    assert not rot.ok and "1/3" in rot.detail
    assert not pc.market_sign({}).ok                   # unreadable fails, never confirms


def test_news_wants_a_catalyst_not_a_price_word():
    assert not pc.news_sign(["Stocks tumble as Nasdaq slides"]).ok
    assert pc.news_sign(["Fed signals rate hike in December"]).ok
    assert not pc.news_sign([]).ok


def test_momentum_turns_when_the_price_breaks():
    assert not pc.momentum_sign(UP, 130.0).ok
    assert pc.momentum_sign(UP, 110.0).ok


def test_core_sells_only_when_losing_and_four_of_four():
    s = run(110.0)
    assert s.confirmed == 4 and s.losing and s.sell
    assert not run(110.0, day=ROTATION).sell           # 3/4 holds
    assert not run(110.0, heads=[]).sell
    assert not run(110.0, entry=105.0).sell            # not losing


def test_other_books_are_scored_never_sold():
    s = run(110.0, book="gemma")
    assert s.confirmed == 4 and not s.sell
    assert "4/4 -> hold" in s.line()


def test_working_orders_include_held_bracket_legs():
    orders = [{"id": "a", "symbol": "SPY", "status": "new"},
              {"id": "b", "symbol": "XOM", "status": "filled",
               "legs": [{"id": "c", "symbol": "XOM", "status": "held"}]},
              {"id": "d", "symbol": "SPY", "status": "filled"}]
    assert pc.working_orders_for(orders, ["SPY"]) == ["a"]
    assert pc.working_orders_for(orders, ["XOM"]) == ["c"]
