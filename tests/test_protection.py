"""What protect.py does with a position that has no live stop.

Before 2026-10-06 a breached stop was reported "for a human" and left naked.
In an unattended system no human comes: INTC sat unprotected through the
2026-10-05 open until a lucky re-arm 30 minutes later. Now a breached stop
(or a reached target) is closed at market -- the exit the bracket would have
made -- and an alert is written either way.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from scanner.execution import protection as pr


def test_inside_the_bracket_is_rearmed():
    assert pr.decide(last=100.0, stop=95.0, target=110.0) == "arm"


def test_at_or_below_the_stop_is_closed():
    assert pr.decide(last=95.0, stop=95.0, target=110.0) == "close_stop"
    assert pr.decide(last=90.0, stop=95.0, target=110.0) == "close_stop"


def test_at_or_above_the_target_is_closed():
    assert pr.decide(last=110.0, stop=95.0, target=110.0) == "close_target"


def test_no_quote_still_rearms_at_the_recorded_levels():
    # Better a stop at the original level than no stop at all.
    assert pr.decide(last=0.0, stop=95.0, target=110.0) == "arm"


def test_no_recorded_levels_needs_a_human():
    assert pr.decide(last=100.0, stop=None, target=None) == "unknown"


def test_exit_is_a_plain_market_sell_for_the_naked_shares_only():
    payload = pr.exit_payload("INTC", 169, datetime(2026, 10, 5, 5, 33))
    assert payload["symbol"] == "INTC" and payload["qty"] == "169"
    assert payload["side"] == "sell" and payload["type"] == "market"
    assert payload["time_in_force"] == "day"
    assert payload["client_order_id"].startswith("protect-close-INTC-")


def test_a_working_close_covers_those_shares():
    # 2026-11-02: the open sale was working and protect re-armed the same
    # shares. The broker answered 403, and the log called them still naked.
    assert pr.uncovered(5, 5) == 0
    assert pr.uncovered(6, 0) == 6
    assert pr.uncovered(5, 2) == 3
    assert pr.uncovered(4, 9) == 0


def test_a_covered_book_removes_the_old_alert(tmp_path):
    stale = tmp_path / "PROTECTION_ALERT.txt"
    stale.write_text("SPY and QQQ, from before their stops were armed", encoding="utf-8")
    assert pr.clear_alert([tmp_path]) == []
    assert not stale.exists()
    assert pr.clear_alert([tmp_path]) == []


def test_a_locked_note_is_reported(tmp_path, monkeypatch):
    note = tmp_path / "PROTECTION_ALERT.txt"
    note.write_text("open in an editor", encoding="utf-8")

    def refuse(self, missing_ok=False):
        raise PermissionError(13, "in use", str(self))

    monkeypatch.setattr(Path, "unlink", refuse)
    stuck = pr.clear_alert([tmp_path])
    assert len(stuck) == 1
    assert str(note) in stuck[0] and "in use" in stuck[0]
    assert note.read_text(encoding="utf-8") == "open in an editor"


def test_a_partial_fill_counts_only_the_shares_still_working():
    # qty stays at the original size while the position shrinks. Counting qty
    # treats the filled shares as still covered, and the rest get no stop.
    orders = [{
        "symbol": "SPY", "side": "sell", "type": "market",
        "status": "partially_filled", "qty": "60", "filled_qty": "40",
    }]
    assert pr.closing_shares(orders) == {"SPY": 20}
    assert pr.uncovered(60, 20) == 40
    done = [{
        "symbol": "SPY", "side": "sell", "type": "market",
        "status": "partially_filled", "qty": "20", "filled_qty": "20",
    }]
    assert pr.closing_shares(done) == {}


def test_working_market_sell_counts_as_already_closing():
    orders = [
        {"symbol": "INTC", "side": "sell", "type": "market", "status": "accepted", "qty": "169"},
        {"symbol": "SLB", "side": "sell", "type": "market", "status": "filled", "qty": "10"},
        {"symbol": "AAPL", "side": "sell", "type": "stop", "status": "new", "qty": "5",
         "stop_price": "180"},
        {"symbol": "MSFT", "side": "buy", "type": "market", "status": "new", "qty": "3"},
    ]
    assert pr.closing_shares(orders) == {"INTC": 169}


def test_alert_names_every_position_and_what_was_done():
    text = pr.alert_text([("INTC", 169, "close_stop", "sell sent"),
                          ("XYZ", 10, "unknown", "no stop on record")],
                         when="2026-10-05 05:33 AKDT")
    assert "INTC" in text and "169" in text and "sell sent" in text
    assert "XYZ" in text and "no stop on record" in text
    assert "until a later pass" in text
    assert "2026-10-05 05:33" in text
