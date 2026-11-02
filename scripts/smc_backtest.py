"""Backtest the SMC sweep -> CHoCH -> BOS setup on 4-hour bars.

    python scripts/smc_backtest.py                      # default basket
    python scripts/smc_backtest.py --symbols MU NVDA AMD
    python scripts/smc_backtest.py --index sp500 --limit 120
    python scripts/smc_backtest.py --k 2 3 4            # parameter sweep

Reads the same question the coiled screen was asked: does the pattern beat
identical trade management from random entries? If it does not, the stops and
targets were doing the work and the structure was decoration.
"""

from __future__ import annotations

import argparse
import asyncio
import random

import httpx

from scanner.backtest.setups import (
    DEFAULT_MAX_BARS,
    STOP,
    TARGET,
    TIMEOUT,
    Report,
    random_control,
    resolve_setups,
)
from scanner.features.intraday import fetch_intraday, max_range, source_interval
from scanner.screens.smc import scan
from scanner.execution.policy import TradingPolicy
from scanner.sources import composite
from scanner.sources.index_history import SP400, SP500, SP600

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"
GREEN, RED, YELLOW = "\033[32m", "\033[31m", "\033[33m"

DEFAULT_BASKET = ("MU", "NVDA", "AMD", "AVGO", "TSM", "AAPL", "MSFT", "META",
                  "AMZN", "GOOGL", "TSLA", "NFLX", "CRM", "ORCL", "QCOM")
INDEXES = {"sp500": (SP500,), "sp400": (SP400,), "sp600": (SP600,)}


def _report(label: str, report: Report, colour: str) -> None:
    if not report.count:
        print(f"  {YELLOW}{label}: no resolved trades{RESET}")
        return
    sign = GREEN if report.expectancy > 0 else RED
    print(f"  {BOLD}{label:<8}{RESET} {report.count:5} trades   "
          f"win {report.win_rate:5.1f}%   "
          f"expectancy {sign}{report.expectancy:+.3f}R{RESET}   "
          f"median {report.median_r:+.2f}R   total {report.total_r:+.0f}R")
    print(f"           {DIM}target {report.share(TARGET):4.1f}%  "
          f"stop {report.share(STOP):4.1f}%  timeout {report.share(TIMEOUT):4.1f}%   "
          f"stdev {report.stdev_r:.2f}R   naive t {report.t_stat:+.2f}{RESET}")


async def collect(symbols: tuple[str, ...], minutes: int, range_: str):
    bars_by_symbol = {}
    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(
            *(fetch_intraday(s, client, minutes=minutes, range_=range_) for s in symbols),
            return_exceptions=True,
        )
    missing = []
    for symbol, outcome in zip(symbols, results):
        if isinstance(outcome, Exception):
            missing.append(symbol)
        else:
            bars_by_symbol[symbol] = outcome
    return bars_by_symbol, missing


async def main(
    symbols: tuple[str, ...], ks: tuple[int, ...], minutes: int, range_: str | None,
    max_bars: int, min_rr: float, min_risk_pct: float, lookback: int,
    control_draws: int, value_sweep: bool = True, holdout_fraction: float | None = None,
    require_trend_context: bool = False, require_rsi_context: bool = False,
    require_macd_context: bool = False,
) -> None:
    range_ = range_ or max_range(minutes)
    print(f"{DIM}fetching {len(symbols)} symbols, {minutes}m bars "
          f"from {source_interval(minutes)} source, {range_}{RESET}")
    bars_by_symbol, missing = await collect(symbols, minutes, range_)
    if not bars_by_symbol:
        raise SystemExit("no price history loaded")

    depth = min(len(b) for b in bars_by_symbol.values())
    print(f"{DIM}loaded {len(bars_by_symbol)} symbols "
          f"({len(missing)} unavailable), shallowest series {depth} bars{RESET}")
    print(f"{DIM}entry: close of the trigger bar | stop: higher low | "
          f"target: range high | time limit: {max_bars} bars{RESET}")

    for k in ks:
        periods = (("full", 0.0, 1.0),)
        if holdout_fraction is not None:
            periods = (("train", 0.0, 1.0 - holdout_fraction),
                       ("holdout", 1.0 - holdout_fraction, 1.0))
        for label, lower, upper in periods:
            real, control = [], []
            rng = random.Random(42)
            symbols_with_setups = 0
            for symbol, bars in bars_by_symbol.items():
                start_index = int(len(bars) * lower)
                end_index = int(len(bars) * upper)
                setups = scan(bars, k=k, lookback=lookback, min_reward_risk=min_rr,
                              min_risk_pct=min_risk_pct, require_value_sweep=value_sweep,
                              require_trend_context=require_trend_context,
                              require_rsi_context=require_rsi_context,
                              require_macd_context=require_macd_context)
                if not setups:
                    continue
                symbols_with_setups += 1
                real.extend(resolve_setups(
                    bars, setups, max_bars=max_bars,
                    start_index=start_index, end_index=end_index,
                ))
                control.extend(random_control(
                    bars, setups, rng, max_bars=max_bars, draws=control_draws,
                    start_index=start_index, end_index=end_index,
                ))

            suffix = f" {label}" if holdout_fraction is not None else ""
            print(f"\n{BOLD}k = {k}{suffix}{RESET}  "
                  f"{DIM}({symbols_with_setups} of {len(bars_by_symbol)} symbols produced a setup){RESET}")
            _report("pattern", Report(label="pattern", outcomes=tuple(real)), GREEN)
            _report("random", Report(label="random", outcomes=tuple(control)), DIM)

            pattern = Report(label="p", outcomes=tuple(real))
            rand = Report(label="r", outcomes=tuple(control))
            if pattern.count and rand.count:
                edge = pattern.expectancy - rand.expectancy
                verdict = (f"{GREEN}beats random by {edge:+.3f}R{RESET}" if edge > 0
                           else f"{RED}loses to random by {edge:+.3f}R{RESET}")
                print(f"  -> {verdict}")

    print(f"\n{YELLOW}Before this number is used for anything:{RESET}")
    print(f"{DIM}  - no commission, no spread, no slippage; intraday spreads are wider{RESET}")
    print(f"{DIM}  - a bar touching stop and target both is scored as a stop, always{RESET}")
    print(f"{DIM}  - ~2 years of 60-minute history is Yahoo's limit: one regime{RESET}")
    print(f"{DIM}  - overlapping setups on one symbol are not independent; t is an upper bound{RESET}")
    print(f"{DIM}  - bars are session-aligned here and may not match your platform{RESET}")


#: Defaults come from the policy, so "what we test" and "what we trade"
#: cannot drift apart without a test failing.
VALIDATED = TradingPolicy()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", nargs="*", default=None)
    p.add_argument("--index", choices=list(INDEXES), default=None)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--k", type=int, nargs="+", default=[VALIDATED.swing_k])
    p.add_argument("--minutes", type=int, default=VALIDATED.interval_minutes,
                   help="bar size: 240 = 4h, 30 = 30m (60d of history only)")
    p.add_argument("--range", dest="range_", default=None)
    p.add_argument("--max-bars", type=int, default=DEFAULT_MAX_BARS)
    p.add_argument("--min-rr", type=float, default=VALIDATED.min_reward_risk)
    p.add_argument("--min-risk-pct", type=float, default=VALIDATED.min_risk_pct)
    p.add_argument("--value-sweep", action=argparse.BooleanOptionalAction,
                   default=VALIDATED.require_value_sweep,
                   help="require the sweep to leave and reclaim the value area "
                        "(on by default, as validated; --no-value-sweep tests "
                        "the raw pattern)")
    p.add_argument("--lookback", type=int, default=60)
    p.add_argument("--control-draws", type=int, default=5)
    p.add_argument("--holdout", type=float, default=None, metavar="FRACTION",
                   help="evaluate chronologically: train on 1-FRACTION, then holdout")
    p.add_argument("--trend-context", action="store_true",
                   help="require bullish 4H EMA21/50/200 alignment")
    p.add_argument("--rsi-context", action="store_true",
                   help="require 4H RSI14 between 40 and 65")
    p.add_argument("--macd-context", action="store_true",
                   help="require positive 4H MACD confirmation")
    a = p.parse_args()
    if a.holdout is not None and not 0.0 < a.holdout < 1.0:
        p.error("--holdout must be between 0 and 1")

    async def _resolve_symbols() -> tuple[str, ...]:
        if a.symbols:
            return tuple(s.upper() for s in a.symbols)
        if a.index:
            async with httpx.AsyncClient() as client:
                universe = await composite.load_universe(client, INDEXES[a.index])
            chosen = tuple(m.symbol for m in universe)
            return chosen[: a.limit] if a.limit else chosen
        return DEFAULT_BASKET

    asyncio.run(
        main(asyncio.run(_resolve_symbols()), tuple(a.k), a.minutes, a.range_,
             a.max_bars, a.min_rr, a.min_risk_pct, a.lookback,
             a.control_draws, a.value_sweep, a.holdout,
             a.trend_context, a.rsi_context, a.macd_context)
    )
