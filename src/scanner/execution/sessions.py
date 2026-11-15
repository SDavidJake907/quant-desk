"""Which US equity session is it? (WORKLIST §59)

Alpaca 24/5 (docs: 24/5 Trading): premarket 04:00-09:30 ET, regular 09:30-16:00,
after-hours 16:00-20:00, overnight 20:00-04:00 from Sunday evening to Friday
morning. Outside regular hours only LIMIT orders, time_in_force day/gtc,
extended_hours=true -- no stop or bracket orders.

`session_at` is PURE: give it the time and the broker's trading days (holidays
and weekends simply are not in the set). `current` asks the broker.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
REGULAR, PRE, AFTER, OVERNIGHT, CLOSED = "regular", "pre", "after", "overnight", "closed"
EXTENDED = (PRE, AFTER, OVERNIGHT)


def accepts_due_orders(session: str) -> bool:
    """Regular hours, and after-hours (16:00-20:00 ET).

    A buy or a sell that is already due may go out in those two sessions.
    Premarket and overnight stay closed to those orders."""
    return session in (REGULAR, AFTER)


def session_at(now: datetime, trading_days: Iterable[date]) -> str:
    days = set(trading_days)
    et = now.astimezone(ET)
    d, t = et.date(), et.time()
    if t < time(4, 0):                               # the overnight that began last evening
        return OVERNIGHT if d in days else CLOSED
    if t >= time(20, 0):                             # overnight into the next session
        return OVERNIGHT if d + timedelta(days=1) in days else CLOSED
    if d not in days:
        return CLOSED
    if t < time(9, 30):
        return PRE
    if t < time(16, 0):
        return REGULAR
    return AFTER


async def current(broker, now: datetime | None = None) -> str:
    """The session right now, from the broker's clock and calendar."""
    now = now or datetime.now(ET)
    clock = await broker._get("/v2/clock")
    if clock.get("is_open"):
        return REGULAR
    et = now.astimezone(ET).date()
    rows = await broker._get("/v2/calendar", start=(et - timedelta(days=1)).isoformat(),
                             end=(et + timedelta(days=5)).isoformat())
    return session_at(now, {date.fromisoformat(r["date"]) for r in rows})
