"""Which strategy owns each broker position, read from order history.

WHY THIS EXISTS (WORKLIST §37)
The weekend flatten closed EVERY position in the account and the hold-limit
closer closed ANY position older than 60 bars. Both were written when one
strategy traded here. With manual picks and the momentum engine in the same
account, a 3-month momentum hold would have been sold on its first Friday and
a manual pick bought on Tuesday the same week.

HOW OWNERSHIP IS KNOWN
Alpaca's position record does not name the order that opened it, but every
order this repo sends carries a prefixed client_order_id, and Alpaca keeps it.
The NEWEST FILLED BUY per symbol names the owner. Protective legs are sells, so
they never decide ownership; a position re-entered by a different strategy
belongs to whoever bought last.

A symbol with no prefixed buy is UNKNOWN: bought by hand in the Alpaca
dashboard, or from before prefixes existed. Unknown positions are never
closed by a rule written for a strategy -- whoever bought them owns the exit.

PURE. Nothing here reaches a broker.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

SMC = "smc"
MANUAL = "manual"
MOMENTUM = "momentum"
#: The operator's list, kept in sync automatically (execution/picks.py).
PICKS = "picks"
#: Gemma's own book: it chooses names from core.txt, the system does the rest.
GEMMA = "gemma"
#: The opening-range breakout day book (execution/orb.py): rules, flat by the close.
ORB = "orb"
#: The SPY core (execution/satellite.py): half the account, held; one GTC stop from 2026-10-30.
CORE = "core"
#: The small-cap book (execution/small_book.py, scripts/small_pick.py): S&P 600, 10%, from 2026-10-27.
SMALL = "small"
UNKNOWN = "unknown"

#: client_order_id prefix -> strategy. The prefix is the whole contract.
PREFIXES: dict[str, str] = {"smc-": SMC, "man-": MANUAL, "mom-": MOMENTUM,
                             "pck-": PICKS, "gem-": GEMMA, "orb-": ORB,
                             "cor-": CORE, "sml-": SMALL}

STRATEGIES = (SMC, MANUAL, MOMENTUM, PICKS, GEMMA, ORB, CORE, SMALL)


def prefix_for(strategy: str) -> str:
    """The client_order_id prefix a strategy must use."""
    for prefix, name in PREFIXES.items():
        if name == strategy:
            return prefix
    raise ValueError(f"unknown strategy {strategy!r}")


def strategy_of(client_order_id: str | None) -> str:
    """The strategy a client_order_id belongs to, or UNKNOWN."""
    coid = str(client_order_id or "")
    for prefix, name in PREFIXES.items():
        if coid.startswith(prefix):
            return name
    return UNKNOWN


def _filled_buys(orders: Iterable[Mapping[str, Any]]):
    """Every filled buy, flattening nested bracket legs."""
    for order in orders:
        for candidate in (order, *(order.get("legs") or ())):
            if str(candidate.get("side", "")).lower() != "buy":
                continue
            if not candidate.get("filled_at"):
                continue
            yield candidate


def owners(orders: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    """{symbol: strategy} from order history (status=all).

    The newest filled buy wins. ISO timestamps from one broker compare
    correctly as strings, which avoids a parse that could fail on one row and
    drop a symbol from the map.
    """
    newest: dict[str, tuple[str, str]] = {}
    for order in _filled_buys(orders):
        symbol = str(order.get("symbol") or "")
        when = str(order.get("filled_at"))
        if symbol and (symbol not in newest or when > newest[symbol][0]):
            newest[symbol] = (when, strategy_of(order.get("client_order_id")))
    return {symbol: strategy for symbol, (_, strategy) in newest.items()}


#: Books with their own sleeve and slot count: they never use the shared
#: max_positions slots (manual + picks + smc) and are sized on their own share.
OWN_SLEEVE = (MOMENTUM, GEMMA, ORB, CORE)


def owner(symbol: str, owner_map: Mapping[str, str]) -> str:
    return owner_map.get(symbol, UNKNOWN)


def positions_for(positions: Iterable[Mapping[str, Any]],
                  owner_map: Mapping[str, str],
                  strategies: Iterable[str]) -> list[Mapping[str, Any]]:
    """The positions owned by any of `strategies`."""
    wanted = set(strategies)
    return [p for p in positions
            if owner(str(p.get("symbol") or ""), owner_map) in wanted]


def orders_for(open_orders: Iterable[Mapping[str, Any]],
               owner_map: Mapping[str, str],
               strategies: Iterable[str]) -> list[Mapping[str, Any]]:
    """Working orders that belong to `strategies`.

    An order belongs if its own id carries the strategy's prefix (an unfilled
    entry), or if it works a symbol that strategy owns (a protective leg; the
    OCO protect.py sends carries no prefix). Anything else -- another
    strategy's entry or stop -- is left alone.
    """
    wanted = set(strategies)
    kept = []
    for order in open_orders:
        own = strategy_of(order.get("client_order_id"))
        held_by = owner(str(order.get("symbol") or ""), owner_map)
        if own in wanted or (own == UNKNOWN and held_by in wanted):
            kept.append(order)
    return kept
