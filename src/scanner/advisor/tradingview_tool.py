"""TradingView pages (operator 2026-10-25: news and total market cap; 2026-11-02: the page text was the menu).

READ-ONLY. Public HTML only. The useful pages are parsed out of the markup, not copied as one blob:
  news           top stories, time and headline
  largest        biggest US stocks by market cap: cap, day's change, sector, analyst rating
  gainers/losers same columns, and only a name at $5 or more with a market cap of $300M or more
  ideas          idea titles only
  symbol_ideas   one stock's idea titles
Prices are left out. The small-cap movers page is the smallest names on the site, not the S&P 600,
and this desk does not use it. Third-party text: information, never an order.
"""

from __future__ import annotations

import html
import re
from email.utils import parsedate_to_datetime

import httpx

from scanner.advisor import web_tools

BASE = "https://www.tradingview.com"
PAGES = {
    "news": "/news/",
    "largest": "/markets/stocks-usa/market-movers-large-cap/",
    "gainers": "/markets/stocks-usa/market-movers-gainers/",
    "losers": "/markets/stocks-usa/market-movers-losers/",
    "ideas": "/ideas/stocks/",
}
EXCHANGES = ("NASDAQ", "NYSE", "AMEX")
#: Gainers and losers below this are the penny list the desk already refused.
MIN_PRICE = 5.0
MIN_CAP = 300_000_000.0
_MULT = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}
_TAG = re.compile(r"<[^>]+>")
_STORY = re.compile(
    r'dateTime="([^"]+)".{0,1200}?data-qa-id="news-headline-title">([^<]+)', re.S)
_ROW = re.compile(r'data-rowkey="([^"]+)"(.*?)</tr>', re.S)
_CELL = re.compile(r"<td\b[^>]*>(.*?)</td>", re.S)
_HEAD = re.compile(r"<thead\b[^>]*>(.*?)</thead>", re.S)
_TH = re.compile(r"<th\b([^>]*)>(.*?)</th>", re.S)
_FIELD = re.compile(r'data-field="([^"]+)"')
_IDEA = re.compile(r'data-qa-id="ui-lib-card-link-title"[^>]*>([^<]+)')
_AMOUNT = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*([KMBT])?")


def _plain(fragment: str) -> str:
    return " ".join(html.unescape(re.sub(r"\s+", " ", _TAG.sub(" ", fragment))).split())


def _amount(text: str) -> float | None:
    """A TradingView amount. Commas are thousands separators, so 1,150.89 is one thousand, not one."""
    cleaned = text.replace("\u202f", " ").replace("\xa0", " ").replace(",", "")
    match = _AMOUNT.search(cleaned)
    if match is None:
        return None
    return float(match.group(1)) * _MULT.get(match.group(2) or "", 1.0)


def _cap_label(text: str) -> str:
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*([KMBT])",
                      text.replace("\u202f", " ").replace(",", ""))
    return f"{match.group(1)}{match.group(2)}" if match else "?"


def _when(stamp: str) -> str:
    try:
        return parsedate_to_datetime(stamp).strftime("%Y-%m-%d %H:%M UTC")
    except (TypeError, ValueError, IndexError):
        return stamp


def news_lines(page: str, limit: int = 8) -> list[str]:
    """Time and headline for each top story. Menu text is not a story."""
    found = []
    seen: set[str] = set()
    for stamp, title in _STORY.findall(page):
        headline = _plain(title)
        if not headline or headline in seen:
            continue
        seen.add(headline)
        found.append(f"{_when(stamp)} — {headline}")
        if len(found) >= limit:
            break
    return found


def _columns(page: str) -> list[str]:
    """Field names from the movers table. Gainers put the change before the cap; large-cap does not."""
    heads = _HEAD.findall(page)
    block = next((head for head in heads if "MarketCap" in head), "")
    fields = []
    for attrs, _body in _TH.findall(block):
        match = _FIELD.search(attrs)
        if match:
            fields.append(match.group(1).split("|", 1)[0])
    return fields


def _listed_name(key: str, cell_html: str) -> str:
    """Symbol plus company. A missing logo is a one-letter span, so the cell text is not the name."""
    symbol = key.split(":")[-1].strip()
    title = re.search(r'title="([^"]+)"', cell_html)
    if symbol and title:
        parts = re.split(r"\s+[−–-]\s+", _plain(title.group(1)), maxsplit=1)
        company = parts[1] if len(parts) == 2 else ""
        return f"{symbol} {company}".strip()
    return _plain(cell_html)


def mover_lines(page: str, *, filtered: bool, limit: int = 12) -> tuple[list[str], int]:
    """One line per row, without the price. Filtered pages drop cheap and tiny names.

    The count is how many rows were left out by the price and cap floor.
    Columns come from the header, because the gainers page is not in the large-cap order.
    """
    fields = _columns(page)
    if not {"TickerUniversal", "MarketCap", "Price", "Change"} <= set(fields):
        return [], 0
    name_at = fields.index("TickerUniversal")
    kept: list[str] = []
    skipped = 0
    for key, body in _ROW.findall(page):
        raw = _CELL.findall(body)
        if len(raw) < len(fields):
            continue
        cells = [_plain(cell) for cell in raw]
        by = dict(zip(fields, cells))
        price, cap = _amount(by["Price"]), _amount(by["MarketCap"])
        if filtered and (price is None or cap is None or price < MIN_PRICE or cap < MIN_CAP):
            skipped += 1
            continue
        kept.append(
            f"{_listed_name(key, raw[name_at])} | cap {_cap_label(by['MarketCap'])} | {by['Change']} | "
            f"{by.get('Sector', '')} | {by.get('AnalystRating', '')}")
        if len(kept) >= limit:
            break
    return kept, skipped


def idea_lines(page: str, limit: int = 8) -> list[str]:
    """Idea titles only. The note under the title often names an entry and a stop."""
    found = []
    seen: set[str] = set()
    for title in _IDEA.findall(page):
        text = _plain(title)
        if not text or text in seen:
            continue
        seen.add(text)
        found.append(text)
        if len(found) >= limit:
            break
    return found


def _join(head: str, rows: list[str], empty: str) -> str:
    return head + "\n" + ("\n".join(f"- {row}" for row in rows) if rows else empty)


async def _fetch(client: httpx.AsyncClient, path: str) -> tuple[int, str]:
    response = await client.get(BASE + path, headers=web_tools.UA, timeout=web_tools.TIMEOUT_S,
                                follow_redirects=True)
    return response.status_code, response.text if response.status_code == 200 else ""


async def tradingview(client: httpx.AsyncClient, broker, page: str, symbol: str = "") -> str:
    page = str(page).strip().lower()
    if page == "small_cap":
        return ("TradingView's small-cap page is the smallest names on the site, many under $1. "
                "It is not the S&P 600. This desk does not use it.")
    if page == "symbol_ideas":
        ticker = str(symbol).strip().upper()
        if not ticker.replace(".", "").isalnum():
            return "symbol_ideas needs a ticker, e.g. symbol='OKTA'."
        for exchange in EXCHANGES:
            code, text = await _fetch(client, f"/symbols/{exchange}-{ticker}/ideas/")
            titles = idea_lines(text) if code == 200 else []
            if titles:
                return _join(f"TradingView ideas for {exchange}:{ticker} (opinions, not orders):",
                             titles, "")
        return f"No TradingView ideas found for {ticker}."
    if page not in PAGES:
        return f"Unknown page {page!r}. Pages: {', '.join([*PAGES, 'symbol_ideas'])}."
    code, text = await _fetch(client, PAGES[page])
    if code != 200 or not text:
        return f"TradingView {page} unavailable (HTTP {code})."
    if page == "news":
        return _join("TradingView top stories (third-party text, not an order):",
                     news_lines(text), "no headlines parsed")
    if page == "ideas":
        return _join("TradingView stock ideas (opinions, not orders; titles only):",
                     idea_lines(text), "no idea titles parsed")
    rows, skipped = mover_lines(text, filtered=page != "largest")
    note = ""
    if page != "largest" and skipped:
        note = f"\n{skipped} names under ${MIN_PRICE:.0f} or under $300M market cap were left out."
    return _join(
        "TradingView " + page + " (cap, change, sector, rating; prices left out on purpose):",
        rows, "no rows parsed") + note


async def morning_block(client: httpx.AsyncClient) -> str:
    """Top stories and the largest names, for the research brief. A dead page is one line."""
    parts = []
    for page in ("news", "largest"):
        try:
            parts.append(await tradingview(client, None, page))
        except Exception as exc:                              # noqa: BLE001 -- the brief still builds
            parts.append(f"TradingView {page} unavailable ({type(exc).__name__}).")
    return "\n\n".join(parts)
