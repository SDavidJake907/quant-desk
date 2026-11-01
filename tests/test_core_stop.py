"""The SPY core keeps a stop under every share (operator 2026-10-30: "every position should have a sl")."""

import asyncio
from datetime import date, datetime, timedelta

from scanner.execution import bookorders, plan_run
from scanner.execution import satellite as sat
from scanner.execution.policy import TradingPolicy

NOW = datetime(2026, 10, 30, 21, 0, tzinfo=sat.ET)


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
                                                 NOW, False, lines.append, TradingPolicy()))
    return broker, alerts, lines


def test_core_without_a_stop_gets_one_at_2_5_atr_under_the_buy(monkeypatch):
    broker, alerts, _ = _run(monkeypatch)
    (order,) = broker.sent
    assert order["type"] == "stop" and order["time_in_force"] == "gtc" and order["qty"] == "64"
    assert order["client_order_id"].startswith(sat.CORE_STOP_PREFIX)
    assert 735 < float(order["stop_price"]) < 745   # 766 - 2.5 x ~9.9
    assert alerts and "armed" in alerts[0]


def test_matching_stop_is_left_alone(monkeypatch):
    working = [{"id": "s1", "side": "sell", "type": "stop", "qty": "64", "stop_price": "745.00",
                "client_order_id": "cor-stop-SPY-1"}]
    broker, alerts, _ = _run(monkeypatch, working=working)
    assert broker.sent == [] and broker.cancelled == [] and broker.replaced == [] and alerts == []


def test_top_up_resends_at_the_new_count_and_never_lowers(monkeypatch):
    working = [{"id": "s1", "side": "sell", "type": "stop", "qty": "32", "stop_price": "750.00",
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
                                                 lambda s: None, TradingPolicy()))
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
