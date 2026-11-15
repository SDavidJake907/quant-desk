"""The SPY core keeps a stop under every share (operator 2026-10-30: "every position should have a sl")."""

import asyncio
import json
from datetime import date, datetime, timedelta

import pytest

from scanner.execution import bookorders, plan_run
from scanner.execution import satellite as sat
from scanner.execution.alpaca import BrokerError
from scanner.execution.policy import TradingPolicy

NOW = datetime(2026, 10, 30, 21, 0, tzinfo=sat.ET)
#: The two-index core these mechanics were written for (the default is QQQ alone from 11-02).
SPLIT = TradingPolicy(core_symbols=("SPY", "QQQ"), core_allocation_pct=50.0)


class Bars:
    def __init__(self, closes):
        self.closes = closes
        self.highs = [c + 5 for c in closes]
        self.lows = [c - 5 for c in closes]
        start = date(2026, 6, 1)
        self.dates = [start + timedelta(days=i) for i in range(len(closes))]

    def __len__(self):
        return len(self.closes)


class FakeBroker:
    def __init__(self):
        self.sent, self.cancelled, self.replaced = [], [], []

    async def submit(self, payload):
        self.sent.append(payload)
        return {"id": "new"}

    async def cancel_order(self, order_id):
        self.cancelled.append(order_id)

    async def replace_stop(self, order_id, stop):
        self.replaced.append((order_id, stop))


def _run(monkeypatch, *, qty=64, price=755.0, entry=766.0, working=()):
    monkeypatch.setattr(bookorders, "log", lambda *a, **k: None)

    async def bars(*a, **k):
        return Bars([760.0] * 30)   # true range 10 -> ATR ~10

    monkeypatch.setattr(plan_run, "fetch_bars", bars)
    broker = FakeBroker()
    positions = [{"symbol": "SPY", "qty": str(qty), "avg_entry_price": str(entry), "current_price": str(price)}]
    lines = []
    alerts = asyncio.run(plan_run.core_stop_pass(broker, None, positions, {"SPY": "core"}, [], list(working),
                                                 NOW, False, lines.append, SPLIT))
    return broker, alerts, lines


def test_core_without_a_stop_gets_one_at_2_5_atr_under_the_buy(monkeypatch):
    broker, alerts, _ = _run(monkeypatch)
    (order,) = broker.sent
    assert order["type"] == "stop" and order["time_in_force"] == "gtc" and order["qty"] == "64"
    assert order["client_order_id"].startswith(sat.CORE_STOP_PREFIX)
    assert 735 < float(order["stop_price"]) < 745   # 766 - 2.5 x ~9.9
    assert alerts and "armed" in alerts[0]


def test_matching_stop_is_left_alone(monkeypatch):
    working = [{"id": "s1", "symbol": "SPY", "side": "sell", "type": "stop", "qty": "64", "stop_price": "745.00",
                "client_order_id": "cor-stop-SPY-1"}]
    broker, alerts, _ = _run(monkeypatch, working=working)
    assert broker.sent == [] and broker.cancelled == [] and broker.replaced == [] and alerts == []


def test_top_up_resends_at_the_new_count_and_never_lowers(monkeypatch):
    working = [{"id": "s1", "symbol": "SPY", "side": "sell", "type": "stop", "qty": "32", "stop_price": "750.00",
                "client_order_id": "cor-stop-SPY-1"}]
    broker, _, _ = _run(monkeypatch, qty=64, price=760.0, working=working)
    assert broker.cancelled == ["s1"]
    (order,) = broker.sent
    assert order["qty"] == "64" and float(order["stop_price"]) == 750.00


def test_price_already_under_the_stop_sells_at_market(monkeypatch):
    broker, alerts, _ = _run(monkeypatch, price=730.0)
    (order,) = broker.sent
    assert order["type"] == "market" and "stop_price" not in order
    assert order["client_order_id"].startswith(sat.CORE_STOP_PREFIX)
    assert alerts and "Sell sent" in alerts[0]


def test_no_core_position_does_nothing(monkeypatch):
    monkeypatch.setattr(bookorders, "log", lambda *a, **k: None)
    broker = FakeBroker()
    alerts = asyncio.run(plan_run.core_stop_pass(broker, None, [], {}, [], [], NOW, False,
                                                 lambda s: None, SPLIT))
    assert alerts == [] and broker.sent == []


def test_rebuy_waits_for_a_new_high_after_a_stop_out():
    today = date(2026, 11, 5)
    flat = [700.0] * 25
    assert sat.core_rebuy_blocked(stopped_on=date(2026, 11, 2), today=today, closes=flat)
    assert not sat.core_rebuy_blocked(stopped_on=date(2026, 11, 2), today=today, closes=flat + [701.0])
    assert not sat.core_rebuy_blocked(stopped_on=date(2026, 9, 1), today=today, closes=flat)
    assert not sat.core_rebuy_blocked(stopped_on=None, today=today, closes=flat)


def test_core_stopped_on_reads_the_stop_fill():
    orders = [{"side": "sell", "filled_at": "2026-11-02T15:00:00Z", "client_order_id": "cor-stop-SPY-9"},
              {"side": "sell", "filled_at": "2026-11-03T15:00:00Z", "client_order_id": "gem-AAA-1"}]
    assert plan_run.core_stopped_on(orders) == date(2026, 11, 2)
    assert plan_run.core_stopped_on([]) is None


# --- no weekend holds (operator 2026-10-30: "we need to stop holding for the weekend") ---

class CalendarBroker:
    def __init__(self, next_day):
        self.next_day = next_day

    async def _get(self, path, **_):
        assert path == "/v2/calendar"
        if self.next_day is None:
            raise RuntimeError("down")
        return [{"date": self.next_day}]


def test_spy_and_day_trades_are_flattened_swing_books_hold():
    scope = set(TradingPolicy().flatten_strategies)
    assert {"core", "orb"} <= scope and not {"gemma", "small"} & scope


def test_full_slot_sizing_and_the_8pct_stop_cap():
    # risk at an 8% stop on a full 25% slot = 2% of the sleeve: the slot binds, never the risk
    assert sat.full_slot_risk_pct(25.0) == 2.0
    # RNG-like: 2.5 ATR is 16% under the buy -> capped at 8%
    assert sat.hard_stop(entry=100.0, atr=6.4, high_close=100.0, floor=False, max_loss_pct=8.0) == 92.0
    # SPY-like: 2.5 ATR is 2.7% under -> the cap does not bind
    assert abs(sat.hard_stop(entry=100.0, atr=1.08, high_close=100.0, floor=False, max_loss_pct=8.0) - 97.3) < 1e-9


def test_no_buys_on_the_last_session_before_a_weekend():
    friday, thursday = date(2026, 11, 6), date(2026, 11, 5)
    assert asyncio.run(plan_run.before_closure(CalendarBroker("2026-11-09"), friday, lambda s: None))
    assert not asyncio.run(plan_run.before_closure(CalendarBroker("2026-11-06"), thursday, lambda s: None))
    assert asyncio.run(plan_run.before_closure(CalendarBroker(None), thursday, lambda s: None))


def test_a_falling_price_does_not_raise_the_stop():
    # The ATR is the bars' own (dollars), not atr_pct x the live price (RNG 70.75 -> 71.66, 10-28..30).
    bars = Bars([100.0] * 30)
    assert abs(plan_run.atr_dollars(bars) - 10.0) < 1e-9
    high, low = (sat.hard_stop(entry=100.0, atr=plan_run.atr_dollars(bars), high_close=100.0, floor=False)
                 for _ in range(2))
    assert high == low == 75.0


# --- the core split SPY + QQQ (operator 2026-11-01: "change over to S&P 500 and NASDAQ") ---

BUY_NOW = datetime(2026, 11, 2, 10, 5, tzinfo=sat.ET)


class CoreBroker(FakeBroker):
    def __init__(self, equity=100_000.0, free=64):
        super().__init__()
        self.equity, self.free = equity, free

    async def _get(self, path, **_):
        if path == "/v2/account":
            return {"equity": str(self.equity)}
        if path.startswith("/v2/positions/"):
            return {"qty": "64", "qty_available": str(self.free)}
        if path == "/v2/calendar":
            return [{"date": "2026-11-03"}]   # Monday: no closure tomorrow
        raise AssertionError(path)


def _core(monkeypatch, tmp_path, positions, *, free=64, working=()):
    monkeypatch.setattr(bookorders, "log", lambda *a, **k: None)

    async def price(symbol, client):
        return {"SPY": 750.0, "QQQ": 600.0}[symbol], 1.0

    async def no_wait(_):
        return None

    monkeypatch.setattr(bookorders, "price_and_atr", price)
    monkeypatch.setattr(plan_run.asyncio, "sleep", no_wait)
    broker = CoreBroker(free=free)
    owner = {p["symbol"]: "core" for p in positions}
    alerts = asyncio.run(plan_run.core_pass(broker, None, positions, owner, list(working), BUY_NOW, False,
                                            lambda s: None, SPLIT, state_path=tmp_path / "core.json"))
    return broker, alerts


def test_core_is_qqq_alone_from_11_02():
    # Operator 2026-11-02: "sell SPY!!! i dont want to see that postion".
    p = TradingPolicy()
    assert p.core_symbols == ("QQQ",) and p.core_allocation_pct == 25.0


def test_an_all_spy_core_is_trimmed_to_half_and_qqq_bought(monkeypatch, tmp_path):
    spy = {"symbol": "SPY", "qty": "64", "market_value": "48000", "current_price": "750", "avg_entry_price": "766"}
    stop = {"id": "s1", "symbol": "SPY", "side": "sell", "type": "stop", "qty": "64", "stop_price": "746",
            "client_order_id": "cor-stop-SPY-1"}
    broker, alerts = _core(monkeypatch, tmp_path, [spy], working=[stop])
    assert broker.cancelled == ["s1"]                       # the stop releases the shares first
    trim, buy = broker.sent
    # SPY target 25% of 100k = 25,000: sell (48,000 - 25,000) // 750 = 30 shares at market
    assert trim["symbol"] == "SPY" and trim["side"] == "sell" and trim["qty"] == "30" and trim["type"] == "market"
    assert trim["client_order_id"].startswith(plan_run.CORE_TRIM_PREFIX)
    assert not trim["client_order_id"].startswith(sat.CORE_STOP_PREFIX)   # not a stop-out: no re-buy block
    # QQQ: the first half of its 25,000 target -> 12,500 // 600 = 20 shares
    assert buy["symbol"] == "QQQ" and buy["side"] == "buy" and buy["qty"] == "20"
    assert buy["client_order_id"].startswith("cor-")


def test_trim_waits_for_shares_and_gives_up_safely(monkeypatch, tmp_path):
    spy = {"symbol": "SPY", "qty": "64", "market_value": "48000", "current_price": "750", "avg_entry_price": "766"}
    broker, alerts = _core(monkeypatch, tmp_path, [spy], free=0)
    assert all(o["side"] == "buy" for o in broker.sent)     # no SPY sale without free shares
    assert any("never released" in a for a in alerts)


def test_core_within_its_target_band_is_left_alone(monkeypatch, tmp_path):
    held = [{"symbol": "SPY", "qty": "33", "market_value": "24750", "current_price": "750", "avg_entry_price": "750"},
            {"symbol": "QQQ", "qty": "41", "market_value": "24600", "current_price": "600", "avg_entry_price": "600"}]
    broker, alerts = _core(monkeypatch, tmp_path, held)
    assert broker.sent == [] and alerts == []


def test_each_index_keeps_its_own_stop(monkeypatch):
    monkeypatch.setattr(bookorders, "log", lambda *a, **k: None)

    async def bars(*a, **k):
        return Bars([600.0] * 30)

    monkeypatch.setattr(plan_run, "fetch_bars", bars)
    broker = FakeBroker()
    positions = [{"symbol": "SPY", "qty": "33", "avg_entry_price": "750", "current_price": "752"},
                 {"symbol": "QQQ", "qty": "20", "avg_entry_price": "600", "current_price": "601"}]
    spy_stop = {"id": "s1", "symbol": "SPY", "side": "sell", "type": "stop", "qty": "33", "stop_price": "740.00",
                "client_order_id": "cor-stop-SPY-1"}
    asyncio.run(plan_run.core_stop_pass(broker, None, positions, {"SPY": "core", "QQQ": "core"}, [], [spy_stop],
                                        NOW, False, lambda s: None, SPLIT))
    (order,) = broker.sent                                   # SPY's stop matches; only QQQ gets one
    assert order["symbol"] == "QQQ" and order["qty"] == "20" and order["client_order_id"].startswith("cor-stop-QQQ")


def test_a_stop_out_blocks_only_its_own_index():
    orders = [{"symbol": "QQQ", "side": "sell", "filled_at": "2026-11-02T15:00:00Z", "client_order_id": "cor-stop-QQQ-9"}]
    assert plan_run.core_stopped_on(orders, "QQQ") == date(2026, 11, 2)
    assert plan_run.core_stopped_on(orders, "SPY") is None


QQQ_STOP = {"id": "qqq-stop", "symbol": "QQQ", "side": "sell", "type": "stop", "qty": "20",
            "stop_price": "659.11", "client_order_id": "cor-stop-QQQ-1"}
WASH = 'order failed (request abc): 403 {"message":"potential wash trade detected"}'


def _qqq_pass(monkeypatch, broker, path, working=(QQQ_STOP,)):
    monkeypatch.setattr(bookorders, "log", lambda *a, **k: None)

    async def price(symbol, client):
        return 600.0, 1.0

    async def no_wait(_):
        return None

    monkeypatch.setattr(bookorders, "price_and_atr", price)
    monkeypatch.setattr(plan_run.asyncio, "sleep", no_wait)
    held = [{"symbol": "QQQ", "qty": "20", "market_value": "12000", "current_price": "600",
             "avg_entry_price": "600"}]
    return asyncio.run(plan_run.core_pass(
        broker, None, held, {"QQQ": "core"}, list(working), BUY_NOW, False,
        lambda s: None, TradingPolicy(), state_path=path))


def test_a_wash_trade_retries_once_and_puts_the_old_stop_back(monkeypatch, tmp_path):
    class WashThenOk(CoreBroker):
        def __init__(self):
            super().__init__()
            self.buys = 0

        async def submit(self, payload):
            if payload.get("side") == "buy":
                self.buys += 1
                if self.buys == 1:
                    raise BrokerError(WASH)
            self.sent.append(payload)
            return {"id": "new"}

    path = tmp_path / "core.json"
    broker = WashThenOk()
    alerts = _qqq_pass(monkeypatch, broker, path)
    assert broker.cancelled == ["qqq-stop"]
    assert broker.buys == 2
    buy, stop = broker.sent
    assert buy["side"] == "buy" and buy["qty"] == "20" and buy["client_order_id"].endswith("-r")
    assert stop["side"] == "sell" and stop["type"] == "stop"
    assert stop["qty"] == "20" and stop["stop_price"] == "659.11"
    assert stop["client_order_id"].startswith("cor-stop-QQQ")
    assert any("core buy" in a for a in alerts)
    assert json.loads(path.read_text(encoding="utf-8"))["day"] == "2026-11-02"
    again = _qqq_pass(monkeypatch, broker, path)
    assert again == [] and broker.buys == 2


def test_a_wash_retry_that_fails_rearms_the_stop_and_does_not_try_a_third_time(monkeypatch, tmp_path):
    class WashThenNo(CoreBroker):
        async def submit(self, payload):
            if payload.get("side") == "buy":
                raise BrokerError(WASH)
            self.sent.append(payload)
            return {"id": "new"}

    path = tmp_path / "core.json"
    broker = WashThenNo()
    alerts = _qqq_pass(monkeypatch, broker, path)
    assert broker.cancelled == ["qqq-stop"]
    (stop,) = broker.sent
    assert stop["type"] == "stop" and stop["qty"] == "20" and stop["stop_price"] == "659.11"
    assert alerts == []
    assert json.loads(path.read_text(encoding="utf-8"))["day"] == "2026-11-02"


def test_a_refusal_that_is_not_a_wash_leaves_the_stop(monkeypatch, tmp_path):
    class Refuse(CoreBroker):
        async def submit(self, payload):
            raise BrokerError("order failed (request abc): 403 buying power")

    path = tmp_path / "core.json"
    broker = Refuse()
    assert _qqq_pass(monkeypatch, broker, path) == []
    assert broker.cancelled == [] and broker.sent == []
    assert json.loads(path.read_text(encoding="utf-8"))["day"] == "2026-11-02"
    assert _qqq_pass(monkeypatch, broker, path) == []


def test_a_stop_that_will_not_cancel_is_left_working(monkeypatch, tmp_path):
    class Stuck(CoreBroker):
        async def cancel_order(self, order_id):
            raise BrokerError("cancel refused")

        async def submit(self, payload):
            if payload.get("side") == "buy":
                raise BrokerError(WASH)
            self.sent.append(payload)
            return {"id": "new"}

    broker = Stuck()
    assert _qqq_pass(monkeypatch, broker, tmp_path / "core.json") == []
    assert broker.cancelled == [] and broker.sent == []


def test_a_stop_already_cancelled_is_put_back_when_the_next_cancel_fails(monkeypatch, tmp_path):
    second = {"id": "qqq-stop-2", "symbol": "QQQ", "side": "sell", "type": "stop", "qty": "5",
              "stop_price": "650.00", "client_order_id": "cor-stop-QQQ-2"}

    class SecondCancelFails(CoreBroker):
        def __init__(self):
            super().__init__()
            self.tries = 0

        async def cancel_order(self, order_id):
            self.tries += 1
            if self.tries == 2:
                raise BrokerError("cancel refused")
            self.cancelled.append(order_id)

        async def submit(self, payload):
            if payload.get("side") == "buy":
                raise BrokerError(WASH)
            self.sent.append(payload)
            return {"id": "new"}

    broker = SecondCancelFails()
    assert _qqq_pass(monkeypatch, broker, tmp_path / "core.json", working=(QQQ_STOP, second)) == []
    assert broker.cancelled == ["qqq-stop"]
    (stop,) = broker.sent
    assert stop["type"] == "stop" and stop["qty"] == "20" and stop["stop_price"] == "659.11"


def test_the_stop_waits_for_its_shares_and_is_sent_once_they_are_free(monkeypatch, tmp_path):
    class SlowRelease(CoreBroker):
        def __init__(self):
            super().__init__()
            self.reads = 0
            self.buys = 0

        async def _get(self, path, **kwargs):
            if path.startswith("/v2/positions/"):
                self.reads += 1
                free = "20" if self.reads >= 3 else "0"
                return {"qty": "20", "qty_available": free}
            return await super()._get(path, **kwargs)

        async def submit(self, payload):
            if payload.get("side") == "buy":
                self.buys += 1
                if self.buys == 1:
                    raise BrokerError(WASH)
            self.sent.append(payload)
            return {"id": "new"}

    broker = SlowRelease()
    alerts = _qqq_pass(monkeypatch, broker, tmp_path / "core.json")
    stops = [o for o in broker.sent if o["type"] == "stop"]
    assert len(stops) == 1 and stops[0]["qty"] == "20"
    assert broker.reads >= 3
    assert any("core buy" in a for a in alerts)
    assert all("could not be put back" not in a for a in alerts)


def test_a_stop_that_keeps_being_refused_is_named_and_the_buy_is_not_repeated(monkeypatch, tmp_path):
    class BuyOkStopNo(CoreBroker):
        def __init__(self):
            super().__init__()
            self.buys = 0
            self.stop_tries = 0

        async def submit(self, payload):
            if payload.get("side") == "buy":
                self.buys += 1
                if self.buys == 1:
                    raise BrokerError(WASH)
                self.sent.append(payload)
                return {"id": "buy"}
            self.stop_tries += 1
            raise BrokerError("order failed (request z): 403 potential wash trade detected")

    path = tmp_path / "core.json"
    broker = BuyOkStopNo()
    alerts = _qqq_pass(monkeypatch, broker, path)
    assert broker.buys == 2
    assert broker.stop_tries == plan_run.TRIM_RELEASE_TRIES
    assert any("core buy" in a for a in alerts)
    assert any("could not be put back" in a for a in alerts)
    assert _qqq_pass(monkeypatch, broker, path) == []
    assert broker.buys == 2


def test_an_old_day_file_without_a_symbol_list_is_already_finished(monkeypatch, tmp_path):
    """This morning's file is {day, core_value, pct_each}. It must keep blocking.
    Deleting it would let the next peak-watch pass send the QQQ add."""
    path = tmp_path / "core.json"
    path.write_text(json.dumps({"day": "2026-11-02", "core_value": {"QQQ": 12000}, "pct_each": 25}),
                    encoding="utf-8")
    broker = CoreBroker()
    assert _qqq_pass(monkeypatch, broker, path) == []
    assert broker.sent == []


def test_a_finished_symbol_is_not_repeated_when_another_is_still_open(monkeypatch, tmp_path):
    path = tmp_path / "core.json"
    path.write_text(json.dumps({"day": "2026-11-02", "done": ["SPY"]}), encoding="utf-8")
    spy = {"symbol": "SPY", "qty": "64", "market_value": "48000", "current_price": "750", "avg_entry_price": "766"}
    stop = {"id": "s1", "symbol": "SPY", "side": "sell", "type": "stop", "qty": "64", "stop_price": "746",
            "client_order_id": "cor-stop-SPY-1"}
    monkeypatch.setattr(bookorders, "log", lambda *a, **k: None)

    async def price(symbol, client):
        return {"SPY": 750.0, "QQQ": 600.0}[symbol], 1.0

    async def no_wait(_):
        return None

    monkeypatch.setattr(bookorders, "price_and_atr", price)
    monkeypatch.setattr(plan_run.asyncio, "sleep", no_wait)
    broker = CoreBroker()
    asyncio.run(plan_run.core_pass(
        broker, None, [spy], {"SPY": "core", "QQQ": "core"}, [stop], BUY_NOW, False,
        lambda s: None, SPLIT, state_path=path))
    assert [o["symbol"] for o in broker.sent if o["side"] == "sell"] == []
    assert any(o["symbol"] == "QQQ" and o["side"] == "buy" for o in broker.sent)
    assert "QQQ" in json.loads(path.read_text(encoding="utf-8"))["done"]


def test_a_crash_before_the_core_attempt_finishes_can_run_again(monkeypatch, tmp_path):
    class Boom(CoreBroker):
        async def submit(self, payload):
            raise RuntimeError("broker down")

    path = tmp_path / "core.json"
    with pytest.raises(RuntimeError):
        _qqq_pass(monkeypatch, Boom(), path)
    assert not path.exists()
    broker = CoreBroker()
    alerts = _qqq_pass(monkeypatch, broker, path, working=())
    assert any(o["side"] == "buy" for o in broker.sent)
    assert any("core buy" in a for a in alerts)
    assert json.loads(path.read_text(encoding="utf-8"))["day"] == "2026-11-02"
