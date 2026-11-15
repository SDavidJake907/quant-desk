"""Entity-resolution tests.

The Novavax case is a real bug caught in a live run on 2026-08-05: Yahoo's NVDA
feed served an article titled "Novavax (NVDA) ..." and the model accepted it
because the literal string NVDA appeared. Novavax is NVAX. That regression is
pinned below.
"""

from __future__ import annotations

from datetime import datetime, timezone

from scanner.entity import build_entity, for_symbol, mentions_entity, prefilter
from scanner.models import NewsItem

NVDA = build_entity("NVDA", 1045810, "NVIDIA CORP")
AAPL = build_entity("AAPL", 320193, "Apple Inc.")
AMD = build_entity("AMD", 2488, "Advanced Micro Devices, Inc.")


def _item(title: str, summary: str = "") -> NewsItem:
    return NewsItem(
        id=title,
        source="test",
        queried_ticker="NVDA",
        title=title,
        summary=summary,
        fetched_at=datetime(2026, 8, 5, tzinfo=timezone.utc),
    )


def test_strips_corporate_suffixes_from_aliases():
    assert "nvidia" in NVDA.aliases
    assert "advanced micro devices" in AMD.aliases
    lly = build_entity("LLY", 59478, "ELI LILLY & Co")
    assert "eli lilly" in lly.aliases
    assert mentions_entity("Eli Lilly raises its outlook", lly)
    iex = build_entity("IEX", 832101, "IDEX CORP /DE/")
    assert mentions_entity("IDEX raises guidance", iex)
    assert not mentions_entity("Indian Energy Exchange Reports Q2 FY27 Financial Results", iex)


def test_pads_cik_for_edgar():
    assert NVDA.cik_padded == "0001045810"


def test_matches_company_name():
    assert mentions_entity("Nvidia raises guidance", NVDA)
    assert mentions_entity("Apple's India sales cross $10 billion", AAPL)


def test_rejects_bare_ticker_without_name():
    """The Novavax regression: NVDA appears, NVIDIA does not."""
    title = "Novavax (NVDA) Could Be 44% Undervalued Following Its Licensing Pivot"
    assert not mentions_entity(title, NVDA)


def test_rejects_unrelated_company():
    assert not mentions_entity("Where Will Bitcoin Be in 3 Years?", NVDA)


def test_rejects_supplier_story_for_wrong_ticker():
    """'Nvidia Partner Hon Hai' is not an Apple story."""
    assert not mentions_entity("Nvidia Partner Hon Hai's Sales Rise 54%", AAPL)


def test_word_boundaries_prevent_substring_hits():
    assert not mentions_entity("The applesauce market is growing", AAPL)


def test_prefilter_keeps_only_named_items():
    items = (
        _item("Nvidia B200 systems are sold out"),
        _item("Novavax (NVDA) Could Be 44% Undervalued"),
        _item("Where Will Bitcoin Be in 3 Years?"),
        _item("Chipmaker update", "Nvidia shipped more units"),
    )
    kept = prefilter(items, NVDA)
    assert len(kept) == 2
    assert all("ovavax" not in i.title for i in kept)


def test_for_symbol_is_newest_first_and_drops_another_company():
    iex = build_entity("IEX", 1, "IDEX CORP")
    items = [
        {"id": 1, "created_at": "2026-10-30T12:00:00Z", "symbols": ["IEX"],
         "headline": "Indian Energy Exchange Reports Q2"},
        {"id": 2, "created_at": "2026-10-29T12:00:00Z", "symbols": ["IEX"],
         "headline": "IDEX names a new plant"},
        {"id": 3, "created_at": "2026-10-31T12:00:00Z", "symbols": ["IEX"],
         "headline": "IDEX updates its outlook"},
    ]
    assert [item["id"] for item in for_symbol(items, "IEX", iex)] == [3, 2]
    spy = for_symbol(items[:1], "IEX", None)
    assert [item["id"] for item in spy] == [1]
    mrk = build_entity("MRK", 310158, "Merck & Co., Inc.")
    other = [{"id": 9, "created_at": "2026-10-30T21:01:00Z", "symbols": ["MRK"],
              "headline": "MilliporeSigma Marks 10th Anniversary",
              "summary": "MilliporeSigma, the life science business of Merck KGaA, celebrates."}]
    own = [{"id": 8, "created_at": "2026-10-30T22:48:00Z", "symbols": ["MRK"],
            "headline": "JP Morgan Maintains Overweight on Merck & Co, Raises Price Target to $175"}]
    assert for_symbol(other, "MRK", mrk) == []
    assert [item["id"] for item in for_symbol(own, "MRK", mrk)] == [8]


def test_prefilter_is_immutable_and_returns_new_tuple():
    items = (_item("Nvidia news"),)
    assert prefilter(items, NVDA) is not items
