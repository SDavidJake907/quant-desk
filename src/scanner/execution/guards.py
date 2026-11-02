"""The checks that run before any order is sent.

An automated system trades whatever edge it has, repeatedly and without
hesitation — including a negative one. These guards do not make a strategy
profitable; they bound how wrong it can go before a human looks at it.

Every guard here is a refusal, never an adjustment. A blocked order is
reported and skipped, not quietly resized into something the caller did not
ask for.
"""

from __future__ import annotations

from scanner.models import Frozen


class RiskLimits(Frozen):
    """Account-level bounds. Breach any and trading stops for the session."""

    #: Concurrent open positions. Correlation is not modelled, so five open
    #: longs in a falling market is one bet, not five.
    max_positions: int = 5

    #: Session loss, as a percentage of starting equity, after which the
    #: system stops opening trades. Existing brackets still run.
    max_daily_loss_pct: float = 3.0

    #: Below this equity the system refuses to trade at all.
    min_equity: float = 1_000.0

    #: Share of equity in any one name.
    max_position_pct: float = 20.0

    #: Share of equity a single losing trade may cost.
    risk_pct: float = 1.0


class Verdict(Frozen):
    """Whether an order may be sent, and why not."""

    allowed: bool
    reason: str = ""

    def __bool__(self) -> bool:
        return self.allowed


ALLOWED = Verdict(allowed=True)


def check_account(
    equity: float,
    starting_equity: float,
    open_positions: int,
    limits: RiskLimits,
    *,
    market_open: bool = True,
    account_status: str = "ACTIVE",
    account_blocked: bool = False,
) -> Verdict:
    """Account-level gate, evaluated once before a scan is acted on."""
    if not market_open:
        return Verdict(allowed=False, reason="market is closed")
    if account_status != "ACTIVE":
        return Verdict(allowed=False, reason=f"account status is {account_status}")
    if account_blocked:
        return Verdict(allowed=False, reason="account trading is blocked or suspended")
    if equity < limits.min_equity:
        return Verdict(
            allowed=False,
            reason=f"equity {equity:,.0f} below floor {limits.min_equity:,.0f}",
        )
    if open_positions >= limits.max_positions:
        return Verdict(
            allowed=False,
            reason=f"already holding {open_positions} of {limits.max_positions}",
        )
    if starting_equity > 0:
        drawdown = (starting_equity - equity) / starting_equity * 100.0
        if drawdown >= limits.max_daily_loss_pct:
            return Verdict(
                allowed=False,
                reason=f"down {drawdown:.2f}% today, limit {limits.max_daily_loss_pct}%",
            )
    return ALLOWED


def session_baseline(configured: float, last_equity: float, equity: float) -> float:
    """The equity the daily-loss guard measures today's drawdown from.

    An explicit setting wins. Otherwise the broker's previous-close equity,
    which is what "today" means for a daily limit. Only if neither exists does
    it fall back to current equity — and then the guard cannot fire, because
    a drawdown measured from where you are now is always zero.
    """
    for candidate in (configured, last_equity):
        if candidate and candidate > 0:
            return candidate
    return equity


def check_symbol(
    symbol: str,
    held: frozenset[str],
    pending: frozenset[str],
) -> Verdict:
    """Per-symbol gate.

    Refusing a symbol already held or already ordered is what stops a restart,
    a duplicate scan or a slow fill from opening the same trade twice — the
    failure that turns one position into three at the worst moment.
    """
    if symbol in held:
        return Verdict(allowed=False, reason="already holding")
    if symbol in pending:
        return Verdict(allowed=False, reason="order already working")
    return ALLOWED
