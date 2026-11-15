"""Gemma's web tools (advisor/web_tools.py, operator + Gemma 2026-10-25)."""

import asyncio

from scanner.advisor import stock_tools
from scanner.advisor import web_tools as wt
from scanner.ops import market_monitor as mm

DDG = ('<a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fokta&amp;rut=x">'
       'Okta <b>beats</b></a><a class="result__snippet" href="x">Okta raised &amp; guidance</a>'
       '<a class="result__a" href="https://duckduckgo.com/y.js?ad=1">Ad</a>'
       '<a class="result__a" href="https://site.com/b">Second</a>')


def test_parse_results_unwraps_links_and_skips_ads():
    rows = wt.parse_results(DDG)
    assert rows[0] == ("Okta beats", "https://example.com/okta", "Okta raised & guidance")
    assert [r[1] for r in rows] == ["https://example.com/okta", "https://site.com/b"]


def test_page_text_drops_scripts_and_menus():
    page = ("<html><head><title>OKTA up</title><script>var x=1</script></head><body><nav>Menu</nav>"
            "<p>Okta   raised its outlook.</p><footer>c</footer></body></html>")
    assert wt.page_text(page) == "OKTA up -- Okta raised its outlook."


def test_private_and_local_addresses_are_refused():
    assert not wt.is_public("127.0.0.1") and not wt.is_public("192.168.1.5") and not wt.is_public("::1")
    assert wt.is_public("8.8.8.8")
    assert "only http(s)" in asyncio.run(wt._check("file:///c:/secrets.txt"))
    assert "not a public address" in asyncio.run(wt._check("http://localhost:11434/api/tags"))
    out = asyncio.run(stock_tools.execute("read_page", {"url": "http://127.0.0.1:8000/"}, client=None, broker=None))
    assert out.startswith("Refused")


def test_web_lookups_join_the_named_stocks_facts():
    calls = [{"tool": "web_search", "args": {"query": "MU guidance cut"}, "result": "Micron cuts guidance"},
             {"tool": "web_search", "args": {"query": "memory chip prices"}, "result": "prices fall"}]
    out = mm.with_lookups({"MU": "f", "MUSA": "g"}, calls)
    assert "Micron cuts guidance" in out["MU"] and out["MUSA"] == "g"


def test_web_tools_are_registered():
    names = {t["function"]["name"] for t in stock_tools.TOOLS}
    assert {"web_search", "read_page"} <= names == set(stock_tools.HANDLERS)


_NEWS = (
    "Search EN Get started "
    '<time dateTime="Mon, 02 Nov 2026 07:49:14 GMT">2 hours ago</time>'
    '<div data-qa-id="news-headline-title">XAUUSD: Gold regains a level</div>'
    '<time dateTime="Mon, 02 Nov 2026 06:10:00 GMT">3 hours ago</time>'
    '<div data-qa-id="news-headline-title">S&amp;P futures slip</div>'
)
_HEAD = (
    "<thead><tr>"
    '<th data-field="TickerUniversal">Symbol</th><th data-field="MarketCap">Mkt cap</th>'
    '<th data-field="Price">Price</th><th data-field="Change|TimeResolution1D">Chg</th>'
    '<th data-field="Sector">Sector</th><th data-field="AnalystRating">Rating</th>'
    "</tr></thead>"
)
_GAIN_HEAD = (
    "<thead><tr>"
    '<th data-field="TickerUniversal">Symbol</th><th data-field="Change|TimeResolution1D">Chg</th>'
    '<th data-field="Price">Price</th><th data-field="MarketCap">Mkt cap</th>'
    '<th data-field="Sector">Sector</th><th data-field="AnalystRating">Rating</th>'
    "</tr></thead>"
)
_ROWS = (
    _HEAD
    + '<tr data-rowkey="NASDAQ:AAPL"><td>AAPL Apple Inc.</td><td>5.03 T USD</td><td>344.63 USD</td>'
    "<td>−1.23%</td><td>Electronic technology</td><td>Buy</td></tr>"
    '<tr data-rowkey="NASDAQ:CHOW"><td>CHOW Chow</td><td>15.62 M USD</td><td>4.40 USD</td>'
    "<td>+106%</td><td>Technology services</td><td>No rating</td></tr>"
)
_GAIN_ROWS = (
    _GAIN_HEAD
    + '<tr data-rowkey="NASDAQ:GDYN"><td>GDYN Grid Dynamics</td><td>+40.78%</td><td>13.36 USD</td>'
    "<td>1.08 B USD</td><td>Technology services</td><td>Strong buy</td></tr>"
    '<tr data-rowkey="NASDAQ:CHOW"><td>CHOW Chow</td><td>+106%</td><td>4.40 USD</td>'
    "<td>15.62 M USD</td><td>Technology services</td><td>No rating</td></tr>"
)
_IDEAS = (
    'Search EN <a data-qa-id="ui-lib-card-link-title">CRCL: a triangle</a>'
    '<p>Stop Loss: 10 Take Profit: 12 Entry: 11</p>'
    '<a data-qa-id="ui-lib-card-link-title">CRCL: a triangle</a>'
)


def test_tradingview_parses_stories_and_rows_and_refuses_the_penny_page():
    from scanner.advisor import tradingview_tool as tv
    stories = tv.news_lines(_NEWS)
    assert stories == ["2026-11-02 07:49 UTC — XAUUSD: Gold regains a level",
                       "2026-11-02 06:10 UTC — S&P futures slip"]
    assert "Search" not in " ".join(stories)
    largest, skipped = tv.mover_lines(_ROWS, filtered=False)
    assert skipped == 0 and largest[0].startswith("AAPL Apple Inc. | cap 5.03T")
    assert "344.63" not in largest[0] and largest[0].endswith("Buy")
    gainers, left = tv.mover_lines(_GAIN_ROWS, filtered=True)
    assert len(gainers) == 1 and gainers[0].startswith("GDYN") and "1.08B" in gainers[0] and left == 1
    assert "40.78" not in gainers[0].split("| cap ", 1)[-1].split("|", 1)[0]
    logo = (
        _GAIN_HEAD
        + '<tr data-rowkey="NASDAQ:RZAI"><td><span class="logo">R</span>'
        '<a title="RZAI − Roze AI Inc.">RZAI</a></td><td>−24.08%</td><td>6.10 USD</td>'
        "<td>368.36 M USD</td><td>Technology services</td><td>No rating</td></tr>"
    )
    named, _left = tv.mover_lines(logo, filtered=True)
    assert named[0].startswith("RZAI Roze AI Inc. | cap 368.36M")
    assert tv._amount("1,150.89 USD") == 1150.89
    pricey = (
        _GAIN_HEAD
        + '<tr data-rowkey="NASDAQ:SNDK"><td>SNDK Sandisk</td><td>−10.00%</td>'
        "<td>1,150.89 USD</td><td>166.21 B USD</td><td>Electronic technology</td><td>Buy</td></tr>"
    )
    kept, left = tv.mover_lines(pricey, filtered=True)
    assert left == 0 and kept[0].startswith("SNDK Sandisk | cap 166.21B") and "1,150.89" not in kept[0]
    assert tv.idea_lines(_IDEAS) == ["CRCL: a triangle"]
    assert "Stop Loss" not in " ".join(tv.idea_lines(_IDEAS))
    out = asyncio.run(stock_tools.execute("tradingview", {"page": "small_cap"}, client=None, broker=None))
    assert "not the S&P 600" in out
    out = asyncio.run(stock_tools.execute("tradingview", {"page": "crypto"}, client=None, broker=None))
    assert out.startswith("Unknown page")
    out = asyncio.run(stock_tools.execute("tradingview", {"page": "symbol_ideas", "symbol": "../x"}, client=None,
                                          broker=None))
    assert "needs a ticker" in out
