"""The desk: what it shows must match what happened."""

from __future__ import annotations

from pathlib import Path

import pytest

from scanner.execution.reconcile import CLOSED_OTHER, FILLED_OPEN, LiveTrade
from scanner.llm.hindsight import build_prompt, call_verdict, ground
from scanner.web import demo
from scanner.web.snapshot import (
    entry_context, funnel, hindsight_by_order, performance, protection,
    resolve_exits, runs, scoreboard, trade_rows,
)


def _row(r, conviction="strong", state="target", tracked=True, pnl=None, verdict=""):
    return {"r": r, "conviction": conviction, "state": state, "tracked": tracked,
            "pnl": pnl if pnl is not None else r * 100, "verdict": verdict}


# ---- runs and the funnel ---------------------------------------------------

def test_events_group_into_runs_newest_first():
    events = [
        {"event": "scan_summary", "run_id": "20261102T090300", "at": "a",
         "detail": {"symbols": 502, "loaded": 499, "liquid": 470, "setups": 40,
                    "fresh": 5, "sent": 1}},
        {"event": "candidate_refused", "run_id": "20261102T090300",
         "reason": "already holding", "stage": "watch"},
        {"event": "candidate_refused", "run_id": "20261102T090300",
         "reason": "already holding", "stage": "watch"},
        {"event": "trade_intent", "run_id": "20261102T090300", "symbol": "MU"},
        {"event": "scan_summary", "run_id": "20261102T083300", "detail": {}},
        {"event": "trade_intent", "symbol": "OLD"},          # pre-run-id record
    ]
    out = runs(events)
    assert [r["run_id"] for r in out] == ["20261102T090300", "20261102T083300"]
    assert out[0]["refused"] == [{"reason": "already holding", "count": 2}]
    steps = {s["label"]: s["count"] for s in funnel(out[0])}
    assert steps["Universe"] == 502 and steps["Orders sent"] == 1
    assert steps["Passed every gate"] == 1


# ---- trades ----------------------------------------------------------------

def test_a_position_closed_outside_its_bracket_is_closed():
    """A time exit closes with a separate order, so the bracket alone would
    read as open forever and the trade would never reach the statistics."""
    trade = LiveTrade(symbol="MU", client_order_id="smc-MU-1", shares=10,
                      intended_entry=100, intended_stop=98, intended_target=104,
                      state=FILLED_OPEN, entry_fill=100.0,
                      submitted_at="2026-10-30T14:00:00Z")
    orders = [{"symbol": "MU", "side": "sell", "filled_avg_price": "101.5",
               "filled_at": "2026-11-02T15:00:00Z"}]
    closed, = resolve_exits((trade,), orders, held=set())
    assert closed.state == CLOSED_OTHER and closed.exit_fill == 101.5
    still, = resolve_exits((trade,), orders, held={"MU"})
    assert still.state == FILLED_OPEN


def test_an_older_sell_does_not_close_a_newer_trade():
    trade = LiveTrade(symbol="MU", client_order_id="c", state=FILLED_OPEN,
                      entry_fill=100.0, intended_entry=100, intended_stop=98,
                      submitted_at="2026-11-01T00:00:00Z")
    orders = [{"symbol": "MU", "side": "sell", "filled_avg_price": "90",
               "filled_at": "2026-10-01T00:00:00Z"}]
    assert resolve_exits((trade,), orders, held=set())[0].state == FILLED_OPEN


def test_rows_carry_r_pnl_and_the_verdict():
    trade = LiveTrade(symbol="MU", client_order_id="c1", shares=10,
                      intended_entry=100, intended_stop=98, intended_target=104,
                      state="target", entry_fill=100.0, exit_fill=104.0)
    intents = {"c1": {"llm_conviction": "weak", "trigger_slot": "open"}}
    row, = trade_rows([trade], intents, [], {}, [])
    assert row["r"] == pytest.approx(2.0) and row["pnl"] == pytest.approx(40.0)
    assert row["verdict"] == "wrong"           # weak, and it won
    assert row["slot"] == "open"


def test_performance_counts_only_this_systems_closed_trades():
    rows = [_row(2.0), _row(-1.0, state="stop"), _row(-1.0, state="stop"),
            _row(5.0, tracked=False), _row(1.0, state=FILLED_OPEN)]
    p = performance(rows)
    assert p["closed"] == 3 and p["wins"] == 1
    assert p["win_rate"] == pytest.approx(100 / 3)
    assert p["expectancy_r"] == pytest.approx(0.0)
    assert p["profit_factor"] == pytest.approx(1.0)
    assert p["unrealized_pnl"] == pytest.approx(100.0)


def test_no_closed_trades_means_no_statistics_rather_than_zeros():
    p = performance([])
    assert p["win_rate"] is None and p["expectancy_r"] is None


def test_scoreboard_groups_by_conviction_and_counts_calls():
    rows = [_row(2.0, "strong", verdict="right"), _row(-1.0, "strong", "stop", verdict="wrong"),
            _row(-1.0, "weak", "stop", verdict="right"), _row(1.0, "neutral", verdict="no call")]
    board = {b["conviction"]: b for b in scoreboard(rows)}
    assert board["strong"]["n"] == 2 and board["strong"]["mean_r"] == pytest.approx(0.5)
    assert board["strong"]["right"] == 1 and board["strong"]["wrong"] == 1
    assert board["weak"]["right"] == 1 and board["neutral"]["right"] == 0


def test_protection_reads_only_live_sell_legs():
    orders = [{"symbol": "MU", "side": "buy", "status": "filled", "legs": [
        {"symbol": "MU", "side": "sell", "status": "held", "stop_price": "98"},
        {"symbol": "MU", "side": "sell", "status": "new", "limit_price": "104"}]},
        {"symbol": "Q", "side": "buy", "status": "filled", "legs": [
            {"symbol": "Q", "side": "sell", "status": "canceled", "stop_price": "9"}]}]
    guards = protection(orders)
    assert guards["MU"] == {"stop": 98.0, "target": 104.0}
    assert "Q" not in guards


# ---- Gemma, at entry and in hindsight -----------------------------------------

def test_entry_context_is_what_gemma_said_before_the_order():
    events = [
        {"event": "llm_ranking", "at": "2026-11-02T09:00:00",
         "detail": {"opinions": [{"symbol": "MU", "conviction": "strong"}]}},
        {"event": "llm_ranking", "at": "2026-11-02T12:00:00",
         "detail": {"opinions": [{"symbol": "MU", "conviction": "weak"}]}},
    ]
    ctx = entry_context(events, "MU", "2026-11-02T10:00:00")
    assert ctx["opinion"]["conviction"] == "strong"


def test_latest_hindsight_wins():
    events = [{"event": "llm_hindsight", "at": "1", "detail": {"client_order_id": "c", "lesson": "a"}},
              {"event": "llm_hindsight", "at": "2", "detail": {"client_order_id": "c", "lesson": "b"}}]
    assert hindsight_by_order(events)["c"]["lesson"] == "b"


@pytest.mark.parametrize("conviction,r,expected", [
    ("strong", 1.5, "right"), ("strong", -1.0, "wrong"),
    ("weak", -1.0, "right"), ("weak", 2.0, "wrong"),
    ("neutral", 2.0, "no call"), ("", 2.0, "no call"), ("strong", None, "no call"),
])
def test_the_verdict_is_arithmetic_not_the_models_opinion(conviction, r, expected):
    assert call_verdict(conviction, r) == expected


def test_a_hindsight_quote_not_in_the_headlines_is_dropped():
    out = ground({"expected": "x", "what_happened": "y", "missed": "z",
                  "driver_in_hindsight": "sector", "evidence_quote": "invented words",
                  "lesson": "l"}, ["Peers fell on the same day"])
    assert out["evidence_quote"] == "" and out["grounded"] is False
    kept = ground({"evidence_quote": "peers fell", "driver_in_hindsight": "bogus"},
                  ["Peers fell on the same day"])
    assert kept["evidence_quote"] == "peers fell" and kept["driver_in_hindsight"] == "none"


def test_the_hindsight_prompt_carries_the_entry_call_and_the_outcome():
    prompt = build_prompt({"symbol": "MU", "state": "stop", "r": -1.0,
                           "conviction": "strong", "driver": "company", "concern": ""},
                          {"evidence": "raises outlook", "headlines": ["MU raises outlook"]},
                          ["Chip stocks slide"])
    for text in ("MU", "stopped out", "strong", "raises outlook", "Chip stocks slide"):
        assert text in prompt


# ---- the page --------------------------------------------------------------------

def test_the_demo_snapshot_has_every_section_the_page_reads():
    snap = demo.build()
    for key in ("account", "equity_curve", "positions", "working", "watchlist",
                "trades", "performance", "scoreboard", "runs", "gemma", "policy"):
        assert key in snap, key
    assert snap["demo"] is True


def test_the_desk_server_has_no_way_to_place_an_order():
    """Read-only is a promise the page makes in writing; pin it."""
    for name in ("scripts/desk.py", "src/scanner/web/snapshot.py"):
        source = Path(name).read_text(encoding="utf-8")
        for forbidden in ("submit(", "cancel_order(", "close_position", "_delete(",
                          "do_POST", "do_DELETE"):
            assert forbidden not in source, f"{name} contains {forbidden}"


async def test_the_live_snapshot_builds_from_alpaca_shaped_responses(tmp_path, monkeypatch):
    """End to end with a fake broker: the path that runs on the real machine."""
    import json as _json

    import httpx

    from scanner.web import snapshot

    log = tmp_path / "audit.jsonl"
    intent = {"symbol": "MU", "client_order_id": "smc-MU-1", "entry_price": 100.0,
              "stop_price": 98.0, "target_price": 104.0, "shares": 10,
              "llm_conviction": "strong", "llm_driver": "company"}
    log.write_text("\n".join(_json.dumps(e) for e in [
        {"event": "llm_ranking", "at": "2026-11-02T09:00:00", "run_id": "r1",
         "detail": {"opinions": [{"symbol": "MU", "conviction": "strong"}]}},
        {"event": "trade_intent", "at": "2026-11-02T09:01:00", "run_id": "r1",
         "symbol": "MU", "intent": intent},
        {"event": "scan_summary", "at": "2026-11-02T09:02:00", "run_id": "r1",
         "detail": {"symbols": 502, "loaded": 500, "liquid": 470, "setups": 30,
                    "fresh": 3, "sent": 1}},
    ]) + "\n")
    bracket = {"id": "o1", "client_order_id": "smc-MU-1", "symbol": "MU", "qty": "10",
               "side": "buy", "status": "filled", "filled_avg_price": "100.0",
               "filled_at": "2026-11-02T14:31:00Z", "submitted_at": "2026-11-02T14:30:00Z",
               "legs": [{"symbol": "MU", "side": "sell", "status": "held", "type": "stop",
                         "stop_price": "98"},
                        {"symbol": "MU", "side": "sell", "status": "new", "type": "limit",
                         "limit_price": "104"}]}
    routes = {
        "/v2/account": {"equity": "101000", "last_equity": "100500", "cash": "50000",
                        "buying_power": "100000", "status": "ACTIVE"},
        "/v2/positions": [{"symbol": "MU", "qty": "10", "avg_entry_price": "100",
                           "current_price": "101", "market_value": "1010",
                           "unrealized_pl": "10", "unrealized_plpc": "0.01",
                           "change_today": "0.004"}],
        "/v2/orders": [bracket],
        "/v2/clock": {"is_open": True},
        "/v2/account/portfolio/history": {"timestamp": [1, 2], "equity": [100000, 101000]},
    }

    def handler(request):
        if request.method != "GET":
            raise AssertionError(f"desk sent a {request.method}")
        return httpx.Response(200, json=routes[request.url.path])

    async def no_bars(*a, **k):
        raise RuntimeError("offline")
    monkeypatch.setattr("scanner.features.intraday.fetch_intraday", no_bars)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        snap = await snapshot.build(client, audit_path=str(log), key_id="k",
                                    secret="s", news=False)

    assert snap["account"]["day_pl"] == pytest.approx(500.0)
    pos, = snap["positions"]
    assert pos["live_stop"] == 98.0 and pos["live_target"] == 104.0
    assert pos["trade"]["r"] == pytest.approx(0.5)
    assert pos["trade"]["entry_context"]["opinion"]["conviction"] == "strong"
    assert snap["runs"][0]["funnel"][0] == {"label": "Universe", "count": 502}
    assert _json.dumps(snap, default=str)            # the server can serialise it
