"""Risk-guard tests.

These are the only thing standing between an unproven strategy and an
unattended account, so they are tested as refusals: the question is never
"does it trade" but "does it stop".
"""

from __future__ import annotations

from scanner.execution.guards import RiskLimits, check_account, check_symbol

LIMITS = RiskLimits(max_positions=3, max_daily_loss_pct=3.0, min_equity=1_000.0)


def _account(equity=100_000.0, start=100_000.0, positions=0, open_market=True):
    return check_account(equity, start, positions, LIMITS, market_open=open_market)


# ---- account gate ------------------------------------------------------

def test_a_healthy_account_may_trade():
    assert _account()


def test_a_closed_market_blocks_everything():
    assert not _account(open_market=False)
    assert "closed" in _account(open_market=False).reason


def test_the_position_limit_is_a_ceiling_not_a_target():
    assert _account(positions=2)
    assert not _account(positions=3)
    assert not _account(positions=9)


def test_the_daily_loss_limit_stops_new_trades():
    """Without this, a bad day compounds unattended."""
    assert _account(equity=98_000)              # -2%, still allowed
    assert not _account(equity=97_000)          # -3%, stop
    assert not _account(equity=80_000)


def test_a_gain_never_blocks_trading():
    assert _account(equity=140_000)


def test_an_account_below_the_floor_cannot_trade():
    assert not _account(equity=500.0, start=500.0)


def test_reasons_are_specific_enough_to_act_on():
    verdict = _account(equity=90_000)
    assert "%" in verdict.reason and "limit" in verdict.reason


def test_zero_starting_equity_does_not_divide_by_zero():
    assert check_account(5_000, 0.0, 0, LIMITS)


def test_non_active_account_is_refused():
    verdict = check_account(100_000, 100_000, 0, LIMITS, account_status="APPROVAL_PENDING")
    assert not verdict and "status" in verdict.reason


def test_blocked_or_suspended_account_is_refused():
    verdict = check_account(100_000, 100_000, 0, LIMITS, account_blocked=True)
    assert not verdict and "blocked" in verdict.reason


# ---- symbol gate -------------------------------------------------------

def test_a_symbol_already_held_is_refused():
    """A restart or a duplicate scan must not double the position."""
    assert not check_symbol("MU", frozenset({"MU"}), frozenset())


def test_a_symbol_with_a_working_order_is_refused():
    """The window between sending an order and seeing the fill is exactly
    when a second scan would fire."""
    assert not check_symbol("MU", frozenset(), frozenset({"MU"}))


def test_an_untouched_symbol_is_allowed():
    assert check_symbol("MU", frozenset({"AAPL"}), frozenset({"NVDA"}))


def test_verdicts_are_usable_as_booleans():
    assert bool(check_symbol("MU", frozenset(), frozenset())) is True
    assert bool(check_symbol("MU", frozenset({"MU"}), frozenset())) is False


# ---- daily-loss baseline -------------------------------------------------

def test_baseline_prefers_the_explicit_setting():
    from scanner.execution.guards import session_baseline
    assert session_baseline(90_000.0, 95_000.0, 88_000.0) == 90_000.0


def test_baseline_falls_back_to_the_previous_close():
    """Unset, the guard used to compare equity to itself and never fire."""
    from scanner.execution.guards import session_baseline
    assert session_baseline(0.0, 100_000.0, 96_000.0) == 100_000.0


def test_a_four_percent_drop_from_the_close_now_halts_trading():
    from scanner.execution.guards import session_baseline
    start = session_baseline(0.0, 100_000.0, 96_000.0)
    verdict = check_account(96_000.0, start, 0, LIMITS)
    assert not verdict and "down 4.00%" in verdict.reason


def test_baseline_with_nothing_known_is_current_equity():
    from scanner.execution.guards import session_baseline
    assert session_baseline(0.0, 0.0, 50_000.0) == 50_000.0


def test_trade_py_uses_the_baseline():
    """Pins the call site: reverting to `session_start_equity or equity`
    silently disables the guard again."""
    from pathlib import Path
    source = Path("scripts/trade.py").read_text(encoding="utf-8")
    assert "session_baseline(" in source
    assert "settings.session_start_equity or account.equity" not in source
