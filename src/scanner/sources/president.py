"""What the President said, signed and is scheduled to do (WORKLIST §52).

Sources, each failing quietly to an empty tuple (a feed outage must never stop
the brief, and the brief says what it could not read):
  - Truth Social posts via the public trumpstruth.org RSS mirror -- THIRD-PARTY,
    labelled as such wherever it is shown.
  - whitehouse.gov RSS: presidential actions, briefings & statements, news.
  - The official public schedule as published by factba.se (usually only the
    day ahead: nobody publishes his moves weeks out).

Posts are kept IN FULL. A cut sentence once read "we will not be attacking
Iran at any ..." and was narrated as "at any time"; the post said "at any time
prior to the Midterm Elections".

Untrusted XML goes through defusedxml, as in yahoo_rss.
"""

from __future__ import annotations

import email.utils
import html
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

import httpx
from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException

from scanner.models import Frozen

TRUTH_FEED = "https://www.trumpstruth.org/feed"
WH_FEEDS = {
    "action": "https://www.whitehouse.gov/presidential-actions/feed/",
    "statement": "https://www.whitehouse.gov/briefings-statements/feed/",
    "news": "https://www.whitehouse.gov/news/feed/",
}
SCHEDULE_URL = "https://media-cdn.factba.se/rss/json/trump/calendar-full.json"
HEADERS = {"User-Agent": "Mozilla/5.0"}
#: Schedule lines that are logistics, not decisions.
LOGISTICS = ("departs", "arrives", "Executive Time", "Pool Call Time", " lid")
#: Image- and video-only posts carry no text, only this placeholder.
NO_TITLE = "[No Title]"


class Post(Frozen):
    when: datetime
    source: str          # truth | white_house:<kind>
    text: str
    link: str = ""


class ScheduleItem(Frozen):
    day: date
    time: str
    details: str


def _clean(raw: str) -> str:
    return " ".join(html.unescape(raw or "").split())


def parse_rss(xml: str, *, source: str, now: datetime, hours: float = 24) -> tuple[Post, ...]:
    """Text posts newer than `hours`, newest first. Never raises."""
    try:
        root = ElementTree.fromstring(xml)
    except (ElementTree.ParseError, DefusedXmlException, ValueError, TypeError):
        return ()
    cutoff = now - timedelta(hours=hours)
    out = []
    for item in root.iter("item"):
        text = _clean(item.findtext("title") or "")
        try:
            when = email.utils.parsedate_to_datetime((item.findtext("pubDate") or "").strip())
        except (TypeError, ValueError):
            continue
        if not text or text.startswith(NO_TITLE) or when < cutoff:
            continue
        out.append(Post(when=when, source=source, text=text,
                        link=_clean(item.findtext("link") or "")))
    return tuple(sorted(out, key=lambda p: p.when, reverse=True))


def parse_schedule(rows: Iterable[dict[str, Any]], *, days: set[date]) -> tuple[ScheduleItem, ...]:
    """The official schedule entries for `days` (pool-report chatter dropped)."""
    out = []
    for row in rows or ():
        try:
            day = date.fromisoformat(str(row.get("date", "")))
        except ValueError:
            continue
        details = _clean(str(row.get("details") or "")).removeprefix("TBD:").strip()
        if (day in days and row.get("type") == "President Schedule" and details
                and not any(word in details for word in LOGISTICS)):
            out.append(ScheduleItem(day=day, time=str(row.get("time_formatted") or "TBD"),
                                    details=details))
    return tuple(out)


async def _get(client: httpx.AsyncClient, url: str) -> str:
    try:
        response = await client.get(url, headers=HEADERS, timeout=20.0, follow_redirects=True)
        response.raise_for_status()
        return response.text
    except httpx.HTTPError:
        return ""


async def collect(client: httpx.AsyncClient, *, now: datetime | None = None
                  ) -> tuple[tuple[Post, ...], int, int]:
    """Truth Social (24h) and White House releases (48h), newest first.

    Returns (posts, feeds that returned nothing, feeds tried). A feed outage is a
    count, not an exception: the position card says when the feed is down.
    """
    now = now or datetime.now(timezone.utc)
    sources = [("truth", TRUTH_FEED, 24.0),
               *[(f"white_house:{kind}", url, 48.0) for kind, url in WH_FEEDS.items()]]
    failed = 0
    found: list[Post] = []
    for source, url, hours in sources:
        xml = await _get(client, url)
        if not xml:
            failed += 1
            continue
        found.extend(parse_rss(xml, source=source, now=now, hours=hours))
    seen, out = set(), []
    for post in sorted(found, key=lambda p: p.when, reverse=True):
        if post.text.lower() not in seen:
            seen.add(post.text.lower())
            out.append(post)
    return tuple(out), failed, len(sources)


async def posts(client: httpx.AsyncClient, *, now: datetime | None = None) -> tuple[Post, ...]:
    """Truth Social (24h) and White House releases (48h), newest first."""
    found, _, _ = await collect(client, now=now)
    return found


async def schedule(client: httpx.AsyncClient, *, days: set[date]) -> tuple[ScheduleItem, ...]:
    try:
        response = await client.get(SCHEDULE_URL, headers=HEADERS, timeout=30.0)
        response.raise_for_status()
        return parse_schedule(response.json(), days=days)
    except (httpx.HTTPError, ValueError):
        return ()
