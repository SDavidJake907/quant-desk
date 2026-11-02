"""Live scan: which stocks are AT this setup right now.

    python scripts/find_setups.py                          # S&P 500, 30m
    python scripts/find_setups.py --minutes 240            # 4h instead
    python scripts/find_setups.py --value-sweep            # profile filter on
    python scripts/find_setups.py --index sp600 --fresh 2  # small caps, last 2 bars

Reports setups whose trigger bar is among the last `--fresh` bars, so the list
is what just fired rather than a history lesson. Tradeability filters come
first, because a perfect pattern on a name that trades 40,000 shares a day is
not a trade you can take.

WHAT THE BACKTEST SAYS, MEASURED ON THE S&P 500
The RAW pattern has no edge: across five runs (4h at k=2/3/4, 30m at k=2/3)
its advantage over random entries with the same stops and targets was -0.039R
to +0.116R, and the SIGN FLIPPED with the swing size. That is noise.

With --value-sweep the picture improves and becomes consistent: all five runs
land non-negative, at +0.001R, +0.003R, +0.058R, +0.024R and +0.169R. The
filter removes the losing configurations. But four of those five are within
noise of zero, and the one real number (+0.169R, 30m, k=2, 662 trades) comes
from the SHALLOWEST data in the study — 86 days, Yahoo's sub-hourly cap.

So: a small consistent tilt, one promising result resting on the weakest
sample. Not established. Treat this output as a watchlist to study.
"""

from __future__ import annotations

import argparse
import asyncio

import httpx

from scanner.features import profile
from scanner.features.intraday import fetch_intraday, max_range
from scanner.screens.smc import Setup, is_live_setup, scan
from scanner.execution.policy import TradingPolicy
from scanner.sources import composite
from scanner.sources.index_history import COMPOSITE_1500, SP400, SP500, SP600

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"
GREEN, RED, YELLOW, CYAN = "\033[32m", "\033[31m", "\033[33m", "\033[36m"

INDEXES = {"sp500": (SP500,), "sp400": (SP400,), "sp600": (SP600,),
           "all": COMPOSITE_1500}

#: Day-trading practicality. A setup you cannot get filled on is not a setup.
MIN_PRICE = 5.0
MIN_DOLLAR_VOLUME = 5_000_000.0   # average per bar
MIN_RANGE_PCT = 0.4               # average bar range, as % of price


class Candidate:
    """A setup that survived the tradeability filters, plus its context."""

    def __init__(self, setup: Setup, bars, bars_ago: int, dollar_volume: float,
                 range_pct: float, prof: profile.VolumeProfile | None):
        self.setup = setup
        self.bars = bars
        self.bars_ago = bars_ago
        self.dollar_volume = dollar_volume
        self.range_pct = range_pct
        self.profile = prof


def _tradeable(bars, window: int = 20) -> tuple[bool, float, float]:
    """Price, liquidity and range checks. Returns (ok, $vol/bar, range%)."""
    recent = slice(-window, None)
    price = bars.closes[-1]
    volumes = bars.volumes[recent]
    closes = bars.closes[recent]
    dollar_volume = (
        sum(v * c for v, c in zip(volumes, closes)) / len(volumes) if volumes else 0.0
    )
    ranges = [
        (h - low) / c * 100.0
        for h, low, c in zip(bars.highs[recent], bars.lows[recent], closes)
        if c
    ]
    range_pct = sum(ranges) / len(ranges) if ranges else 0.0
    ok = (
        price >= MIN_PRICE
        and dollar_volume >= MIN_DOLLAR_VOLUME
        and range_pct >= MIN_RANGE_PCT
    )
    return ok, dollar_volume, range_pct


async def _load(symbols: tuple[str, ...], minutes: int, range_: str):
    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(
            *(fetch_intraday(s, client, minutes=minutes, range_=range_) for s in symbols),
            return_exceptions=True,
        )
    loaded, failed = {}, 0
    for symbol, outcome in zip(symbols, results):
        if isinstance(outcome, Exception):
            failed += 1
        else:
            loaded[symbol] = outcome
    return loaded, failed


def _show(c: Candidate) -> None:
    s = c.setup
    bars = c.bars
    when = bars.times[s.trigger_index]
    age = "current bar" if c.bars_ago == 0 else f"{c.bars_ago} bars ago"
    rr_colour = GREEN if s.reward_risk >= 2 else YELLOW

    print(f"\n{BOLD}{s.symbol:<6}{RESET} {DIM}{when:%Y-%m-%d %H:%M} ({age}){RESET}")
    print(f"   trigger  {s.entry_price:>9,.2f}   "
          f"stop {RED}{s.stop_price:>9,.2f}{RESET}   "
          f"target {GREEN}{s.target_price:>9,.2f}{RESET}   "
          f"{rr_colour}{s.reward_risk:.2f}R{RESET}")
    print(f"   {DIM}risk {s.risk / s.entry_price * 100:.2f}% of price   "
          f"sweep {s.sweep_price:,.2f}   CHoCH {s.choch_price:,.2f}   "
          f"HL {s.higher_low_price:,.2f}{RESET}")
    if c.profile is not None:
        p = c.profile
        inside = "inside value" if p.contains(s.entry_price) else "outside value"
        print(f"   {CYAN}VAH {p.value_area_high:,.2f}  PoC {p.poc_price:,.2f}  "
              f"VAL {p.value_area_low:,.2f}{RESET}  {DIM}entry {inside}{RESET}")
    print(f"   {DIM}${c.dollar_volume / 1e6:,.1f}M per bar   "
          f"avg bar range {c.range_pct:.2f}%{RESET}")


async def main(
    symbols: tuple[str, ...], minutes: int, k: int, fresh: int, lookback: int,
    min_rr: float, min_risk_pct: float, value_sweep: bool, show: int,
    trend_context: bool = False, rsi_context: bool = False,
    macd_context: bool = False,
) -> None:
    range_ = max_range(minutes)
    print(f"{DIM}scanning {len(symbols)} symbols on {minutes}m bars ({range_} of history){RESET}")
    loaded, failed = await _load(symbols, minutes, range_)
    print(f"{DIM}loaded {len(loaded)} ({failed} unavailable){RESET}")

    candidates: list[Candidate] = []
    illiquid = 0
    for symbol, bars in loaded.items():
        ok, dollar_volume, range_pct = _tradeable(bars)
        setups = scan(
            bars, k=k, lookback=lookback, min_reward_risk=min_rr,
            min_risk_pct=min_risk_pct, require_value_sweep=value_sweep,
            require_trend_context=trend_context,
            require_rsi_context=rsi_context,
            require_macd_context=macd_context,
        )
        recent = [
            s for s in setups
            if len(bars) - 1 - s.trigger_index < fresh
            and is_live_setup(bars, s)
        ]
        if not recent:
            continue
        if not ok:
            illiquid += len(recent)
            continue
        for setup in recent:
            try:
                prof = profile.build(
                    bars, setup.sweep_index - lookback, setup.trigger_index
                )
            except profile.ProfileError:
                prof = None
            candidates.append(
                Candidate(setup, bars, len(bars) - 1 - setup.trigger_index,
                          dollar_volume, range_pct, prof)
            )

    candidates.sort(key=lambda c: (c.bars_ago, -c.setup.reward_risk))
    filters = f"k={k}, R:R>={min_rr}, stop>={min_risk_pct}%, last {fresh} bars"
    if value_sweep:
        filters += ", value-area sweep required"
    print(f"\n{BOLD}{len(candidates)} tradeable setups{RESET} {DIM}({filters}; "
          f"{illiquid} more failed the liquidity floor){RESET}")

    for candidate in candidates[:show]:
        _show(candidate)

    if not candidates:
        print(f"{DIM}  nothing triggered in the last {fresh} bars{RESET}")

    print(f"\n{YELLOW}This is a watchlist, not a signal.{RESET}")
    if value_sweep:
        print(f"{DIM}  With this filter the pattern beat random by +0.00R to +0.17R across{RESET}")
        print(f"{DIM}  five S&P 500 runs — consistently non-negative, mostly near zero, and{RESET}")
        print(f"{DIM}  the best number rests on 86 days of data. Promising, not proven.{RESET}")
    else:
        print(f"{DIM}  Unfiltered, this pattern showed NO edge over random entries and the{RESET}")
        print(f"{DIM}  sign flipped with the swing size k. Add --value-sweep.{RESET}")
    print(f"{DIM}  A very high R:R here means a tight stop, not a good trade.{RESET}")


#: Defaults come from the policy, so "what we test" and "what we trade"
#: cannot drift apart without a test failing.
VALIDATED = TradingPolicy()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", nargs="*", default=None)
    p.add_argument("--index", choices=list(INDEXES), default="sp500")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--minutes", type=int, default=VALIDATED.interval_minutes, help="30 = 30m, 240 = 4h")
    p.add_argument("--k", type=int, default=VALIDATED.swing_k)
    p.add_argument("--fresh", type=int, default=VALIDATED.fresh_bars,
                   help="trigger must be within this many bars of now")
    p.add_argument("--lookback", type=int, default=60)
    p.add_argument("--min-rr", type=float, default=VALIDATED.min_reward_risk)
    p.add_argument("--min-risk-pct", type=float, default=VALIDATED.min_risk_pct,
                   help="skip stops tighter than this %% of price; costs eat them")
    p.add_argument("--value-sweep", action=argparse.BooleanOptionalAction,
                   default=VALIDATED.require_value_sweep,
                   help="require the sweep to leave and reclaim the value area "
                        "(on by default, as validated; --no-value-sweep for the "
                        "raw pattern)")
    p.add_argument("--trend-context", action="store_true",
                   help="require bullish EMA21/50/200 alignment")
    p.add_argument("--rsi-context", action="store_true",
                   help="require RSI14 between 40 and 65")
    p.add_argument("--macd-context", action="store_true",
                   help="require positive MACD confirmation")
    p.add_argument("--show", type=int, default=15)
    a = p.parse_args()

    async def _symbols() -> tuple[str, ...]:
        if a.symbols:
            return tuple(s.upper() for s in a.symbols)
        async with httpx.AsyncClient() as client:
            universe = await composite.load_universe(client, INDEXES[a.index])
        chosen = tuple(m.symbol for m in universe)
        return chosen[: a.limit] if a.limit else chosen

    asyncio.run(
        main(asyncio.run(_symbols()), a.minutes, a.k, a.fresh, a.lookback,
             a.min_rr, a.min_risk_pct, a.value_sweep, a.show,
             a.trend_context, a.rsi_context, a.macd_context)
    )
