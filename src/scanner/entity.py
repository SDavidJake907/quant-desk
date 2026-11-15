"""Entity resolution — deciding whether text is actually about a company.

The professional term for this problem is *entity resolution*. Vendors like
RavenPack build 12M-entity graphs to solve it. We solve the narrow US-equity
case with the SEC's own name registry, which is free and authoritative.

Design rule learned the hard way: a bare ticker match is NOT evidence.
"Novavax (NVDA)" contains the string NVDA but is about NVAX. So we require a
COMPANY NAME match; the ticker only strengthens an existing name hit.
"""

from __future__ import annotations

import re

from pydantic import ConfigDict

from scanner.models import Frozen, NewsItem

# Corporate suffixes carry no identifying signal — "NVIDIA CORP" -> "nvidia".
_SUFFIXES = {
    "corp", "corporation", "inc", "incorporated", "co", "company",
    "ltd", "limited", "plc", "lp", "llc", "holdings", "holding",
    "group", "sa", "nv", "ag", "the", "class", "common", "stock",
}
#: A joiner, not a name. Left in, "ELI LILLY & Co" becomes "eli lilly &", which
#: never appears in a headline that says "Eli Lilly".
_JOINERS = {"&", "and"}

_WORD = re.compile(r"[a-z0-9&]+")
#: The wire tags Merck KGaA (MilliporeSigma) onto US MRK. "Merck" is in both names.
_OTHER_COMPANY = {"MRK": ("merck kgaa", "milliporesigma")}


class Entity(Frozen):
    """A resolved company. Aliases are what we actually match text against."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ticker: str
    cik: int
    official_name: str
    aliases: tuple[str, ...]

    @property
    def cik_padded(self) -> str:
        """EDGAR requires the zero-padded 10-digit form."""
        return f"{self.cik:010d}"


def _core_name(name: str) -> str:
    """Strip corporate boilerplate down to the distinctive part of the name."""
    words = [w for w in _WORD.findall(name.lower()) if w not in _SUFFIXES and w not in _JOINERS]
    return " ".join(words)


def build_entity(ticker: str, cik: int, official_name: str) -> Entity:
    """Derive the alias set once, at resolution time."""
    ticker = ticker.upper()
    core = _core_name(official_name)

    aliases = {official_name.lower(), core}
    # First token is usually the brand: "advanced micro devices" -> "advanced".
    # Only useful when distinctive, so require length > 3.
    head = core.split(" ")[0] if core else ""
    if len(head) > 3:
        aliases.add(head)

    return Entity(
        ticker=ticker,
        cik=cik,
        official_name=official_name,
        aliases=tuple(sorted(a for a in aliases if a)),
    )


def _contains_word(haystack: str, needle: str) -> bool:
    """Word-boundary match so 'co' cannot match inside 'company'."""
    if not needle:
        return False
    return re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", haystack) is not None


def mentions_entity(text: str, entity: Entity) -> bool:
    """True if the company is named in the text.

    Deliberately name-driven. A bare ticker is not sufficient evidence, which
    is exactly what rejects 'Novavax (NVDA)' from an NVDA scan.
    """
    lowered = text.lower()
    return any(_contains_word(lowered, alias) for alias in entity.aliases)


def _about_company(text: str, entity: Entity) -> bool:
    """The company is named, and the text is not the other firm that shares the word."""
    if not mentions_entity(text, entity):
        return False
    lowered = text.lower()
    others = _OTHER_COMPANY.get(entity.ticker, ())
    if any(phrase in lowered for phrase in others):
        return entity.ticker == "MRK" and ("merck & co" in lowered or "merck and co" in lowered)
    return True


def for_symbol(items: list[dict] | tuple[dict, ...], ticker: str,
               entity: Entity | None, *, limit: int | None = None) -> list[dict]:
    """Alpaca news for one symbol, newest first.

    A story is kept when its tag list contains the ticker. When `entity` is
    given, the headline or summary must also name that company, so a shared
    ticker (IEX the Indian exchange, on IDEX) and a listicle about someone else
    do not count. `entity` None keeps the tag: the caller has no name to check,
    which is how the index funds stay on the wire.
    """
    key = ticker.upper().replace("-", ".")
    seen: set[object] = set()
    ordered: list[dict] = []
    for item in items:
        ident = item.get("id") or (item.get("headline"), item.get("created_at"))
        if ident in seen:
            continue
        seen.add(ident)
        ordered.append(item)
    ordered.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    kept: list[dict] = []
    headlines: set[str] = set()
    for item in ordered:
        tagged = {str(s).upper().replace("-", ".") for s in (item.get("symbols") or [])}
        if key not in tagged:
            continue
        headline = str(item.get("headline") or "").strip()
        if not headline or headline in headlines:
            continue
        if entity is not None:
            text = f"{headline} {item.get('summary') or ''}"
            if not _about_company(text, entity):
                continue
        headlines.add(headline)
        kept.append(item)
        if limit is not None and len(kept) >= limit:
            break
    return kept


def prefilter(
    items: tuple[NewsItem, ...], entity: Entity
) -> tuple[NewsItem, ...]:
    """Drop items that never name the company, before any LLM call.

    Deterministic, microsecond-fast, and unit-testable — so it does the bulk
    rejection and the model only sees plausible candidates.
    """
    return tuple(
        item
        for item in items
        if mentions_entity(f"{item.title} {item.summary}", entity)
    )
