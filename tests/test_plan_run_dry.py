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
