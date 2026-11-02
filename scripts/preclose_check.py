"""Before the close: score every open position on four signs; sell the SPY core only when it is
losing and all four confirm. Rules in scanner/execution/preclose.py.

    python scripts/preclose_check.py                                   # dry run, scores only
    python scripts/preclose_check.py --submit --only-if-closing-within 25

Two triggers share the job (08:40 and 11:40 AK) for the 13:00 ET early closes, as the flatten
does: only the run within 25 minutes of the bell acts. Writes logs/preclose/latest.md (the
command center shows it) and a `preclose_check` audit event. A failed read of any input fails
that sign -- the strict rule then holds, and the core's own stop is still underneath.

THE SALE carries a `cor-stop-` client_order_id, so plan_run.core_stopped_on sees it and the core
re-buys only on a new 20-day closing high, exactly as after its stop fills. The working stop is
cancelled first (it reserves the shares); if the sale is then refused, peak watch re-arms the
stop within five minutes.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

import httpx

from scanner.config import settings
from scanner.execution import ownership
from scanner.execution import preclose as pc
from scanner.execution import satellite as sat
from scanner.execution.alpaca import Alpaca, BrokerError
from scanner.execution.audit import append_event
from scanner.execution.flatten import closing_within
from scanner.execution.policy import TradingPolicy
from scanner.execution.provenance import build_id
from scanner.execution.safety import kill_switch_active
from scanner.models import AuditEvent
from scanner.sources import alpaca_bars

POLICY = TradingPolicy()
NEWS_URL = "https://data.alpaca.markets/v1beta1/news"
MARKET_NEWS = ("SPY", "QQQ", "DIA")
REPORT = Path("logs") / "preclose" / "latest.md"
RELEASE_TRIES, RELEASE_WAIT = 20, 2.0


def _api(sym: str) -> str:
    return sym.replace("-", ".")


async def session_close(broker: Alpaca) -> datetime | None:
    today = datetime.now(sat.ET).date().isoformat()
    try:
        days = await broker._get("/v2/calendar", start=today, end=today)
    except BrokerError:
        return None
    for d in days or []:
        if d.get("date") == today:
            return datetime.strptime(f"{today} {d.get('close', '16:00')}", "%Y-%m-%d %H:%M").replace(tzinfo=sat.ET)
    return None


async def daily_bars(symbols: list[str], client: httpx.AsyncClient) -> dict[str, list[dict]]:
    """Completed sessions only (SIP is allowed for data older than 15 minutes)."""
    today = datetime.combine(datetime.now(sat.ET).date(), time(0), tzinfo=sat.ET)
    try:
        return await alpaca_bars.bars([_api(s) for s in symbols], client, timeframe="1Day",
                                      start=today - timedelta(days=200), end=today, feed="sip")
    except httpx.HTTPError as exc:
        print(f"  daily bars unreadable ({exc}) -- chart and momentum signs fail")
        return {}


async def day_moves(symbols: list[str], client: httpx.AsyncClient) -> tuple[dict[str, float], dict[str, float]]:
    """(last price, % change on the day) by symbol, from IEX snapshots."""
    try:
        snaps = await alpaca_bars.snapshots([_api(s) for s in symbols], client)
    except httpx.HTTPError as exc:
        print(f"  snapshots unreadable ({exc}) -- market sign fails")
        return {}, {}
    last, pct = {}, {}
    for s, snap in snaps.items():
        try:
            p, prev = float(snap["latestTrade"]["p"]), float(snap["prevDailyBar"]["c"])
        except (KeyError, TypeError, ValueError):
            continue
        last[s] = p
        if prev > 0:
            pct[s] = (p / prev - 1) * 100
    return last, pct


async def headlines(symbols: list[str], client: httpx.AsyncClient) -> list[dict]:
    """Today's headlines (since 04:00 ET) for `symbols`, newest first. [] when unreadable."""
    now = datetime.now(sat.ET)
    # Never after now: Alpaca answers 400 to a start in the future (a dry run before 04:00 ET).
    start = min(datetime.combine(now.date(), time(4), tzinfo=sat.ET), now - timedelta(minutes=1))
    try:
        r = await client.get(NEWS_URL, timeout=20, headers={
            "APCA-API-KEY-ID": settings.alpaca_key_id, "APCA-API-SECRET-KEY": settings.alpaca_secret_key},
            params={"symbols": ",".join(_api(s) for s in symbols), "limit": 50, "sort": "desc",
                    "start": start.astimezone(timezone.utc).isoformat()})
        r.raise_for_status()
        return r.json().get("news", [])
    except (httpx.HTTPError, ValueError) as exc:
        print(f"  news unreadable ({exc}) -- news sign fails")
        return []


def _for(items: list[dict], symbol: str) -> list[str]:
    return [str(n.get("headline") or "") for n in items if _api(symbol) in (n.get("symbols") or [])]


async def sell_core(broker: Alpaca, symbol: str, history: list[dict]) -> str:
    """Cancel the core's working orders, wait for the shares, sell them at market (cor-stop- id)."""
    for oid in pc.working_orders_for(history, [symbol]):
        try:
            await broker.cancel_order(oid)
        except BrokerError as exc:
            print(f"  cancel {oid}: {exc}")
    for attempt in range(RELEASE_TRIES):
        if attempt:
            await asyncio.sleep(RELEASE_WAIT)
        try:
            pos = await broker._get(f"/v2/positions/{symbol}")
        except BrokerError as exc:
            return f"not sold: position unreadable ({exc})"
        qty, free = int(float(pos.get("qty") or 0)), int(float(pos.get("qty_available") or 0))
        if qty <= 0:
            return "not sold: no shares held"
        if free < qty and attempt < RELEASE_TRIES - 1:
            continue
        coid = f"{sat.CORE_STOP_PREFIX}{symbol}-pc-{int(datetime.now().timestamp())}"
        try:
            await broker.submit({"symbol": symbol, "qty": str(qty), "side": "sell", "type": "market",
                                 "time_in_force": "day", "client_order_id": coid})
        except BrokerError as exc:
            return f"NOT SOLD: {exc} (peak watch re-arms the stop)"
        return f"sold {qty} at market ({coid})"
    return "not sold: shares never released"


def write_report(scores: list[pc.Score], when: datetime, acted: list[str]) -> None:
    rows = ["| Position | Book | vs buy | Chart | Market | News | Momentum | Signs | Verdict |",
            "|---|---|---:|---|---|---|---|---:|---|"]
    for s in scores:
        cells = [f"{'YES' if g.ok else 'no'}: {g.detail}".replace("|", "/") for g in s.signs]
        verdict = "SELL" if s.sell else ("hold (report only)" if s.book not in POLICY.preclose_sell_strategies else "hold")
        rows.append(f"| {s.symbol} | {s.book} | {(s.price / s.entry - 1) * 100:+.1f}% | " + " | ".join(cells)
                    + f" | {s.confirmed}/4 | {verdict} |")
    text = (f"Checked {when:%Y-%m-%d %H:%M} ET. The SPY core sells only when under its buy AND 4/4.\n\n"
            + "\n".join(rows) + ("\n\n" + "\n".join(f"- {a}" for a in acted) if acted else "") + "\n")
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(text, encoding="utf-8")


async def main(submit: bool, only_within: int | None) -> int:
    async with httpx.AsyncClient() as client:
        broker = Alpaca(settings.alpaca_key_id, settings.alpaca_secret_key, client)
        now = datetime.now(sat.ET)
        close_at = await session_close(broker)
        if close_at is None:
            print("no session today per the broker calendar" + ("" if submit else " -- dry run scores the last session"))
            if submit:
                return 0
        elif only_within is not None and not closing_within(now, close_at=close_at, minutes=only_within):
            print(f"session closes {close_at:%H:%M} ET -- not the pre-close run, nothing to do")
            return 0
        positions = [p for p in await broker._get("/v2/positions") if float(p.get("qty") or 0) > 0]
        history = await broker._get("/v2/orders", status="all", limit=500, nested="true")
        owner_map = ownership.owners(history)
        if not positions:
            print("no open positions")
            return 0
        symbols = [p["symbol"] for p in positions]
        bars = await daily_bars(symbols, client)
        last, pct = await day_moves([*pc.MARKET, *symbols], client)
        market_items = await headlines(list(MARKET_NEWS), client)
        own_items = await headlines(symbols, client)
        market_heads = [str(n.get("headline") or "") for n in market_items]

        scores = []
        for p in positions:
            s = p["symbol"]
            b = bars.get(_api(s), [])
            scores.append(pc.score(
                symbol=s, book=ownership.owner(s, owner_map),
                price=last.get(_api(s)) or float(p["current_price"]), entry=float(p["avg_entry_price"]),
                closes=[x["c"] for x in b], lows=[x["l"] for x in b], day_pct=pct,
                headlines=[*_for(own_items, s), *market_heads], sell_books=POLICY.preclose_sell_strategies))
        for s in scores:
            print(s.line())

        acted = []
        for s in (x for x in scores if x.sell):
            if not submit:
                acted.append(f"WOULD SELL {s.symbol} (dry run)")
            elif kill_switch_active(settings.kill_switch_path):
                acted.append(f"{s.symbol}: kill switch active -- not sold")
            else:
                acted.append(f"{s.symbol}: {await sell_core(broker, s.symbol, history)}")
        for a in acted:
            print(a)
        write_report(scores, now, acted)
        append_event(settings.audit_log_path, AuditEvent(
            event="preclose_check", at=datetime.now().astimezone(), symbol="", stage="trade",
            policy=POLICY.fingerprint, build=build_id(),
            reason="; ".join(f"{s.symbol} {s.confirmed}/4{' SELL' if s.sell else ''}" for s in scores)
                   + (" | " + "; ".join(acted) if acted else "")))
        return 1 if any("NOT SOLD" in a for a in acted) else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--submit", action="store_true", help="sell when the rule says so; omit for a dry run")
    ap.add_argument("--only-if-closing-within", type=int, default=None, metavar="MINUTES")
    a = ap.parse_args()
    raise SystemExit(asyncio.run(main(a.submit, a.only_if_closing_within)))
