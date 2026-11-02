"""Historical earnings announcement dates, from SEC 8-K item 2.02.

Item 2.02 is "Results of Operations and Financial Condition" — the 8-K a
company files when it releases earnings. The filing date is the announcement
date as filed, not a scraped estimate. EDGAR's `recent` block holds roughly the
last thousand filings, which for most S&P 500 companies reaches back six to
eight years.

THE ONE THING TO KNOW ABOUT THESE DATES
A company must file within four business days, and most file the same day as
the press release — but not all. A release after Tuesday's close can be filed
Wednesday. Anything that measures the market's reaction should therefore start
its window a day BEFORE the filing date, or it will miss the reaction it is
looking for.
"""

from __future__ import annotations

import datetime as dt

import httpx

from scanner.config import settings

SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"


def parse_earnings_dates(recent: dict) -> tuple[dt.date, ...]:
    """Filing dates of every 8-K carrying item 2.02, oldest first."""
    forms = recent.get("form", [])
    items = recent.get("items", [])
    dates = recent.get("filingDate", [])
    out: set[dt.date] = set()
    for i, form in enumerate(forms):
        if form != "8-K" or i >= len(dates):
            continue
        if "2.02" not in (items[i] if i < len(items) else ""):
            continue
        try:
            out.add(dt.date.fromisoformat(dates[i]))
        except ValueError:
            continue
    return tuple(sorted(out))


async def earnings_dates(cik: int, client: httpx.AsyncClient) -> tuple[dt.date, ...]:
    """Announcement dates for one company. Empty on any failure — a missing
    company shrinks the sample, it must not stop the study."""
    try:
        response = await client.get(
            SUBMISSIONS.format(cik=str(cik).zfill(10)),
            headers={"User-Agent": settings.sec_user_agent},
            follow_redirects=True, timeout=30,
        )
        if response.status_code != 200:
            return ()
        recent = response.json()["filings"]["recent"]
    except (httpx.HTTPError, ValueError, KeyError):
        return ()
    return parse_earnings_dates(recent)
