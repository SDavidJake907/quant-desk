"""Alpaca adapter tests.

Two properties matter more than the rest: paper must be the default, and the
same setup must never be able to open two positions. Everything else is
plumbing that fails loudly; those two fail silently and expensively.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import httpx
import pytest

from scanner.execution.alpaca import (
    LIVE_HOST,
    PAPER_HOST,
    Alpaca,
    BrokerError,
    bracket_payload,
    client_order_id,
    round_tick,
)
from scanner.execution.sizing import Sizing
from scanner.features.intraday import EXCHANGE_TZ
from scanner.screens.smc import Setup

AT = datetime(2026, 8, 5, 13, 30, tzinfo=EXCHANGE_TZ)
SETUP = Setup(
    symbol="MU", trigger_index=100, sweep_index=80, sweep_price=737.88,
    choch_index=90, choch_price=930.88, higher_low_index=95,
    higher_low_price=770.15, entry_price=893.19, stop_price=770.15,
    target_price=1011.77,
)
SIZING = Sizing(shares=12, risk_dollars=1476.0, position_dollars=10718.0,
                limited_by="risk budget")


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient()


# ---- paper by default --------------------------------------------------

def test_paper_is_the_default_host():
    broker = Alpaca("key", "secret", _client())
    assert broker.host == PAPER_HOST
    assert broker.paper is True


def test_live_requires_saying_so():
    assert Alpaca("key", "secret", _client(), paper=False).host == LIVE_HOST


def test_missing_keys_fail_immediately():
    with pytest.raises(BrokerError):
        Alpaca("", "", _client())


def test_account_is_not_tradeable_when_not_active_or_suspended():
    from scanner.execution.alpaca import Account

    assert not Account(equity=100_000, cash=100_000, buying_power=100_000,
                       status="APPROVAL_PENDING").tradeable
    assert not Account(equity=100_000, cash=100_000, buying_power=100_000,
                       blocked=True).tradeable


# ---- the duplicate guard -----------------------------------------------

def test_the_same_bar_always_produces_the_same_order_id():
    """A restart or a repeated scan must not open a second position."""
    assert client_order_id(SETUP, AT) == client_order_id(SETUP, AT)


def test_a_different_bar_produces_a_different_id():
    assert client_order_id(SETUP, AT) != client_order_id(SETUP, AT + timedelta(minutes=30))


def test_a_different_symbol_produces_a_different_id():
    other = SETUP.model_copy(update={"symbol": "NVDA"})
    assert client_order_id(SETUP, AT) != client_order_id(other, AT)


def test_the_id_is_not_derived_from_the_wall_clock():
    """Deriving it from now() would give two ids for one bar, which is
    exactly the bug the id exists to prevent."""
    first = client_order_id(SETUP, AT)
    assert client_order_id(SETUP, AT) == first


# ---- the order body ----------------------------------------------------

def test_the_bracket_carries_all_three_prices():
    payload = bracket_payload(SETUP, SIZING, AT)
    assert payload["order_class"] == "bracket"
    assert payload["limit_price"] == "893.19"
    assert payload["stop_loss"]["stop_price"] == "770.15"
    assert payload["take_profit"]["limit_price"] == "1011.77"


def test_entry_is_a_limit_not_a_market_order():
    """The backtest assumed a fill at the trigger close. A market order
    fills wherever the book is and breaks that correspondence."""
    assert bracket_payload(SETUP, SIZING, AT)["type"] == "limit"


def test_quantity_comes_from_sizing():
    assert bracket_payload(SETUP, SIZING, AT)["qty"] == "12"


def test_extended_hours_is_off():
    """Extended hours rejects stop orders outright, so a bracket sent there
    would lose its protection."""
    assert bracket_payload(SETUP, SIZING, AT)["extended_hours"] is False


def test_prices_are_rounded_to_the_tick():
    assert round_tick(100.123456) == pytest.approx(100.12)
    assert round_tick(99.999) == pytest.approx(100.00)


def test_sub_penny_prices_never_reach_the_broker():
    odd = SETUP.model_copy(update={"entry_price": 12.34567, "stop_price": 11.11111,
                                   "target_price": 15.98765})
    payload = bracket_payload(odd, SIZING, AT)
    for price in (payload["limit_price"], payload["stop_loss"]["stop_price"],
                  payload["take_profit"]["limit_price"]):
        assert len(price.split(".")[1]) == 2


def test_bracket_legs_must_survive_the_close():
    """The bug this pins cost three positions their stops over a weekend.

    `time_in_force` applies to the protective legs as well as the entry, so
    `day` cancelled every stop and target at 16:00 and nothing errored — the
    orders did exactly what `day` means. Only 54% of these setups resolve
    inside one session, so roughly half of all positions were spending the
    night unprotected while the backtest assumed a live stop.
    """
    from datetime import datetime, timezone

    from scanner.execution.alpaca import bracket_payload
    from scanner.execution.sizing import Sizing
    from scanner.screens.smc import Setup

    setup = Setup(
        symbol="Q", trigger_index=10, sweep_index=4, sweep_price=130.0,
        choch_index=6, choch_price=140.0, higher_low_index=8,
        higher_low_price=133.59, entry_price=138.30, stop_price=133.59,
        target_price=146.08,
    )
    sizing = Sizing(shares=143, risk_dollars=673.0,
                    position_dollars=19776.0, limited_by="position cap")
    payload = bracket_payload(setup, sizing, datetime.now(timezone.utc))

    assert payload["time_in_force"] == "gtc", (
        "day-order brackets lose their stop at the close; this strategy holds "
        "positions overnight by design"
    )
    assert payload["order_class"] == "bracket"
    assert payload["stop_loss"]["stop_price"] == "133.59"
    assert payload["take_profit"]["limit_price"] == "146.08"


# ---- reconciliation: does every position still have a stop? ------------
#
# The `gtc` fix above stops NEW orders losing their protection. It does
# nothing for a position already naked, and nothing noticed for two whole
# sessions that three of them were — because a cancelled stop is not an
# error, it is an absence. These tests pin the detector for that absence.

def test_a_position_with_no_orders_at_all_is_unprotected():
    """The exact weekend state: stops cancelled at the close, book empty."""
    from scanner.execution.alpaca import unprotected

    naked = unprotected([{"symbol": "Q", "qty": "143"}], [])
    assert [p.symbol for p in naked] == ["Q"]
    assert naked[0].qty == 143


def test_a_live_stop_leg_counts_as_protection():
    """A bracket's legs arrive nested under the parent, not as top-level
    orders. Reading only the top level would call every protected position
    naked and re-arm a stop that is already there."""
    from scanner.execution.alpaca import unprotected

    orders = [{
        "symbol": "Q", "side": "buy", "status": "filled", "legs": [
            {"symbol": "Q", "side": "sell", "status": "held",
             "qty": "143", "stop_price": "133.59"},
            {"symbol": "Q", "side": "sell", "status": "held",
             "qty": "143", "limit_price": "146.08"},
        ],
    }]
    assert unprotected([{"symbol": "Q", "qty": "143"}], orders) == ()


def test_a_leg_without_a_share_count_is_treated_as_no_cover():
    """Fail towards the false alarm. An unreadable leg reported as covered
    is the silence this whole check exists to break; reported as naked it
    costs one needless warning.
    """
    from scanner.execution.alpaca import unprotected

    orders = [{"symbol": "Q", "side": "buy", "status": "filled", "legs": [
        {"symbol": "Q", "side": "sell", "status": "held", "stop_price": "133.59"},
    ]}]
    assert [p.symbol for p in unprotected([{"symbol": "Q", "qty": "143"}], orders)] == ["Q"]


def test_a_cancelled_stop_leg_is_not_protection():
    """Precisely the weekend failure: the leg exists in the order record,
    with status `canceled`. Matching on presence rather than on status is
    how a naked position reads as covered."""
    from scanner.execution.alpaca import unprotected

    orders = [{
        "symbol": "Q", "side": "buy", "status": "filled", "legs": [
            {"symbol": "Q", "side": "sell", "status": "canceled", "stop_price": "133.59"},
            {"symbol": "Q", "side": "sell", "status": "expired", "limit_price": "146.08"},
        ],
    }]
    assert [p.symbol for p in unprotected([{"symbol": "Q", "qty": "143"}], orders)] == ["Q"]


def test_a_working_buy_order_does_not_protect_anything():
    """A resting BUY is not a stop. Counting any open order for the symbol
    as cover would have hidden this bug too."""
    from scanner.execution.alpaca import unprotected

    orders = [{"symbol": "Q", "side": "buy", "status": "new", "legs": []}]
    assert [p.symbol for p in unprotected([{"symbol": "Q", "qty": "143"}], orders)] == ["Q"]


def test_a_partial_stop_leaves_the_remainder_unprotected():
    """A stop for 43 shares of a 143-share position covers 43 shares. The
    other 100 are naked and must be reported as such."""
    from scanner.execution.alpaca import unprotected

    orders = [{"symbol": "Q", "side": "sell", "status": "new",
               "qty": "43", "stop_price": "133.59", "legs": []}]
    naked = unprotected([{"symbol": "Q", "qty": "143"}], orders)
    assert [(p.symbol, p.qty) for p in naked] == [("Q", 100)]


def test_only_the_naked_names_come_back():
    from scanner.execution.alpaca import unprotected

    positions = [{"symbol": "CEG", "qty": "73"}, {"symbol": "IP", "qty": "475"}]
    orders = [{"symbol": "CEG", "side": "sell", "status": "new",
               "qty": "73", "stop_price": "265.00", "legs": []}]
    assert [p.symbol for p in unprotected(positions, orders)] == ["IP"]


# ---- re-arming a naked position ---------------------------------------

def test_protective_payload_is_a_gtc_oco_sell():
    """Re-arming an ALREADY FILLED position cannot use a bracket — there is
    no entry left to attach to. OCO is the exit-only form: two sells, either
    one cancels the other.
    """
    from scanner.execution.alpaca import protective_payload

    payload = protective_payload("Q", 143, stop=133.5925, target=146.08)
    assert payload["order_class"] == "oco"
    assert payload["side"] == "sell"
    assert payload["qty"] == "143"
    assert payload["time_in_force"] == "gtc", "the whole point of the fix"
    assert payload["stop_loss"]["stop_price"] == "133.59"
    assert payload["take_profit"]["limit_price"] == "146.08"


def test_protective_payload_rounds_to_the_tick():
    from scanner.execution.alpaca import protective_payload

    payload = protective_payload("Q", 143, stop=133.59249, target=146.08001)
    for price in (payload["stop_loss"]["stop_price"],
                  payload["take_profit"]["limit_price"]):
        assert len(price.split(".")[1]) == 2


def test_protective_payload_refuses_an_inverted_stop():
    """A stop above the target is a sign the caller mixed up its arguments.
    Sent to the broker it would either reject or fill instantly at a loss."""
    from scanner.execution.alpaca import protective_payload

    with pytest.raises(ValueError):
        protective_payload("Q", 143, stop=146.08, target=133.59)


def test_trade_py_reconciles_protection_before_it_scans():
    """The detector is only worth having if it actually runs.

    Nothing in this repo looked for a missing stop, so three positions sat
    naked for two sessions while every log line reported success. This pins
    the call site, not just the function — deleting the check from trade.py
    must fail a test rather than quietly restoring that silence.
    """
    from pathlib import Path

    source = Path("scripts/trade.py").read_text(encoding="utf-8")
    assert "unprotected_positions()" in source, (
        "trade.py no longer reconciles open positions against live stops"
    )
    # It must run BEFORE sizing decides to add yet another position.
    assert source.index("unprotected_positions()") < source.index("size_position("), (
        "protection is reconciled after new positions are sized — a naked "
        "position should be found before the scanner looks for more"
    )


# ---- account baseline and time exits ----------------------------------------

def _broker(handler) -> Alpaca:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return Alpaca("key", "secret", client)


async def test_account_reads_the_previous_close():
    def handler(request):
        return httpx.Response(200, json={
            "equity": "96000", "cash": "1000", "buying_power": "2000",
            "last_equity": "100000", "status": "ACTIVE",
        })
    account = await _broker(handler).account()
    assert account.last_equity == 100_000.0


async def test_closing_cancels_the_bracket_legs_first():
    """Open legs reserve the shares, and Alpaca refuses the close until
    they are gone. The cancel has to happen first, and the close only once
    nothing is still open."""
    calls: list[str] = []
    open_orders = [{"id": "stop-1", "symbol": "MU", "side": "sell"},
                   {"id": "other", "symbol": "AMD", "side": "sell"}]

    def handler(request):
        calls.append(f"{request.method} {request.url.path}")
        if request.method == "GET":
            return httpx.Response(200, json=open_orders)
        if request.url.path == "/v2/orders/stop-1":
            open_orders[:] = [o for o in open_orders if o["id"] != "stop-1"]
        return httpx.Response(204)

    await _broker(handler).close_position_and_legs("MU", poll_s=0)
    deletes = [c for c in calls if c.startswith("DELETE")]
    assert deletes == ["DELETE /v2/orders/stop-1", "DELETE /v2/positions/MU"]


async def test_close_is_not_attempted_while_legs_are_still_open():
    calls: list[str] = []

    def handler(request):
        calls.append(f"{request.method} {request.url.path}")
        if request.method == "GET":
            return httpx.Response(200, json=[{"id": "stop-1", "symbol": "MU"}])
        return httpx.Response(204)          # cancel accepted, never completes

    with pytest.raises(BrokerError, match="NOT closed"):
        await _broker(handler).close_position_and_legs(
            "MU", wait_s=0.02, poll_s=0.01)
    assert "DELETE /v2/positions/MU" not in calls


async def test_a_failed_close_after_cancelling_says_the_stop_is_gone():
    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json=[])
        return httpx.Response(403, text="insufficient qty available")

    with pytest.raises(BrokerError, match="NO STOP"):
        await _broker(handler).close_position_and_legs("MU", poll_s=0)


def test_the_time_exit_is_scheduled_before_the_scan():
    """The backtest closed every trade at 60 bars. Unscheduled, that exit
    never happened and live positions rode on untested terms."""
    from pathlib import Path
    bat = Path("scripts/scheduled_scan.bat").read_text(encoding="utf-8")
    assert "manage_positions.py\" --execute" in bat
    assert bat.index('"scripts\\manage_positions.py"') < bat.index('"scripts\\trade.py"')
    script = Path("scripts/manage_positions.py").read_text(encoding="utf-8")
    assert "close_position_and_legs(" in script
