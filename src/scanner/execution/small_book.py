"""The small-cap book: 10% of the account in up to 4 S&P SmallCap 600 names.

Operator 2026-10-27: "lets look into penny stocks.. low caps. and make a gate for it %10 of the
account"; chose S&P 600 members priced $5+, funded from the rules book, IWM above its 200-day as
the gate. Its checks are satellite.buy_refusal: top-third 12-1 momentum within this pool,
6-month return up, above the 50-day, up over the last month, no earnings within the buffer, 2 a
sector, the 30-day re-buy rule. Its exits are plan_run.exit_pass with the full 2.5 ATR stop.
`sml-` order ids.

CHOSEN, not measured: S&P 600 momentum beat random by +0.83 pts a 3-month hold, t 1.28, and lost
to it 2017-2020 (WORKLIST §31). Graded forward against IWM.

PURE: lists in, new lists out. scripts/small_pick.py does the reading and the queueing.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from scanner.advisor.gemma_pool import PoolRow

#: The 9:45 pick's queue; plan_run.buy_pass sends it from 10:00 ET (as the rules book's).
QUEUE_PATH = "data/small/buy_queue.json"
#: Days the dollar-volume floor averages over.
WINDOW = 20

Check = Callable[[PoolRow, Mapping[str, int]], "str | None"]


def liquid(closes: Sequence[float], volumes: Sequence[float], *, min_price: float,
           min_dollar_volume: float, window: int = WINDOW) -> bool:
    """Last close at or above the price floor and average daily dollar volume at or above its floor."""
    if len(closes) < window or len(volumes) < window or closes[-1] < min_price:
        return False
    traded = [c * v for c, v in zip(closes[-window:], volumes[-window:])]
    return sum(traded) / window >= min_dollar_volume


def pick(ranked: Sequence[PoolRow], *, check: Check, held_sectors: Mapping[str, int],
         slots: int) -> tuple[list[PoolRow], dict[str, str]]:
    """Walk `ranked` in order: each name `check` passes is taken (and counts toward its sector for
    the next) until `slots` are filled. (picks, {symbol: why refused})."""
    sectors = dict(held_sectors)
    picks: list[PoolRow] = []
    refused: dict[str, str] = {}
    for row in ranked:
        if len(picks) >= slots:
            break
        why = check(row, sectors)
        if why:
            refused[row.symbol] = why
            continue
        picks.append(row)
        sectors = {**sectors, row.sector: sectors.get(row.sector, 0) + 1}
    return picks, refused


def queue_item(row: PoolRow, *, rank: int, passing: int) -> dict[str, Any]:
    """A buy-queue item: the plan's only menu choices, and the reason the audit log keeps."""
    mom = f"{row.mom_12_1:+.0f}%" if row.mom_12_1 is not None else "n/a"
    return {"reason": f"small caps: #{rank} of {passing} by 12-1 momentum ({mom})",
            "stop": "normal", "target": "none", "size": "normal"}
