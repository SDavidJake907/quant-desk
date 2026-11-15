"""Public Polymarket odds for names already held, plus four macro questions.

Gamma is a catalog. This module does not open a wallet, sign an order, or
call the trading API. A price on this page does not buy and does not sell.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import NamedTuple

import httpx

from scanner.ops.position_card import names_symbol

SEARCH_URL = "https://gamma-api.polymarket.com/public-search"
EVENT_URL = "https://polymarket.com/event/"
MACRO = ("Federal Reserve", "crude oil", "tariff", "S&P 500")
_HEADER = (
    "Report only. A price here does not buy, does not sell, and is not an entry. "
    "No bet is sent."
)


class Market(NamedTuple):
    question: str
    price: str
    url: str
    yes: float | None = None


def _json_list(value: object) -> list:
    if isinstance(value, list):
        return value
    if not isinstance(value, str) or not value.strip():
        return []
    try:
        parsed = json.loads(value)
    except ValueError:
        return []
    return parsed if isinstance(parsed, list) else []


def _pct(value: object) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "n/a"
    if number != number:
        return "n/a"
    return f"{number * 100:.0f}%"


def _yes_number(outcomes: object, prices: object) -> float | None:
    names = [str(item) for item in _json_list(outcomes)]
    marks = _json_list(prices)
    if not names or not marks:
        return None
    chosen = marks[0]
    for name, mark in zip(names, marks):
        if name.strip().lower() == "yes":
            chosen = mark
            break
    try:
        number = float(chosen)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return number


def price_text(outcomes: object, prices: object) -> str:
    """The Yes price when the market has one, otherwise the first outcome."""
    names = [str(item) for item in _json_list(outcomes)]
    number = _yes_number(outcomes, prices)
    if number is None or not names:
        return "n/a"
    label = "Yes"
    for name in names:
        if name.strip().lower() == "yes":
            break
    else:
        label = names[0]
    return f"{label} {_pct(number)}"


def markets_from_search(payload: object) -> tuple[Market, ...]:
    """Open markets from one public-search body. A closed row is left out."""
    if not isinstance(payload, dict):
        return ()
    events = payload.get("events")
    if not isinstance(events, list):
        return ()
    found: list[Market] = []
    seen: set[str] = set()
    for event in events:
        if not isinstance(event, dict) or event.get("closed") is True:
            continue
        slug = str(event.get("slug") or "")
        url = f"{EVENT_URL}{slug}" if slug else ""
        markets = event.get("markets")
        if not isinstance(markets, list) or not markets:
            title = " ".join(str(event.get("title") or "").split())
            if title and title not in seen:
                seen.add(title)
                found.append(Market(title, "n/a", url, None))
            continue
        group: list[Market] = []
        for market in markets:
            if not isinstance(market, dict) or market.get("closed") is True:
                continue
            question = " ".join(str(market.get("question") or event.get("title") or "").split())
            if not question or question in seen:
                continue
            yes = _yes_number(market.get("outcomes"), market.get("outcomePrices"))
            group.append(Market(question, price_text(market.get("outcomes"), market.get("outcomePrices")), url, yes))
        if not group:
            continue
        # A price ladder has many strikes. The one nearest even odds is the live question.
        picked = min(group, key=lambda row: abs((row.yes if row.yes is not None else 0.0) - 0.5))
        seen.add(picked.question)
        found.append(picked)
    return tuple(found)


def for_symbol(markets: Sequence[Market], symbol: str) -> tuple[Market, ...]:
    """Questions that name this ticker. A two-letter name needs a cashtag."""
    return tuple(market for market in markets if names_symbol(market.question, symbol))


def format_report(
    *,
    written: str,
    symbols: Sequence[str],
    by_symbol: Mapping[str, Sequence[Market]],
    macro: Sequence[Market],
    error: str | None,
) -> str:
    """The morning page. Macro odds are listed once and do not attach to a name."""
    lines = ["# Polymarket", "", _HEADER, "", f"Written {written}."]
    if error:
        lines.extend(["", f"Polymarket unreadable ({error}). Nothing was bet."])
    lines.extend(["", "## Open book"])
    if not symbols:
        lines.append("No open positions.")
    for symbol in symbols:
        lines.extend(["", f"### {symbol}"])
        rows = list(by_symbol.get(symbol) or ())
        if not rows:
            lines.append("- No Polymarket question names this symbol.")
            continue
        for market in rows[:3]:
            link = f" {market.url}" if market.url else ""
            lines.append(f"- {market.question} — {market.price}.{link}")
    lines.extend([
        "",
        "## Macro",
        "These questions do not name a holding. They do not move a stop.",
    ])
    if not macro:
        lines.append("- No macro question was returned.")
    else:
        for market in macro[:8]:
            link = f" {market.url}" if market.url else ""
            lines.append(f"- {market.question} — {market.price}.{link}")
    return "\n".join(lines) + "\n"


async def search(client: httpx.AsyncClient, query: str) -> tuple[Market, ...]:
    """One public catalog search. Raises on a dead feed so the caller can say so."""
    response = await client.get(SEARCH_URL, params={
        "q": query,
        "limit_per_type": 5,
        "search_profiles": "false",
        "search_tags": "false",
    }, timeout=20)
    response.raise_for_status()
    return markets_from_search(response.json())
