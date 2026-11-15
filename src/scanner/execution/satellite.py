"""The core-and-satellite plan (operator + Gemma, 2026-10-23): the rules, as numbers.

Operator: "selling at the top, and finding new stocks, or the same one, will also
create more profit". The account fell 2.5x the market Oct 2-22 (-3.2% vs SPY -1.3%);
none of the 71 positions sold in 30 days rose 5%+ afterwards -- the leak was buys and
churn (round trips of minutes). Drafted with the operator, agreed by Gemma with her
three additions (equal weight, max 2 per sector, a 10-day time stop), then three
trend gates, one per size group (operator: "gate1 big, gate2 mid, gate3 small").

  CORE       50% of the account in SPY, bought in two halves; from 2026-10-30 one GTC stop
             under all of it (CORE_STOP_PREFIX), re-bought after a stop-out only on a new high
  SATELLITE  Gemma's 40%: 6 names, equal size (the per-name cap binds), max 2 a sector
  BUY        her group's gate open (SPY / MDY / IJR above its 200-day average), top
             third of her pool by 12-1 momentum, 6-month return up, above the 50-day,
             up over the last month (the month 12-1 skips), no earnings within 5 days;
             regular session only, from 10:00 ET; a name sold in the last 30 days
             only on a new 20-day closing high
  EXIT       stop 2.5 ATR under the buy (bought before WIDE_STOP_FROM: never more than 3% --
             profit_lock.loss_floor; from it, no cap and a smaller size, see same_risk_pct);
             from +1 ATR the stop is at least break-even; then a CLOSE under the
             highest close less 3 ATR sells; no fixed target; nothing reporting
             earnings is held into the report; no progress in 10 days sells; once a close
             was +5%, a close giving back half the best gain sells (the profit lock)
  HOLD       her own drops wait 3 trading days (stops, the loss limit and her
             sell-ifs from the day after the buy still act)

FROM 2026-11-03 the rules book (a gem- position opened on or after RULES_BOOK_FROM)
is a different book. It ranks the S&P 500 by the return from 10 months ago to 7
months ago, holds the top 10 for 15 sessions, and sells at the close when the
S&P 500 is more than 3% above its 20-day average. From 2026-11-14 that same
regular close also uses the profit lock: a completed close of +5% that gives
back over half sells. A wick does not arm it, and the entry day cannot fire it.
The same day the 10-7 book also sells on a ready mark: up at least 2% with 4 of
6 peak readings, then the 5-minute trend turns down while the position is still
in profit. Small caps, the sweep, and QQQ stay off that sale.
From 2026-11-10 those positions also have the full-slot broker stop (2.5 ATR,
never more than 8% under the entry). A gem- position opened before that day
keeps the exits below. The small-cap book still uses the checks below, including
the last-month check.

CHOSEN, not measured: graded monthly and quarterly against SPY. PURE.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from scanner.execution import profit_lock

ET = ZoneInfo("America/New_York")
GATES = {"large": "SPY", "mid": "MDY", "small": "IJR"}
GATE_DAYS = 200
INITIAL_ATR = 2.5
BREAKEVEN_ATR = 1.0
TRAIL_ATR = 3.0
MIN_HOLD_DAYS = 3
TIME_STOP_DAYS = 10
#: PROFIT LOCK (operator + Gemma, 2026-10-23: "holding blew out the profit"; 17 real trades were up
#: 3%+, 9 ended at a loss): once a close has been LOCK_AT_PCT over the buy, a close under
#: buy + LOCK_KEEP x the best gain sells -- give back at most half. Lab, 2-4 week holds: mid caps
#: +3.8 -> +6.4 pts/yr vs the index, small -2.9 -> +3.9, big about -0.5.
LOCK_AT_PCT = 5.0
LOCK_KEEP = 0.5
MAX_PER_SECTOR = 2
MOMENTUM_TOP_FRACTION = 1 / 3
EARNINGS_BUFFER_DAYS = 5
REBUY_LOOKBACK_DAYS = 30
NEW_HIGH_DAYS = 20
#: WIDE STOP (Gemma 2026-10-26, choice B2): the 3% cap sat inside one normal day's move for her
#: buys (ATR 3.2-5.3%; MRNA, ATR 11.7%, stopped at -3% overnight), so the 2.5 ATR stop never
#: applied. Her buys from this day keep the full 2.5 ATR stop and are sized so the dollars lost
#: at it equal a 3% stop on a full-size position; positions bought before it keep the 3% cap.
WIDE_STOP_FROM = date(2026, 10, 27)
#: CORE STOP (operator 2026-10-30: "every position should have a sl"): the SPY core keeps one
#: GTC stop for all its shares, the books' rule (hard_stop, no 3% cap): 2.5 ATR under the
#: average buy, break-even from +1 ATR, never lowered. After it fills the core re-buys only on
#: a new 20-day closing high within REBUY_LOOKBACK_DAYS. CHOSEN, not measured.
CORE_STOP_PREFIX = "cor-stop-"
#: FULL SLOTS (operator 2026-10-30, "full equal slots, stop max 8%"): sizing a buy so its wide
#: stop lost a 3% stop's dollars left the books 14-61% invested (VSTS bought for $46). From
#: FULL_SLOT_FROM each rules-book / small-cap buy takes its full equal slot, and its stop is
#: 2.5 ATR under the buy but never more than MAX_STOP_PCT under it -- held positions too.
MAX_STOP_PCT = 8.0
FULL_SLOT_FROM = date(2026, 11, 2)
#: RULES BOOK (operator 2026-11-03: install the large-cap cell that beat the S&P
#: on return and Sharpe in 2007-2024 and again in 2025). Return from 10 months
#: ago to 7 months ago (21 sessions a month, the lab's momentum_series), top 10,
#: refill every 5 weekdays, sell at the close after 15 sessions or when the
#: S&P 500 is more than 3% above its 20-day average. From 2026-11-14 the regular
#: close also sells on the profit lock (LOCK_AT_PCT / LOCK_KEEP). The same day
#: a ready mark sells when the 5-minute trend turns down while still in profit
#: (peak_sell.rules_peak_sell). The extension and the 15-session hold win when
#: both would fire at the close. The lock does not go out after hours. The
#: 5-minute sale can, when the candle is fresh. No entry filter. From 2026-11-10 the operator added the
#: full-slot broker stop on the open names and on later buys. Positions opened
#: before RULES_BOOK_FROM keep the exits above.
#: The 2026 stub through 2026-10-23 did not confirm this cell.
RULES_BOOK_FROM = date(2026, 11, 3)
RULES_TOP_N = 10
RULES_REBALANCE_DAYS = 5
RULES_HOLD_DAYS = 15
RULES_MOM_LONG = 10
RULES_MOM_SKIP = 7
RULES_MONTH = 21
RULES_EXTENSION_PCT = 3.0
RULES_SMA_DAYS = 20
BUY_FROM = time(10, 0)
#: Last minutes of the session: the close-based exits are decided here.
CLOSE_WINDOW = (time(15, 50), time(16, 0))


def trading_days_between(start: date, end: date) -> int:
    """Weekdays after `start` up to and including `end` (holidays count; close enough
    for a 3- or 10-day rule)."""
    days, d = 0, start
    while d < end:
        d += timedelta(days=1)
        days += d.weekday() < 5
    return days


def gate_open(closes: Sequence[float], days: int = GATE_DAYS) -> bool | None:
    """The group's ETF closed above its `days`-day average. None without enough history."""
    if len(closes) < days:
        return None
    return closes[-1] > sum(closes[-days:]) / days


def segment_of(symbol: str, members: Mapping[str, str]) -> str:
    """large / mid / small from index membership; anything else is treated as large."""
    return members.get(symbol, "large")


def momentum_top(symbol: str, mom_12_1: Mapping[str, float | None],
                 fraction: float = MOMENTUM_TOP_FRACTION) -> bool:
    ranked = sorted((v, s) for s, v in mom_12_1.items() if v is not None)
    if symbol not in {s for _, s in ranked}:
        return False
    cut = max(1, int(len(ranked) * fraction))
    return symbol in {s for _, s in ranked[-cut:]}


def month_return(closes: Sequence[float]) -> float | None:
    """Percent change over the last 21 sessions (the last close against the close 21 sessions
    earlier). None without that history. The same window as the weekly trend label."""
    if len(closes) <= 21:
        return None
    prior = closes[-22]
    return (closes[-1] / prior - 1) * 100 if prior else 0.0


def momentum_window(closes: Sequence[float], *, long: int = RULES_MOM_LONG,
                    skip: int = RULES_MOM_SKIP) -> float | None:
    """Percent change from `long` months ago to `skip` months ago, 21 sessions a month.

    On the last bar this is the lab's momentum_series(closes, long, skip): the close
    `skip` months back divided by the close `long` months back. None without that
    history, and None when the older close is 0 (the lab treats that bar as missing)."""
    a, b = RULES_MONTH * long, RULES_MONTH * skip
    i = len(closes) - 1
    if i < a:
        return None
    older = closes[i - a]
    if not older:
        return None
    return (closes[i - b] / older - 1) * 100


def market_extended(closes: Sequence[float], *, band: float = RULES_EXTENSION_PCT,
                    days: int = RULES_SMA_DAYS) -> bool | None:
    """True when the last close is more than `band` percent above its `days`-day average.

    The average includes the last close, as the lab's SMA does. The comparison is
    strict (`v > band`): a reading of exactly 3% does not sell. None without `days`
    closes, or when the average is 0. A missing reading never sells."""
    if len(closes) < days:
        return None
    avg = sum(closes[-days:]) / days
    if not avg:
        return None
    return (closes[-1] / avg - 1) * 100 > band


def rules_rebalance_due(today: date, *, start: date = RULES_BOOK_FROM) -> bool:
    """The rules book looks for new names every 5 weekdays, counting from `start`
    (the lab refills every 5 sessions and does not buy on the days between)."""
    if today < start:
        return False
    return trading_days_between(start, today) % RULES_REBALANCE_DAYS == 0


def rules_rotation(entered: date | None, *, start: date = RULES_BOOK_FROM) -> bool:
    """A rules-book buy filled on or after the 10-7 book. Unknown, or earlier, keeps
    the old stop and the old close exits."""
    return entered is not None and entered >= start


def rules_book_has_no_stop(book: str, entered: date | None) -> bool:
    """A missing stop is a gap to re-arm.

    The 10-7 book was bought with no broker stop from 2026-11-03. On 2026-11-10
    the operator asked for a stop on those open positions. The close sale stays.
    `book` and `entered` stay in the signature so the callers do not change.
    """
    del book, entered
    return False


def rotation_stop(entry: float, atr_pct: float) -> float:
    """Broker stop for a 10-7 buy: 2.5 ATR under the entry, never more than 8% under it.

    A missing ATR uses the 8% cap. The buy has not closed a day, so this does
    not raise the stop to the entry.
    """
    if entry <= 0:
        return 0.0
    if atr_pct <= 0:
        return entry * (1 - MAX_STOP_PCT / 100)
    return hard_stop(
        entry=entry, atr=entry * atr_pct / 100, high_close=entry,
        floor=False, max_loss_pct=MAX_STOP_PCT)


def raised_stop(entry: float, best_close: float, initial: float) -> float:
    """The 10-7 broker stop once a winner has closed.

    `initial` is the stop already at the broker, 2.5 ATR under the buy and
    never more than 8% under it. Once a completed close is at least 5% over
    the buy, the stop rises to halfway back to the entry. It never falls.
    Today's price is not in `best_close`, so a wick does not lift it.
    """
    if entry <= 0 or initial <= 0 or best_close <= entry:
        return initial
    gain = best_close / entry - 1
    if gain * 100 < LOCK_AT_PCT:
        return initial
    return max(initial, entry * (1 + LOCK_KEEP * gain))


def rules_close_exit(*, held_days: int, extended: bool | None) -> str | None:
    """Why a 10-7 position sells for the extension or the hold, or None.

    The extension is checked first, as the lab checks its sell rule before the
    hold. It does not fire on the entry day: the lab fills after that day's
    exit check, so a buy is not sold at the same close. A missing market
    reading does not sell and does not block the hold. The profit lock is
    separate (`profit_lock_exit`) and stays in the regular close window."""
    if extended is True and held_days >= 1:
        return (f"market extended: the S&P is more than {RULES_EXTENSION_PCT:g}% "
                f"above its {RULES_SMA_DAYS}-day average")
    if held_days >= RULES_HOLD_DAYS:
        return f"held {held_days} trading days (the book sells after {RULES_HOLD_DAYS})"
    return None


def slot_shares(*, dollars: float, room: float, limit: float) -> int:
    """Whole shares of one equal slot. The slot dollar amount and the cash still
    free in the sleeve both bind. Nothing is sized from a stop distance."""
    if dollars <= 0 or room <= 0 or limit <= 0:
        return 0
    return int(min(dollars, room) / limit)


def buy_refusal(*, gate: bool | None, gate_name: str, top_momentum: bool, ret_6m: float | None,
                above_50d: bool | None, ret_1m: float | None, earnings_in_days: int | None,
                sector: str, held_sectors: Mapping[str, int], sold_recently: bool,
                new_20d_high: bool) -> str | None:
    """Why a new buy is refused, or None. Every rule is checked; the first miss is named."""
    if gate is not True:
        return f"{gate_name} gate closed (its ETF is not above its {GATE_DAYS}-day average)"
    if not top_momentum:
        return "not in the top third of the pool by 12-1 momentum"
    if ret_6m is None or ret_6m <= 0:
        return "6-month return not positive"
    if not above_50d:
        return "not above its 50-day average"
    if ret_1m is None or ret_1m <= 0:
        return "not up over the last month"
    if earnings_in_days is not None and earnings_in_days <= EARNINGS_BUFFER_DAYS:
        return f"earnings in {earnings_in_days} days"
    if sector != "unknown" and held_sectors.get(sector, 0) >= MAX_PER_SECTOR:
        return f"already {MAX_PER_SECTOR} in {sector}"
    if sold_recently and not new_20d_high:
        return f"sold in the last {REBUY_LOOKBACK_DAYS} days; re-buy only on a new {NEW_HIGH_DAYS}-day closing high"
    return None


def new_closing_high(closes: Sequence[float], days: int = NEW_HIGH_DAYS) -> bool:
    return len(closes) > days and closes[-1] > max(closes[-days - 1:-1])


def wide_stop(entered: date | None) -> bool:
    """A Gemma position bought on or after WIDE_STOP_FROM (unknown buy date: the 3% cap)."""
    return entered is not None and entered >= WIDE_STOP_FROM


def full_slot_risk_pct(cap_pct: float) -> float:
    """Risk per buy (% of the sleeve) that a full slot (`cap_pct`) stopped MAX_STOP_PCT under
    the buy would lose: never binding below the slot, so the slot size decides."""
    return cap_pct * MAX_STOP_PCT / 100


def same_risk_pct(cap_pct: float) -> float:
    """Risk per buy (% of the sleeve) equal to a full-size position (`cap_pct` of the sleeve)
    stopped at profit_lock's 3%: wider stops then buy fewer shares, never more dollars at risk."""
    return cap_pct * profit_lock.MAX_LOSS_PCT / 100


def hard_stop(*, entry: float, atr: float, high_close: float, floor: bool = True,
              max_loss_pct: float | None = None) -> float:
    """The broker's stop (it acts intraday and at a gap): 2.5 ATR under the buy, never
    more than profit_lock's 3% under it unless `floor` is off (wide_stop), and never more
    than `max_loss_pct` under it when given; from +1 ATR at least break-even."""
    stop = entry - INITIAL_ATR * atr
    if floor:
        stop = max(stop, profit_lock.loss_floor(entry))
    if max_loss_pct is not None:
        stop = max(stop, entry * (1 - max_loss_pct / 100))
    if high_close >= entry + BREAKEVEN_ATR * atr:
        stop = max(stop, entry)
    return stop


def trail_level(*, entry: float, atr: float, high_close: float) -> float | None:
    """The close-based trail once armed (+1 ATR): the highest close less 3 ATR."""
    if high_close < entry + BREAKEVEN_ATR * atr:
        return None
    return high_close - TRAIL_ATR * atr


def profit_lock_exit(*, entry: float, closes_since_buy: Sequence[float], price: float) -> str | None:
    """Once a completed close was +5% over the buy, a close under half of that gain sells.

    `closes_since_buy` are the completed daily closes after the buy day and before
    today. `price` stands in for today's close, and it is included in the high, so
    the high itself is not under the floor. A wick is not in this list. A gain
    under 5% does not sell. The 10-7 book and the small-cap book share this floor.
    """
    if not entry:
        return None
    high = max([*closes_since_buy, price, entry])
    best = high / entry - 1
    if best * 100 >= LOCK_AT_PCT and price < entry * (1 + LOCK_KEEP * best):
        return (f"profit lock: close {price:,.2f} gave back over half the best gain (best close {high:,.2f}, "
                f"+{best * 100:.1f}%; floor {entry * (1 + LOCK_KEEP * best):,.2f})")
    return None


def close_exit(*, entry: float, atr: float, closes_since_buy: Sequence[float], price: float,
               held_days: int, reports_before_next_session: bool) -> str | None:
    """At the end of the session: why to sell now, or None. `closes_since_buy` are the
    completed daily closes after the buy day; `price` stands in for today's close."""
    if reports_before_next_session:
        return "earnings before the next session: not held into the report"
    locked = profit_lock_exit(entry=entry, closes_since_buy=closes_since_buy, price=price)
    if locked:
        return locked
    high = max([*closes_since_buy, price, entry])
    trail = trail_level(entry=entry, atr=atr, high_close=high)
    if trail is not None and price < trail:
        return (f"trailing exit: close {price:,.2f} under the trail {trail:,.2f} "
                f"(highest close {high:,.2f} less {TRAIL_ATR:g} ATR)")
    if held_days >= TIME_STOP_DAYS:
        recent = list(closes_since_buy)[-TIME_STOP_DAYS:]
        earlier = list(closes_since_buy)[:-TIME_STOP_DAYS] or [entry]
        if max([*recent, price]) <= max(earlier) and price < entry + BREAKEVEN_ATR * atr:
            return f"time stop: no new high in {TIME_STOP_DAYS} trading days and under +1 ATR"
    return None


def reports_before_next_session(report_day: date | None, when: str, today: date,
                                next_session: date) -> bool:
    """A report today after the close, or before the next session opens (or at an
    unknown time on either day)."""
    if report_day is None:
        return False
    if report_day == today:
        return when != "pre-mkt"           # this morning's report has already happened
    return report_day == next_session and when in ("pre-mkt", "")


def in_close_window(now: datetime) -> bool:
    t = now.astimezone(ET).time()
    return CLOSE_WINDOW[0] <= t < CLOSE_WINDOW[1]


def may_buy_now(now: datetime) -> bool:
    return now.astimezone(ET).time() >= BUY_FROM


def may_drop(held_days: int) -> bool:
    return held_days >= MIN_HOLD_DAYS


def core_buy_dollars(*, equity: float, core_value: float, pct: float,
                     band: float = 0.9, tranches: int = 2) -> float:
    """Dollars of the core to buy now: nothing while it is within `band` of its target;
    otherwise up to the gap, at most 1/`tranches` of the target a day."""
    target = equity * pct / 100
    if core_value >= target * band:
        return 0.0
    return max(0.0, min(target - core_value, target / tranches))


def core_rebuy_blocked(*, stopped_on: date | None, today: date, closes: Sequence[float]) -> bool:
    """True while the core's stop filled within REBUY_LOOKBACK_DAYS and SPY has made no new
    20-day closing high since -- the books' re-buy rule, so a stop-out is not bought straight back."""
    if stopped_on is None or (today - stopped_on).days > REBUY_LOOKBACK_DAYS:
        return False
    return not new_closing_high(closes)


def sector_counts(symbols: Iterable[str], sectors: Mapping[str, str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for s in symbols:
        sec = sectors.get(s, "unknown")
        out[sec] = out.get(sec, 0) + 1
    return out
