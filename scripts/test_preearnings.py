"""Is there a tradeable run-up into earnings, exited well before the report?

    python scripts/test_preearnings.py --index sp500

THE TRADE
Enter N trading days before an earnings announcement, exit M days before it.
M is 4 or 5, so the position is closed well clear of the release and never
carries gap risk. This is the opposite of the live system's earnings gate,
which refuses anything inside 7 days — not a contradiction, a different trade.

WHERE THE DATES COME FROM
SEC EDGAR 8-K filings carrying item 2.02, "results of operations". That is the
actual announcement date as filed, not a scraped estimate, and it reaches back
about eight years. Yahoo's quoteSummary returns 401 and its chart earnings
events are empty, so this is the only honest source available here.

THE CONTROL IS THE POINT
The last two years were strongly up. "Buy 15 days before earnings" will show a
positive return in that tape whether or not earnings had anything to do with
it. So every window is paired with the SAME holding period started on random
dates in the same symbols. If the two match, there is no earnings effect —
only market drift, which you could have had by holding an index fund.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import math
import random
import statistics

import httpx

from scanner.features.bars import fetch_bars
from scanner.sources import composite, sec_tickers
from scanner.sources.earnings_history import earnings_dates
from scanner.sources.index_history import COMPOSITE_1500, SP400, SP500, SP600

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"
GREEN, RED, YELLOW = "\033[32m", "\033[31m", "\033[33m"

INDEXES = {"sp500": (SP500,), "sp400": (SP400,), "sp600": (SP600,),
           "all": COMPOSITE_1500}

def _index_on_or_before(days: list[dt.date], when: dt.date) -> int | None:
    lo, hi = 0, len(days) - 1
    found = None
    while lo <= hi:
        mid = (lo + hi) // 2
        if days[mid] <= when:
            found, lo = mid, mid + 1
        else:
            hi = mid - 1
    return found


def _stats(rs):
    if len(rs) < 2:
        return 0.0, 0.0, 0.0
    mean = statistics.fmean(rs)
    se = statistics.stdev(rs) / math.sqrt(len(rs))
    return mean, se, (mean / se if se else 0.0)


def _report(label, rs, control=None):
    mean, se, t = _stats(rs)
    colour = GREEN if t > 2 else (YELLOW if t > 1 else DIM)
    edge = ""
    if control:
        cmean, _, _ = _stats(control)
        gap = mean - cmean
        edge = f"   vs control {cmean:+.2f}%  edge {gap:+.2f}%"
    print(f"    {label:<26}{len(rs):>6} trades {colour}{mean:>+7.2f}%{RESET}"
          f"  se {se:>5.2f}  t {t:>+5.2f}{edge}")


async def run(index: str, holdout: float, seed: int) -> None:
    random.seed(seed)
    async with httpx.AsyncClient(timeout=60) as client:
        universe = await composite.load_universe(client, INDEXES[index])
        symbols = [m.symbol for m in universe]
        ciks = await sec_tickers.load_universe(client)
        print(f"{DIM}fetching daily bars for {len(symbols)} symbols{RESET}")
        frames = await asyncio.gather(
            *(fetch_bars(s, client, range_="5y") for s in symbols),
            return_exceptions=True)

        print(f"{DIM}fetching EDGAR 8-K item 2.02 dates (polite batches){RESET}")
        found: dict[str, tuple[dt.date, ...]] = {}
        for start in range(0, len(symbols), 8):
            chunk = [s for s in symbols[start:start + 8] if s in ciks]
            got = await asyncio.gather(
                *(earnings_dates(ciks[s][0], client) for s in chunk),
                return_exceptions=True)
            for symbol, dates in zip(chunk, got):
                if isinstance(dates, tuple) and dates:
                    found[symbol] = dates
            await asyncio.sleep(0.12)     # SEC asks for <10 req/s

    print(f"{DIM}{len(found)} symbols with earnings history{RESET}")

    # (announcement_date, entry_offset, exit_offset, return%, is_control)
    rows: list[tuple[dt.date, int, int, float, bool]] = []
    for bars, symbol in zip(frames, symbols):
        if isinstance(bars, Exception) or symbol not in found:
            continue
        days = list(bars.dates)
        closes = bars.closes
        if len(days) < 60:
            continue
        for announced in found[symbol]:
            anchor = _index_on_or_before(days, announced)
            if anchor is None:
                continue
            for entry_off in (10, 15, 20, 25, 30):
                for exit_off in (4, 5):
                    ei, xi = anchor - entry_off, anchor - exit_off
                    if ei < 1 or xi <= ei or xi >= len(closes):
                        continue
                    rows.append((announced, entry_off, exit_off,
                                 (closes[xi] / closes[ei] - 1) * 100, False))
                    # control: same hold length, random anchor in the same series
                    hold = xi - ei
                    ri = random.randrange(1, max(2, len(closes) - hold - 1))
                    rows.append((announced, entry_off, exit_off,
                                 (closes[ri + hold] / closes[ri] - 1) * 100, True))
            # ---- what happens AFTER the report ---------------------------
            # Negative entry offsets mean "days after". Same control, so the
            # question is whether post-earnings behaviour differs from drift.
            for after in (1, 5, 10, 20):
                ei, xi = anchor, anchor + after
                if ei < 1 or xi >= len(closes):
                    continue
                rows.append((announced, -1, -after,
                             (closes[xi] / closes[ei] - 1) * 100, False))
                ri = random.randrange(1, max(2, len(closes) - after - 1))
                rows.append((announced, -1, -after,
                             (closes[ri + after] / closes[ri] - 1) * 100, True))

    if not rows:
        raise SystemExit("no usable earnings windows")

    rows.sort(key=lambda r: r[0])
    cut = int(len(rows) * (1 - holdout))
    for name, part in (("TRAIN", rows[:cut]), ("HOLDOUT", rows[cut:])):
        print(f"\n{BOLD}{name}{RESET}")
        for exit_off in (4, 5):
            print(f"  {DIM}exit {exit_off} days before the announcement{RESET}")
            for entry_off in (10, 15, 20, 25, 30):
                real = [r for _, e, x, r, c in part
                        if e == entry_off and x == exit_off and not c]
                ctrl = [r for _, e, x, r, c in part
                        if e == entry_off and x == exit_off and c]
                if real:
                    _report(f"enter {entry_off}d before", real, ctrl)

    # ---- what happens AFTER the report -----------------------------------
    print(f"\n{BOLD}AFTER THE REPORT{RESET} {DIM}(held from the announcement "
          f"date forward){RESET}")
    for after in (1, 5, 10, 20):
        real = [r for _, e, x, r, c in rows if e == -1 and x == -after and not c]
        ctrl = [r for _, e, x, r, c in rows if e == -1 and x == -after and c]
        if real:
            _report(f"+{after} days after", real, ctrl)

    # ---- the clustering problem ------------------------------------------
    # Hundreds of companies report inside the same two-week window, so those
    # thousands of "trades" are a few dozen correlated cross-sections, not
    # independent observations. Every t statistic above is inflated by exactly
    # that. Collapsing to ONE number per calendar quarter is the honest
    # correction: it asks whether the effect showed up in most quarters, which
    # a market-drift artifact would not.
    print(f"\n{BOLD}EDGE BY QUARTER{RESET} {DIM}(enter 20d, exit 5d — one "
          f"observation per quarter, which is what is actually independent){RESET}")
    by_quarter: dict[str, list[tuple[float, bool]]] = {}
    for announced, entry_off, exit_off, value, is_control in rows:
        if entry_off != 20 or exit_off != 5:
            continue
        key = f"{announced.year}Q{(announced.month - 1) // 3 + 1}"
        by_quarter.setdefault(key, []).append((value, is_control))

    edges = []
    for key in sorted(by_quarter):
        real = [v for v, c in by_quarter[key] if not c]
        ctrl = [v for v, c in by_quarter[key] if c]
        if len(real) < 20 or len(ctrl) < 20:
            continue
        edge = statistics.fmean(real) - statistics.fmean(ctrl)
        edges.append(edge)
        colour = GREEN if edge > 0 else RED
        print(f"    {key}  n={len(real):>4}  {colour}{edge:+6.2f}%{RESET}")

    if len(edges) > 1:
        mean, se, t_stat = _stats(edges)
        wins = sum(1 for e in edges if e > 0)
        print(f"\n    {BOLD}{len(edges)} quarters{RESET}   mean edge {mean:+.2f}%"
              f"   se {se:.2f}   t {t_stat:+.2f}   positive in {wins}/{len(edges)}")
        print(f"    {DIM}This t is the one to believe. The per-trade figures")
        print(f"    above count the same earnings season hundreds of times.{RESET}")

    print(f"\n{DIM}The control is the same holding period on random dates in the")
    print(f"same names. If 'edge' is near zero the run-up is market drift, not")
    print(f"an earnings effect.{RESET}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--index", choices=sorted(INDEXES), default="sp500")
    p.add_argument("--holdout", type=float, default=0.333)
    p.add_argument("--seed", type=int, default=7)
    a = p.parse_args()
    asyncio.run(run(a.index, a.holdout, a.seed))
