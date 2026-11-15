"""The position card joins numbers, the six readings, headlines and the President feed. It never sells."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

from scanner.ops import position_card as card
from scanner.ops.peak_sell import CHECKS

NOW = datetime(2026, 11, 2, 15, 0, tzinfo=timezone.utc)


def holding(**kw) -> card.Holding:
    base = dict(symbol="APA", book="gemma", qty=6, entry=51.2, last=52.83,
                upl=9.78, upl_pct=3.2, today=-1.2, stop=50.11, atr_pct=2.0,
                sector="Energy", values={k: None for k in CHECKS})
    base.update(kw)
    return card.Holding(**base)


def test_stop_line_uses_the_stocks_own_atr():
    assert card.stop_line(52.83, 50.11, 2.0) == "Stop 50.11 · 5.1% above it · 2.6 daily ATR away"
    assert card.stop_line(52.83, None, 2.0) == "No live stop."
    assert "through it" in card.stop_line(49.0, 50.0, 2.0)


def test_stop_distance_is_the_dollar_gap_in_atrs():
    """SM on 2026-11-02: live 39.40, Friday close 40.38, ATR 3.154% of that close,
    stop 38.96. The dollar gap is 0.3 ATR. Dividing the live percent by the
    close's ATR percent prints 0.4."""
    from scanner.ops.peak_sell import atr_percent_of
    scaled = atr_percent_of(3.154, 40.38, 39.40)
    assert card.stop_line(39.40, 38.96, scaled).endswith("0.3 daily ATR away")


def test_indicators_name_the_session_and_keep_a_small_schaff():
    values = {k: None for k in CHECKS}
    values["vwap_5m"] = -0.016
    values["stc_1d"] = 0.14
    values["trend_5m"] = None
    lines = card.indicator_lines("SPY", values, session_day="2026-10-30")
    assert any("2026-10-30 regular-session VWAP: now -0.02% -> not met" in line for line in lines)
    assert any("daily Schaff Trend Cycle >= 80: now 0.1 -> not met" in line for line in lines)
    assert any("no fresh 5-minute candle" in line for line in lines)


def test_the_card_says_which_bars_each_reading_uses():
    text = card.render(
        written=NOW, holdings=[holding(session_day="2026-10-30", values={k: 0.0 for k in CHECKS})],
        market=[], headlines={}, news_error=None, posts=[], feed_failed=0, feed_tried=1,
    )
    assert "2026-10-30 regular session" in text
    assert "Daily Schaff and %B include the live price." in text


def test_indicators_show_each_reading_against_its_threshold():
    values = {k: None for k in CHECKS}
    values["crsi_1h"] = 41
    values["pctb_1d"] = 0.7994
    lines = card.indicator_lines("APA", values)
    assert any("Connors RSI >= 63: now 41 -> not met" in line for line in lines)
    assert any("daily Bollinger %B >= 0.80: now 0.799 -> not met" in line for line in lines)
    assert any("missing" in line for line in lines)
    assert card.indicator_lines("APA", None) == ["The six readings were not computed this pass."]


def test_a_takeover_label_is_named_and_does_not_sell():
    from scanner.execution.news_gate import NewsFlag
    flag = NewsFlag(symbol="APA", deal_role="target", headlines=("APA agrees to be acquired",))
    lines = card.label_lines(flag)
    assert "being acquired" in lines[0]
    assert "does not sell" in lines[0]
    assert lines[1] == "APA agrees to be acquired"
    clean = card.label_lines(NewsFlag(symbol="APA"))
    assert clean == ["No lawsuit, earnings date, or takeover. Report only. This does not sell."]
    missed = card.label_lines(NewsFlag(symbol="APA", checked=False))
    assert missed == ["Not labeled. A missed label does not sell."]


def test_the_card_shows_a_stored_label_and_says_it_does_not_sell(tmp_path: Path):
    path = tmp_path / "held_news.json"
    card.save_labels(path, "Tue 2026-11-04 07:15 AKST", {"APA": ["No lawsuit, earnings date, or takeover."]})
    loaded = card.load_labels(path)
    assert loaded is not None and loaded[0].startswith("Tue")
    text = card.render(
        written=NOW, holdings=[holding()], market=[], headlines={"APA": []}, news_error=None,
        posts=[], feed_failed=0, feed_tried=0, held_labels=loaded[1], held_at=loaded[0],
    )
    assert "Company news labeled Tue 2026-11-04 07:15 AKST" in text
    assert "It does not sell" in text
    assert "No lawsuit, earnings date, or takeover." in text
    assert "### Company news" in text


def test_a_headline_is_shown_when_the_word_list_misses_it():
    text = card.render(
        written=NOW, holdings=[holding()], market=["SPY slides on a chip forecast"],
        headlines={"APA": ["Hormuz talks stall, oil holds"]}, news_error=None,
        posts=[], feed_failed=0, feed_tried=4,
    )
    assert "Hormuz talks stall, oil holds" in text
    assert "Word list matched none of 1" in text
    assert "SPY slides on a chip forecast" in text
    assert "Report only" in text


def test_the_word_list_is_named_when_a_headline_matches():
    text = card.render(
        written=NOW, holdings=[holding()], market=[],
        headlines={"APA": ["Missile strike near a tanker", "Oil holds"]}, news_error=None,
        posts=[], feed_failed=0, feed_tried=4,
    )
    assert "Word list matched 1 of 2" in text
    assert "Oil holds" in text


def test_a_post_that_names_the_symbol_is_shown_and_a_small_word_is_not():
    posts = [
        ("Truth Social", "11-01 18:00", "APA reports Wednesday after the close"),
        ("Truth Social", "11-01 18:05", "the small caps rallied"),
        ("White House news", "11-01 19:00", "Two more tankers hit mines in the Strait of Hormuz"),
    ]
    text = card.render(
        written=NOW, holdings=[holding(), holding(symbol="NTAP", book="gemma", sector="Information Technology")],
        market=[], headlines={}, news_error=None, posts=posts, feed_failed=0, feed_tried=4,
    )
    import re
    parts = re.split(r"(?m)^## ", text)
    apa, ntap = parts[2], parts[3]
    assert "names the symbol" in apa and "APA reports Wednesday" in apa
    assert "Hormuz" in apa and "energy book" in apa
    assert "small caps rallied" not in apa
    assert "Hormuz" not in ntap
    assert "No matching post. 3 posts were read." in ntap


def test_an_oil_company_endorsement_is_not_the_oil_market():
    assert card.post_reason("She is a Strong Supporter of Texas Oil & Gas!", "APA", "Energy") is None
    assert card.post_reason("President of Middleton Oil Company", "APA", "Energy") is None
    assert card.post_reason("endorsed by the Kansas Oil and Gas Industry", "SM", "Energy") is None
    assert card.post_reason("Oil fell 3% overnight.", "APA", "Energy") == "energy book: the post says oil"
    assert card.post_reason("The price of oil and gas fell overnight.", "APA", "Energy") == (
        "energy book: the post says oil")
    assert card.post_reason("US oil and gas output rose.", "APA", "Energy") == "energy book: the post says oil"
    assert card.post_reason("Crude oil and gas prices fell.", "APA", "Energy") == "energy book: the post says oil"
    assert card.post_reason("The oil industry is shutting in barrels.", "APA", "Energy") == (
        "energy book: the post says oil")
    assert card.post_reason("Oil companies halted exports.", "APA", "Energy") == "energy book: the post says oil"
    assert card.post_reason("The oil company halted exports.", "APA", "Energy") == "energy book: the post says oil"
    both = "She supports Texas Oil & Gas. Two tankers hit mines in the Strait of Hormuz."
    assert card.post_reason(both, "APA", "Energy") == "energy book: the post says tanker, Hormuz"
    assert card.post_reason("Iranian officials warned shippers.", "APA", "Energy") == (
        "energy book: the post says Iran")
    assert card.post_reason("Oil fell 3% overnight.", "NTAP", "Information Technology") is None
    assert card.post_reason("APA reports Wednesday", "APA", "Energy") == "names the symbol"
    posts = [
        ("Truth Social", "11-01 10:28", "Ken Paxton will Champion Texas Oil & Gas."),
        ("Truth Social", "11-01 12:00", "Oil fell 3% overnight after the settlement."),
    ]
    text = card.render(
        written=NOW, holdings=[holding(), holding(symbol="NTAP", sector="Information Technology")],
        market=[], headlines={}, news_error=None, posts=posts, feed_failed=0, feed_tried=4,
    )
    assert "Ken Paxton" not in text
    assert "Oil fell 3% overnight after the settlement." in text


def test_an_x_post_is_shown_only_when_it_names_the_holding():
    assert card.names_symbol("$CI reports Friday", "CI")
    assert not card.names_symbol("the CI of the matter", "CI")
    assert card.names_symbol("GLW raised its range", "GLW")
    posts = [
        ("11-11 06:10", "$GLW is bid up before the open"),
        ("11-11 06:12", "the small caps rallied"),
        ("11-11 06:14", "Oil fell and the CI of the matter is demand"),
    ]
    text = card.render(
        written=NOW, holdings=[holding(symbol="GLW", sector="Information Technology"),
                                holding(symbol="CI", sector="Health Care")],
        market=[], headlines={}, news_error=None, posts=[], feed_failed=0, feed_tried=1,
        x_posts=posts,
    )
    assert "An X post does not sell" in text
    assert "$GLW is bid up before the open" in text
    assert "small caps rallied" not in text
    assert "CI of the matter" not in text
    assert "No X post names this symbol." in text


def test_a_dead_president_feed_still_shows_the_x_line():
    text = card.render(
        written=NOW, holdings=[holding()], market=[], headlines={}, news_error=None,
        posts=[], feed_failed=4, feed_tried=4, x_error="not configured",
    )
    assert "President feed unreadable" in text
    assert "Feed unreadable" in text
    assert "X feed unreadable (not configured)." in text


def test_a_dead_feed_is_said_out_loud():
    text = card.render(
        written=NOW, holdings=[holding()], market=[], headlines={}, news_error="TimeoutException",
        posts=[], feed_failed=4, feed_tried=4,
    )
    assert "Alpaca news unreadable" in text
    assert "President feed unreadable" in text
    assert "Not read this pass" in text
    assert "Feed unreadable" in text


def test_due_is_true_when_the_card_is_missing_or_old(tmp_path: Path):
    path = tmp_path / "latest.md"
    assert card.due(path, NOW)
    path.write_text("x", encoding="utf-8")
    fresh = datetime.now(timezone.utc)
    assert not card.due(path, fresh)
    old = fresh + timedelta(seconds=card.REFRESH_S + 5)
    assert card.due(path, old)


def test_cached_index_pages_know_the_energy_names():
    pages = Path("data")
    if not (pages / "sp500_wikitext.txt").exists():
        return
    sectors = card.sector_map(pages)
    assert sectors["APA"] == "Energy"
    assert sectors["INSW"] == "Energy"
    assert sectors["NTAP"].startswith("Information")
    assert sectors["SM"] == "Energy"
