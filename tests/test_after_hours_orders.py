"""A buy or sell that is already due may go out after hours as a day limit.

The regular session is unchanged. Premarket and overnight do not send these
orders. A 10-7 buy is still a day limit. Its stop is recorded for the open.
"""

import asyncio
import json
from datetime import datetime

from scanner.execution.alpaca import BrokerError, round_tick
from scanner.execution import bookorders, buy_queue, extended, plan_run, sessions
from scanner.execution import satellite as sat
from scanner.execution.ownership import GEMMA
from scanner.execution.policy import TradingPolicy


def test_only_the_regular_session_and_after_hours_take_a_due_order():
    assert sessions.accepts_due_orders(sessions.REGULAR)
    assert sessions.accepts_due_orders(sessions.AFTER)
    assert not sessions.accepts_due_orders(sessions.PRE)
    assert not sessions.accepts_due_orders(sessions.OVERNIGHT)
    assert not sessions.accepts_due_orders(sessions.CLOSED)


class _Account:
    async def _get(self, path, **_):
        assert path == "/v2/account"
        return {"equity": "100000", "last_equity": "100000", "buying_power": "200000",
                "cash": "50000", "status": "ACTIVE"}


def _queue(tmp_path, monkeypatch, day="2026-11-10"):
    path = tmp_path / "buy_queue.json"
    body = {"day": day, "items": {
        "NEW": {"reason": "10-7", "price": 40.0, "alerts": [], "rotation": True},
    }}
    path.write_text(json.dumps(body))
    monkeypatch.setattr(buy_queue, "PATH", str(path))

    async def none_skipped(client, due, today, log):
        return {}

    monkeypatch.setattr(plan_run, "_rechecks", none_skipped)
    monkeypatch.setattr(bookorders, "log", lambda *a, **k: None)
    return path, body


def test_after_hours_sends_a_queued_buy_the_regular_clock_would_refuse(tmp_path, monkeypatch):
    path, body = _queue(tmp_path, monkeypatch)
    seen = []

    async def fake_rotation(broker, client, symbol, **kw):
        seen.append(kw["session"])
        return bookorders.Bought(symbol=symbol, ok=True, message="sent",
                                 sizing=bookorders.Sizing(shares=1, risk_dollars=0.0,
                                                          position_dollars=40.0, limited_by="equal slot"))

    monkeypatch.setattr(bookorders, "buy_rotation", fake_rotation)
    now = datetime(2026, 11, 10, 17, 5, tzinfo=sat.ET)
    asyncio.run(plan_run.buy_pass(_Account(), None, [], {}, [], now, False, lambda s: None,
                                  TradingPolicy(), session=sessions.AFTER))
    assert seen == [sessions.AFTER]
    assert "NEW" not in json.loads(path.read_text())["items"]
    assert body["items"]["NEW"]["rotation"] is True


def test_the_same_clock_does_not_buy_in_premarket_or_as_a_regular_order(tmp_path, monkeypatch):
    path, body = _queue(tmp_path, monkeypatch)

    async def boom(*a, **k):
        raise AssertionError("no order")

    monkeypatch.setattr(bookorders, "buy_rotation", boom)
    pre = datetime(2026, 11, 10, 8, 0, tzinfo=sat.ET)
    late = datetime(2026, 11, 10, 17, 5, tzinfo=sat.ET)
    asyncio.run(plan_run.buy_pass(_Account(), None, [], {}, [], pre, False, lambda s: None,
                                  TradingPolicy(), session=sessions.PRE))
    asyncio.run(plan_run.buy_pass(_Account(), None, [], {}, [], late, False, lambda s: None,
                                  TradingPolicy()))
    assert json.loads(path.read_text()) == body


def test_a_missing_after_hours_price_keeps_the_queued_buy(tmp_path, monkeypatch):
    path, body = _queue(tmp_path, monkeypatch)

    async def waiting(broker, client, symbol, **kw):
        return bookorders.Bought(symbol=symbol, ok=False, message="no fresh after price")

    monkeypatch.setattr(bookorders, "buy_rotation", waiting)
    now = datetime(2026, 11, 10, 17, 5, tzinfo=sat.ET)
    asyncio.run(plan_run.buy_pass(_Account(), None, [], {}, [], now, False, lambda s: None,
                                  TradingPolicy(), session=sessions.AFTER))
    assert json.loads(path.read_text()) == body


def test_after_hours_does_not_cancel_a_day_limit_at_the_25_minute_mark(tmp_path, monkeypatch):
    path, _body = _queue(tmp_path, monkeypatch)
    cancelled = []

    class Broker(_Account):
        async def cancel_order(self, order_id):
            cancelled.append(order_id)

    async def sent(broker, client, symbol, **kw):
        return bookorders.Bought(symbol=symbol, ok=True, message="sent",
                                 sizing=bookorders.Sizing(shares=1, risk_dollars=0.0,
                                                          position_dollars=40.0, limited_by="equal slot"))

    monkeypatch.setattr(bookorders, "buy_rotation", sent)
    now = datetime(2026, 11, 10, 17, 5, tzinfo=sat.ET)
    old = {"id": "old", "symbol": "OLD", "side": "buy", "filled_qty": "0",
           "client_order_id": "gem-OLD-1", "submitted_at": "2026-11-10T20:00:00Z"}
    asyncio.run(plan_run.buy_pass(Broker(), None, [], {}, [old], now, False, lambda s: None,
                                  TradingPolicy(), session=sessions.AFTER))
    assert cancelled == []
    assert "NEW" not in json.loads(path.read_text())["items"]


def test_a_10_7_after_hours_buy_is_a_limit_with_no_stop(monkeypatch):
    sent = []

    class Broker:
        async def submit(self, payload):
            sent.append(payload)
            return {"id": "1"}

    async def regular_price(symbol, client):
        return 100.0, 3.0

    async def after_price(symbol, client, session, now=None):
        return 101.0

    monkeypatch.setattr(bookorders, "price_and_atr", regular_price)
    monkeypatch.setattr(bookorders.extended, "price", after_price)
    monkeypatch.setattr(bookorders, "log", lambda *a, **k: None)
    result = asyncio.run(bookorders.buy_rotation(
        Broker(), None, "CIEN", strategy=GEMMA, sleeve=20_000, room=20_000, slots=10,
        policy=TradingPolicy(), reason="due", session=sessions.AFTER))
    assert result.ok and len(sent) == 1
    payload = sent[0]
    assert payload["extended_hours"] is True and payload["type"] == "limit"
    assert payload["time_in_force"] == "day" and payload["side"] == "buy"
    assert "stop_loss" not in payload and "order_class" not in payload
    assert float(payload["limit_price"]) > 101.0


def test_a_10_7_buy_records_the_full_slot_stop(monkeypatch):
    logged = {}

    class Broker:
        async def submit(self, payload):
            return {"id": "1"}

    def capture(event, symbol, reason, policy, **kw):
        if event == "order_submitted":
            logged["stop"] = kw["intent"].stop_price
            logged["payload_checked"] = True

    async def regular_price(symbol, client):
        return 100.0, 4.0

    monkeypatch.setattr(bookorders, "price_and_atr", regular_price)
    monkeypatch.setattr(bookorders, "log", capture)
    result = asyncio.run(bookorders.buy_rotation(
        Broker(), None, "CIEN", strategy=GEMMA, sleeve=20_000, room=20_000, slots=10,
        policy=TradingPolicy(), reason="due"))
    limit = round_tick(100.0 * (1 + TradingPolicy().entry_limit_offset_pct / 100))
    assert result.ok
    assert logged["stop"] == round_tick(sat.rotation_stop(limit, 4.0))


def test_a_regular_10_7_buy_stays_off_extended_hours(monkeypatch):
    sent = []

    class Broker:
        async def submit(self, payload):
            sent.append(payload)
            return {"id": "1"}

    async def regular_price(symbol, client):
        return 100.0, 3.0

    monkeypatch.setattr(bookorders, "price_and_atr", regular_price)
    monkeypatch.setattr(bookorders, "log", lambda *a, **k: None)
    result = asyncio.run(bookorders.buy_rotation(
        Broker(), None, "CIEN", strategy=GEMMA, sleeve=20_000, room=20_000, slots=10,
        policy=TradingPolicy(), reason="due"))
    assert result.ok and sent[0]["extended_hours"] is False
    assert float(sent[0]["limit_price"]) < 101.0


class _CoreBroker:
    def __init__(self):
        self.sent, self.cancelled = [], []

    async def _get(self, path, **_):
        if path == "/v2/account":
            return {"equity": "100000", "last_equity": "100000", "buying_power": "200000",
                    "status": "ACTIVE"}
        if path == "/v2/calendar":
            return [{"date": "2026-11-11"}]
        if path.startswith("/v2/positions/"):
            return {"qty": "20", "qty_available": "20"}
        raise AssertionError(path)

    async def submit(self, payload):
        self.sent.append(payload)
        return {"id": "1"}

    async def cancel_order(self, order_id):
        self.cancelled.append(order_id)

    async def replace_stop(self, order_id, stop):
        raise AssertionError(stop)


def test_a_due_core_buy_after_hours_is_an_extended_limit(monkeypatch, tmp_path):
    async def regular_price(symbol, client):
        return 600.0, 1.0

    async def after_price(symbol, client, session, now=None):
        assert session == sessions.AFTER
        return 601.0

    monkeypatch.setattr(bookorders, "price_and_atr", regular_price)
    monkeypatch.setattr(extended, "price", after_price)
    monkeypatch.setattr(bookorders, "log", lambda *a, **k: None)
    broker = _CoreBroker()
    now = datetime(2026, 11, 10, 17, 5, tzinfo=sat.ET)
    asyncio.run(plan_run.core_pass(
        broker, None, [], {}, [], now, False, lambda s: None, TradingPolicy(),
        state_path=tmp_path / "core.json", session=sessions.AFTER))
    assert len(broker.sent) == 1
    order = broker.sent[0]
    assert order["symbol"] == "QQQ" and order["side"] == "buy" and order["type"] == "limit"
    assert order["extended_hours"] is True and "stop_price" not in order
    assert float(order["limit_price"]) > 601.0


def test_a_core_buy_with_no_after_hours_price_is_retried(monkeypatch, tmp_path):
    async def regular_price(symbol, client):
        return 600.0, 1.0

    async def no_price(symbol, client, session, now=None):
        return None

    monkeypatch.setattr(bookorders, "price_and_atr", regular_price)
    monkeypatch.setattr(extended, "price", no_price)
    monkeypatch.setattr(bookorders, "log", lambda *a, **k: None)
    broker = _CoreBroker()
    now = datetime(2026, 11, 10, 17, 5, tzinfo=sat.ET)
    path = tmp_path / "core.json"
    asyncio.run(plan_run.core_pass(
        broker, None, [], {}, [], now, False, lambda s: None, TradingPolicy(),
        state_path=path, session=sessions.AFTER))
    assert broker.sent == []
    assert not path.exists()


def test_a_core_stop_breach_after_hours_is_a_limit_and_keeps_the_stop_without_a_price(monkeypatch):
    class Bars:
        def __init__(self):
            self.closes = [760.0] * 30
            self.highs = [765.0] * 30
            self.lows = [755.0] * 30
            self.dates = []

        def __len__(self):
            return len(self.closes)

    async def bars(*a, **k):
        return Bars()

    prices = {"live": None}

    async def after_price(symbol, client, session, now=None):
        return prices["live"]

    monkeypatch.setattr(plan_run, "fetch_bars", bars)
    monkeypatch.setattr(extended, "price", after_price)
    monkeypatch.setattr(bookorders, "log", lambda *a, **k: None)
    broker = _CoreBroker()
    position = [{"symbol": "QQQ", "qty": "20", "avg_entry_price": "766", "current_price": "700"}]
    working = [{"id": "stop-1", "symbol": "QQQ", "side": "sell", "type": "stop", "qty": "20",
                "stop_price": "740.00", "client_order_id": "cor-stop-QQQ-1"}]
    now = datetime(2026, 11, 10, 17, 5, tzinfo=sat.ET)
    asyncio.run(plan_run.core_stop_pass(
        broker, object(), position, {"QQQ": "core"}, [], working, now, False, lambda s: None,
        TradingPolicy(), session=sessions.AFTER))
    assert broker.cancelled == [] and broker.sent == []
    prices["live"] = 699.0
    asyncio.run(plan_run.core_stop_pass(
        broker, object(), position, {"QQQ": "core"}, [], working, now, False, lambda s: None,
        TradingPolicy(), session=sessions.AFTER))
    assert broker.cancelled == ["stop-1"]
    order = broker.sent[0]
    assert order["type"] == "limit" and order["side"] == "sell" and order["extended_hours"] is True
    assert float(order["limit_price"]) < 699.0


def test_a_core_stop_breach_outside_after_hours_does_not_go_out_at_market(monkeypatch):
    class Bars:
        def __init__(self):
            self.closes = [760.0] * 30
            self.highs = [765.0] * 30
            self.lows = [755.0] * 30
            self.dates = []

        def __len__(self):
            return len(self.closes)

    async def bars(*a, **k):
        return Bars()

    monkeypatch.setattr(plan_run, "fetch_bars", bars)
    monkeypatch.setattr(bookorders, "log", lambda *a, **k: None)
    broker = _CoreBroker()
    position = [{"symbol": "QQQ", "qty": "20", "avg_entry_price": "766", "current_price": "700"}]
    now = datetime(2026, 11, 10, 21, 5, tzinfo=sat.ET)
    asyncio.run(plan_run.core_stop_pass(
        broker, None, position, {"QQQ": "core"}, [], [], now, False, lambda s: None,
        TradingPolicy(), session=sessions.OVERNIGHT))
    assert broker.sent == [] and broker.cancelled == []


def test_a_due_rules_close_sells_after_hours_and_not_before_the_open(monkeypatch):
    called = []

    async def sell(broker, client, symbol, book, why, open_orders, session):
        called.append((symbol, session, why))
        return True

    async def extended_market(client, today, log):
        return True

    monkeypatch.setattr(plan_run, "_spx_extended", extended_market)
    orders = [{"symbol": "CIEN", "side": "buy", "filled_at": "2026-11-03T15:00:00Z",
               "client_order_id": "gem-CIEN-1"}]
    position = [{"symbol": "CIEN", "avg_entry_price": "279.42", "current_price": "272.89", "qty": "6"}]
    book = {"CIEN": GEMMA}
    after = datetime(2026, 11, 10, 17, 5, tzinfo=sat.ET)
    asyncio.run(plan_run.exit_pass(
        None, None, position, book, orders, [], sessions.AFTER, after, False, lambda s: None, sell))
    assert called and called[0][0] == "CIEN" and called[0][1] == sessions.AFTER
    called.clear()
    pre = datetime(2026, 11, 10, 8, 0, tzinfo=sat.ET)
    asyncio.run(plan_run.exit_pass(
        None, None, position, book, orders, [], sessions.PRE, pre, False, lambda s: None, sell))
    assert called == []


def test_an_entry_day_rules_position_is_not_sold_after_hours(monkeypatch):
    async def sell(*a, **k):
        raise AssertionError("sold")

    async def extended_market(client, today, log):
        return True

    monkeypatch.setattr(plan_run, "_spx_extended", extended_market)
    orders = [{"symbol": "CIEN", "side": "buy", "filled_at": "2026-11-10T15:00:00Z",
               "client_order_id": "gem-CIEN-1"}]
    position = [{"symbol": "CIEN", "avg_entry_price": "279.42", "current_price": "290", "qty": "6"}]
    after = datetime(2026, 11, 10, 17, 5, tzinfo=sat.ET)
    asyncio.run(plan_run.exit_pass(
        None, None, position, {"CIEN": GEMMA}, orders, [], sessions.AFTER, after, False,
        lambda s: None, sell))


def _lock_bars():
    class Bars:
        def __init__(self):
            from datetime import date
            self.closes = [112.0, 112.0]
            self.highs = [113.0, 113.0]
            self.lows = [111.0, 111.0]
            self.dates = [date(2026, 11, 11), date(2026, 11, 12)]

        def __len__(self):
            return len(self.closes)

    return Bars()


def test_the_10_7_profit_lock_sells_only_in_the_regular_close(monkeypatch):
    called = []
    fetched = []

    async def sell(broker, client, symbol, book, why, open_orders, session):
        called.append((symbol, why))
        return True

    async def bars(symbol, client, range_="6mo"):
        fetched.append(symbol)
        return _lock_bars()

    async def not_extended(client, today, log):
        return False

    monkeypatch.setattr(plan_run, "fetch_bars", bars)
    monkeypatch.setattr(plan_run, "_spx_extended", not_extended)
    orders = [{"symbol": "CIEN", "side": "buy", "filled_at": "2026-11-10T15:00:00Z",
               "client_order_id": "gem-CIEN-1"}]
    position = [{"symbol": "CIEN", "avg_entry_price": "100", "current_price": "105", "qty": "6"}]
    book = {"CIEN": GEMMA}
    close = datetime(2026, 11, 13, 15, 55, tzinfo=sat.ET)
    asyncio.run(plan_run.exit_pass(
        None, None, position, book, orders, [], sessions.REGULAR, close, False, lambda s: None, sell))
    assert fetched == ["CIEN"]
    assert called and called[0][0] == "CIEN" and called[0][1].startswith("profit lock")
    assert "106.00" in called[0][1]
    called.clear()
    fetched.clear()
    after = datetime(2026, 11, 13, 17, 5, tzinfo=sat.ET)
    asyncio.run(plan_run.exit_pass(
        None, None, position, book, orders, [], sessions.AFTER, after, False, lambda s: None, sell))
    assert called == [] and fetched == []


def test_the_10_7_extension_sells_ahead_of_the_profit_lock(monkeypatch):
    called = []

    async def sell(broker, client, symbol, book, why, open_orders, session):
        called.append(why)
        return True

    async def bars(*_a, **_k):
        raise AssertionError("the lock was asked for bars")

    async def extended_market(client, today, log):
        return True

    monkeypatch.setattr(plan_run, "fetch_bars", bars)
    monkeypatch.setattr(plan_run, "_spx_extended", extended_market)
    orders = [{"symbol": "CIEN", "side": "buy", "filled_at": "2026-11-10T15:00:00Z",
               "client_order_id": "gem-CIEN-1"}]
    position = [{"symbol": "CIEN", "avg_entry_price": "100", "current_price": "105", "qty": "6"}]
    close = datetime(2026, 11, 13, 15, 55, tzinfo=sat.ET)
    asyncio.run(plan_run.exit_pass(
        None, None, position, {"CIEN": GEMMA}, orders, [], sessions.REGULAR, close, False,
        lambda s: None, sell))
    assert called and called[0].startswith("market extended")


def _raise_bars():
    """Entry day 150 and today's 130 stay out. The completed close is 112."""

    class Bars:
        def __init__(self):
            from datetime import date
            self.closes = [150.0, 112.0, 112.0, 130.0]
            self.highs = [151.0, 113.0, 113.0, 131.0]
            self.lows = [149.0, 111.0, 111.0, 129.0]
            self.dates = [date(2026, 11, 10), date(2026, 11, 11),
                          date(2026, 11, 12), date(2026, 11, 13)]

        def __len__(self):
            return len(self.closes)

    return Bars()


def _raise_orders(stop="92"):
    return [
        {"symbol": "CIEN", "side": "buy", "filled_at": "2026-11-10T15:00:00Z",
         "client_order_id": "gem-CIEN-1"},
        {"symbol": "CIEN", "side": "sell", "type": "stop", "status": "new",
         "stop_price": stop, "id": "stop-1"},
    ]


def test_the_10_7_stop_rises_in_regular_hours_and_not_through_the_price(monkeypatch):
    fetched = []

    async def sell(*_a, **_k):
        raise AssertionError("sold")

    async def bars(symbol, client, range_="6mo"):
        fetched.append(symbol)
        return _raise_bars()

    monkeypatch.setattr(plan_run, "fetch_bars", bars)
    position = [{"symbol": "CIEN", "avg_entry_price": "100", "current_price": "108", "qty": "6"}]
    book = {"CIEN": GEMMA}
    midday = datetime(2026, 11, 13, 11, 0, tzinfo=sat.ET)
    lines = []
    asyncio.run(plan_run.exit_pass(
        None, None, position, book, _raise_orders(), [], sessions.REGULAR, midday, True,
        lines.append, sell))
    assert fetched == ["CIEN"]
    assert any("WOULD RAISE CIEN stop 92.00 -> 106.00" in line for line in lines)
    lines.clear()
    fetched.clear()
    position[0]["current_price"] = "104"
    asyncio.run(plan_run.exit_pass(
        None, None, position, book, _raise_orders(), [], sessions.REGULAR, midday, True,
        lines.append, sell))
    assert not any("WOULD RAISE" in line or "WOULD SELL" in line for line in lines)
    lines.clear()
    fetched.clear()
    position[0]["current_price"] = "106"
    asyncio.run(plan_run.exit_pass(
        None, None, position, book, _raise_orders(), [], sessions.REGULAR, midday, True,
        lines.append, sell))
    assert not any("WOULD RAISE" in line for line in lines)
    lines.clear()
    fetched.clear()
    pre = datetime(2026, 11, 13, 8, 0, tzinfo=sat.ET)
    asyncio.run(plan_run.exit_pass(
        None, None, position, book, _raise_orders(), [], sessions.PRE, pre, True,
        lines.append, sell))
    assert fetched == []
    assert not any("WOULD RAISE" in line for line in lines)


def test_the_10_7_stop_raise_is_sent_on_the_live_pass(monkeypatch):
    async def sell(*_a, **_k):
        raise AssertionError("sold")

    async def bars(*_a, **_k):
        return _raise_bars()

    logged = []
    sent = []

    class Broker:
        async def replace_stop(self, order_id, price):
            sent.append((order_id, price))

    monkeypatch.setattr(plan_run, "fetch_bars", bars)
    monkeypatch.setattr(bookorders, "log", lambda *a, **k: logged.append(a))
    position = [{"symbol": "CIEN", "avg_entry_price": "100", "current_price": "108", "qty": "6"}]
    midday = datetime(2026, 11, 13, 11, 0, tzinfo=sat.ET)
    lines = []
    asyncio.run(plan_run.exit_pass(
        Broker(), None, position, {"CIEN": GEMMA}, _raise_orders(), [], sessions.REGULAR,
        midday, False, lines.append, sell))
    assert sent == [("stop-1", 106.0)]
    assert logged and logged[0][0] == "gemma_stop_raised" and logged[0][1] == "CIEN"
    assert any("RAISED CIEN stop 92.00 -> 106.00" in line for line in lines)

    class Refused(Broker):
        async def replace_stop(self, order_id, price):
            raise BrokerError("above the market")

    sent.clear()
    lines.clear()
    asyncio.run(plan_run.exit_pass(
        Refused(), None, position, {"CIEN": GEMMA}, _raise_orders(), [], sessions.REGULAR,
        midday, False, lines.append, sell))
    assert sent == []
    assert any("RAISE FAILED" in line and "old stop stands" in line for line in lines)


def test_a_missing_10_7_bar_leaves_the_stop(monkeypatch):
    async def sell(*_a, **_k):
        raise AssertionError("sold")

    async def bars(*_a, **_k):
        raise RuntimeError("down")

    async def not_extended(client, today, log):
        return False

    monkeypatch.setattr(plan_run, "fetch_bars", bars)
    monkeypatch.setattr(plan_run, "_spx_extended", not_extended)
    orders = [{"symbol": "CIEN", "side": "buy", "filled_at": "2026-11-10T15:00:00Z",
               "client_order_id": "gem-CIEN-1"}]
    position = [{"symbol": "CIEN", "avg_entry_price": "100", "current_price": "105", "qty": "6"}]
    lines = []
    close = datetime(2026, 11, 13, 15, 55, tzinfo=sat.ET)
    asyncio.run(plan_run.exit_pass(
        None, None, position, {"CIEN": GEMMA}, orders, [], sessions.REGULAR, close, False,
        lines.append, sell))
    assert any("broker stop stands" in line for line in lines)


def test_the_10_7_lock_ignores_the_entry_day_close(monkeypatch):
    async def sell(*_a, **_k):
        raise AssertionError("sold")

    class Bars:
        def __init__(self):
            from datetime import date
            self.closes = [112.0]
            self.highs = [113.0]
            self.lows = [111.0]
            self.dates = [date(2026, 11, 13)]

        def __len__(self):
            return 1

    async def bars(*_a, **_k):
        return Bars()

    async def not_extended(client, today, log):
        return False

    monkeypatch.setattr(plan_run, "fetch_bars", bars)
    monkeypatch.setattr(plan_run, "_spx_extended", not_extended)
    orders = [{"symbol": "CIEN", "side": "buy", "filled_at": "2026-11-13T15:00:00Z",
               "client_order_id": "gem-CIEN-1"}]
    position = [{"symbol": "CIEN", "avg_entry_price": "100", "current_price": "100", "qty": "6"}]
    close = datetime(2026, 11, 13, 15, 55, tzinfo=sat.ET)
    asyncio.run(plan_run.exit_pass(
        None, None, position, {"CIEN": GEMMA}, orders, [], sessions.REGULAR, close, False,
        lambda s: None, sell))


def test_the_older_close_exits_stay_inside_the_regular_close_window(monkeypatch):
    async def sell(*a, **k):
        raise AssertionError("sold")

    class Bars:
        def __init__(self):
            from datetime import date, timedelta
            self.closes = [100.0] * 40
            self.highs = [101.0] * 40
            self.lows = [99.0] * 40
            start = date(2026, 9, 1)
            self.dates = [start + timedelta(days=i) for i in range(40)]

        def __len__(self):
            return len(self.closes)

    async def bars(*a, **k):
        return Bars()

    monkeypatch.setattr(plan_run, "fetch_bars", bars)
    orders = [{"symbol": "OLD", "side": "buy", "filled_at": "2026-10-01T15:00:00Z",
               "client_order_id": "gem-OLD-1"}]
    position = [{"symbol": "OLD", "avg_entry_price": "100", "current_price": "110", "qty": "10"}]
    after = datetime(2026, 11, 10, 17, 5, tzinfo=sat.ET)
    asyncio.run(plan_run.exit_pass(
        None, None, position, {"OLD": GEMMA}, orders, [], sessions.AFTER, after, False,
        lambda s: None, sell))
