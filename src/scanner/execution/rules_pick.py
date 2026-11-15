"""The rules book's 9:45 pick.

From 2026-11-03 the book is the large-cap 10-7 rotation (execution/satellite.py): the S&P 500
through the scanner's floor, ranked by the return from 10 months ago to 7 months ago, the
top names into the free slots. No model is asked. The `gem-` order ids stay, so a position
opened before that day keeps its stop and its old exits.

`ranked` and `queue_item` are the earlier 12-1 ordering. The live pick uses `momentum_ranked`.

PURE: rows in, new lists out. scripts/rules_pick.py does the reading and the queueing.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from scanner.advisor.gemma_pool import PoolRow


def ranked(rows: Iterable[PoolRow], exclude: Iterable[str] = ()) -> list[PoolRow]:
    """Strongest 12-1 momentum first, unknown momentum last; `exclude` (held, other books) left out."""
    skip = set(exclude)
    keep = [r for r in rows if r.symbol not in skip]
    return sorted(keep, key=lambda r: (r.mom_12_1 is None, -(r.mom_12_1 or 0.0), r.symbol))


def choose(candidates: Sequence[PoolRow], *, refused: Mapping[str, str], slots: int) -> list[PoolRow]:
    """The first `slots` candidates the buy checks did not refuse, in order."""
    if slots <= 0:
        return []
    return [r for r in candidates if r.symbol not in refused][:slots]


def free_slots(*, max_positions: int, held: int, working: int) -> int:
    return max(0, max_positions - held - working)


def queue_item(row: PoolRow, *, rank: int, passing: int) -> dict[str, Any]:
    """A buy_queue item: the plan's only menu choices, and the reason the audit log keeps."""
    mom = f"{row.mom_12_1:+.0f}%" if row.mom_12_1 is not None else "n/a"
    return {"reason": f"rules: #{rank} of {passing} passing the buy checks by 12-1 momentum ({mom})",
            "stop": "normal", "target": "none", "size": "normal"}


def momentum_ranked(rows: Iterable[PoolRow], scores: Mapping[str, float | None],
                    exclude: Iterable[str] = ()) -> list[PoolRow]:
    """Strongest 10-month-to-7-month return first. A name with no score is left out,
    as the lab leaves out a bar whose momentum is missing."""
    skip = set(exclude)
    keep = [r for r in rows if r.symbol not in skip and scores.get(r.symbol) is not None]
    return sorted(keep, key=lambda r: (-float(scores[r.symbol]), r.symbol))


def rotation_item(row: PoolRow, *, rank: int, passing: int, momentum: float) -> dict[str, Any]:
    """A buy_queue item for the 10-7 book. `rotation` is how the 10:00 pass tells it
    from a queue written by the older 12-1 pick. No stop."""
    return {"reason": (f"rules: #{rank} of {passing} by momentum from 10 months ago to 7 months ago "
                       f"({momentum:+.0f}%)"),
            "stop": "none", "target": "none", "size": "slot", "rotation": True}
