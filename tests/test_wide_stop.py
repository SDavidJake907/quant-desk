"""Gemma's choice B2 (2026-10-26): new Gemma buys keep the full 2.5 ATR stop (no 3% cap) and are
sized smaller, so the dollars lost at the stop equal a 3% stop on a full-size position. Positions
bought before WIDE_STOP_FROM keep the 3% cap ("new buys only")."""

import asyncio
import json
from datetime import date, datetime

import pytest

from scanner.config import settings
from scanner.execution import bookorders, buy_queue, plan_run
from scanner.execution import satellite as sat
from scanner.execution.policy import TradingPolicy


def test_hard_stop_without_the_floor_is_the_full_2_5_atr():
    assert sat.hard_stop(entry=100, atr=4, high_close=100, floor=False) == pytest.approx(90.0)
    assert sat.hard_stop(entry=100, atr=4, high_close=100) == pytest.approx(97.0)      # old positions
    # break-even after +1 ATR still applies
    assert sat.hard_stop(entry=100, atr=4, high_close=104, floor=False) == pytest.approx(100.0)


def test_wide_stop_only_for_buys_from_the_change_day():
    assert sat.wide_stop(sat.WIDE_STOP_FROM)
    assert not sat.wide_stop(date(2026, 10, 26))       # today's APA, SM, CRWD, RVTY keep 3%
    assert not sat.wide_stop(None)                     # unknown buy date: the old, tighter rule


def test_same_dollar_risk_as_a_3pct_stop_on_a_full_position():
    assert sat.same_risk_pct(100 / 6) == pytest.approx(0.5)


def _buy(monkeypatch, tmp_path, *, atr_pct, **kw):
    sent = []

    class FakeBroker:
        async def submit(self, payload):
            sent.append(payload)
            return {"id": "1"}

    async def fake_price_and_atr(symbol, client):
        return 100.0, atr_pct

    monkeypatch.setattr(bookorders, "price_and_atr", fake_price_and_atr)
    monkeypatch.setattr(settings, "audit_log_path", tmp_path / "audit.jsonl")
    res = asyncio.run(bookorders.buy_with_stop(
        FakeBroker(), None, "AAA", strategy="gemma", sleeve=60_000, room=60_000, cap_pct=100 / 6,
        policy=TradingPolicy(), reason="test", atr_multiple=2.5, reward_risk=0.0, **kw))
    return res, sent


def test_wide_buy_is_smaller_with_the_same_dollar_risk(monkeypatch, tmp_path):
    full, _ = _buy(monkeypatch, tmp_path, atr_pct=4.0, risk_pct=1.0)               # 3% cap: full size
    wide, sent = _buy(monkeypatch, tmp_path, atr_pct=4.0, risk_pct=0.5, loss_floor=False)
    stop = float(sent[0]["stop_loss"]["stop_price"])
    limit = float(sent[0]["limit_price"])
    # "target none": a take-profit leg at 3x the buy (was 1000x -- $261,080 on CRWD, operator 10-26)
    assert float(sent[0]["take_profit"]["limit_price"]) == pytest.approx(limit * 3, abs=0.01)
    assert (limit - stop) / limit == pytest.approx(0.10, abs=0.002)                # 2.5 x 4%
    assert full.sizing.limited_by == "position cap"
    assert wide.sizing.limited_by == "risk budget"
    assert wide.sizing.position_dollars < full.sizing.position_dollars * 0.4
    assert wide.sizing.risk_dollars == pytest.approx(full.sizing.risk_dollars, rel=0.03)


def test_a_calm_stock_still_gets_full_size(monkeypatch, tmp_path):
    wide, _ = _buy(monkeypatch, tmp_path, atr_pct=1.0, risk_pct=0.5, loss_floor=False)
    assert wide.sizing.limited_by == "position cap"                                # 2.5% stop < 3%


def test_the_10am_buy_pass_sends_wide_stops_at_same_dollar_risk(tmp_path, monkeypatch):
    queue = tmp_path / "buy_queue.json"
    queue.write_text(json.dumps({"day": "2026-10-27", "items": {"AAA": {"reason": "x", "price": 50.0}}}))
    monkeypatch.setattr(buy_queue, "PATH", str(queue))

    async def none_skipped(client, due, today, log):
        return {}

    calls = []

    async def fake_buy(broker, client, symbol, **kw):
        calls.append(kw)
        return bookorders.Bought(symbol=symbol, ok=False, message="test")

    class FakeBroker:
        async def _get(self, path, **_):
            return {"equity": "100000", "last_equity": "100000", "buying_power": "200000", "status": "ACTIVE"}

    monkeypatch.setattr(plan_run, "_rechecks", none_skipped)

    async def midweek(*a, **k):
        return False

    monkeypatch.setattr(plan_run, "before_closure", midweek)
    monkeypatch.setattr(bookorders, "buy_with_stop", fake_buy)
    monkeypatch.setattr(bookorders, "log", lambda *a, **k: None)
    now = datetime(2026, 10, 27, 10, 5, tzinfo=sat.ET)
    asyncio.run(plan_run.buy_pass(FakeBroker(), None, [], {}, [], now, False, lambda s: None, TradingPolicy()))
    assert calls and calls[0]["loss_floor"] is False
    # A 3% stop's dollars on a full fifth (5 slots from 2026-10-27; a sixth, 0.5%, before).
    assert calls[0]["risk_pct"] == pytest.approx(0.6)
