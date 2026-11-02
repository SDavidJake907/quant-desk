"""Does a strong earnings reaction keep drifting? Post-earnings drift, tested.

    python scripts/test_pead.py                 # S&P 500, last third held out
    python scripts/test_pead.py --index all     # all 1,504 names

THE TRADE
After a company reports, measure its move against the index over the
announcement (close two days before the filing to close the day after). If
that reaction is in the top fifth of all reactions, buy at that close and hold
for N trading days. Long only, like the rest of this system. The bottom fifth
is shown too, for information — this repo does not short.

THE PROTOCOL
- Returns are excess over the S&P 500, net of 20bps round trip.
- The top-fifth cutoff is set on the TRAINING quarters only, then applied
  unchanged to the held-out quarters.
- The holding period is chosen on training only, by quarter-level t, and only
  that one is read on the holdout. The others are printed for context.
- One number per calendar quarter is the unit for t. Reports cluster in the
  same few weeks, so per-trade t-statistics would count one earnings season
  hundreds of times.
- The control is the same-size move on a random NON-earnings day in the same
  stocks. "Beats the index" and "beats ordinary momentum" are different
  claims; only the second is evidence of an earnings effect.

KNOWN BIASES
Today's index members only, so companies that were dropped or delisted are
missing — survivorship flatters every number here. No beta adjustment. Yahoo's
dividend-adjusted closes.
"""

from __future__ import annotations

import argparse
import asyncio
import random

import httpx

from scanner.backtest.pead import (
    BOTTOM, TOP, align, by_quarter, control_windows, cutoffs,
    earnings_windows, quarter, quarter_edges, split_quarters, stats,
)
from scanner.features.bars import fetch_bars
from scanner.sources import composite, sec_tickers
from scanner.sources.earnings_history import earnings_dates
from scanner.sources.index_history import COMPOSITE_1500, SP400, SP500, SP600

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"
GREEN, RED, YELLOW = "\033[32m", "\033[31m", "\033[33m"

INDEXES = {"sp500": (SP500,), "sp400": (SP400,), "sp600": (SP600,),
           "all": COMPOSITE_1500}
HOLDS = (5, 10, 20, 40, 60)


def _colour(t: float) -> str:
    return GREEN if t >= 2 else (YELLOW if t >= 1 else (RED if t < 0 else DIM))


def _line(label: str, per_quarter: dict[str, float], cost: float, n: int) -> None:
    mean, se, t = stats(v - cost for v in per_quarter.values())
    wins = sum(1 for v in per_quarter.values() if v - cost > 0)
    print(f"    {label:<30} {n:>6} trades  {_colour(t)}{mean:>+6.2f}%{RESET}"
          f"  t {t:>+5.2f}  {wins}/{len(per_quarter)} qtrs up")


def _edge_line(label: str, edges: dict[str, float]) -> float:
    mean, se, t = stats(edges.values())
    wins = sum(1 for v in edges.values() if v > 0)
    print(f"    {label:<30} {'':>6}         {_colour(t)}{mean:>+6.2f}%{RESET}"
          f"  t {t:>+5.2f}  {wins}/{len(edges)} qtrs up")
    return t


def _count(windows, hold, which, low, high, quarters) -> int:
    from scanner.backtest.pead import bucket
    return sum(1 for w in windows if hold in w.excess and quarter(w.anchor) in quarters
               and (which == "all" or bucket(w, low, high) == which))


def _section(name, events, controls, quarters, low, high, cost, holds, chosen=None):
    ev = [w for w in events if quarter(w.anchor) in quarters]
    ct = [w for w in controls if quarter(w.anchor) in quarters]
    print(f"\n{BOLD}{name}{RESET} {DIM}({len(quarters)} quarters, "
          f"{min(quarters)} to {max(quarters)}){RESET}")
    for hold in holds:
        mark = f"  {BOLD}<- chosen on train{RESET}" if hold == chosen else ""
        print(f"  {BOLD}hold {hold} days{RESET}{mark}")
        _line("top fifth, after earnings", by_quarter(ev, hold, TOP, low, high), cost,
              _count(ev, hold, TOP, low, high, quarters))
        _line("top fifth, ordinary days", by_quarter(ct, hold, TOP, low, high), cost,
              _count(ct, hold, TOP, low, high, quarters))
        _edge_line("earnings minus ordinary", quarter_edges(ev, ct, hold, TOP, low, high))
        _line("all reports", by_quarter(ev, hold, "all", low, high), cost,
              _count(ev, hold, "all", low, high, quarters))
        _line("bottom fifth (not traded)", by_quarter(ev, hold, BOTTOM, low, high), 0.0,
              _count(ev, hold, BOTTOM, low, high, quarters))


async def load(index: str, range_: str, seed: int):
    rng = random.Random(seed)
    async with httpx.AsyncClient(timeout=60) as client:
        universe = await composite.load_universe(client, INDEXES[index])
        symbols = [m.symbol for m in universe]
        ciks = await sec_tickers.load_universe(client)
        bench = await fetch_bars("^GSPC", client, range_=range_)
        print(f"{DIM}fetching {range_} daily bars for {len(symbols)} symbols{RESET}")
        frames = await asyncio.gather(
            *(fetch_bars(s, client, range_=range_) for s in symbols),
            return_exceptions=True)

        print(f"{DIM}fetching EDGAR 8-K item 2.02 dates (polite batches){RESET}")
        found: dict[str, tuple] = {}
        for start in range(0, len(symbols), 8):
            chunk = [s for s in symbols[start:start + 8] if s in ciks]
            got = await asyncio.gather(
                *(earnings_dates(ciks[s][0], client) for s in chunk),
                return_exceptions=True)
            for symbol, dates in zip(chunk, got):
                if isinstance(dates, tuple) and dates:
                    found[symbol] = dates
            await asyncio.sleep(0.12)       # SEC asks for <10 requests/second

    events, controls = [], []
    for symbol, bars in zip(symbols, frames):
        if isinstance(bars, Exception) or symbol not in found or len(bars) < 80:
            continue
        d, c, b = align(bars.dates, bars.closes, bench.dates, bench.closes)
        events += earnings_windows(symbol, d, c, b, found[symbol], HOLDS)
        controls += control_windows(symbol, d, c, b, found[symbol], HOLDS, rng)
    return events, controls, len(found), len(symbols)


def main(index: str, range_: str, holdout: float, cost_bps: float, seed: int) -> None:
    events, controls, with_dates, total = asyncio.run(load(index, range_, seed))
    if len(events) < 100:
        raise SystemExit(f"only {len(events)} usable reports — not enough to test")
    cost = cost_bps / 100.0

    train_q, hold_q = split_quarters(events, holdout)
    low, high = cutoffs([w.reaction for w in events if quarter(w.anchor) in train_q])
    print(f"\n{DIM}{with_dates}/{total} symbols with earnings history, "
          f"{len(events)} reports, {len(controls)} control days{RESET}")
    print(f"{DIM}top fifth = reaction >= {high:+.2f}% vs index; "
          f"bottom fifth <= {low:+.2f}%  (set on train only){RESET}")
    print(f"{DIM}figures are mean excess over the S&P 500 per quarter, "
          f"net of {cost_bps:.0f}bps{RESET}")

    # Choose the hold on TRAIN ONLY: the one where the earnings bucket most
    # clearly beat ordinary days. Then read that one hold on the holdout.
    train_ev = [w for w in events if quarter(w.anchor) in train_q]
    train_ct = [w for w in controls if quarter(w.anchor) in train_q]
    chosen = max(HOLDS, key=lambda h: stats(
        quarter_edges(train_ev, train_ct, h, TOP, low, high).values())[2])

    _section("TRAIN", events, controls, train_q, low, high, cost, HOLDS, chosen)
    _section("HOLDOUT", events, controls, hold_q, low, high, cost, HOLDS, chosen)

    hold_ev = [w for w in events if quarter(w.anchor) in hold_q]
    hold_ct = [w for w in controls if quarter(w.anchor) in hold_q]
    net = {q: v - cost for q, v in by_quarter(hold_ev, chosen, TOP, low, high).items()}
    edge = quarter_edges(hold_ev, hold_ct, chosen, TOP, low, high)
    m_net, _, t_net = stats(net.values())
    m_edge, _, t_edge = stats(edge.values())
    print(f"\n{BOLD}VERDICT{RESET} {DIM}(holdout, {chosen}-day hold, chosen on train){RESET}")
    print(f"    beats the index after costs:     {m_net:+.2f}% per trade, t {t_net:+.2f}")
    print(f"    beats ordinary momentum:         {m_edge:+.2f}% per trade, t {t_edge:+.2f}")
    print(f"{DIM}    t >= 2 on both is the bar for 'worth paper trading'. Survivorship")
    print(f"    bias still flatters the first line; the second is less exposed.{RESET}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--index", choices=sorted(INDEXES), default="sp500")
    p.add_argument("--range", dest="range_", default="10y",
                   help="daily bar history to fetch (EDGAR reaches ~6-8 years)")
    p.add_argument("--holdout", type=float, default=0.333)
    p.add_argument("--cost-bps", type=float, default=20.0)
    p.add_argument("--seed", type=int, default=7)
    a = p.parse_args()
    main(a.index, a.range_, a.holdout, a.cost_bps, a.seed)
