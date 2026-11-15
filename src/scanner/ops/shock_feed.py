"""Live readings for the shock alerts (ops/shock_alert.py) and their store. Never raises: a reading
it cannot get is None, and an alert that needs it does not fire."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from scanner.execution import extended, satellite as sat
from scanner.execution.sessions import REGULAR
from scanner.ops import shock_alert as sa
from scanner.sources import alpaca_bars, composite
from scanner.sources import calendar as event_calendar
from scanner.sources.index_history import SP500

ET = ZoneInfo("America/New_York")
STORE = Path("data/monitor/shock_alerts.json")
WATCH = ("SPY", "QQQ", "SMH")


def _snap_date(bar: dict | None) -> date | None:
    try:
        return datetime.fromisoformat(str(bar["t"]).replace("Z", "+00:00")).astimezone(ET).date()
    except (TypeError, KeyError, ValueError):
        return None


async def _vix(client: httpx.AsyncClient) -> float | None:
    try:
        r = await client.get("https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX",
                             params={"range": "1d", "interval": "5m"}, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        return float(r.json()["chart"]["result"][0]["meta"]["regularMarketPrice"])
    except Exception:                                          # noqa: BLE001
        return None


async def readings(client: httpx.AsyncClient, session: str, now: datetime | None = None) -> dict:
    """{'spy','qqq','smh': % vs the prior close, 'vix', 'up_share' (regular hours only)}."""
    now = (now or datetime.now(ET)).astimezone(ET)
    out: dict = {"spy": None, "qqq": None, "smh": None, "vix": await _vix(client), "up_share": None}
    try:
        snaps = await alpaca_bars.snapshots(list(WATCH), client)
    except Exception:                                          # noqa: BLE001
        return out
    for sym in WATCH:
        s = snaps.get(sym) or {}
        daily, prev = s.get("dailyBar") or {}, s.get("prevDailyBar") or {}
        if session == REGULAR:
            base, last = prev.get("c"), (s.get("latestTrade") or {}).get("p")
        else:
            # Before the open the daily bar is the last session's; after the close it is today's.
            base, last = daily.get("c"), await extended.price(sym, client, session)
        if base and last:
            out[sym.lower()] = (float(last) / float(base) - 1) * 100
    if session == REGULAR:
        try:
            members = [m.symbol for m in await composite.load_universe(client, (SP500,))]
            snaps = await alpaca_bars.snapshots(members, client)
            moves = [(v.get("latestTrade") or {}).get("p", 0) > (v.get("prevDailyBar") or {}).get("c", 1e18)
                     for v in snaps.values() if _snap_date(v.get("dailyBar")) == now.date()]
            out["up_share"] = sum(moves) / len(moves) if len(moves) >= 300 else None
        except Exception:                                      # noqa: BLE001
            pass
    return out


async def reporting_bellwethers(client: httpx.AsyncClient, broker, today: date) -> list[str]:
    """Bellwethers reporting before the next regular open (after today's close, or tomorrow pre-market)."""
    try:
        days = await broker._get("/v2/calendar", start=(today + timedelta(days=1)).isoformat(),
                                 end=(today + timedelta(days=7)).isoformat())
        nxt = date.fromisoformat(days[0]["date"]) if days else today + timedelta(days=1)
        found, failed = await event_calendar.with_moomoo(frozenset(sa.BELLWETHERS), client,
                                                               horizon=(nxt - today).days + 1, today=today)
    except Exception:                                          # noqa: BLE001
        return []
    return sorted(s for s, e in found.items() if sat.reports_before_next_session(e.on, e.when, today, nxt))


def store(alerts: list[sa.Alert], today: date, *, until_next: date | None = None, path: Path = STORE) -> list[sa.Alert]:
    """Add today's NEW alerts (one per kind a day); returns the ones that are new. A bellwether alert
    stays in force through the next session (until_next)."""
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        rows = []
    rows = [r for r in rows if str(r.get("until", "")) >= (today - timedelta(days=7)).isoformat()]
    seen = {(r["kind"], r["day"]) for r in rows}
    new = [a for a in alerts if (a.kind, today.isoformat()) not in seen]
    for a in new:
        until = until_next if a.kind == "bellwether" and until_next else today
        rows.append({"kind": a.kind, "text": a.text, "day": today.isoformat(), "until": until.isoformat(),
                     "at": datetime.now(ET).isoformat(timespec="minutes")})
    if new:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rows, indent=1), encoding="utf-8")
    return new


def read(path: Path = STORE) -> list[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
