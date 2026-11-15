from datetime import date

from scanner.advisor import claude_desk as cd
from scanner.advisor.claude_desk import Bar, Book, Call, Plan, Position

D1, D2 = date(2026, 11, 2), date(2026, 11, 3)


def bar(o, h, l, c, day=D1):  # noqa: E741
    return Bar(day=day, o=o, h=h, l=l, c=c)


def buy(sym="ABC", kind="limit", entry=100.0, stop=95.0, target=None, max_days=None):
    return Call(symbol=sym, action="buy", entry_type=kind, entry=entry, stop=stop, target=target,
                max_days=max_days, why="test")


def held(sym="ABC", stop=95.0, target=None, max_days=30, held_days=0):
    return Position(symbol=sym, opened=D1, entry=100.0, shares=60.0, stop=stop, target=target,
                    max_days=max_days, held_days=held_days, last=100.0)


# --- check ---------------------------------------------------------------------------------

def test_grok_stdout_is_one_object_even_with_a_preamble():
    out = cd.grok_object('updater: ready\n{"structuredOutput": {"ok": true}, "text": "x"}\n')
    assert out["structuredOutput"] == {"ok": True}
    try:
        cd.grok_object("not json")
    except ValueError as exc:
        assert "no answer" in str(exc)
    else:
        raise AssertionError("non-json stdout must be refused")


def test_grok_json_is_the_structured_plan_and_its_cost():
    plan, cost = cd.grok_answer(
        {"structuredOutput": {"market": "flat", "calls": []}, "text": "{}", "total_cost_usd": 0.04},
        structured=True)
    assert plan == {"market": "flat", "calls": []}
    assert cost == 0.04
    text, free = cd.grok_answer({"text": " Sunday read. "}, structured=False)
    assert text == "Sunday read." and free == 0.0


def test_grok_json_without_a_plan_is_refused():
    try:
        cd.grok_answer({"type": "error", "message": "Couldn't start session"}, structured=True)
    except ValueError as exc:
        assert "Couldn't start" in str(exc)
    else:
        raise AssertionError("an error object must be refused")
    try:
        cd.grok_answer({"text": "not json"}, structured=True)
    except ValueError as exc:
        assert "no structured plan" in str(exc)
    else:
        raise AssertionError("text without structuredOutput must be refused")


def test_a_buy_without_an_entry_type_is_not_saved_as_a_call():
    plan = Plan(day=D1, calls=(buy(kind=None, stop=None),))
    out = cd.accepted(plan, closes={"ABC": 100.0}, book=Book())
    assert out.calls == ()
    assert any("entry type and a stop" in line for line in out.skipped)


def test_a_buy_needs_a_stop_under_the_entry_and_within_the_cap():
    assert cd.check(buy(stop=None), last_close=100.0, held=None)
    assert "not under" in cd.check(buy(stop=101.0), last_close=100.0, held=None)
    assert "8%" in cd.check(buy(stop=91.0), last_close=100.0, held=None)
    assert cd.check(buy(stop=93.0), last_close=100.0, held=None) is None


def test_an_entry_far_from_the_last_close_is_refused():
    assert "5%" in cd.check(buy(entry=106.0, stop=100.0), last_close=100.0, held=None)


def test_sell_and_hold_need_a_shadow_position_and_a_stop_never_drops():
    assert "not held" in cd.check(Call(symbol="ABC", action="sell"), last_close=100.0, held=None)
    lower = Call(symbol="ABC", action="hold", stop=90.0)
    assert "never lowered" in cd.check(lower, last_close=100.0, held=held())
    assert cd.check(Call(symbol="ABC", action="hold", stop=97.0), last_close=100.0, held=held()) is None


def test_buys_past_the_free_slots_are_skipped():
    book = Book(open=tuple(held(sym=f"H{i}") for i in range(4)))
    plan = Plan(day=D1, calls=(buy("A"), buy("B")))
    out = cd.accepted(plan, closes={"A": 100.0, "B": 100.0}, book=book)
    assert [c.symbol for c in out.calls] == ["A"]
    assert any("no free slot" in s for s in out.skipped)


# --- play: entries --------------------------------------------------------------------------

def test_limit_fills_only_when_the_low_reaches_it():
    plan = Plan(day=D1, calls=(buy(entry=100.0, stop=95.0),))
    assert cd.play(Book(), plan, {"ABC": bar(102, 103, 100.5, 102)}, D1).open == ()
    out = cd.play(Book(), plan, {"ABC": bar(102, 103, 99.0, 101)}, D1)
    assert len(out.open) == 1 and abs(out.open[0].entry - 100.0 * (1 + cd.COST)) < 1e-9


def test_a_limit_gapped_under_fills_at_the_open():
    out = cd.play(Book(), Plan(day=D1, calls=(buy(entry=100.0, stop=95.0),)),
                  {"ABC": bar(98, 99, 97, 98.5)}, D1)
    assert abs(out.open[0].entry - 98.0 * (1 + cd.COST)) < 1e-9


def test_breakout_fills_when_the_high_reaches_it():
    plan = Plan(day=D1, calls=(buy(kind="breakout", entry=105.0, stop=100.0),))
    assert cd.play(Book(), plan, {"ABC": bar(102, 104, 101, 103)}, D1).open == ()
    assert len(cd.play(Book(), plan, {"ABC": bar(102, 106, 101, 105.5)}, D1).open) == 1


def test_a_buy_stopped_on_its_fill_bar_is_closed_that_bar():
    out = cd.play(Book(), Plan(day=D1, calls=(buy(entry=100.0, stop=97.0),)),
                  {"ABC": bar(101, 101, 96, 98)}, D1)
    assert out.open == () and out.closed[0].reason == "stop (fill day)"
    assert out.cash < cd.SLEEVE


def test_a_slot_is_one_fifth_of_the_sleeve():
    out = cd.play(Book(), Plan(day=D1, calls=(buy(kind="open", entry=None, stop=95.0),)),
                  {"ABC": bar(100, 101, 99, 100)}, D1)
    pos = out.open[0]
    assert abs(pos.shares * pos.entry - cd.SLOT) < 1e-6


# --- play: exits ----------------------------------------------------------------------------

def test_stop_gap_target_and_both_in_one_bar():
    book = Book(cash=24_000.0, open=(held(stop=95.0, target=110.0),))
    gap = cd.play(book, None, {"ABC": bar(93, 94, 92, 93, D2)}, D2).closed[0]
    assert gap.reason == "stop (gap)" and abs(gap.exit - 93 * (1 - cd.COST)) < 1e-9
    hit = cd.play(book, None, {"ABC": bar(99, 111, 98, 108, D2)}, D2).closed[0]
    assert hit.reason == "target"
    both = cd.play(book, None, {"ABC": bar(100, 111, 94, 100, D2)}, D2).closed[0]
    assert both.reason == "stop"


def test_time_exit_and_sell_call():
    book = Book(cash=24_000.0, open=(held(max_days=3, held_days=2),))
    assert cd.play(book, None, {"ABC": bar(101, 102, 100, 101.5, D2)}, D2).closed[0].reason == "time"
    sold = cd.play(Book(open=(held(),)), Plan(day=D2, calls=(Call(symbol="ABC", action="sell"),)),
                   {"ABC": bar(103, 104, 102, 103, D2)}, D2).closed[0]
    assert sold.reason == "sell call" and abs(sold.exit - 103 * (1 - cd.COST)) < 1e-9


def test_a_hold_call_raises_the_stop():
    plan = Plan(day=D2, calls=(Call(symbol="ABC", action="hold", stop=99.0),))
    out = cd.play(Book(open=(held(stop=95.0),)), plan, {"ABC": bar(100, 101, 98.5, 100, D2)}, D2)
    assert out.closed[0].reason == "stop" and out.closed[0].exit < 99.0


def test_no_bar_holds_the_position_at_its_last_close():
    out = cd.play(Book(cash=24_000.0, open=(held(),)), None, {}, D2)
    assert out.open[0].held_days == 0 and out.marks[-1] == (D2, 30_000.0)


def test_drawdown():
    marks = ((D1, 31_000.0), (D2, 27_900.0))
    assert abs(cd.max_drawdown_pct(marks) - 10.0) < 1e-9


# --- Grok's check --------------------------------------------------------------------------

def _verdict(sym, approve, why=""):
    return cd.Verdict(symbol=sym, approve=approve, why=why)


def test_an_approved_call_is_kept_and_the_plan_is_signed():
    out = cd.apply_verdicts(Plan(day=D1, market="tape", calls=(buy(),)),
                            [_verdict("ABC", True, "matches the brief")], market_ok=True, market_why="ok")
    assert out.verified and [c.symbol for c in out.calls] == ["ABC"]
    assert out.market == "tape" and out.skipped == ()


def test_a_disapproved_call_is_skipped_with_the_reason():
    out = cd.apply_verdicts(Plan(day=D1, calls=(buy(),), skipped=("OLD: refused",)),
                            [_verdict("ABC", False, "stop is too wide")], market_ok=True, market_why="")
    assert out.calls == () and out.verified
    assert out.skipped == ("OLD: refused", "ABC: Grok disapproved (stop is too wide)")


def test_a_missing_verdict_is_a_disapproval():
    out = cd.apply_verdicts(Plan(day=D1, calls=(buy(), buy("DEF"))),
                            [_verdict("ABC", True)], market_ok=True, market_why="")
    assert [c.symbol for c in out.calls] == ["ABC"]
    assert out.skipped == ("DEF: Grok disapproved (no reason given)",)


def test_an_extra_symbol_does_not_add_a_buy_and_the_first_verdict_wins():
    out = cd.apply_verdicts(Plan(day=D1, calls=(buy("abc"),)),
                            [_verdict("abc", False, "no"), _verdict("ABC", True, "yes"),
                             _verdict("ZZZ", True, "buy this")],
                            market_ok=True, market_why="")
    assert out.calls == ()
    assert out.skipped == ("abc: Grok disapproved (no)",)


def test_a_bad_market_note_is_marked_and_approved_calls_stay():
    out = cd.apply_verdicts(Plan(day=D1, market="SPY at 900", calls=(buy(),)),
                            [_verdict("ABC", True)], market_ok=False, market_why="  ")
    assert out.verified and len(out.calls) == 1
    assert out.market.startswith("Grok disapproved the market note (no reason given). SPY at 900")


def test_an_old_plan_without_the_flag_is_unsigned():
    plan = cd.Plan.model_validate_json('{"day":"2026-11-02","market":"","calls":[],"skipped":[]}')
    assert plan.verified is False
    assert cd.for_settle(None) is None
    assert cd.for_settle(plan) is None
    signed = plan.model_copy(update={"verified": True})
    assert cd.for_settle(signed) is signed


def test_the_sunday_banner_names_each_answer():
    assert cd.sunday_banner(approve=True, why="") == "Grok approved this read."
    assert cd.sunday_banner(approve=False, why="  ") == "Grok DISAPPROVED this read: no reason given."
    assert cd.sunday_banner(approve=None, why="timeout") == (
        "Grok could not verify this read (timeout). It is not accepted.")


def _desk_script():
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / "scripts" / "claude_desk.py"
    spec = importlib.util.spec_from_file_location("claude_desk_script", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_a_missing_grok_program_is_an_error_the_checker_can_catch(monkeypatch, tmp_path):
    desk = _desk_script()
    monkeypatch.setattr(desk.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(desk.shutil, "which", lambda *_: None)
    try:
        desk.grok_exe()
    except RuntimeError as exc:
        assert "not found" in str(exc)
    except SystemExit:
        raise AssertionError("a missing Grok program must not skip the rest of the check")
    else:
        raise AssertionError("a missing Grok program must be reported")


def test_verify_checks_gemma_only(monkeypatch, capsys):
    desk = _desk_script()
    seen = []

    def fake(name, folder, today):
        seen.append(name)
        raise SystemExit("grok CLI not found")

    monkeypatch.setattr(desk, "verify_one", fake)
    monkeypatch.setattr(desk, "log", lambda *args: None)
    assert desk.CHECKED == ("gemma",)
    assert desk.verify(date(2026, 11, 2)) == 1
    assert seen == ["gemma"]
    assert "could not verify" in capsys.readouterr().out


def _sunday(monkeypatch, tmp_path, grok):
    import asyncio
    desk = _desk_script()
    saved = []

    async def brief(today):
        return "brief", {}

    monkeypatch.setattr(desk, "build_brief", brief)
    monkeypatch.setattr(desk, "grok_call", grok)
    monkeypatch.setattr(desk, "save_json", lambda path, text: saved.append((path, text)))
    monkeypatch.setattr(desk, "log", lambda *args: None)
    monkeypatch.setattr(desk, "SUNDAY_DIR", tmp_path)
    monkeypatch.setattr(desk, "SUNDAY_LATEST", tmp_path / "sunday.md")
    code = asyncio.run(desk.sunday(date(2026, 11, 8)))
    pages = [text for path, text in saved if path.name == "sunday.md" or path.parent.name == "sunday"]
    return code, pages


def test_sunday_is_written_by_grok(monkeypatch, tmp_path):
    code, pages = _sunday(monkeypatch, tmp_path, lambda *args, **kwargs: ("The week ahead.", 0.0))
    assert code == 0
    assert pages
    assert all("The week ahead." in text and "by Grok" in text for text in pages)


def test_sunday_writes_no_page_when_grok_cannot_start(monkeypatch, tmp_path):
    code, pages = _sunday(
        monkeypatch, tmp_path,
        lambda *args, **kwargs: (_ for _ in ()).throw(SystemExit("grok CLI not found")))
    assert code == 1
    assert pages == []


def test_the_checker_names_only_tools_this_cli_has():
    from pathlib import Path
    desk = _desk_script()
    names = desk.GROK_TOOLS.split(",")
    assert "open_page" not in names
    for name in ("read_file", "grep", "list_dir", "web_search", "web_fetch"):
        assert name in names
    assert int(desk.VERIFY_TURNS) >= 12
    prompt = (Path(__file__).resolve().parents[1] / "scripts" / "grok_verify_prompt.md").read_text(encoding="utf-8")
    assert "page you opened" not in prompt
    assert "page you fetched" in prompt


def test_settle_daily_bars_ask_iex(monkeypatch):
    import asyncio
    desk = _desk_script()
    seen = {}

    async def fake_bars(symbols, client, **kwargs):
        seen.update(kwargs)
        return {"QQQ": [{"t": "2026-11-02T21:00:00Z", "o": 1.0, "h": 2.0, "l": 0.5, "c": 1.5}]}

    monkeypatch.setattr(desk.alpaca_bars, "bars", fake_bars)
    out = asyncio.run(desk.daily_bars(None, ["QQQ"], date(2026, 11, 2), date(2026, 11, 2)))
    assert seen["feed"] == "iex"
    assert out["QQQ"][date(2026, 11, 2)].c == 1.5
