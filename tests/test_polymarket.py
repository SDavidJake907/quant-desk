"""Polymarket odds are a report. The module does not trade."""

from pathlib import Path

from scanner.sources import polymarket as pm


def test_a_yes_price_is_a_percent_and_a_closed_market_is_left_out():
    payload = {
        "events": [
            {
                "slug": "fed-decision",
                "closed": False,
                "markets": [
                    {
                        "question": "Will the Fed cut rates in December?",
                        "outcomes": "[\"Yes\", \"No\"]",
                        "outcomePrices": "[\"0.62\", \"0.38\"]",
                        "closed": False,
                    },
                    {"question": "Old question", "closed": True, "outcomes": "[\"Yes\"]", "outcomePrices": "[\"0.1\"]"},
                ],
            },
            {"slug": "done", "closed": True, "title": "Already settled", "markets": []},
        ]
    }
    markets = pm.markets_from_search(payload)
    assert markets == (pm.Market(
        "Will the Fed cut rates in December?", "Yes 62%",
        "https://polymarket.com/event/fed-decision", 0.62,
    ),)


def test_a_price_ladder_keeps_the_strike_nearest_even_odds():
    payload = {"events": [{
        "slug": "wti",
        "closed": False,
        "markets": [
            {"question": "WTI hits $160", "outcomes": "[\"Yes\", \"No\"]", "outcomePrices": "[\"0.01\", \"0.99\"]"},
            {"question": "WTI hits $80", "outcomes": "[\"Yes\", \"No\"]", "outcomePrices": "[\"0.48\", \"0.52\"]"},
        ],
    }]}
    markets = pm.markets_from_search(payload)
    assert len(markets) == 1
    assert markets[0].question == "WTI hits $80"
    assert markets[0].price == "Yes 48%"


def test_a_question_names_a_holding_and_a_short_ticker_needs_a_cashtag():
    markets = (
        pm.Market("Will $GLW close above 120?", "Yes 40%", "https://polymarket.com/event/glw"),
        pm.Market("Will the CI of demand rise?", "Yes 10%", ""),
        pm.Market("NVDA above 200", "Yes 55%", ""),
    )
    assert pm.for_symbol(markets, "GLW")[0].question.startswith("Will $GLW")
    assert pm.for_symbol(markets, "CI") == ()
    assert pm.for_symbol(markets, "NVDA")[0].price == "Yes 55%"


def test_the_page_says_a_price_is_not_a_bet():
    text = pm.format_report(
        written="Wed 2026-11-11 05:15 AKST",
        symbols=["GLW"],
        by_symbol={"GLW": ()},
        macro=(pm.Market("Fed cut", "Yes 62%", "https://polymarket.com/event/fed"),),
        error=None,
    )
    assert "No bet is sent." in text
    assert "does not sell" in text
    assert "No Polymarket question names this symbol." in text
    assert "They do not move a stop." in text
    assert "Fed cut — Yes 62%." in text


def test_the_module_does_not_trade():
    text = Path("src/scanner/sources/polymarket.py").read_text(encoding="utf-8")
    for banned in ("clob", "place_order", "private_key", "signature"):
        assert banned not in text.lower()
