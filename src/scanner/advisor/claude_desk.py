"""Claude's research desk: a morning plan per stock, played out in a shadow book. Pure: no I/O.

WHAT IT IS FOR
Operator 2026-10-31: "lets have you do the research, News, stocks, and find better entry and
exit per stocks.. cause every stocks is diffrent." Before each session Claude reads the
market, the news and each name's own chart (scripts/claude_desk.py plan) and writes one call
per stock: buy with its own entry and stop, hold (optionally raising the stop), or sell.

WHAT IT IS NOT
It never places, cancels or changes an order. The calls are played out here against daily
bars in a paper sleeve of SLEEVE dollars (the rules book's 30%) and graded at the freeze's
end (FREEZE.md, 2026-12-31). A sleeve with real orders is the operator's call after that.

HOW A CALL PLAYS OUT (one daily bar at a time, pessimistic when a bar is ambiguous)
- A buy is good for the plan's session only. "limit" fills when the low reaches the entry
  (at the open if it opens under it); "breakout" when the high reaches it (at the open if it
  opens over it); "open" at the open.
- Every buy has a stop, never more than MAX_STOP_PCT under the entry (the books' cap).
- A held position exits at its stop (at the open on a gap through it), at its target (at the
  open on a gap over it), at the close after max_days sessions, or at the open after a sell
  call. A bar touching both stop and target counts as the stop. A buy whose stop is touched
  on its fill bar is stopped that bar.
- A hold call may raise a stop, never lower it (every position keeps a stop).
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import Field

from scanner.models import Frozen

SLEEVE = 30_000.0
SLOTS = 5
SLOT = SLEEVE / SLOTS
MAX_STOP_PCT = 8.0
#: An entry this far from the last close is a different trade than the one researched.
MAX_ENTRY_GAP_PCT = 5.0
MAX_DAYS = 30
#: Each side's cost in the shadow fills (spread + slippage), as a fraction of price.
COST = 0.0005
FIRST_DAY = date(2026, 11, 2)
#: The plan's pass rule is fixed in FREEZE.md before the first plan (2026-10-31).
JUDGE_DAY = date(2026, 12, 31)

Action = Literal["buy", "hold", "sell"]
EntryType = Literal["limit", "breakout", "open"]


class Call(Frozen):
    symbol: str
    action: Action
    entry_type: EntryType | None = None
    entry: float | None = None
    stop: float | None = None
    target: float | None = None
    max_days: int | None = None
    confidence: Literal["low", "medium", "high"] = "medium"
    why: str = ""


class Plan(Frozen):
    day: date
    market: str = ""
    calls: tuple[Call, ...] = ()
    skipped: tuple[str, ...] = Field(default=(), description="refused calls, with the reason")


class Position(Frozen):
    symbol: str
    opened: date
    entry: float
    shares: float
    stop: float
    target: float | None = None
    max_days: int = MAX_DAYS
    held_days: int = 0
    sell_next_open: bool = False
    why: str = ""
    #: The latest close seen, for the sleeve's value on a day the name has no bar.
    last: float | None = None


class Closed(Frozen):
    symbol: str
    opened: date
    closed: date
    entry: float
    exit: float
    shares: float
    reason: str

    @property
    def pnl(self) -> float:
        return (self.exit - self.entry) * self.shares

    @property
    def pct(self) -> float:
        return (self.exit / self.entry - 1) * 100


class Book(Frozen):
    cash: float = SLEEVE
    open: tuple[Position, ...] = ()
    closed: tuple[Closed, ...] = ()
    #: Last session played; plans for later sessions are still to come.
    through: date | None = None
    #: (day, sleeve value at that close), for the equity curve and drawdown.
    marks: tuple[tuple[date, float], ...] = ()


class Bar(Frozen):
    day: date
    o: float
    h: float
    l: float  # noqa: E741 -- the bar's low, as Alpaca names it
    c: float


#: The --json-schema Claude's plan must match (validated again by `check` below).
SCHEMA: dict = {
    "type": "object",
    "required": ["market", "calls"],
    "properties": {
        "market": {"type": "string", "description": "2-5 sentences: the tape, the news that matters, the stance"},
        "calls": {"type": "array", "items": {
            "type": "object",
            "required": ["symbol", "action", "why"],
            "properties": {
                "symbol": {"type": "string"},
                "action": {"enum": ["buy", "hold", "sell"]},
                "entry_type": {"enum": ["limit", "breakout", "open"]},
                "entry": {"type": "number"},
                "stop": {"type": "number"},
                "target": {"type": "number"},
                "max_days": {"type": "integer", "minimum": 1, "maximum": MAX_DAYS},
                "confidence": {"enum": ["low", "medium", "high"]},
                "why": {"type": "string"},
            },
        }},
    },
}


def check(call: Call, *, last_close: float | None, held: Position | None) -> str | None:
    """Why a call cannot be played as written, or None when it can."""
    if call.action == "buy":
        if held is not None:
            return "already held in the shadow book"
        if last_close is None:
            return "no price for it"
        if call.entry_type is None or call.stop is None:
            return "a buy needs an entry type and a stop"
        entry = last_close if call.entry_type == "open" else call.entry
        if entry is None or entry <= 0:
            return "a limit or breakout buy needs an entry price"
        if abs(entry / last_close - 1) * 100 > MAX_ENTRY_GAP_PCT:
            return f"entry {entry:.2f} is over {MAX_ENTRY_GAP_PCT:.0f}% from the last close {last_close:.2f}"
        if call.stop >= entry:
            return "the stop is not under the entry"
        if (1 - call.stop / entry) * 100 > MAX_STOP_PCT:
            return f"the stop is over {MAX_STOP_PCT:.0f}% under the entry"
        if call.target is not None and call.target <= entry:
            return "the target is not over the entry"
        return None
    if held is None:
        return "not held in the shadow book"
    if call.action == "hold" and call.stop is not None and call.stop < held.stop:
        return f"a stop is never lowered ({held.stop:.2f} -> {call.stop:.2f})"
    return None


def accepted(plan: Plan, *, closes: dict[str, float], book: Book) -> Plan:
    """The plan with unplayable calls moved to `skipped`; buys past the free slots dropped."""
    held = {p.symbol: p for p in book.open}
    keep: list[Call] = []
    skipped: list[str] = list(plan.skipped)
    free = SLOTS - len(book.open) + sum(1 for c in plan.calls if c.action == "sell" and c.symbol in held)
    seen: set[str] = set()
    for raw in plan.calls:
        call = raw.model_copy(update={"symbol": raw.symbol.strip().upper()})
        if call.symbol in seen:
            skipped.append(f"{call.symbol}: second call on the same name")
            continue
        seen.add(call.symbol)
        why = check(call, last_close=closes.get(call.symbol), held=held.get(call.symbol))
        if why is None and call.action == "buy":
            if free <= 0:
                why = f"no free slot ({SLOTS} max)"
            else:
                free -= 1
        if why:
            skipped.append(f"{call.symbol}: {why}")
        else:
            keep.append(call)
    return plan.model_copy(update={"calls": tuple(keep), "skipped": tuple(skipped)})


def _fill(call: Call, bar: Bar) -> float | None:
    if call.entry_type == "open":
        return bar.o
    assert call.entry is not None
    if call.entry_type == "limit":
        return min(bar.o, call.entry) if bar.l <= call.entry else None
    return max(bar.o, call.entry) if bar.h >= call.entry else None


def _exit(p: Position, bar: Bar) -> tuple[float, str] | None:
    if p.sell_next_open:
        return bar.o, "sell call"
    if bar.o <= p.stop:
        return bar.o, "stop (gap)"
    if bar.l <= p.stop:
        return p.stop, "stop"
    if p.target is not None:
        if bar.o >= p.target:
            return bar.o, "target (gap)"
        if bar.h >= p.target:
            return p.target, "target"
    if p.held_days + 1 >= p.max_days:
        return bar.c, "time"
    return None


def play(book: Book, plan: Plan | None, bars: dict[str, Bar], day: date) -> Book:
    """One session: the plan's sells and stop raises, exits, then the plan's buys, then the mark.
    `bars` holds this session's bar for every symbol in play; a missing bar holds the position."""
    cash = book.cash
    closed = list(book.closed)
    calls = {c.symbol: c for c in plan.calls} if plan else {}
    still: list[Position] = []
    for p in book.open:
        call = calls.get(p.symbol)
        if call and call.action == "sell":
            p = p.model_copy(update={"sell_next_open": True})
        elif call and call.action == "hold":
            p = p.model_copy(update={"stop": max(p.stop, call.stop or p.stop),
                                     "target": call.target if call.target is not None else p.target})
        bar = bars.get(p.symbol)
        out = _exit(p, bar) if bar else None
        if out is None:
            still.append(p.model_copy(update={"held_days": p.held_days + (1 if bar else 0),
                                              "last": bar.c if bar else p.last}))
            continue
        price = out[0] * (1 - COST)
        cash += price * p.shares
        closed.append(Closed(symbol=p.symbol, opened=p.opened, closed=day, entry=p.entry,
                             exit=price, shares=p.shares, reason=out[1]))
    for call in (calls.values() if plan else ()):
        bar = bars.get(call.symbol)
        if call.action != "buy" or bar is None or len(still) >= SLOTS:
            continue
        fill = _fill(call, bar)
        if fill is None:
            continue
        fill *= 1 + COST
        shares = min(SLOT, cash) / fill
        if shares <= 0:
            continue
        cash -= shares * fill
        pos = Position(symbol=call.symbol, opened=day, entry=fill, shares=shares, stop=call.stop or 0.0,
                       target=call.target, max_days=call.max_days or MAX_DAYS, why=call.why, last=bar.c)
        if bar.l <= pos.stop:                         # stopped on its fill bar: assume the worst
            price = min(pos.stop, bar.c) * (1 - COST)
            cash += price * shares
            closed.append(Closed(symbol=pos.symbol, opened=day, closed=day, entry=fill, exit=price,
                                 shares=shares, reason="stop (fill day)"))
            continue
        still.append(pos)
    value = cash + sum(p.shares * (p.last or p.entry) for p in still)
    return Book(cash=cash, open=tuple(still), closed=tuple(closed), through=day,
                marks=(*book.marks, (day, round(value, 2))))


def max_drawdown_pct(marks: tuple[tuple[date, float], ...], start: float = SLEEVE) -> float:
    peak, worst = start, 0.0
    for _, v in marks:
        peak = max(peak, v)
        worst = max(worst, (1 - v / peak) * 100)
    return worst
