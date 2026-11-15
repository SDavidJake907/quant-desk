"""How much open profit each position has given back.

Report only. Nothing here sells, queues a sell, or moves a stop. The 30-minute
task writes the page. The installed exits stay in plan_run.
"""

from __future__ import annotations

from datetime import date
from typing import Sequence

from scanner.execution import satellite as sat
from scanner.models import Frozen

#: A giveback smaller than this stays on the page and does not raise a fresh flag.
#: Display only. It is not a sell rule.
FLAG_DOLLARS = 50.0

BOOK_LABEL = {
    "gemma": "rules",
    "small": "small",
    "smc": "sweep",
    "core": "core",
    "picks": "picks",
    "orb": "orb",
    "manual": "manual",
    "momentum": "momentum",
    "unknown": "unknown",
}


class Watch(Frozen):
    symbol: str
    book: str
    qty: float
    entry: float
    last: float
    prior_close: float | None = None
    best: float | None = None
    #: Completed closes after the fill, plus the live price. The profit-lock label uses this.
    best_close: float | None = None
    open_pl: float = 0.0
    day_pl: float = 0.0
    entry_day: date | None = None


def best_price(entry: float, last: float, highs: Sequence[tuple[date, float]],
               entry_day: date | None) -> float | None:
    """Highest price since the day after the buy. The entry day's high can print before the fill.

    Unknown entry day, or no bars at all, returns None. A series that stops on
    the entry day returns the fill and the live price only."""
    if entry_day is None:
        return None
    if not highs:
        return None
    # The entry day's high can print before the fill. Later days are prices
    # the position actually held.
    later = [high for day, high in highs if day > entry_day]
    return max(entry, last, *later)


def best_close(entry: float, last: float, closes: Sequence[tuple[date, float]],
               entry_day: date | None, today: date | None = None) -> float | None:
    """Peak the close profit lock would see: closes after the fill and before today, plus the live price.

    The entry day's close and every intraday high stay out. On the entry day there is
    no completed close yet, so the lock label stays off."""
    if entry_day is None or not closes:
        return None
    if today is not None and entry_day >= today:
        return None
    held = [close for day, close in closes if day > entry_day and (today is None or day < today)]
    if not held:
        return None
    return max(entry, last, *held)


def profit_given_back(row: Watch) -> float:
    """Dollars of open profit that existed at the best price and are gone now.

    A name that was never above its entry has given nothing back. Loss past
    the entry is not called a giveback."""
    if row.best is None or row.qty == 0 or row.entry <= 0:
        return 0.0
    peak = max(row.best, row.entry)
    peak_profit = max(0.0, (peak - row.entry) * row.qty)
    given = max(0.0, (peak - row.last) * row.qty)
    return round(min(peak_profit, given), 2)


def day_given_back(row: Watch, *, today: date | None = None) -> float:
    """Dollars of yesterday's open profit that today's price has given back.

    Yesterday's mark is already in yesterday's equity, so this is the piece
    of today's account change that used to be profit. A buy from today never
    held yesterday's close."""
    if today is not None and row.entry_day == today:
        return 0.0
    if row.prior_close is None or row.qty == 0:
        return 0.0
    marked = max(0.0, (row.prior_close - row.entry) * row.qty)
    if marked <= 0 or row.last >= row.prior_close:
        return 0.0
    return round(min(marked, (row.prior_close - row.last) * row.qty), 2)


def half_given_back(row: Watch) -> bool:
    """True when a completed close was at least 5% up and half of that gain is gone.

    The same floor as satellite.close_exit. A wick does not arm it. Naming it does not sell."""
    peak = row.best_close
    if peak is None or row.entry <= 0:
        return False
    gain = peak / row.entry - 1
    if gain * 100 < sat.LOCK_AT_PCT:
        return False
    floor = row.entry * (1 + sat.LOCK_KEEP * gain)
    return row.last < floor


def lock_applies(row: Watch) -> bool:
    """The close profit lock is live for small caps and for the rules book, including 10-7.

    The page only names it. Peak watch sells it, and only in the regular close window."""
    return row.book in ("small", "gemma", "picks")


def flagged(row: Watch, *, today: date | None = None) -> bool:
    return (half_given_back(row) or day_given_back(row, today=today) >= FLAG_DOLLARS
            or profit_given_back(row) >= FLAG_DOLLARS)


def new_flags(previous: set[str], rows: Sequence[Watch], *, today: date | None = None) -> list[str]:
    """Symbols newly over the flag line. A repeat of the same name stays quiet."""
    now = {row.symbol for row in rows if flagged(row, today=today)}
    return sorted(now - previous)


def exit_note(row: Watch) -> str:
    """The exit that already applies. The page does not add a sale."""
    if row.book == "gemma" and sat.rules_rotation(row.entry_day):
        return ("10-7 hold. A ready mark, up at least 2% with 4 of 6 peak readings, "
                "sells when the 5-minute trend turns down while the position is still "
                "in profit. At the regular close, a completed close of at least 5% "
                "that gives back over half is the profit lock. The broker stop starts "
                "at 2.5 ATR under the buy and never more than 8% under it. Once a "
                "completed close is at least 5% over the buy, that stop rises to "
                "halfway back to the entry and is never lowered. The S&P sale and the "
                "15-session sale still come first, and not on the entry day.")
    if row.book in ("small", "gemma", "picks"):
        return ("Broker stop stands. At the close, a 5% gain that gives back half "
                "is the profit lock.")
    if row.book == "smc":
        return "Sweep stop stands. A giveback does not sell on its own."
    if row.book == "core":
        return "Core stop stands. A giveback does not sell on its own."
    return "The position's own exit stands. A giveback does not sell on its own."


def _usd(amount: float) -> str:
    if amount < 0:
        return f"-${abs(amount):,.2f}"
    return f"${amount:,.2f}"


def _line(row: Watch, *, today: date | None = None) -> list[str]:
    label = BOOK_LABEL.get(row.book, row.book)
    head = (f"{label} {row.symbol}  {row.qty:.0f} shares  entry {row.entry:,.2f}  "
            f"last {row.last:,.2f}  open {_usd(row.open_pl)}  today {_usd(row.day_pl)}")
    given = profit_given_back(row)
    from_yesterday = day_given_back(row, today=today)
    if row.best is None:
        peak = "Best price since the buy is unavailable."
    elif given > 0:
        peak = (f"Best {row.best:,.2f}, gave back {_usd(given)} of the gain, "
                f"still {_usd(row.open_pl)} versus the entry.")
    else:
        peak = f"Best {row.best:,.2f}. No open profit has been given back."
    if from_yesterday > 0:
        peak += f" From yesterday's close, gave back {_usd(from_yesterday)} today."
    if half_given_back(row) and lock_applies(row):
        peak = "Half of a 5%+ gain is gone. " + peak
    return [head, peak, exit_note(row)]


def render(rows: Sequence[Watch], when: str, *, today: date | None = None) -> str:
    """The page peak watch does not send. Every open name is on it."""
    ordered = sorted(
        rows,
        key=lambda row: (-max(profit_given_back(row), day_given_back(row, today=today)), row.symbol),
    )
    lines = [
        "# Position watch",
        "",
        f"{when}. Report only. No order.",
        "",
    ]
    if not ordered:
        lines.append("No open positions.")
        return "\n".join(lines) + "\n"
    giving = [row for row in ordered if flagged(row, today=today)]
    quiet = [row for row in ordered if row not in giving]
    lines.append("## Profit given back")
    lines.append("")
    if giving:
        for row in giving:
            lines.extend(_line(row, today=today))
            lines.append("")
    else:
        missing = any(row.best is None for row in ordered)
        lines.append("No measured giveback. A name with no best price says so below."
                     if missing else "No open profit has been given back.")
        lines.append("")
    if quiet:
        lines.append("## The rest of the book")
        lines.append("")
        for row in quiet:
            lines.extend(_line(row, today=today))
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"
