"""Scheduled events that cause gaps.

WHY THIS IS DIFFERENT FROM EVERY OTHER FILTER HERE
Seventeen signal filters were tested against this strategy and most made it
worse. This one is not a signal. It removes a KNOWN, DATED event from a
strategy whose measured weakness is precisely that event.

The evidence: gapped stops are the fat left tail. The five worst trades in the
study were all gaps — CF -3.56R, QCOM -2.78R, LITE -2.01R, UAL -1.60R,
MOS -1.58R — against a -1.00R mean for stops that filled normally. A position
sized to lose 1R lost 3.56R.

Earnings are the most predictable gap event that exists, and this strategy
holds overnight: only 54% of setups resolve inside one session. The news layer
reads what has already been published; it cannot know a company reports
tomorrow. This can.

SOURCE
Nasdaq's public calendar API — free, no key, verified returning 527 companies
for a single session. It is undocumented, so every failure is swallowed and
reported as "unknown" rather than "clear": a calendar outage must not silently
become permission to trade through earnings.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import date, timedelta

import httpx

from scanner.models import Frozen

EARNINGS_URL = "https://api.nasdaq.com/api/calendar/earnings"
ECONOMIC_URL = "https://api.nasdaq.com/api/calendar/economicevents"

#: Nasdaq rejects generic clients; this is their public web front end's shape.
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

#: Days ahead to look. The strategy's time limit is 60 bars of 30m, about 4.6
#: sessions, so a week covers the whole holding period.
DEFAULT_HORIZON = 7

#: Nasdaq's economic calendar answers `date=D` with D-1's releases. Checked
#: 2026-10-08: date=10-09 returned Thursday 10-08's jobless claims and 30-year
#: auction; date=10-15 returned Wednesday 10-14's CPI. Until then every caller
#: showed YESTERDAY's releases as today's (Gemma's market line, the weekly
#: report). Ask for the next day.
ECONOMIC_DATE_OFFSET = timedelta(days=1)
#: Nasdaq's earnings calendar is NOT shifted: `date=D` returns D's reports.
#: Checked 2026-10-22: date=10-22 listed RNG (reported 10-22 after the close),
#: date=10-21 listed TSLA, IBM, T. The 10-15 note that set this to one day
#: (ALLY, PRGO) was wrong or Nasdaq changed -- with it, every date came back a
#: day early and a report due TODAY was never seen (RNG was bought into its pop).
EARNINGS_DATE_OFFSET = timedelta(0)
#: Only US releases: the feed carries German and Chinese CPI under the same names.
US = "United States"

#: Sub-series printed alongside a headline release (CPI's n.s.a. index, the
#: Philly Fed's components, nowcasts): one line per release is enough.
SUB_SERIES = re.compile(r"n\.s\.a|\bs\.a\b|CPI Index|Cleveland|Philly Fed|GDPNow|Core Retail|Retail Control|Retail Sales Ex|Cushing|KC Fed|Dallas Fed|Chicago Fed|Richmond Fed|GDP Price|GDP Sales|PCE Prices", re.IGNORECASE)

#: US releases that move the whole tape rather than one name.
MARKET_MOVING = ("FOMC", "CPI", "Nonfarm", "PPI", "GDP", "Unemployment",
                 "Interest Rate", "Fed ",
                 # added 2026-10-08 (WORKLIST §54): the rest of what moves the index
                 "Retail Sales", "PCE", "ISM", "Beige Book", "Michigan Consumer Sentiment",
                 "Manufacturing PMI", "10-Year Note Auction", "30-Year Bond Auction")
#: Foreign central banks whose decisions move global yields, and with them US
#: stocks (WORKLIST §58): (country, event name) exactly as the feed prints them.
GLOBAL_CENTRAL_BANKS = (
    ("Euro Zone", "ECB Interest Rate Decision"),
    ("Japan", "BoJ Interest Rate Decision"),
    ("United Kingdom", "BoE Interest Rate Decision"),
    ("China", "China Loan Prime Rate 5Y"),
    ("South Korea", "Interest Rate Decision"),     # home of Samsung and SK Hynix memory
)
IPO_URL = "https://api.nasdaq.com/api/ipo/calendar"

#: Releases that move one part of the market: name token -> sectors it touches.
SECTOR_EVENTS: dict[str, tuple[str, ...]] = {
    "OPEC": ("Energy",),
    "IEA Monthly": ("Energy",),
    "Crude Oil Inventories": ("Energy",),
    "Housing Starts": ("Real Estate", "Consumer Discretionary"),
    "Building Permits": ("Real Estate", "Consumer Discretionary"),
    "Existing Home Sales": ("Real Estate", "Consumer Discretionary"),
    "New Home Sales": ("Real Estate", "Consumer Discretionary"),
}


class Earnings(Frozen):
    """When a company reports, if it reports soon."""

    symbol: str
    on: date
    when: str = ""      # pre-mkt | after-close | ""

    @property
    def label(self) -> str:
        return f"{self.on:%a %m/%d}" + (f" {self.when}" if self.when else "")


class Release(Frozen):
    """One scheduled release: what came in, what was expected, what came before
    (WORKLIST §56). Blank until published; several rows of one name (CPI m/m
    and y/y) are joined with " / "."""

    name: str
    actual: str = ""
    consensus: str = ""
    previous: str = ""


class CalendarError(RuntimeError):
    """Raised only where a caller has asked to be told about failure."""


def _session_days(start: date, horizon: int) -> list[date]:
    return [
        start + timedelta(days=offset)
        for offset in range(horizon)
        if (start + timedelta(days=offset)).weekday() < 5
    ]


def parse_earnings(payload: dict, on: date, wanted: frozenset[str]) -> dict[str, Earnings]:
    """Pure parse of one day's calendar page."""
    found: dict[str, Earnings] = {}
    rows = (payload.get("data") or {}).get("rows") or []
    for row in rows:
        symbol = str(row.get("symbol") or "").strip().upper()
        if not symbol or (wanted and symbol not in wanted):
            continue
        raw = str(row.get("time") or "").lower()
        when = "pre-mkt" if "pre" in raw else ("after-close" if "after" in raw else "")
        found.setdefault(symbol, Earnings(symbol=symbol, on=on, when=when))
    return found


async def upcoming_earnings(
    symbols: frozenset[str],
    client: httpx.AsyncClient,
    *,
    horizon: int = DEFAULT_HORIZON,
    today: date | None = None,
) -> tuple[dict[str, Earnings], tuple[date, ...]]:
    """Which of `symbols` report within `horizon` days.

    Returns `(found, failed_days)`. `failed_days` lists session days whose
    calendar page could not be read — the caller can tell "nothing scheduled"
    from "did not look".

    The earnings page is not shifted: `date=D` returns D's reports (checked
    again 2026-11-02: date=11-03 listed SM after the close, date=11-04 listed
    APA, date=11-05 listed VTRS and INSW before the open). The economic page
    is the one that answers with the day before. `EARNINGS_DATE_OFFSET` is 0.
    """
    start = today or date.today()
    found: dict[str, Earnings] = {}
    failed: list[date] = []
    for day in _session_days(start, horizon):
        query_day = day + EARNINGS_DATE_OFFSET
        try:
            response = await client.get(
                EARNINGS_URL, params={"date": query_day.isoformat()},
                headers=HEADERS, timeout=15.0,
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            failed.append(day)
            continue
        for symbol, event in parse_earnings(payload, day, symbols).items():
            found.setdefault(symbol, event)
    return found, tuple(failed)


def apply_moomoo(found: dict[str, Earnings], failed: tuple[date, ...],
                 extra: dict[str, tuple[date, str]], covered: tuple[date, ...],
                 ) -> tuple[dict[str, Earnings], tuple[date, ...]]:
    """Keep Nasdaq's row. A day Moomoo actually read leaves the failed list."""
    merged = dict(found)
    seen = set(covered)
    for symbol, (on, when) in extra.items():
        merged.setdefault(symbol, Earnings(symbol=symbol, on=on, when=when))
    return merged, tuple(day for day in failed if day not in seen)


async def with_moomoo(
    symbols: frozenset[str],
    client: httpx.AsyncClient,
    *,
    horizon: int = DEFAULT_HORIZON,
    today: date | None = None,
    backup=None,
) -> tuple[dict[str, Earnings], tuple[date, ...]]:
    """Nasdaq first. Moomoo fills the session days Nasdaq did not return.

    The 10-7 close exit does not call this. A rules-book name opened on or
    after the book date is not sold because a report date appears.
    """
    start = today or date.today()
    found, failed = await upcoming_earnings(symbols, client, horizon=horizon, today=start)
    if not failed:
        return found, failed
    filler = backup
    if filler is None:
        from scanner.sources.moomoo_quote import earnings_backup
        filler = earnings_backup
    try:
        extra, covered = filler(symbols, failed, start)
    except Exception:
        return found, failed
    return apply_moomoo(found, failed, extra, covered)


def is_market_moving(event_name: str) -> bool:
    """Whether an economic release moves the whole tape."""
    return any(token.lower() in event_name.lower() for token in MARKET_MOVING)


def economic_query_date(day: date) -> date:
    return day + ECONOMIC_DATE_OFFSET


def parse_economic(payload: dict, *, country: str = US) -> tuple[str, ...]:
    """Pure parse: the day's market-moving releases for `country`, each once."""
    rows = (payload.get("data") or {}).get("rows") or []
    names = (str(row.get("eventName") or row.get("event") or "").strip() for row in rows
             if str(row.get("country") or "") == country)
    seen, out = set(), []
    for n in names:
        if n and is_market_moving(n) and not SUB_SERIES.search(n) and n.lower() not in seen:
            seen.add(n.lower())
            out.append(n)
    return tuple(out)


def _value(raw: object) -> str:
    text = str(raw or "").replace("&nbsp;", " ").strip()
    return text


def parse_releases(payload: dict, *, country: str = US) -> tuple[Release, ...]:
    """Pure parse: the day's market-moving and sector releases with their numbers."""
    rows = (payload.get("data") or {}).get("rows") or []
    found: dict[str, dict[str, list[str]]] = {}
    for row in rows:
        name = str(row.get("eventName") or row.get("event") or "").strip()
        if (str(row.get("country") or "") != country or not name or SUB_SERIES.search(name)
                or not (is_market_moving(name)
                        or any(t.lower() in name.lower() for t in SECTOR_EVENTS))):
            continue
        slot = found.setdefault(name, {"actual": [], "consensus": [], "previous": []})
        for key in slot:
            if value := _value(row.get(key)):
                slot[key].append(value)
    return tuple(Release(name=n, **{k: " / ".join(v) for k, v in vals.items()})
                 for n, vals in found.items())


def _number(text: str) -> float | None:
    raw = text.split("/")[0].strip().replace(",", "").rstrip("%")
    scale = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}.get(raw[-1:].upper(), 1.0) if raw else 1.0
    raw = raw[:-1] if scale != 1.0 else raw
    try:
        return float(raw) * scale
    except ValueError:
        return None


def surprise(actual: str, consensus: str) -> str:
    """above / below / in line, or "" when either number is missing."""
    a, c = _number(actual or ""), _number(consensus or "")
    if a is None or c is None:
        return ""
    if abs(a - c) <= 1e-9 * max(1.0, abs(c)):
        return "in line"
    return "above" if a > c else "below"


def parse_global(payload: dict) -> tuple[Release, ...]:
    """Pure parse: the day's foreign central-bank decisions, named with their country."""
    rows = (payload.get("data") or {}).get("rows") or []
    out: dict[str, Release] = {}
    for row in rows:
        key = (str(row.get("country") or ""), str(row.get("eventName") or "").strip())
        if key in GLOBAL_CENTRAL_BANKS:
            name = f"{key[0]}: {key[1]}"
            out.setdefault(name, Release(name=name, actual=_value(row.get("actual")),
                                         consensus=_value(row.get("consensus")),
                                         previous=_value(row.get("previous"))))
    return tuple(out.values())


def parse_ipos(payload: dict, *, min_dollars: float = 1e9) -> tuple[tuple[date, str], ...]:
    """Pure parse: upcoming IPOs raising at least `min_dollars`, as (day, text)."""
    rows = ((((payload.get("data") or {}).get("upcoming") or {}).get("upcomingTable") or {})
            .get("rows") or [])
    out = []
    for row in rows:
        try:
            dollars = float(str(row.get("dollarValueOfSharesOffered") or "").replace("$", "")
                            .replace(",", ""))
            month, day, year = (int(x) for x in str(row.get("expectedPriceDate")).split("/"))
        except (ValueError, TypeError):
            continue
        if dollars >= min_dollars:
            out.append((date(year, month, day),
                        f"IPO: {row.get('companyName', '?')} ({row.get('proposedTickerSymbol', '?')}), "
                        f"about ${dollars / 1e9:.1f}B raised"))
    return tuple(sorted(out))


async def big_ipos(client: httpx.AsyncClient, months: Iterable[date],
                   *, min_dollars: float = 1e9) -> tuple[tuple[date, str], ...]:
    """Big upcoming IPOs for the given months (one request per month)."""
    found: list[tuple[date, str]] = []
    for m in months:
        try:
            response = await client.get(IPO_URL, params={"date": f"{m:%Y-%m}"},
                                        headers=HEADERS, timeout=15.0)
            response.raise_for_status()
            found += parse_ipos(response.json(), min_dollars=min_dollars)
        except (httpx.HTTPError, ValueError):
            continue
    return tuple(sorted(set(found)))


def parse_sector_events(payload: dict, *, country: str = US
                        ) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Pure parse: the day's sector-moving releases as (name, sectors), each once."""
    rows = (payload.get("data") or {}).get("rows") or []
    out: dict[str, tuple[str, ...]] = {}
    for row in rows:
        name = str(row.get("eventName") or row.get("event") or "").strip()
        if str(row.get("country") or "") != country or not name or SUB_SERIES.search(name):
            continue
        for token, sectors in SECTOR_EVENTS.items():
            if token.lower() in name.lower():
                out.setdefault(name, sectors)
    return tuple(out.items())


async def scheduled_events(client: httpx.AsyncClient, *, on: date
                           ) -> tuple[tuple[Release, ...], tuple[tuple[str, tuple[str, ...]], ...]]:
    """(market-wide releases with their numbers, sector releases) for one day,
    from one request."""
    try:
        response = await client.get(
            ECONOMIC_URL, params={"date": economic_query_date(on).isoformat()},
            headers=HEADERS, timeout=15.0,
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return (), ()
    wide = set(parse_economic(payload))
    return (tuple(r for r in parse_releases(payload) if r.name in wide) + parse_global(payload),
            parse_sector_events(payload))


async def releases_on(client: httpx.AsyncClient, *, on: date) -> tuple[Release, ...]:
    """Every market-wide and sector release on `on`, with its numbers."""
    try:
        response = await client.get(
            ECONOMIC_URL, params={"date": economic_query_date(on).isoformat()},
            headers=HEADERS, timeout=15.0,
        )
        response.raise_for_status()
        return parse_releases(response.json())
    except (httpx.HTTPError, ValueError):
        return ()


async def market_moving_events(
    client: httpx.AsyncClient, *, on: date | None = None
) -> tuple[str, ...]:
    """Today's releases big enough to gap the index, not just one stock."""
    day = on or date.today()
    try:
        response = await client.get(
            ECONOMIC_URL, params={"date": economic_query_date(day).isoformat()},
            headers=HEADERS, timeout=15.0,
        )
        response.raise_for_status()
        return parse_economic(response.json())
    except (httpx.HTTPError, ValueError):
        return ()
