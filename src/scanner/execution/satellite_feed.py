"""Fetches what the plan's buy rules (execution/satellite.py) need for Gemma's new buys:
the three gates, index membership and sectors, her pool's momentum, each name's daily
bars, earnings dates and her recent sales. Raises nothing it can avoid: a name whose
facts cannot be read is refused with that reason -- a buy needs every check."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date, datetime, timedelta

import httpx

from scanner.advisor import gemma_pool as gp
from scanner.execution import satellite as sat
from scanner.execution.scorecard import read_rows
from scanner.features.bars import fetch_bars, fetch_many
from scanner.models import Frozen
from scanner.sources import calendar as event_calendar
from scanner.sources import composite

INDEX_SEGMENT = {"S&P 500": "large", "S&P MidCap 400": "mid", "S&P SmallCap 600": "small"}
SEGMENT_LABEL = {"large": "big caps (gate 1, SPY)", "mid": "mid caps (gate 2, MDY)",
                 "small": "small caps (gate 3, IJR)"}


class Gates(Frozen):
    open: dict[str, bool | None]
    vs_average: dict[str, float | None]

    def text(self) -> str:
        parts = []
        for seg, etf in sat.GATES.items():
            pct = self.vs_average.get(seg)
            state = {True: "OPEN", False: "CLOSED", None: "UNKNOWN (no data: closed)"}[self.open.get(seg)]
            parts.append(f"{SEGMENT_LABEL[seg]} {state}" + (f" ({pct:+.1f}% vs its 200-day)" if pct is not None else ""))
        return "Your buy gates today: " + "; ".join(parts) + "."


async def gates(client: httpx.AsyncClient) -> Gates:
    is_open, vs = {}, {}
    for seg, etf in sat.GATES.items():
        try:
            closes = (await fetch_bars(etf, client, range_="2y")).closes
            is_open[seg] = sat.gate_open(closes)
            avg = sum(closes[-sat.GATE_DAYS:]) / sat.GATE_DAYS if len(closes) >= sat.GATE_DAYS else None
            vs[seg] = (closes[-1] / avg - 1) * 100 if avg else None
        except Exception:                                     # noqa: BLE001 -- unknown = closed
            is_open[seg], vs[seg] = None, None
    return Gates(open=is_open, vs_average=vs)


def sold_recently(rows: Iterable[dict], since: datetime) -> set[str]:
    """Names Gemma's book closed since `since` (audit gemma_exit events)."""
    out = set()
    for r in rows:
        if r.get("event") != "gemma_exit" or not r.get("symbol"):
            continue
        try:
            if datetime.fromisoformat(str(r["at"])) >= since:
                out.add(str(r["symbol"]))
        except ValueError:
            continue
    return out


async def refusals(client: httpx.AsyncClient, buys: Sequence[str], held: Iterable[str],
                   g: Gates, audit_path: str, today: date | None = None,
                   cumulative: bool = True) -> dict[str, str]:
    """{symbol: why refused} for the new buys; a name absent from the result may be bought.
    Each accepted buy counts toward the sector cap for the next (`cumulative`; off for check_lines,
    where each candidate is judged against the holdings alone)."""
    today = today or date.today()
    if not buys:
        return {}
    members = await composite.load_universe(client)
    segment = {m.symbol: INDEX_SEGMENT.get(m.index, "large") for m in members}
    sectors = {m.symbol: m.sector for m in members}
    pool = gp.latest(today)
    mom = {r.symbol: r.mom_12_1 for r in pool}
    sectors.update({r.symbol: r.sector for r in pool if r.symbol not in sectors})
    bars, _ = await fetch_many(tuple(buys), client, range_="1y")
    found, failed = await event_calendar.with_moomoo(
        frozenset(buys), client, horizon=sat.EARNINGS_BUFFER_DAYS + 3, today=today)
    sold = sold_recently(read_rows(audit_path), datetime.now().astimezone()
                         - timedelta(days=sat.REBUY_LOOKBACK_DAYS))
    held_sectors = sat.sector_counts(held, sectors)
    out: dict[str, str] = {}
    for s in buys:
        b = bars.get(s)
        if b is None or len(b.closes) < 130:
            out[s] = "no year of daily bars to check the rules"
            continue
        if failed:
            out[s] = "earnings calendar unreadable -- refused (fails closed)"
            continue
        c = b.closes
        seg = sat.segment_of(s, segment)
        e = found.get(s)
        why = sat.buy_refusal(
            gate=g.open.get(seg), gate_name=SEGMENT_LABEL[seg], top_momentum=sat.momentum_top(s, mom),
            ret_6m=(c[-1] / c[-127] - 1) * 100, above_50d=c[-1] > sum(c[-50:]) / 50,
            ret_1m=sat.month_return(c),
            earnings_in_days=(e.on - today).days if e else None, sector=sectors.get(s, "unknown"),
            held_sectors=held_sectors, sold_recently=s in sold, new_20d_high=sat.new_closing_high(c))
        if why:
            out[s] = why
        elif cumulative:
            sec = sectors.get(s, "unknown")
            held_sectors = {**held_sectors, sec: held_sectors.get(sec, 0) + 1}
    return out


def price_refusal(symbol: str, closes: Sequence[float], *, mom: dict[str, float | None],
                  segment: dict[str, str], g: Gates, earnings_in_days: int | None) -> str | None:
    """The buy rules that do not depend on her holdings or recent sales: gate, top-third momentum,
    6-month return, 50-day, last month, earnings. Same rule function as refusals()."""
    if len(closes) < 130:
        return "no year of daily bars to check the rules"
    seg = sat.segment_of(symbol, segment)
    return sat.buy_refusal(
        gate=g.open.get(seg), gate_name=SEGMENT_LABEL[seg], top_momentum=sat.momentum_top(symbol, mom),
        ret_6m=(closes[-1] / closes[-127] - 1) * 100, above_50d=closes[-1] > sum(closes[-50:]) / 50,
        ret_1m=sat.month_return(closes),
        earnings_in_days=earnings_in_days, sector="unknown", held_sectors={}, sold_recently=False,
        new_20d_high=True)


async def buyable_pool(client: httpx.AsyncClient, rows: Sequence[gp.PoolRow], g: Gates,
                       today: date) -> tuple[list[gp.PoolRow], tuple[str, ...]]:
    """Her step-1 pool cut to the names that pass price_refusal (Gemma 2026-10-26, choice A1: "every
    name I shortlist is actually actionable"), each with its 50-day distance and next report.
    Momentum ranks against all `rows` -- the saved pool refusals() ranks against. Returns (rows, names
    whose earnings could not be read: kept, the buy-time check fails closed on them)."""
    members = await composite.load_universe(client)
    segment = {m.symbol: INDEX_SEGMENT.get(m.index, "large") for m in members}
    mom = {r.symbol: r.mom_12_1 for r in rows}
    bars, _ = await fetch_many(tuple(r.symbol for r in rows), client, range_="2y")
    closes = {s: b.closes for s, b in bars.items() if b is not None}
    pre = [r for r in rows if r.symbol in closes and price_refusal(
        r.symbol, closes[r.symbol], mom=mom, segment=segment, g=g, earnings_in_days=None) is None]
    found, failed = await event_calendar.with_moomoo(
        frozenset(r.symbol for r in pre), client, horizon=gp.EARNINGS_SHOWN_DAYS, today=today)
    out = []
    for r in pre:
        e = found.get(r.symbol)
        if e is not None and (e.on - today).days <= sat.EARNINGS_BUFFER_DAYS:
            continue
        c = closes[r.symbol]
        report = e.on.isoformat() if e is not None else "unknown" if r.symbol in failed else "none"
        out.append(r.model_copy(update={"vs_50d": (c[-1] / (sum(c[-50:]) / 50) - 1) * 100,
                                        "earnings": report}))
    return out, tuple(failed)


async def check_lines(client: httpx.AsyncClient, candidates: Sequence[str], held: Iterable[str],
                      g: Gates, audit_path: str, today: date | None = None) -> dict[str, str]:
    """{symbol: 'BUY CHECKS: PASS' | '*** BUY CHECKS: FAIL - why ***'} under each candidate in her 9:45
    facts (Gemma 2026-10-25, choice A2: "pick only names that pass"). The same checks as refusals(),
    each name against her holdings alone. Information; the buy-time refusal still decides."""
    refused = await refusals(client, candidates, held, g, audit_path, today, cumulative=False)
    return {s: f"*** BUY CHECKS: FAIL - {refused[s]} ***" if s in refused else "BUY CHECKS: PASS"
            for s in candidates}
