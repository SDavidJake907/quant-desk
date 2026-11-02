"""The command center page (scripts/command_center.py): read-only, current books and tasks."""

import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "command_center.py"
spec = importlib.util.spec_from_file_location("command_center", SCRIPT)
cc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cc)


def test_every_current_book_prefix_is_named():
    assert cc.book_of("cor-SPY-1") == "SPY core"
    assert cc.book_of("gem-VTRS-1") == "Rules book"
    assert cc.book_of("sml-INSW-1") == "Small caps"
    assert cc.book_of("orb-NUE-1") == "ORB"
    assert cc.book_of("3f2a-uuid") == "Not ours"


def test_slot_limits_come_from_the_policy():
    from scanner.execution.policy import TradingPolicy
    p = TradingPolicy()
    slots = cc.slot_limits(SCRIPT.parents[1])
    assert slots["Rules book"][1] == p.gemma_max_positions
    assert slots["Small caps"][1] == p.small_max_positions
    assert slots["ORB"][1] == p.orb_max_positions


def test_last_run_reads_only_the_latest_block():
    lines = ["===== SCAN Mon", "Traceback (most recent call last):", "===== SCAN Tue", "ok"]
    assert cc.last_run(lines) == ["===== SCAN Tue", "ok"]
    two_blocks = ["===== RULES PICK Tue", "queued VTRS", "===== SMALL PICK Tue", "gate open"]
    assert cc.last_run(two_blocks, "===== RULES PICK") == two_blocks


def test_errors_are_case_sensitive():
    assert cc.ERROR_RE.search("Traceback (most recent call last):")
    assert not cc.ERROR_RE.search("ghost scanner: 501 symbols scanned (0 failed)")


def test_session_day_maps_alpacas_stamp_to_the_new_york_date():
    # 2026-10-23 20:00 New York = 2026-10-24 00:00 UTC: Friday's session, not Saturday.
    assert cc.session_day(1792800000) == "2026-10-23"


def test_vs_index_rebases_the_account_and_each_index_to_the_first_day():
    hist = [("2026-10-01", 100.0), ("2026-10-02", 110.0)]
    closes = {"SPY": {"2026-10-01": 200.0, "2026-10-02": 202.0}, "IWM": {"2026-10-02": 50.0}}
    out = cc.vs_index(hist, closes)
    assert out["Account"] == [("2026-10-01", 0.0), ("2026-10-02", 10.000000000000009)]
    assert round(out["SPY"][-1][1], 6) == 1.0
    assert "IWM" not in out                     # no close on the first day: not drawn


def test_the_spy_core_is_flagged_when_naked(tmp_path):
    # 2026-10-30 (operator: "every position should have a sl"): the core is no longer stopless.
    fixture = {
        "/v2/account": {"equity": "100000", "last_equity": "100000", "cash": "0", "status": "ACTIVE"},
        "/v2/positions": [
            {"symbol": "SPY", "qty": "64", "avg_entry_price": "760", "current_price": "770", "market_value": "49280",
             "unrealized_pl": "640", "unrealized_plpc": "0.013", "unrealized_intraday_pl": "100"},
            {"symbol": "APA", "qty": "100", "avg_entry_price": "50", "current_price": "51", "market_value": "5100",
             "unrealized_pl": "100", "unrealized_plpc": "0.02", "unrealized_intraday_pl": "10"}],
        "/v2/orders": [
            {"symbol": "SPY", "side": "buy", "status": "filled", "client_order_id": "cor-SPY-1",
             "filled_at": "2026-10-27T14:00:00Z"},
            {"symbol": "APA", "side": "buy", "status": "filled", "client_order_id": "gem-APA-1",
             "filled_at": "2026-10-26T14:00:00Z"}],
    }
    path = tmp_path / "fixture.json"
    path.write_text(json.dumps(fixture))
    (tmp_path / "logs").mkdir()
    d = cc.gather(tmp_path, cc.Broker({}, fixture=path))
    assert sorted(d["naked"]) == ["APA", "SPY"]
    page = cc.render(d)
    assert "no stop by design" not in page and "No live stop: APA, SPY" in page


def _fill(sym, side, qty, px, at, coid="x", typ="market"):
    return {"symbol": sym, "side": side, "filled_qty": str(qty), "filled_avg_price": str(px),
            "filled_at": at, "client_order_id": coid, "type": typ, "status": "filled"}


def test_closed_trades_match_sells_to_buys_first_in_first_out():
    import cc_trades
    orders = [
        _fill("NUE", "buy", 10, 100, "2026-10-27T14:00:00Z", "gem-NUE-1"),
        _fill("NUE", "buy", 10, 110, "2026-10-28T14:00:00Z", "gem-NUE-2"),
        _fill("NUE", "sell", 15, 120, "2026-10-29T19:00:00Z", typ="stop"),
        _fill("ZBRA", "buy", 5, 50, "2026-10-20T14:00:00Z", "gem-ZBRA-1"),
        _fill("ZBRA", "sell", 5, 45, "2026-10-28T19:00:00Z"),
        _fill("XYZ", "sell", 5, 45, "2026-10-28T19:00:00Z"),  # buy older than the history read: left out
    ]
    trades = {(t["symbol"], t["opened"]): t for t in cc_trades.round_trips(orders)}
    assert set(trades) == {("NUE", "2026-10-27"), ("NUE", "2026-10-28"), ("ZBRA", "2026-10-20")}
    assert trades[("NUE", "2026-10-27")]["pnl"] == 200 and trades[("NUE", "2026-10-27")]["how"] == "stop"
    assert trades[("NUE", "2026-10-28")]["qty"] == 5 and trades[("NUE", "2026-10-28")]["pnl"] == 50
    assert trades[("NUE", "2026-10-27")]["book"] == "Rules book"
    assert trades[("ZBRA", "2026-10-20")]["book"] == "Gemma (retired)"  # gem- before 10-26


def test_book_summary_puts_current_books_first():
    import cc_trades
    trades = [
        {"book": "Picks", "pnl": 10, "pct": 1.0},
        {"book": "ORB", "pnl": -5, "pct": -0.5},
        {"book": "ORB", "pnl": 15, "pct": 1.5},
    ]
    rows = cc_trades.book_summary(trades)
    assert [r["book"] for r in rows] == ["ORB", "Picks"]
    assert rows[0]["won"] == 1 and rows[0]["pnl"] == 10 and not rows[1]["current"]


def test_position_chart_flags_a_missing_stop():
    import cc_trades
    p = {"symbol": "NUE", "book": "Rules book", "upl": -1, "uplpc": -1.0, "entry": 100, "stop": None,
         "stop_live": False}
    closes = [(f"2026-10-{d:02d}", 100 + d) for d in range(1, 11)]
    assert "no live stop" in cc_trades.position_chart(p, closes, None, "2026-10-05")
    assert "no live stop" not in cc_trades.position_chart({**p, "stop": 95, "stop_live": True}, closes, None, None)
