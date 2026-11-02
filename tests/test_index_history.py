"""Point-in-time index membership tests.

Survivorship bias is the quiet killer of momentum backtests: screen today's
index members over the last five years and you have pre-selected companies
that survived those five years. These tests pin the reconstruction that
undoes it — walking Wikipedia's add/remove log backwards from today.

The property that matters: a company deleted from the index after collapsing
must be present in the universe on the dates it was actually a member.
"""

from __future__ import annotations

from datetime import date

import pytest

from scanner.sources.index_history import (
    IndexHistoryError,
    MembershipChange,
    build,
    clean_cell,
    parse_changes,
    parse_members,
)

# Two row styles on purpose: the live page mixes inline "||" rows with
# one-cell-per-line rows, and a parser that only handles one silently drops
# half the history.
WIKITEXT = """
==S&P 500 component stocks==
{| class="wikitable sortable" id="constituents"
|-
!Symbol
! Security !! GICS Sector !! GICS Sub-Industry !! Headquarters Location !! Date added !! CIK !! Founded
|-
|{{NyseSymbol|MMM}}
|[[3M]]|| Industrials || Industrial Conglomerates || [[Saint Paul, Minnesota]] || 1957-03-04 || 0000066740 || 1902
|-
|{{NasdaqSymbol|GOOGL}}
|[[Alphabet Inc.]] Class A|| Communication Services || Interactive Media || [[Mountain View, California]] || 2014-04-03 || 0001652044 || 1998
|-
|{{NasdaqSymbol|GOOG}}
|[[Alphabet Inc.]] Class C|| Communication Services || Interactive Media || [[Mountain View, California]] || 2006-04-03 || 0001652044 || 1998
|-
|{{NyseSymbol|BRK.B}}
|[[Berkshire Hathaway]]|| Financials || Multi-Sector Holdings || [[Omaha, Nebraska]] || 2010-02-16 || 0001067983 || 1839
|-
|{{NasdaqSymbol|PLTR}}
|[[Palantir Technologies]]|| Information Technology || Software || [[Denver]], Colorado || 2024-09-23 || 0001321655 || 2003
|}

==Selected changes to the list of S&P 500 components==
{|  class="wikitable sortable" id="changes"
|-
! data-sort-type="date" rowspan="2" | Effective Date
! colspan="2" | Added
! colspan="2" | Removed
! rowspan="2" | Reason
|-
! Ticker || Security || Ticker || Security
|-
|September 23, 2024 || PLTR || [[Palantir Technologies]] || AAL || [[American Airlines Group]] || Market cap change.<ref name="x">{{cite web |url=http://e.x |title=A || B}}</ref>
|-
|June 24, 2022
|BRK.B
|[[Berkshire Hathaway]]
|CERN
|[[Cerner]]
|Oracle acquired Cerner.
|-
|March 2, 2020 ||  ||  || XEC || [[Cimarex Energy]] || Pure deletion, no replacement.
|-
|April 3, 2014 || GOOG || [[Alphabet Inc.]] ||  ||  || Share class split, pure addition.
|}
"""


# ---- cell cleaning -----------------------------------------------------

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("{{NyseSymbol|MMM}}", "MMM"),
        ("{{NasdaqSymbol|BRK.B}}", "BRK.B"),
        ("[[Alphabet Inc.]] Class A", "Alphabet Inc. Class A"),
        ("[[AMD|Advanced Micro Devices]]", "Advanced Micro Devices"),
        ("'''BOLD'''", "BOLD"),
        ("  spaced  ", "spaced"),
        ("", ""),
    ],
)
def test_clean_cell_unwraps_markup(raw, expected):
    assert clean_cell(raw) == expected


# ---- current members ---------------------------------------------------

def test_parses_current_members_from_both_row_styles():
    members = parse_members(WIKITEXT)
    assert [m.symbol for m in members] == ["MMM", "GOOGL", "GOOG", "BRK.B", "PLTR"]
    assert members[0].sector == "Industrials"
    assert members[0].cik == "0000066740"


def test_missing_constituents_table_is_an_error():
    with pytest.raises(IndexHistoryError):
        parse_members("no tables here")


# ---- change log --------------------------------------------------------

def test_parses_changes_newest_first():
    changes = parse_changes(WIKITEXT)
    assert [c.effective for c in changes] == [
        date(2024, 9, 23),
        date(2022, 6, 24),
        date(2020, 3, 2),
        date(2014, 4, 3),
    ]


def test_parses_both_sides_of_a_replacement():
    latest = parse_changes(WIKITEXT)[0]
    assert latest.added == "PLTR"
    assert latest.removed == "AAL"


def test_a_ref_containing_a_cell_separator_does_not_shift_columns():
    """`{{cite web |title=A || B}}` inside a <ref> would split into extra
    cells and slide Reason into Removed if refs were not stripped first."""
    latest = parse_changes(WIKITEXT)[0]
    assert latest.removed == "AAL"
    assert "cite web" not in latest.reason


def test_pure_deletion_has_no_added_ticker():
    deletion = next(c for c in parse_changes(WIKITEXT) if c.removed == "XEC")
    assert deletion.added == ""


def test_pure_addition_has_no_removed_ticker():
    addition = next(c for c in parse_changes(WIKITEXT) if c.added == "GOOG")
    assert addition.removed == ""


# ---- membership reconstruction ----------------------------------------

def _history():
    return build(WIKITEXT)


def test_today_is_the_current_member_list():
    history = _history()
    assert history.members_on(date(2026, 1, 1)) == frozenset(
        {"MMM", "GOOGL", "GOOG", "BRK.B", "PLTR"}
    )


def test_a_deleted_company_reappears_before_its_deletion_date():
    """The whole point. AAL left the index in 2024; a 2023 screen must be
    able to buy it, and lose money on it."""
    members = _history().members_on(date(2023, 6, 1))
    assert "AAL" in members
    assert "PLTR" not in members


def test_change_applies_on_its_effective_date():
    history = _history()
    day_of = history.members_on(date(2024, 9, 23))
    day_before = history.members_on(date(2024, 9, 22))
    assert "PLTR" in day_of and "AAL" not in day_of
    assert "AAL" in day_before and "PLTR" not in day_before


def test_undo_stacks_across_multiple_changes():
    members = _history().members_on(date(2021, 1, 1))
    assert "CERN" in members   # removed 2022
    assert "AAL" in members    # removed 2024
    assert "BRK.B" not in members
    assert "PLTR" not in members


def test_pure_deletion_is_undone_without_inventing_an_addition():
    members = _history().members_on(date(2019, 1, 1))
    assert "XEC" in members


def test_pure_addition_is_undone_without_inventing_a_deletion():
    members = _history().members_on(date(2013, 1, 1))
    assert "GOOG" not in members
    assert "GOOGL" in members  # GOOGL's own add predates the log we carry


def test_symbols_ever_spans_the_whole_window():
    ever = _history().symbols_ever(date(2019, 1, 1), date(2026, 1, 1))
    assert {"AAL", "CERN", "XEC", "PLTR", "MMM"} <= ever


def test_symbols_ever_excludes_names_gone_before_the_window():
    ever = _history().symbols_ever(date(2023, 1, 1), date(2026, 1, 1))
    assert "AAL" in ever      # still a member into 2024
    assert "CERN" not in ever  # left in 2022, before the window


# ---- share classes -----------------------------------------------------

def test_share_classes_collapse_in_a_point_in_time_set():
    """GOOG and GOOGL are one bet. Ranking both double-counts it — the same
    regression `deduplicate_share_classes` fixes for the live universe."""
    history = _history()
    members = history.members_on(date(2026, 1, 1), collapse_share_classes=True)
    assert "GOOGL" in members
    assert "GOOG" not in members


def test_share_classes_are_kept_by_default():
    assert "GOOG" in _history().members_on(date(2026, 1, 1))


# ---- honesty guards ----------------------------------------------------

def test_reconstruction_reports_how_far_back_it_is_trustworthy():
    """Wikipedia's log is 'selected changes' — it thins out badly before the
    2000s. Callers need to know where the data stops being usable."""
    history = _history()
    assert history.oldest_change == date(2014, 4, 3)


def test_size_on_reports_the_reconstructed_member_count():
    """A reconstruction that drifts far from ~500 names has gone wrong; the
    count is the cheapest available smoke test."""
    assert _history().size_on(date(2023, 6, 1)) == 5


def test_empty_change_log_is_an_error():
    with pytest.raises(IndexHistoryError):
        parse_changes('{| class="wikitable" id="changes"\n|-\n! Ticker\n|}')


def test_membership_change_is_immutable():
    change = MembershipChange(effective=date(2020, 1, 1), added="A", removed="B")
    with pytest.raises(Exception):
        change.added = "C"  # type: ignore[misc]


# ---- recycled tickers --------------------------------------------------

def test_exit_dates_records_when_a_name_left():
    exits = _history().exit_dates(date(2019, 1, 1), date(2026, 1, 1))
    assert exits["AAL"] == date(2024, 9, 23)
    assert exits["CERN"] == date(2022, 6, 24)


def test_exit_dates_ignores_departures_outside_the_window():
    exits = _history().exit_dates(date(2023, 1, 1), date(2026, 1, 1))
    assert "CERN" not in exits


# ---- cell attributes ---------------------------------------------------
#
# The MidCap 400 and SmallCap 600 pages write cells as
# `| style="border-color:inherit;" | AA`. A parser written against the 500
# page alone returns that whole string as the ticker, which then 404s at the
# price feed instead of failing loudly — 403 of 1,504 symbols, silently.

def test_cell_attributes_are_stripped():
    assert clean_cell('style="border-color:inherit;" | AA') == "AA"
    assert clean_cell('data-sort-value="Apple" | Apple Inc.') == "Apple Inc."
    assert clean_cell("align=center | Industrials") == "Industrials"


def test_attribute_stripping_leaves_ordinary_cells_alone():
    assert clean_cell("{{NyseSymbol|MMM}}") == "MMM"
    assert clean_cell("[[AMD|Advanced Micro Devices]]") == "Advanced Micro Devices"
    assert clean_cell("Costco Wholesale Corp.") == "Costco Wholesale Corp."


def test_attribute_style_rows_parse_to_clean_symbols():
    wikitext = """
{| class="wikitable" id="constituents"
|-
!Symbol
! Security !! GICS Sector
|-
| style="border-color:inherit;" | AA
| style="border-color:inherit;" |[[Alcoa]]|| style="border-color:inherit;" |Materials
|}
{| class="wikitable" id="changes"
|-
! Effective Date
! Ticker || Security || Ticker || Security || Reason
|-
| style="border-color:inherit;" |March 4, 2024 || AA || [[Alcoa]] || ZZZ || [[Gone Inc]] || Swap.
|}
"""
    assert [m.symbol for m in parse_members(wikitext)] == ["AA"]
    assert parse_members(wikitext)[0].sector == "Materials"
    change = parse_changes(wikitext)[0]
    assert change.added == "AA" and change.removed == "ZZZ"


def test_template_backed_external_links_yield_a_bare_ticker():
    """The 600 page builds some tickers as an external link whose URL is a
    template: `[{{#invoke:Stock tickers/NYSE|GetURL|PRK}} PRK]`. Strip the
    template and a bare `[ PRK]` is left — which 404s at the price feed."""
    assert clean_cell("[{{#invoke:Stock tickers/NYSE|GetURL|PRK|exchange=AMEX}} PRK]") == "PRK"
    assert clean_cell("[https://example.com/x TMP]") == "TMP"


def test_external_link_stripping_leaves_plain_text_alone():
    assert clean_cell("Clearway Energy, Inc. (Class A)") == "Clearway Energy, Inc. (Class A)"
    assert clean_cell("{{NyseSymbol|CWEN.A}}") == "CWEN.A"


def test_a_double_pipe_cell_does_not_keep_a_stray_pipe():
    """GEO Group's row writes its sector as `||Industrials`. Stripping one
    leading pipe left `|Industrials`, which showed up as its own
    single-company sector on the sector board."""
    wikitext = """
{| class="wikitable" id="constituents"
|-
!Symbol
! Security !! GICS Sector
|-
|{{NyseSymbol|GEO}}
|[[GEO Group | GEO Group, Inc.]]
||Industrials
||Environmental & Facilities Services
|}
{| class="wikitable" id="changes"
|-
! Effective Date
! Ticker || Security || Ticker || Security || Reason
|-
|March 4, 2024 || AA || [[Alcoa]] || ZZZ || [[Gone]] || Swap.
|}
"""
    member = parse_members(wikitext)[0]
    assert member.symbol == "GEO"
    assert member.sector == "Industrials"


def test_ordinary_single_pipe_rows_still_split_normally():
    assert clean_cell("Industrials") == "Industrials"


# ---- where the documents come from, and how long a copy is trusted ----------

def test_the_sp500_change_log_is_read_from_its_own_article():
    """Wikipedia moved it off the list page in October 2026; without this the
    S&P 500 failed to load and only the 400 and 600 were scanned."""
    from scanner.sources.index_history import SP400, SP500, SP600
    assert SP500.changes_page == "Historical components of the S&P 500"
    assert not SP400.changes_page and not SP600.changes_page


async def test_both_pages_are_fetched_and_joined(monkeypatch):
    from scanner.sources import index_history as ih
    seen = []

    async def fake(client, page):
        seen.append(page)
        return f"<{page}>"
    monkeypatch.setattr(ih, "fetch_page", fake)
    text = await ih.fetch_wikitext(None, ih.SP500)
    assert seen == [ih.SP500.page, ih.SP500.changes_page]
    assert ih.SP500.page in text and ih.SP500.changes_page in text


def test_a_day_old_cache_is_stale(tmp_path):
    import os
    import time
    from scanner.sources.index_history import is_stale
    path = tmp_path / "x.txt"
    path.write_text("x")
    assert not is_stale(path)
    old = time.time() - 2 * 86_400
    os.utime(path, (old, old))
    assert is_stale(path)


async def test_a_failed_refresh_falls_back_to_the_cached_copy(tmp_path, monkeypatch, capsys):
    import os
    import time
    from scanner.sources import index_history as ih

    good = await _good_source(ih)
    spec = ih.SP400.model_copy(update={"key": "t"})
    monkeypatch.setattr(ih, "CACHE_DIR", tmp_path)
    spec.cache_path.write_text(good, encoding="utf-8")
    old = time.time() - 2 * 86_400
    os.utime(spec.cache_path, (old, old))

    async def broken(client, s):
        raise ih.IndexHistoryError("wikipedia moved the table")
    monkeypatch.setattr(ih, "fetch_wikitext", broken)

    history = await ih.load(None, spec=spec)
    assert history.members
    assert "using cached copy" in capsys.readouterr().out


async def test_a_failed_refresh_with_no_cache_still_raises(tmp_path, monkeypatch):
    from scanner.sources import index_history as ih
    spec = ih.SP400.model_copy(update={"key": "none"})
    monkeypatch.setattr(ih, "CACHE_DIR", tmp_path)

    async def broken(client, s):
        raise ih.IndexHistoryError("down")
    monkeypatch.setattr(ih, "fetch_wikitext", broken)
    with pytest.raises(ih.IndexHistoryError):
        await ih.load(None, spec=spec)


async def _good_source(ih) -> str:
    return WIKITEXT
