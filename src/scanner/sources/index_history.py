"""Point-in-time S&P index membership, reconstructed from Wikipedia change logs.

WHY THIS EXISTS
A backtest run over today's index members is not a backtest of the index. It
is a backtest of the companies that were good enough to still be in it — the
losers were deleted and are invisible. That is survivorship bias, and for a
momentum screen it flatters results specifically, because the names that
cratered hard enough to be dropped are exactly the ones a trend screen would
have been holding on the way down.

No amount of clever code fixes that. Only historical membership does.

COVERAGE
The S&P 500, MidCap 400 and SmallCap 600 pages share a table layout, so one
parser serves all three — 1,504 unique symbols with full GICS sectors. All
three reconstruct to within 2% of their fixed size back to 2021, measured by
`membership_drift`. Do not take that on trust for a new index: run the drift
audit, because a plausible-looking reconstruction is exactly what a subtly
broken parser produces (this one once "proved" the 400's log was unusable when
the real fault was unstripped cell attributes).

HOW IT WORKS
Each page carries two tables: today's members,
and a log of "AAA replaces BBB on <date>" changes. Membership on any past
date is today's list with every later change undone, newest first:

    added ticker   -> take it back out
    removed ticker -> put it back in

WHAT THIS DOES NOT FIX

  1. The log is titled "SELECTED changes". It is dense from roughly 2000
     onward and sparse before that. `oldest_change` and `size_on` exist so
     callers can see where the reconstruction stops being trustworthy —
     a reconstructed year that drifts far from ~500 names is telling you the
     log has holes, not that the index shrank.

  2. Reconstruction gives you the ticker. It does not give you price history
     for it. Yahoo drops or reuses symbols of delisted companies, so some
     resurrected names will simply have no bars. Coverage that is worse for
     failed companies than for surviving ones is survivorship bias leaking
     back in through the data source — measure it, report it, do not assume
     it is small.

  3. Tickers are as-of-removal. A company that changed ticker before being
     deleted is recorded under the later one.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
from dateutil import parser as date_parser

from scanner.config import settings
from scanner.models import Frozen

WIKI_API = "https://en.wikipedia.org/w/api.php"
CACHE_DIR = Path("data")

# Wikimedia 403s generic clients; their robot policy wants a descriptive
# agent with contact details, the same deal SEC EDGAR insists on.
_CONSTITUENTS_TABLE = 'id="constituents"'
_CHANGES_TABLE = 'id="changes"'

_REF = re.compile(r"<ref[^>]*/>|<ref[^>]*>.*?</ref>", re.DOTALL | re.IGNORECASE)
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_SYMBOL_TEMPLATE = re.compile(r"\{\{\w*Symbol\|([^}|]+)\}\}")
_TEMPLATE = re.compile(r"\{\{[^{}]*\}\}")
_LINK = re.compile(r"\[\[(?:[^\]|]*\|)?([^\]|]*)\]\]")
_TAG = re.compile(r"<[^>]+>")
#: External-link syntax `[url label]`. The SmallCap 600 page builds some
#: tickers as `[{{#invoke:Stock tickers/NYSE|GetURL|PRK}} PRK]`, so once the
#: template is stripped a bare `[ PRK]` is left behind — a ticker that 404s.
_EXTERNAL_LINK = re.compile(r"\[\s*(?:\S+\s+)?([^\]]*)\]")
#: A wikitable cell may carry HTML attributes ahead of a single pipe:
#: `| style="border-color:inherit;" | AA`. The 400 and 600 pages use them
#: heavily and the 500 page does not, so a parser written against the 500
#: alone silently returns `STYLE="..." | AA` as the ticker.
_CELL_ATTRS = re.compile(
    r"""^\s*(?:[A-Za-z-]+\s*=\s*(?:"[^"]*"|'[^']*'|[^\s|]+)\s*)+\|(?!\|)"""
)


class IndexHistoryError(RuntimeError):
    """Raised when membership history cannot be loaded or parsed."""


class IndexSpec(Frozen):
    """One index: where to read it, and whether its past can be trusted.

    All three S&P pages share a table layout, so one parser serves them. They
    do NOT share data quality — see `point_in_time`.
    """

    key: str
    label: str
    page: str
    #: Whether the change log reconstructs history faithfully. Measured, not
    #: assumed: a fixed-size index whose reconstructed membership drifts has a
    #: log with holes in it. See MEMBERSHIP_DRIFT_TOLERANCE.
    point_in_time: bool = True
    #: Where the change log lives, when it is not on `page`. In October 2026
    #: Wikipedia moved the S&P 500's log to its own article; the 400 and 600
    #: logs are still on their list pages. Both documents are fetched and
    #: cached together, so the parser sees one text either way.
    changes_page: str = ""

    @property
    def cache_path(self) -> Path:
        return CACHE_DIR / f"{self.key}_wikitext.txt"


SP500 = IndexSpec(
    key="sp500", label="S&P 500", page="List of S&P 500 companies",
    changes_page="Historical components of the S&P 500",
)
SP400 = IndexSpec(
    key="sp400", label="S&P MidCap 400", page="List of S&P 400 companies"
)
SP600 = IndexSpec(
    key="sp600", label="S&P SmallCap 600", page="List of S&P 600 companies"
)

#: The S&P Composite 1500 — 1,504 unique symbols with full sector coverage.
COMPOSITE_1500 = (SP500, SP400, SP600)

#: How far a reconstructed member count may drift from today's before the
#: change log is considered unreliable.
MEMBERSHIP_DRIFT_TOLERANCE = 0.10


class Member(Frozen):
    symbol: str
    name: str = ""
    sector: str = ""
    cik: str = ""


class MembershipChange(Frozen):
    """One index change. Either side may be empty.

    S&P usually swaps one name for another, but spin-offs add without
    removing and acquisitions remove without adding. Assuming every row has
    both sides corrupts the reconstruction in both directions.
    """

    effective: date
    added: str = ""
    removed: str = ""
    reason: str = ""


# ---- wikitext parsing --------------------------------------------------

def clean_cell(raw: str) -> str:
    """Strip wiki markup down to the text a human sees.

    Cell attributes go first: they sit ahead of the content behind a single
    pipe, and leaving them in produces tickers like `STYLE="..." | AA`, which
    404 at the price feed rather than failing loudly.
    """
    text = _CELL_ATTRS.sub("", raw)
    text = _SYMBOL_TEMPLATE.sub(r"\1", text)
    text = _TEMPLATE.sub("", text)
    text = _LINK.sub(r"\1", text)
    # After the template above is gone, `[{{…}} PRK]` is a bare `[ PRK]`.
    text = _EXTERNAL_LINK.sub(r"\1", text)
    text = _TAG.sub("", text)
    return text.replace("'''", "").replace("''", "").strip()


def _table(wikitext: str, marker: str) -> str:
    start = wikitext.find(marker)
    if start < 0:
        raise IndexHistoryError(f"wikitext has no table matching {marker}")
    end = wikitext.find("\n|}", start)
    if end < 0:
        raise IndexHistoryError(f"table {marker} is never closed")
    return wikitext[start:end]


def _rows(table: str) -> list[list[str]]:
    """Split a wikitable into rows of cleaned cells.

    Refs go first and whole: `{{cite web |title=A || B}}` inside one would
    otherwise split into two cells and shift every column after it.
    """
    body = _COMMENT.sub("", _REF.sub("", table))
    rows: list[list[str]] = []
    for block in body.split("\n|-")[1:]:
        cells: list[str] = []
        header = False
        for line in block.splitlines():
            line = line.strip()
            if line.startswith("!"):
                header = True
                continue
            if not line.startswith("|") or line.startswith("|}"):
                continue
            # Strip ALL leading pipes, not one. Some rows write a cell as
            # `||Industrials` — legal enough in wikitext, and taking a single
            # character off leaves `|Industrials` as the sector, which then
            # becomes its own one-company sector in every downstream grouping.
            cells.extend(clean_cell(part) for part in line.lstrip("|").split("||"))
        if cells and not header:
            rows.append(cells)
    return rows


def parse_members(wikitext: str) -> tuple[Member, ...]:
    """Today's constituents, in page order."""
    rows = _rows(_table(wikitext, _CONSTITUENTS_TABLE))
    members = tuple(
        Member(
            symbol=row[0].upper(),
            name=row[1] if len(row) > 1 else "",
            sector=row[2] if len(row) > 2 else "",
            cik=row[6] if len(row) > 6 else "",
        )
        for row in rows
        if row and row[0]
    )
    if not members:
        raise IndexHistoryError("constituents table parsed to zero rows")
    return members


def parse_changes(wikitext: str) -> tuple[MembershipChange, ...]:
    """The change log, newest first.

    Rows that do not carry a parseable date are dropped rather than guessed
    at — a change applied on the wrong date is worse than a missing one,
    because it silently moves a company in and out of the wrong years.
    """
    changes: list[MembershipChange] = []
    for row in _rows(_table(wikitext, _CHANGES_TABLE)):
        if len(row) < 5 or not row[0]:
            continue
        try:
            effective = date_parser.parse(row[0]).date()
        except (ValueError, OverflowError):
            continue
        changes.append(
            MembershipChange(
                effective=effective,
                added=row[1].upper(),
                removed=row[3].upper(),
                reason=row[5] if len(row) > 5 else "",
            )
        )
    if not changes:
        raise IndexHistoryError("changes table parsed to zero usable rows")
    return tuple(sorted(changes, key=lambda c: c.effective, reverse=True))


# ---- reconstruction ----------------------------------------------------

class IndexHistory(Frozen):
    """Today's membership plus the log needed to walk it backwards."""

    members: tuple[Member, ...]
    changes: tuple[MembershipChange, ...]
    spec: IndexSpec = SP500

    @property
    def oldest_change(self) -> date:
        """Earliest logged change. Before this, `members_on` is a guess."""
        return self.changes[-1].effective

    @property
    def newest_change(self) -> date:
        return self.changes[0].effective

    def _keepers(self) -> dict[str, str]:
        """Map every share class to the one class that represents the company.

        Grouped by CIK, which identifies the filer — GOOG and GOOGL share
        one, a name match would not catch BRK.B, and ranking both listings
        turns one bet into two "ideas".
        """
        keeper_by_cik: dict[str, str] = {}
        mapping: dict[str, str] = {}
        for member in self.members:
            key = member.cik or f"name:{member.name.lower()}"
            keeper_by_cik.setdefault(key, member.symbol)
            mapping[member.symbol] = keeper_by_cik[key]
        return mapping

    def members_on(
        self, as_of: date, *, collapse_share_classes: bool = False
    ) -> frozenset[str]:
        """Index membership at the close of `as_of`.

        A change effective on `as_of` is treated as already applied, matching
        S&P's convention that the effective date is the first day the new
        member trades in the index.
        """
        symbols = {m.symbol for m in self.members}
        for change in self.changes:  # newest first; order matters
            if change.effective <= as_of:
                break
            if change.added:
                symbols.discard(change.added)
            if change.removed:
                symbols.add(change.removed)

        if collapse_share_classes:
            keeper = self._keepers()
            symbols = {s for s in symbols if keeper.get(s, s) == s}
        return frozenset(symbols)

    def size_on(self, as_of: date) -> int:
        """Reconstructed member count. Smoke test: it should barely move."""
        return len(self.members_on(as_of))

    def membership_drift(self, as_of: date) -> float:
        """How far the reconstruction at `as_of` strays from today's size.

        S&P indices are fixed-size by construction: the 500 holds 500 names in
        every year, the 400 holds 400. So a reconstruction that returns a
        different count has not discovered that the index changed size — it
        has revealed a problem, either in the change log or in the parser
        reading it. Both happen; this audit caught the parser.

        Small drift is normal and is not a defect: announced-but-not-effective
        changes and same-day swaps leave a name or two double-counted. All
        three indices sit under 2% back to 2021.
        """
        today = len(self.members)
        if not today:
            return 0.0
        return abs(self.size_on(as_of) - today) / today

    def reconstructs_reliably(
        self, as_of: date, *, tolerance: float = MEMBERSHIP_DRIFT_TOLERANCE
    ) -> bool:
        """Whether this index's history can be trusted back to `as_of`."""
        return self.spec.point_in_time and self.membership_drift(as_of) <= tolerance

    def exit_dates(self, start: date, end: date) -> dict[str, date]:
        """When each symbol last left the index within [start, end].

        Needed to catch recycled tickers: exchanges reassign the symbols of
        dead companies, so IHS Markit's INFO and Signature Bank's SBNY now
        return somebody else's prices. Screening those bars as if they were
        the original company is worse than having no data at all.
        """
        exits: dict[str, date] = {}
        for change in self.changes:  # newest first, so the first win stands
            if change.removed and start < change.effective <= end:
                exits.setdefault(change.removed, change.effective)
        return exits

    def symbols_ever(
        self, start: date, end: date, *, collapse_share_classes: bool = False
    ) -> frozenset[str]:
        """Every symbol that was a member at any point in [start, end].

        This is the set a backtest must fetch price history for — fetching
        only current members reintroduces exactly the bias this module removes.
        """
        ever = set(self.members_on(start, collapse_share_classes=collapse_share_classes))
        keeper = self._keepers() if collapse_share_classes else {}
        for change in self.changes:
            if not (start < change.effective <= end) or not change.added:
                continue
            if collapse_share_classes and keeper.get(change.added, change.added) != change.added:
                continue
            ever.add(change.added)
        return frozenset(ever)


def build(wikitext: str, *, spec: IndexSpec = SP500) -> IndexHistory:
    return IndexHistory(
        members=parse_members(wikitext),
        changes=parse_changes(wikitext),
        spec=spec,
    )


# ---- fetching ----------------------------------------------------------

def _wiki_user_agent() -> str:
    """Wikimedia's robot policy 403s clients that pose as a browser.

    It wants a descriptive agent with a contact address — the same demand SEC
    EDGAR makes, so reuse that setting rather than adding a second one to
    forget. Note this is deliberately NOT `settings.user_agent`, which is the
    browser string Yahoo needs; sending that here gets you blocked.
    """
    return f"{settings.sec_user_agent} python-httpx"


def parse_api_payload(payload: dict) -> str:
    text = (payload.get("parse") or {}).get("wikitext")
    if isinstance(text, dict):  # older formatversion nests under "*"
        text = text.get("*")
    if not isinstance(text, str) or not text.strip():
        raise IndexHistoryError("Wikipedia API returned no wikitext")
    return text


#: How long a cached membership document is trusted. Index changes are
#: announced days ahead and take effect on a known date, so a day-old copy is
#: at most one change behind. A copy that never expires is not: it silently
#: freezes the universe at whatever day it was first downloaded.
CACHE_MAX_AGE = timedelta(days=1)


async def fetch_wikitext(
    client: httpx.AsyncClient, spec: IndexSpec = SP500
) -> str:
    """The list page, plus the change-log page when it lives separately."""
    text = await fetch_page(client, spec.page)
    if spec.changes_page:
        text = f"{text}\n\n{await fetch_page(client, spec.changes_page)}"
    return text


async def fetch_page(client: httpx.AsyncClient, page: str) -> str:
    try:
        response = await client.get(
            WIKI_API,
            params={
                "action": "parse",
                "page": page,
                "prop": "wikitext",
                "format": "json",
                "formatversion": "2",
            },
            headers={"User-Agent": _wiki_user_agent(), "Accept": "application/json"},
            timeout=settings.http_timeout_s,
            follow_redirects=True,
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise IndexHistoryError(f"could not fetch index history: {exc}") from exc
    return parse_api_payload(payload)


async def load(
    client: httpx.AsyncClient, *, refresh: bool = False, spec: IndexSpec = SP500
) -> IndexHistory:
    """Load membership history, caching the raw wikitext to disk.

    The cache is the source document, not a parsed derivative: when the
    parser changes, old caches stay usable and the reconstruction stays
    auditable against what Wikipedia actually said.
    """
    cached = spec.cache_path
    if not refresh and cached.exists() and not is_stale(cached):
        return build(cached.read_text(encoding="utf-8"), spec=spec)

    try:
        wikitext = await fetch_wikitext(client, spec)
        history = build(wikitext, spec=spec)  # parse before caching; never cache garbage
    except IndexHistoryError as exc:
        # A stale universe is better than none: one Wikipedia outage or page
        # edit must not stop the scanner. But it is said out loud, because a
        # universe that quietly stops updating is how this cache went wrong.
        if not cached.exists():
            raise
        print(f"  ! {spec.label}: refresh failed ({exc}); using cached copy "
              f"from {_cache_date(cached)}")
        return build(cached.read_text(encoding="utf-8"), spec=spec)
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_text(wikitext, encoding="utf-8")
    return history


def is_stale(path: Path, now: datetime | None = None) -> bool:
    modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return (now or datetime.now(timezone.utc)) - modified > CACHE_MAX_AGE


def _cache_date(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d")
