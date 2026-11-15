"""Gemma's choice A1 (2026-10-26): her step-1 pool shows only names that pass the buy rules that do
not depend on her holdings, each with its 50-day distance and earnings date (on 10-26 only 5 of
her 20 shortlisted names could be bought)."""

import asyncio
from datetime import date, timedelta
from types import SimpleNamespace as NS

from scanner.advisor import gemma_pool as gp
from scanner.execution import satellite_feed as sf

TODAY = date(2026, 10, 27)
G = sf.Gates(open={"large": True, "mid": False, "small": True}, vs_average={})
RISING = [100.0 + i * 0.5 for i in range(200)]            # up 6 months, above its 50-day
FALLING = [200.0 - i * 0.5 for i in range(200)]


def row(s, mom):
    return gp.PoolRow(symbol=s, sector="Tech", r1m=1.0, r3m=2.0, mom_12_1=mom, atr_pct=2.0)


def test_price_refusal_is_the_buy_rules_without_holdings():
    mom = {"A": 90.0, "B": 80.0, "C": 10.0}
    seg = {"M": "mid"}
    assert sf.price_refusal("A", RISING, mom=mom, segment=seg, g=G, earnings_in_days=None) is None
    assert "top third" in sf.price_refusal("C", RISING, mom=mom, segment=seg, g=G, earnings_in_days=None)
    assert "50-day" in sf.price_refusal("A", RISING[:-1] + [RISING[-60]], mom=mom, segment=seg, g=G,
                                        earnings_in_days=None)
    assert "6-month" in sf.price_refusal("A", FALLING, mom=mom, segment=seg, g=G, earnings_in_days=None)
    assert "earnings in 3" in sf.price_refusal("A", RISING, mom=mom, segment=seg, g=G, earnings_in_days=3)
    assert "gate closed" in sf.price_refusal("M", RISING, mom={"M": 99.0}, segment=seg, g=G,
                                             earnings_in_days=None)


def test_buyable_pool_filters_and_adds_50d_and_earnings(monkeypatch):
    rows = [row("AAA", 90.0), row("BBB", 80.0), row("CCC", 70.0), row("DDD", 60.0), row("MMM", 95.0),
            *(row(f"Z{i}", 1.0) for i in range(10))]
    closes = {r.symbol: RISING for r in rows} | {"DDD": FALLING}

    async def load_universe(client):
        return [NS(symbol="MMM", index="S&P MidCap 400")]

    async def fetch_many(symbols, client, range_="1y"):
        return {s: NS(closes=closes[s]) for s in symbols}, {}

    async def upcoming_earnings(symbols, client, horizon, today):
        assert "DDD" not in symbols and "MMM" not in symbols            # price rules first
        return {"BBB": NS(on=today + timedelta(days=2)), "CCC": NS(on=today + timedelta(days=20))}, ()

    monkeypatch.setattr(sf.composite, "load_universe", load_universe)
    monkeypatch.setattr(sf, "fetch_many", fetch_many)
    monkeypatch.setattr(sf.event_calendar, "upcoming_earnings", upcoming_earnings)
    shown, failed = asyncio.run(sf.buyable_pool(None, rows, G, TODAY))
    assert [r.symbol for r in shown] == ["AAA", "CCC"]        # BBB earnings in 2 days, DDD falling, MMM gate
    assert failed == ()
    ccc = shown[1]
    assert ccc.earnings == "2026-11-16" and ccc.vs_50d is not None and ccc.vs_50d > 0
    assert "vs 50d +" in gp.line(ccc) and "earnings 2026-11-16" in gp.line(ccc)
    assert "earnings none in" in gp.line(shown[0])


def test_a_name_above_its_50_day_but_down_this_month_is_refused():
    """12-1 skips the last month, and the 50-day check still passes on a shallow fade.
    That mixed case is the downward month the pool used to buy."""
    rise = [100.0 + i for i in range(150)]
    fade = [249.0 - i * 0.4 for i in range(1, 22)]
    closes = rise + fade
    assert closes[-1] > sum(closes[-50:]) / 50
    assert closes[-1] / closes[-22] - 1 < 0
    assert closes[-1] / closes[-127] - 1 > 0
    mom = {"ROLL": 90.0, "B": 10.0, "C": 1.0}
    why = sf.price_refusal("ROLL", closes, mom=mom, segment={}, g=G, earnings_in_days=None)
    assert why is not None and "last month" in why
    assert "50-day" not in why and "6-month" not in why


def test_old_pool_lines_and_files_unchanged():
    r = row("AAA", 90.0)
    assert "vs 50d" not in gp.line(r) and "earnings" not in gp.line(r)
    assert gp.PoolRow(**r.model_dump()) == r
