"""Portfolio review tests — read-only, advisory, never enforced.

These verify that Gemma's portfolio review is built correctly from audit
rows and broker records, and that it never raises on missing data.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from scanner.execution.portfolio_review import (
    BookStats,
    Position,
    PortfolioReview,
    Refusal,
    SectorExposure,
    TAviso,
    WorkingOrder,
    build_review,
    review_text,
)


def _stamp(days_ago: int = 0) -> str:
    """A real timestamp relative to now. Fixed dates age out of the 9-day window."""
    return (datetime.now().astimezone() - timedelta(days=days_ago)).isoformat(timespec="seconds")


def _row(event: str, symbol: str = "", reason: str = "", at: str | None = None,
         intent: dict | None = None, outcome: dict | None = None) -> dict:
    return {
        "event": event,
        "at": at or _stamp(),
        "symbol": symbol,
        "reason": reason,
        "intent": intent or {},
        "outcome": outcome or {},
    }


def _intent(symbol: str, entry: float, stop: float, target: float,
            strategy: str = "smc", coid: str = "smc-TEST-123") -> dict:
    return {
        "client_order_id": coid,
        "symbol": symbol,
        "strategy": strategy,
        "entry_price": entry,
        "stop_price": stop,
        "target_price": target,
        "trigger_at": "2026-10-15T05:33:00-08:00",
    }


def _position(symbol: str, mv: float = 10000.0, upl: float = 100.0,
              uplpc: float = 1.0, qty: int = 100, owner: str = "smc") -> dict:
    return {
        "symbol": symbol,
        "qty": qty,
        "market_value": mv,
        "unrealized_pl": upl,
        "unrealized_plpc": uplpc,
        "cost_basis": mv - upl,
    }


def _order(symbol: str, side: str = "buy", type_: str = "limit",
           limit_price: float = 100.0, stop_price: float = 95.0,
           coid: str = "smc-TEST-123") -> dict:
    return {
        "symbol": symbol,
        "side": side,
        "type": type_,
        "limit_price": limit_price,
        "stop_price": stop_price,
        "client_order_id": coid,
    }


def _account(equity: float = 100000.0, bp: float = 50000.0,
             status: str = "ACTIVE") -> dict:
    return {"equity": equity, "buying_power": bp, "status": status}


# --- build_review ----------------------------------------------------------

def test_build_review_empty_inputs():
    review = build_review(
        rows=[], positions=[], orders=[], account=_account(),
        owner_map={}, sector_of={},
    )
    assert isinstance(review, PortfolioReview)
    from datetime import date as _date
    assert review.date == _date.today().isoformat()
    assert review.account_equity == 100000.0
    assert review.account_buying_power == 50000.0
    assert review.account_status == "ACTIVE"
    assert review.positions == ()
    assert review.by_book == ()


def test_build_review_with_position():
    rows = [_row("trade_intent", "NVDA", intent=_intent("NVDA", 100.0, 95.0, 110.0))]
    positions = [_position("NVDA", mv=10000.0, upl=500.0, uplpc=5.0, qty=100)]
    account = _account()
    owner_map = {"NVDA": "smc"}
    sector_of = {"NVDA": "Technology"}

    review = build_review(
        rows=rows, positions=positions, orders=[], account=account,
        owner_map=owner_map, sector_of=sector_of,
    )
    assert len(review.positions) == 1
    p = review.positions[0]
    assert p.symbol == "NVDA"
    assert p.qty == 100
    assert p.owner == "smc"
    assert p.entry_price == 100.0
    assert p.stop_price == 95.0
    assert p.target_price == 110.0


def test_build_review_with_closed_trade():
    rows = [
        _row("trade_outcome", "NVDA",
             outcome={"client_order_id": "smc-NVDA-123",
                      "realized_r": 2.5, "realized_dollars": 250.0,
                      "exit_reason": "target"}),
    ]
    intents = {"smc-NVDA-123": _intent("NVDA", 100.0, 95.0, 110.0)}
    # Inject intent into the row so build_review sees it
    rows[0]["intent"] = intents["smc-NVDA-123"]

    review = build_review(
        rows=rows, positions=[], orders=[], account=_account(),
        owner_map={"NVDA": "smc"}, sector_of={"NVDA": "Technology"},
    )
    assert len(review.by_book) == 1
    b = review.by_book[0]
    assert b.strategy == "smc"
    assert b.trades == 1
    assert b.wins == 1
    assert b.total_r == 2.5
    assert b.mean_r == 2.5
    assert b.win_rate == 1.0


def test_build_review_with_working_order():
    orders = [_order("NVDA", coid="smc-NVDA-456")]
    owner_map = {}
    review = build_review(
        rows=[], positions=[], orders=orders, account=_account(),
        owner_map=owner_map, sector_of={},
    )
    assert len(review.working_orders) == 1
    wo = review.working_orders[0]
    assert wo.symbol == "NVDA"
    assert wo.owner == "smc"


def test_build_review_sector_exposure():
    positions = [
        _position("NVDA", mv=10000.0, owner="smc"),
        _position("MSFT", mv=5000.0, owner="smc"),
        _position("KO", mv=3000.0, owner="pck"),
    ]
    account = _account(equity=100000.0)
    sector_of = {"NVDA": "Technology", "MSFT": "Technology", "KO": "Consumer Staples"}
    review = build_review(
        rows=[], positions=positions, orders=[], account=account,
        owner_map={"NVDA": "smc", "MSFT": "smc", "KO": "pck"},
        sector_of=sector_of,
    )
    assert len(review.sector_exposure) == 2
    tech = [s for s in review.sector_exposure if s.sector == "Technology"][0]
    assert tech.positions == 2
    assert tech.market_value == 15000.0
    assert tech.pct_of_account == 15.0


def test_build_review_recent_activity_window():
    # An entry from today should appear; one from 10 days ago should not.
    # The window is 3 sessions x 3 days = 9 days (portfolio_review.LOOKBACK_SESSIONS).
    rows = [
        _row("order_submitted", "NVDA",
             intent=_intent("NVDA", 100.0, 95.0, 110.0),
             at=_stamp(0)),
        _row("order_submitted", "AAPL",
             intent=_intent("AAPL", 100.0, 95.0, 110.0),
             at=_stamp(10)),
    ]
    review = build_review(
        rows=rows, positions=[], orders=[], account=_account(),
        owner_map={}, sector_of={},
    )
    # Today's entry should be in recent_entries
    assert any("NVDA" in e for e in review.recent_entries)
    # 10-day-old entry should be outside the lookback window
    assert not any("AAPL" in e for e in review.recent_entries)


def test_build_review_refusals():
    rows = [
        _row("candidate_refused", "NVDA", "sector cap: 3 open in Technology"),
        _row("gemma_refused", "AAPL", "Gemma dropped it"),
    ]
    review = build_review(
        rows=rows, positions=[], orders=[], account=_account(),
        owner_map={}, sector_of={},
    )
    assert len(review.recent_refusals) == 2
    assert review.recent_refusals[0].symbol == "NVDA"
    assert "sector cap" in review.recent_refusals[0].reason


def test_review_text_includes_advisory_disclaimer():
    review = build_review(
        rows=[], positions=[], orders=[], account=_account(),
        owner_map={}, sector_of={},
    )
    text = review_text(review)
    assert "Advisory only" in text
    assert "Portfolio as of" in text


# --- frozen models ---------------------------------------------------------

def test_position_is_frozen():
    p = Position(symbol="NVDA", qty=100, market_value=10000.0,
                 unrealized_pl=100.0, unrealized_plpc=1.0, owner="smc")
    try:
        p.qty = 200  # type: ignore[misc]
        assert False, "should be frozen"
    except Exception:
        pass


def test_portfolio_review_is_frozen():
    r = PortfolioReview(
        date="2026-10-15", account_equity=100000.0,
        account_buying_power=50000.0, account_status="ACTIVE",
    )
    try:
        r.account_equity = 99999.0  # type: ignore[misc]
        assert False, "should be frozen"
    except Exception:
        pass


def test_book_stats_is_frozen():
    b = BookStats(strategy="smc", trades=5, wins=3, total_r=2.5,
                  mean_r=0.5, win_rate=0.6, open_positions=1)
    try:
        b.trades = 6  # type: ignore[misc]
        assert False, "should be frozen"
    except Exception:
        pass


def test_sector_exposure_is_frozen():
    s = SectorExposure(sector="Technology", positions=2,
                       market_value=15000.0, pct_of_account=15.0)
    try:
        s.positions = 3  # type: ignore[misc]
        assert False, "should be frozen"
    except Exception:
        pass


def test_refusal_is_frozen():
    r = Refusal(symbol="NVDA", reason="sector cap", day="2026-10-15")
    try:
        r.reason = "changed"  # type: ignore[misc]
        assert False, "should be frozen"
    except Exception:
        pass


def test_taviso_is_frozen():
    t = TAviso(symbol="NVDA", rating="Overweight", trader="Buy", day="2026-10-15")
    try:
        t.rating = "Hold"  # type: ignore[misc]
        assert False, "should be frozen"
    except Exception:
        pass


def test_working_order_is_frozen():
    o = WorkingOrder(symbol="NVDA", side="buy", type="limit",
                     limit_price=100.0, stop_price=95.0, owner="smc")
    try:
        o.limit_price = 99.0  # type: ignore[misc]
        assert False, "should be frozen"
    except Exception:
        pass
