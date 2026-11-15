"""Alpaca adapter: account state, and bracket orders built from setups.

PAPER IS THE DEFAULT AND LIVE IS DELIBERATELY AWKWARD
`paper=True` unless someone writes otherwise, and the live host is only
reachable by passing `paper=False` explicitly. There is no environment
variable that silently flips it, because the difference between the two is
whether losses are real.

WHY BRACKET ORDERS
A setup is three prices: entry, stop, target. Sent as one bracket, the broker
holds the stop and the target from the moment the entry fills, and cancels one
when the other triggers. Sent as three separate orders, there is a window in
which the position exists with no stop behind it — and if the process dies in
that window, the position stays naked until someone notices.

IDEMPOTENCY
Every order carries a `client_order_id` derived from the symbol and the
trigger bar's timestamp. Alpaca rejects a duplicate id, so a restart, a retry
or two scans of the same bar cannot open the same trade twice. This is the
cheapest protection against the worst bug an unattended trader can have.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from scanner.execution.sizing import Sizing
from scanner.models import Frozen, TargetTier
from scanner.screens.smc import Setup

PAPER_HOST = "https://paper-api.alpaca.markets"
LIVE_HOST = "https://api.alpaca.markets"

#: Alpaca rejects orders priced below a penny and sub-penny increments above $1.
TICK = 0.01


class BrokerError(RuntimeError):
    """Raised when the broker cannot be reached or refuses a request."""


class Account(Frozen):
    equity: float
    cash: float
    buying_power: float
    status: str = "ACTIVE"
    blocked: bool = False

    @property
    def tradeable(self) -> bool:
        return self.status == "ACTIVE" and not self.blocked and self.equity > 0


def round_tick(price: float) -> float:
    """Alpaca rejects sub-penny prices above $1."""
    return round(price / TICK) * TICK


def client_order_id(setup: Setup, at: datetime) -> str:
    """Stable per (symbol, trigger bar). The duplicate guard.

    Deriving it from the bar rather than from the clock is the point: two
    scans of the same bar produce the same id and the second is rejected,
    where a timestamp would produce two ids and two positions.
    """
    return f"smc-{setup.symbol}-{int(at.timestamp())}"


def order_payloads(setup: Any, sizing: Sizing, at: datetime,
                   entry_offset_pct: float = 0.0) -> list[dict[str, Any]]:
    """Orders for one fresh setup.

    A sweep setup has `target_price` and no `targets`. The 12:03 scan on
    2026-11-02 read `setup.targets`, raised AttributeError, and sent nothing.
    One target is one bracket, so the stop exists when the entry fills.
    A setup that carries tiers still gets the ladder.
    """
    tiers = getattr(setup, "targets", None) or ()
    if not tiers:
        return [bracket_payload(setup, sizing, at, entry_offset_pct=entry_offset_pct)]
    coid = client_order_id(setup, at)
    entry = entry_payload(
        setup.symbol, sizing.shares, setup.entry_price, at, coid,
        entry_offset_pct=entry_offset_pct)
    remaining = sizing.shares
    payloads = [entry]
    for tier in tiers:
        tier_shares = max(1, int(sizing.shares * tier.pct_of_shares))
        remaining -= tier_shares
        payloads.append(take_profit_payload(
            setup.symbol, tier_shares, tier.target_price, tier.label, at, coid))
    payloads.append(stop_payload(
        setup.symbol, max(1, remaining), setup.stop_price, at, coid))
    return payloads


def bracket_payload(setup: Setup, sizing: Sizing, at: datetime,
                    entry_offset_pct: float = 0.0) -> dict[str, Any]:
    """The order body: buy `shares`, with stop and target attached.

    A LIMIT entry at the trigger close, not a market order. The backtest
    assumed a fill at that price; a market order would fill wherever the book
    happens to be and quietly break the correspondence.

    GTC, NOT DAY — THIS WAS A LIVE BUG
    `day` applies to the protective legs as well as the entry, so every stop
    and target CANCELLED AT THE CLOSE. Three positions went into a weekend
    naked, 59,186 dollars of exposure with no stop, and nothing reported an
    error because the orders had done exactly what `day` means.

    Only 54% of these setups resolve inside one session. The other half are
    held overnight by design, so roughly half of all positions were losing
    their protection every night while the backtest assumed a stop that was
    always live. An overnight gap has no floor under it.

    The cost of `gtc` is that an unfilled ENTRY also persists, and a limit
    resting for days could fill long after its setup went stale. `trade.py`
    cancels working entries older than the freshness window for that reason —
    a stale entry we control beats a naked position we do not.

    A CUSHION ABOVE THE SIGNAL (`entry_offset_pct`). The order reaches the
    broker ~3.5 minutes after the trigger bar closes, and these setups bounce
    fast: a limit AT the close only filled 91% on 538 setups replayed on
    1-minute bars, and the misses were net winners (META, LULU, SLB touched
    their limits and never filled). +0.30% filled 99% and earned the most
    total R, more than a market order, while still capping what is paid for a
    spike (limit_minute_test.py). Stop and target are unchanged.
    """
    return {
        "symbol": setup.symbol,
        "qty": str(sizing.shares),
        "side": "buy",
        "type": "limit",
        "limit_price": f"{round_tick(setup.entry_price * (1 + entry_offset_pct / 100)):.2f}",
        "time_in_force": "gtc",
        "order_class": "bracket",
        "take_profit": {"limit_price": f"{round_tick(setup.target_price):.2f}"},
        "stop_loss": {"stop_price": f"{round_tick(setup.stop_price):.2f}"},
        "client_order_id": client_order_id(setup, at),
        "extended_hours": False,
    }


def take_profit_payload(
    symbol: str,
    shares: int,
    target_price: float,
    label: str,
    at: datetime,
    base_coid: str,
) -> dict[str, Any]:
    """One take-profit limit sell order for a tier of the position.

    Each tier sells `shares` at `target_price`. Orders are GTC limit sells
    with `reduce_only=true` so they never sell more than the remaining
    position — if an earlier tier already filled, this one sells whatever is
    left (up to its qty).

    Used for take-profit ladders. Single-target setups still use
    `bracket_payload` above.
    """
    return {
        "symbol": symbol,
        "qty": str(shares),
        "side": "sell",
        "type": "limit",
        "limit_price": f"{round_tick(target_price):.2f}",
        "time_in_force": "gtc",
        "client_order_id": f"{base_coid}-tp{label}",
        "reduce_only": True,
        "extended_hours": False,
    }


def stop_payload(
    symbol: str,
    shares: int,
    stop_price: float,
    at: datetime,
    base_coid: str,
) -> dict[str, Any]:
    """Protective stop sell order.

    Defaults to full position size. Pass `shares` to protect only the
    remaining shares after take-profit tiers have been filled.
    """
    return {
        "symbol": symbol,
        "qty": str(shares),
        "side": "sell",
        "type": "stop",
        "stop_price": f"{round_tick(stop_price):.2f}",
        "time_in_force": "gtc",
        "client_order_id": f"{base_coid}-sl",
        "reduce_only": True,
        "extended_hours": False,
    }


def entry_payload(
    symbol: str,
    shares: int,
    entry_price: float,
    at: datetime,
    client_order_id: str,
    entry_offset_pct: float = 0.0,
) -> dict[str, Any]:
    """Plain limit entry order (no attached legs).

    Used for multi-target setups where the bracket form can't carry multiple
    take-profit legs. The stop and take-profit legs are submitted separately.
    """
    return {
        "symbol": symbol,
        "qty": str(shares),
        "side": "buy",
        "type": "limit",
        "limit_price": f"{round_tick(entry_price * (1 + entry_offset_pct / 100)):.2f}",
        "time_in_force": "gtc",
        "client_order_id": client_order_id,
        "extended_hours": False,
    }


#: Order statuses in which the broker will still act on the order. Anything
#: else — `canceled`, `expired`, `filled`, `rejected` — is a historical
#: record, not protection. The weekend bug produced legs with status
#: `canceled` sitting in the order history looking exactly like stops.
LIVE_STATUSES = frozenset({
    "new", "accepted", "held", "partially_filled",
    "pending_new", "accepted_for_bidding", "pending_replace",
})


class Unprotected(Frozen):
    """An open position with no live stop behind some or all of its shares."""

    symbol: str
    qty: int


def _protecting_shares(orders: list[dict[str, Any]]) -> dict[str, int]:
    """Shares covered by a live protective SELL, per symbol.

    Counts the parent order and its nested legs alike, because a bracket's
    stop arrives as a leg and reading only the top level would report every
    correctly protected position as naked.
    """
    covered: dict[str, int] = {}
    def consider(order: dict[str, Any]) -> None:
        if order.get("side") != "sell":
            return
        if str(order.get("status", "")) not in LIVE_STATUSES:
            return
        # A protective exit is a stop or a limit; a plain market sell is
        # someone closing out, not protection standing by.
        if not (order.get("stop_price") or order.get("limit_price")):
            return
        symbol = str(order.get("symbol", ""))
        qty = int(float(order.get("qty") or 0))
        covered[symbol] = max(covered.get(symbol, 0), qty)

    for order in orders:
        consider(order)
        for leg in (order.get("legs") or []):
            consider(leg)
    return covered


def unprotected(
    positions: list[dict[str, Any]], orders: list[dict[str, Any]]
) -> tuple[Unprotected, ...]:
    """Open positions whose shares are not all behind a live stop.

    THE DETECTOR THIS REPO DID NOT HAVE
    A cancelled stop raises nothing. It is an absence, and absences are only
    found by looking for them — which is why three positions sat naked for
    two sessions with every log line reporting success. This is the check
    that turns that silence into a message.

    Deliberately compares SHARE COUNTS, not just symbols: a stop for part of
    a position leaves the remainder just as naked as no stop at all, and a
    symbol-level check would call it covered.
    """
    covered = _protecting_shares(orders)
    naked: list[Unprotected] = []
    for position in positions:
        symbol = str(position.get("symbol", ""))
        held = abs(int(float(position.get("qty") or 0)))
        exposed = held - covered.get(symbol, 0)
        if exposed > 0:
            naked.append(Unprotected(symbol=symbol, qty=exposed))
    return tuple(naked)


def protective_payload(
    symbol: str, qty: int, *, stop: float, target: float
) -> dict[str, Any]:
    """An OCO sell that re-arms a position which lost its bracket.

    WHY OCO AND NOT A BRACKET
    A bracket attaches protection to an ENTRY. These positions are already
    filled — there is no entry left to hang legs from, so the bracket form is
    simply not available. OCO is the exit-only equivalent: a stop and a
    target where filling either cancels the other, which is the same shape
    the bracket's legs had before the close cancelled them.

    GTC for the reason in `bracket_payload`: a `day` OCO would re-create the
    original bug at 16:00 tonight.
    """
    if stop >= target:
        raise ValueError(
            f"{symbol}: stop {stop} is not below target {target} — "
            "arguments are almost certainly swapped"
        )
    return {
        "symbol": symbol,
        "qty": str(qty),
        "side": "sell",
        "type": "limit",
        "time_in_force": "gtc",
        "order_class": "oco",
        "take_profit": {"limit_price": f"{round_tick(target):.2f}"},
        "stop_loss": {"stop_price": f"{round_tick(stop):.2f}"},
    }


class Alpaca:
    """Thin async client. Only the endpoints this project needs."""

    def __init__(
        self,
        key_id: str,
        secret_key: str,
        client: httpx.AsyncClient,
        *,
        paper: bool = True,
    ):
        if not key_id or not secret_key:
            raise BrokerError("Alpaca API keys are not configured")
        self.paper = paper
        self.host = PAPER_HOST if paper else LIVE_HOST
        self._client = client
        self._headers = {
            "APCA-API-KEY-ID": key_id,
            "APCA-API-SECRET-KEY": secret_key,
            "accept": "application/json",
        }
        self.last_request_id = ""

    async def _get(self, path: str, **params) -> Any:
        try:
            response = await self._client.get(
                f"{self.host}{path}", headers=self._headers,
                params=params or None, timeout=30.0,
            )
            self.last_request_id = response.headers.get("X-Request-ID", "")
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            suffix = f" (request {self.last_request_id})" if self.last_request_id else ""
            raise BrokerError(f"GET {path} failed{suffix}: {exc}") from exc

    async def _delete(self, path: str) -> None:
        """The only mutating verbs here besides `submit` — and like `submit`,
        paper-only by construction, since `host` is the paper endpoint."""
        try:
            response = await self._client.delete(
                f"{self.host}{path}", headers=self._headers, timeout=30.0
            )
            self.last_request_id = response.headers.get("X-Request-ID", "")
            if response.status_code not in (200, 204, 207):
                raise BrokerError(f"DELETE {path}: {response.text[:160]}")
        except httpx.HTTPError as exc:
            raise BrokerError(f"DELETE {path} failed: {exc}") from exc

    async def close_position(self, symbol: str) -> None:
        """Close at market. Used only for the backtest's time limit."""
        await self._delete(f"/v2/positions/{symbol}")

    async def cancel_order(self, order_id: str) -> None:
        await self._delete(f"/v2/orders/{order_id}")

    async def replace_stop(self, order_id: str, stop_price: float) -> dict[str, Any]:
        """Move a working stop (a bracket/OCO leg included) to `stop_price` -- the
        profit lock's raise (execution/profit_lock.py). Alpaca replaces the order:
        the answer carries the NEW order id."""
        try:
            response = await self._client.patch(
                f"{self.host}/v2/orders/{order_id}", headers=self._headers,
                json={"stop_price": f"{round_tick(stop_price):.2f}"}, timeout=30.0,
            )
            self.last_request_id = response.headers.get("X-Request-ID", "")
            if response.status_code == 422:
                raise BrokerError(f"stop replace rejected: {response.text[:200]}")
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise BrokerError(f"PATCH /v2/orders/{order_id} failed: {exc}") from exc

    async def account(self) -> Account:
        raw = await self._get("/v2/account")
        return Account(
            equity=float(raw.get("equity") or 0.0),
            cash=float(raw.get("cash") or 0.0),
            buying_power=float(raw.get("buying_power") or 0.0),
            status=str(raw.get("status") or "UNKNOWN"),
            blocked=bool(
                raw.get("trading_blocked")
                or raw.get("account_blocked")
                or raw.get("trade_suspended_by_user")
            ),
        )

    async def market_open(self) -> bool:
        return bool((await self._get("/v2/clock")).get("is_open"))

    async def held_symbols(self) -> frozenset[str]:
        return frozenset(p["symbol"] for p in await self._get("/v2/positions"))

    async def positions_with_origin(self) -> tuple[tuple[str | None, float], ...]:
        """Open positions as (opening client_order_id, market value).

        Alpaca's position record does not name the order that created it, so
        the link is rebuilt from order history: a position is attributed to the
        strategy when this account has a filled order for that symbol carrying
        our prefix. A symbol we never traded reports `None` and is treated as
        somebody else's money — which is what a manual purchase in the same
        account actually is.
        """
        positions = await self._get("/v2/positions")
        orders = await self._get("/v2/orders", status="all", limit=500,
                                 nested="true")
        origin: dict[str, str] = {}
        for order in orders:
            coid = str(order.get("client_order_id", ""))
            symbol = str(order.get("symbol", ""))
            if coid and symbol and symbol not in origin:
                origin[symbol] = coid
        return tuple(
            (origin.get(str(p.get("symbol", ""))), float(p.get("market_value", 0.0)))
            for p in positions
        )

    async def cancel_stale_entries(self, older_than_minutes: float) -> tuple[str, ...]:
        """Cancel unfilled BUY entries older than the freshness window.

        The price of GTC brackets: the protective legs now survive the close,
        and so does an entry that never filled. A limit resting for days would
        eventually fill on a setup that expired long ago, at a price the screen
        would no longer produce — a trade the backtest never modelled.

        Only unfilled buys are touched. Protective sell legs are never
        cancelled here, which is the whole point of the GTC change. Only the
        sweep's own (`smc-`) buys: since it shares the account with the other
        books (2026-11-02), a resting index-core or rules-book buy is theirs.
        """
        cancelled: list[str] = []
        for order in await self._get("/v2/orders", status="open", nested="true"):
            if order.get("side") != "buy" or float(order.get("filled_qty") or 0) > 0:
                continue
            if not str(order.get("client_order_id") or "").startswith("smc-"):
                continue
            submitted = order.get("submitted_at") or order.get("created_at")
            if not submitted:
                continue
            try:
                when = datetime.fromisoformat(str(submitted).replace("Z", "+00:00"))
            except ValueError:
                continue
            age = (datetime.now(timezone.utc) - when).total_seconds() / 60.0
            if age <= older_than_minutes:
                continue
            try:
                await self.cancel_order(str(order.get("id", "")))
                cancelled.append(str(order.get("symbol", "")))
            except BrokerError:
                continue      # a fill can race the cancel; leave it alone
        return tuple(cancelled)

    async def pending_symbols(self) -> frozenset[str]:
        orders = await self._get("/v2/orders", status="open", nested="true")
        return frozenset(o["symbol"] for o in orders)

    async def unprotected_positions(self) -> tuple[Unprotected, ...]:
        """Open positions with no live stop. The reconciliation check.

        Thin by design: the judgement lives in `unprotected`, which is pure
        and tested against the exact order shapes that fooled this repo for
        two sessions.
        """
        return unprotected(
            await self._get("/v2/positions"),
            await self._get("/v2/orders", status="open", nested="true"),
        )

    async def submit(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Send one order. Never called for live accounts by this repo's
        scripts without an explicit, interactive opt-in from the operator."""
        try:
            response = await self._client.post(
                f"{self.host}/v2/orders", headers=self._headers,
                json=payload, timeout=30.0,
            )
            self.last_request_id = response.headers.get("X-Request-ID", "")
            if response.status_code >= 400:
                # Keep the body. A 403 used to become "Client error 403 Forbidden"
                # and the broker's reason (a wash trade, on the QQQ add) was lost.
                suffix = f" (request {self.last_request_id})" if self.last_request_id else ""
                body = (response.text or "").replace("\n", " ")[:300]
                if response.status_code == 422:
                    raise BrokerError(f"rejected{suffix}: {body}")
                raise BrokerError(f"order failed{suffix}: {response.status_code} {body}")
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            suffix = f" (request {self.last_request_id})" if self.last_request_id else ""
            raise BrokerError(f"order failed{suffix}: {exc}") from exc
