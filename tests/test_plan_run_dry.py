"""A dry run of the 10:00 buy pass writes nothing Gemma reads (found 2026-10-24: a simulated
recheck skip landed in the audit log as a real gemma_refused row)."""

import asyncio
import json
from datetime import datetime

from scanner.execution import bookorders, buy_queue, plan_run
from scanner.execution import satellite as sat
from scanner.execution.policy import TradingPolicy


class FakeBroker:
    async def _get(self, path, **_):
        if path == "/v2/calendar":
            return [{"date": "2026-10-27"}]   # Monday -> Tuesday: not the last session before a weekend
        assert path == "/v2/account"
        return {"equity": "100000", "last_equity": "100000", "buying_power": "200000", "status": "ACTIVE"}


def test_dry_buy_pass_logs_nothing_and_keeps_the_queue(tmp_path, monkeypatch):
    queue = tmp_path / "buy_queue.json"
    body = {"day": "2026-10-26", "items": {"AAA": {"reason": "x", "price": 50.0, "alerts": []},
                                           "BBB": {"reason": "y", "price": 20.0, "alerts": []}}}
    queue.write_text(json.dumps(body))
    monkeypatch.setattr(buy_queue, "PATH", str(queue))

    async def skipped(client, due, today, log):
        return {"AAA": "fell -3.0% since her 9:45 decision"}

    monkeypatch.setattr(plan_run, "_rechecks", skipped)
    written = []
    monkeypatch.setattr(bookorders, "log", lambda *a, **k: written.append(a))
    lines = []
    now = datetime(2026, 10, 26, 10, 5, tzinfo=sat.ET)

    asyncio.run(plan_run.buy_pass(FakeBroker(), None, [], {}, [], now, True, lines.append, TradingPolicy()))

    assert written == []
    assert any("skipped at 10:00" in s for s in lines)
    assert any("WOULD BUY (queued) BBB" in s for s in lines)
    assert json.loads(queue.read_text()) == body


def test_the_10_7_book_buys_a_rotation_item_and_drops_an_old_queue(tmp_path, monkeypatch):
    queue = tmp_path / "buy_queue.json"
    body = {"day": "2026-11-03", "items": {
        "OLD": {"reason": "12-1", "price": 50.0, "alerts": []},
        "NEW": {"reason": "10-7", "price": 40.0, "alerts": [], "rotation": True},
    }}
    queue.write_text(json.dumps(body))
    monkeypatch.setattr(buy_queue, "PATH", str(queue))

    async def none_skipped(client, due, today, log):
        return {}

    calls = []

    async def fake_rotation(broker, client, symbol, **kw):
        calls.append(symbol)
        return bookorders.Bought(symbol=symbol, ok=True, message="sent",
                                 sizing=bookorders.Sizing(shares=1, risk_dollars=0.0,
                                                          position_dollars=40.0, limited_by="equal slot"))

    class Broker:
        async def _get(self, path, **_):
            return {"equity": "100000", "last_equity": "100000", "buying_power": "200000",
                    "cash": "50000", "status": "ACTIVE"}

    monkeypatch.setattr(plan_run, "_rechecks", none_skipped)
    monkeypatch.setattr(bookorders, "buy_rotation", fake_rotation)
    monkeypatch.setattr(bookorders, "buy_with_stop", lambda *a, **k: (_ for _ in ()).throw(AssertionError("stop")))
    monkeypatch.setattr(bookorders, "log", lambda *a, **k: None)
    now = datetime(2026, 11, 3, 10, 5, tzinfo=sat.ET)
    asyncio.run(plan_run.buy_pass(Broker(), None, [], {}, [], now, False, lambda s: None, TradingPolicy()))
    assert calls == ["NEW"]
