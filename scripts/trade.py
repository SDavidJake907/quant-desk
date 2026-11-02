"""Scan, size, and place bracket orders on a PAPER account.

    python scripts/trade.py                    # scan and print, send nothing
    python scripts/trade.py --submit-paper     # explicitly submit paper orders
    python scripts/trade.py --index all --show 20

Needs SCANNER_ALPACA_KEY_ID and SCANNER_ALPACA_SECRET_KEY in .env.

WHAT THIS DOES
    1. refuse to run if the market is closed, equity is low, the daily loss
       limit is hit, or too many positions are already open
    2. scan for setups that triggered in the last few bars
    3. skip anything already held or already ordered
    4. size each by risk, capped by position size and buying power
    5. send one bracket order per setup: limit entry, stop, target

LIVE TRADING IS NOT AVAILABLE HERE, BY DESIGN
There is no --live flag. This script talks to the paper endpoint only. Running
real money through this strategy is a decision to make deliberately, with your
own hand on the switch, against evidence that currently amounts to ~200 trades
from 86 days of data with a threshold chosen by looking at those same trades.

The honest use of this script is to accumulate OUT-OF-SAMPLE results: every
paper trade it takes is one the backtest did not pick its parameters from.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta
from pathlib import Path

import httpx

from scanner.config import settings
from scanner.execution.alpaca import (
    Alpaca, BrokerError, bracket_payload, entry_payload, stop_payload,
    take_profit_payload,
)
from scanner.execution.audit import append_event
from scanner.execution import continuity
from scanner.execution.guards import (
    RiskLimits, check_account, check_symbol, late_signal, sector_concentration,
)
from scanner.execution.policy import CONFIRM, SCAN, TRADE, WATCH, Decision, TradingPolicy, rejections
from scanner.execution.provenance import build_id
from scanner.execution.funnel import Funnel
from scanner.execution.flatten import should_wind_down
from scanner.execution.taper import deployment_for, position_cap_for
from scanner.execution.session import crosses_session, is_stale_session
from scanner.execution.health import probe as probe_model
from scanner.execution.news_gate import check as check_news
from scanner.execution.safety import kill_switch_active
from scanner.execution import sleeve
from scanner.execution.sizing import size_position
from scanner.execution import watchlist
from scanner.execution import preflight
from scanner.execution import safety
from scanner.features import intraday_context
from scanner.features.intraday import (
    EXCHANGE_TZ, drop_incomplete, fetch_intraday, max_range, session_slot,
)
from scanner.llm import ranking
from scanner.screens.smc import is_live_setup, priority, scan
from scanner.sources import calendar as event_calendar
from scanner.sources import composite
from scanner.features import sector_strength
from scanner.sources import dividends
from scanner.sources import news_store
from scanner.sources.index_history import COMPOSITE_1500, SP400, SP500, SP600
from scanner.models import AuditEvent, TradeIntent, SlateEntry

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"
GREEN, RED, YELLOW, CYAN = "\033[32m", "\033[31m", "\033[33m", "\033[36m"

INDEXES = {"sp500": (SP500,), "sp400": (SP400,), "sp600": (SP600,),
           "all": COMPOSITE_1500}

#: Read from the policy so these cannot drift from what was measured.
#: It did drift: this file carried its own 5M constant while the policy
#: said something else, so changing the policy changed nothing live.
#:
#: `min_price` had the SAME bug and survived that fix — the comment above was
#: written for the line below it and the line above it was left hardcoded.
#: Worse than the original: `min_price` is a policy field, so it is hashed
#: into the fingerprint. Editing it declared a new evaluation window and
#: changed nothing live, which is a false signal rather than a missed one.
POLICY = TradingPolicy()
MIN_PRICE = POLICY.min_price
MIN_DOLLAR_VOLUME = POLICY.min_dollar_volume
#: Average bar range as a % of price. See `policy.min_range_pct` for why the
#: default is 0.0 and what enabling it would cost.
MIN_RANGE_PCT = POLICY.min_range_pct


async def _trend_context(client, setups) -> dict[str, dict]:
    """Daily trend facts per candidate: the arithmetic the screen discards.

    12-1 relative strength against ^GSPC, distance from the 200-day average,
    daily RSI, and the setup's own geometry. Returns {} on any failure — this
    informs a prompt and one gate, and neither may take down a scan.

    The 12-1 window skips the most recent month, because short-horizon reversal
    contaminates it. That is the same definition `momentum_factor` uses.
    """
    from scanner.features import technicals
    from scanner.features.bars import fetch_bars, fetch_many

    symbols = tuple({s.symbol for s in setups})
    if not symbols:
        return {}
    try:
        benchmark = technicals.compute(
            await fetch_bars("^GSPC", client, range_="2y"))
        daily, _ = await fetch_many(symbols, client, range_="2y")
    except Exception:                                        # noqa: BLE001
        return {}

    out: dict[str, dict] = {}
    for setup in setups:
        series = daily.get(setup.symbol)
        if series is None:
            continue
        try:
            features = technicals.compute(series)
            closes = series.closes
            facts: dict[str, float] = {}
            strength = technicals.relative_strength(features, benchmark)
            if strength is not None:
                facts["rs"] = strength
            # Features already carries these; recomputing them here would be
            # a second definition of the same number.
            if features.sma200:
                facts["from_200"] = (closes[-1] / features.sma200 - 1.0) * 100.0
            if features.pct_from_52w_high is not None:
                facts["from_52w_high"] = features.pct_from_52w_high
            risk = setup.entry_price - setup.stop_price
            if risk > 0 and setup.entry_price > 0:
                facts["reward_risk"] = (setup.target_price
                                        - setup.entry_price) / risk
                facts["risk_pct"] = risk / setup.entry_price * 100.0
            if facts:
                out[setup.symbol] = facts
        except Exception:                                    # noqa: BLE001
            continue
    return out


async def _sector_standing(client, universe) -> dict[str, float]:
    """Median relative strength per sector, from the once-a-day cache.

    MOVED OUT OF THE SCAN. This used to fetch two years of daily bars for all
    501 symbols on every scan that found a candidate: 14.42s cold, 4.45s warm,
    against a ~17s scan. The inputs are DAILY bars, so the answer cannot change
    between the 06:03 scan and the 11:33 one — thirteen runs a day produced
    thirteen identical numbers.

    `sector_strength.today()` serves a date-stamped file written by the daily
    collector and falls back to computing it once if that task has not run, so
    a scan never depends on another job having succeeded.
    """
    return await sector_strength.today(client, universe)


def _dollar_volume(bars, window: int = 20) -> float:
    closes, volumes = bars.closes[-window:], bars.volumes[-window:]
    if not closes:
        return 0.0
    return sum(v * c for v, c in zip(volumes, closes)) / len(closes)


def _liquidity_tier(dollar_volume: float) -> str:
    """Band, for the record only. The most liquid band looked best on 16
    holdout trades — the same sample-shrinkage artifact that made four other
    filters look good today. Recorded, never enforced."""
    if dollar_volume >= 50_000_000:
        return "over-50M"
    if dollar_volume >= 20_000_000:
        return "20-50M"
    return "5-20M"


def _record_slate(candidates, ranks, llm_rank, llm_order, volumes, sectors,
                  policy) -> int:
    """Write every ranked candidate, taken or not. Returns how many.

    A function rather than an inline block so it can be exercised by a test
    without running a whole scan. The version that shipped inline had never
    once executed — every scan that day found zero candidates — which is
    exactly the shape of code that fails first on an unattended schedule.
    """
    if not candidates:
        return 0
    slate = tuple(
        SlateEntry(
            symbol=setup.symbol,
            priority_rank=ranks.get(setup.symbol, 0),
            priority_score=round(priority(setup), 2),
            llm_rank=llm_rank.get(setup.symbol, 0),
            llm_conviction=(llm_order[setup.symbol].conviction
                            if setup.symbol in llm_order else ""),
            llm_driver=(llm_order[setup.symbol].driver
                        if setup.symbol in llm_order else ""),
            llm_concern=(llm_order[setup.symbol].concern
                         if setup.symbol in llm_order else ""),
            entry_price=setup.entry_price,
            stop_price=setup.stop_price,
            target_price=setup.target_price,
            reward_risk=setup.reward_risk,
            dollar_volume=round(volumes.get(setup.symbol, 0.0), 2),
            liquidity_tier=_liquidity_tier(volumes.get(setup.symbol, 0.0)),
            sector=sectors.get(setup.symbol, ""),
            rsi_gate_passed=bool(rsi_shadow),
            trigger_at=at.isoformat(),
            trigger_slot=session_slot(at),
            trigger_weekday=at.strftime("%a"),
        )
        for setup, at, rsi_shadow in candidates
    )
    append_event(settings.audit_log_path, AuditEvent(
        event="candidate_slate", at=datetime.now().astimezone(),
        symbol="", slate=slate, stage=SCAN,
        policy=policy.fingerprint, build=build_id(),
        reason=f"{len(slate)} candidate(s) ranked",
    ))
    return len(slate)


def _range_pct(bars, window: int = 20) -> float:
    """Average bar range over the window, as a % of that bar's close."""
    highs, lows = bars.highs[-window:], bars.lows[-window:]
    closes = bars.closes[-window:]
    spans = [(h - l) / c * 100 for h, l, c in zip(highs, lows, closes) if c]
    return sum(spans) / len(spans) if spans else 0.0


CORE_LIST = Path("watchlists/core.txt")


def _core_list(path: Path = CORE_LIST) -> tuple[str, ...]:
    """The operator's hand-picked names, or () to scan the full index."""
    if not path.exists():
        return ()
    names: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        name = line.split("#", 1)[0].strip().upper()
        if name and name not in names:
            names.append(name)
    return tuple(names)


def _liquid(bars, window: int = 20) -> bool:
    """Tradeability, not signal. A floor that says "you could actually get
    filled here" is a different thing from one that claims liquid names
    perform better.

    The range check is here so the preview and the trader screen on the same
    three numbers; at the shipped default of 0.0 it admits everything, which
    is what the holdout measured.
    """
    closes = bars.closes[-window:]
    if not closes or closes[-1] < MIN_PRICE:
        return False
    if _range_pct(bars, window) < MIN_RANGE_PCT:
        return False
    return _dollar_volume(bars, window) >= MIN_DOLLAR_VOLUME


async def main(
    index: str, minutes: int, k: int, fresh: int, min_rr: float,
    min_risk_pct: float, limits: RiskLimits, dry_run: bool, show: int,
    trend_context: bool = False, rsi_context: bool = False,
    macd_context: bool = False, news_gate: bool = True,
    skip_open: bool = False, skip_weekdays: tuple[str, ...] = (),
    policy: TradingPolicy | None = None, live: bool = False,
) -> None:
    policy = policy or TradingPolicy()
    decisions: list[Decision] = []
    #: Only the submitting path asks the broker. A dry run reports bar
    #: freshness without judging it, because outside session hours the newest
    #: bar is legitimately hours old.
    open_market = False

    def record(stage, symbol, reason="", intent=None, order_id="", request_id="",
               event="candidate_refused"):
        """Every refusal is data. Printing it and moving on loses the only
        record of what the rules actually did on a quiet day."""
        decisions.append(Decision(symbol=symbol, stage=stage, reason=reason))
        append_event(settings.audit_log_path, AuditEvent(
            event=event, at=datetime.now().astimezone(), symbol=symbol,
            build=build_id(),
            intent=intent, reason=reason, stage=stage,
            policy=policy.fingerprint, order_id=order_id, request_id=request_id,
        ))

    # SAY SO IF THIS SYSTEM STOPPED RUNNING.
    #
    # The host was off from 2026-08-24 to 09-24. Nothing failed — the four
    # scheduled tasks were never reached, so there was no error, no non-zero
    # exit and nothing in any log but an absence. A human found it a month
    # later by reading timestamps.
    #
    # Absence cannot be reported while it is happening. It CAN be reported the
    # moment the system comes back, which is most of the value: this would
    # have announced the blackout on the first scan of 09-24 rather than
    # leaving it to be noticed.
    #
    # Recorded as well as printed, because the terminal is not read at 05:33.
    try:
        gap = continuity.check(settings.audit_log_path)
        if gap is not None and gap.is_alerting:
            print(f"{RED}{BOLD}*** GAP IN COVERAGE ***{RESET}")
            print(f"{RED}    {gap.describe()}{RESET}")
            print(f"{YELLOW}    Trades either side of a gap were taken in "
                  f"different market conditions. FREEZE.md records how the "
                  f"last one was handled.{RESET}\n")
            append_event(settings.audit_log_path, AuditEvent(
                event="pipeline_gap", at=datetime.now().astimezone(),
                symbol="", stage=SCAN, policy=policy.fingerprint,
                build=build_id(), reason=gap.describe(),
            ))
    except Exception as exc:                          # noqa: BLE001
        # Diagnostics must never stop a scan.
        print(f"{DIM}! continuity check skipped: {type(exc).__name__}{RESET}")

    # PRE-FLIGHT. Model environment and broker reachability are checked before
    # any bar is fetched or order considered. Paper mode reports problems
    # loudly but continues (fail open). Live mode hard-blocks: a dead model,
    # missing model environment, non-paper endpoint, or no session-start equity
    # means no orders can go out.
    pf_report = None
    try:
        async with httpx.AsyncClient() as pf_client:
            pf_report = await preflight.run(pf_client, live=live)
        print(f"{DIM}{preflight.banner(pf_report)}{RESET}")
        if pf_report.blocked:
            print(f"{RED}pre-flight blocked: {pf_report.reason}{RESET}")
        if not pf_report.model.complete:
            print(f"{YELLOW}model environment incomplete — news gate and dedup "
                  f"may be degraded{RESET}")
    except Exception as exc:                          # noqa: BLE001
        # Pre-flight must never stop a scan.
        print(f"{DIM}! pre-flight check skipped: {type(exc).__name__}{RESET}")

    if live and pf_report is not None and pf_report.blocked:
        print(f"{RED}{BOLD}LIVE MODE BLOCKED — pre-flight failed.{RESET}")
        print(f"{RED}    {pf_report.reason}{RESET}")
        print(f"{RED}    No orders will be sent. Fix the problems above and re-run.{RESET}")
        return

    # AUTO KILL SWITCH. Before scanning, check whether conditions warrant
    # halting new entries: consecutive rejections, model environment down, or
    # broker unreachable. If activated, the scan still runs (evaluate only)
    # but sends no orders — same as the daily-loss guard.
    model_complete = pf_report.model.complete if pf_report else False
    broker_reachable = pf_report.broker.reachable if pf_report else False
    auto_activate, auto_reason = safety.should_auto_activate(
        settings.kill_switch_path,
        model_env_complete=model_complete,
        broker_reachable=broker_reachable,
    )
    if auto_activate:
        safety.activate_kill_switch(settings.kill_switch_path, auto_reason)
        print(f"{RED}{BOLD}*** KILL SWITCH AUTO-ACTIVATED ***{RESET}")
        print(f"{RED}    {auto_reason}{RESET}")
        print(f"{YELLOW}    evaluating only, no orders will be sent{RESET}\n")
        dry_run = True

    async with httpx.AsyncClient() as client:
        broker = None
        if not dry_run:
            if kill_switch_active(settings.kill_switch_path):
                print(f"{RED}not trading: kill switch is active ({settings.kill_switch_path}){RESET}")
                return
            broker = Alpaca(
                settings.alpaca_key_id, settings.alpaca_secret_key, client
            )
            account = await broker.account()
            # Capture session start equity for the daily-loss guard when
            # SCANNER_SESSION_START_EQUITY is not set, so a drawdown that
            # happened before this process started can be detected.
            if settings.session_start_equity <= 0:
                safety.capture_session_equity(account.equity)
                print(f"{DIM}session start equity captured: {account.equity:,.2f}{RESET}")
            open_market = await broker.market_open()
            held = await broker.held_symbols()
            # GTC brackets keep the stop alive overnight, and an unfilled
            # ENTRY alive with it. A limit resting past its freshness window
            # would fill on a setup the screen no longer produces, so it is
            # cancelled first — before `pending_symbols` reads the book.
            stale = await broker.cancel_stale_entries(
                policy.fresh_bars * policy.interval_minutes
            )
            if stale:
                print(f"{DIM}cancelled {len(stale)} stale entry order(s): "
                      f"{', '.join(stale)}{RESET}")
            pending = await broker.pending_symbols()

            # RECONCILE BEFORE SCANNING FOR ANYTHING NEW.
            #
            # A cancelled stop is not an error, it is an absence, and nothing
            # here was looking for absences — so three positions worth 58,701
            # dollars sat naked through a weekend and a full Monday session
            # while every log line reported success.
            #
            # Finding new setups while an existing position has no floor
            # under it is precisely the wrong order of business, so this runs
            # first and says so in the loudest terms the terminal has.
            naked = await broker.unprotected_positions()
            if naked:
                print(f"\n{RED}{BOLD}*** {len(naked)} UNPROTECTED POSITION(S) "
                      f"— NO LIVE STOP ***{RESET}")
                for p in naked:
                    print(f"{RED}    {p.symbol:<6} {p.qty:>6} shares naked{RESET}")
                print(f"{YELLOW}    re-arm with: python scripts/protect.py "
                      f"--submit{RESET}\n")
                for p in naked:
                    record(TRADE, p.symbol, f"{p.qty} shares with no live stop",
                           event="position_unprotected")

            sleeves = sleeve.split(
                account.equity,
                swing_allocation_pct=policy.swing_allocation_pct,
                swing_cap=policy.swing_cap,
                positions=await broker.positions_with_origin(),
            )
            print(f"{DIM}paper account: equity {account.equity:,.2f}, "
                  f"buying power {account.buying_power:,.2f}, "
                  f"{len(held)} positions, {len(pending)} working orders{RESET}")
            if sleeves.swing_allocation_pct < 100.0 or sleeves.swing_cap:
                print(f"{DIM}sleeves: {sleeves.describe()}{RESET}")
            if sleeves.over_deployed:
                print(f"{YELLOW}swing positions ({sleeves.swing_deployed:,.0f}) "
                      f"exceed the swing sleeve ({sleeves.swing_equity:,.0f}) — "
                      f"no new entries until they close{RESET}")

            # Session start equity baseline for the daily-loss guard:
            # explicit env var > captured session equity > current equity.
            session_start = (
                settings.session_start_equity
                if settings.session_start_equity > 0
                else safety.load_session_equity()
                or account.equity
            )
            gate = check_account(
                account.equity, session_start,
                len(held), limits, market_open=open_market,
                account_status=account.status, account_blocked=account.blocked,
            )
            if not gate:
                # EVALUATE ANYWAY, JUST NEVER SUBMIT.
                #
                # Returning here used to skip the scan, the news gate, Gemma's
                # ranking and the audit record — so overnight the system knew
                # nothing, and at the open it started from cold with whatever
                # had been announced after yesterday's close still unread.
                #
                # But most material news lands when the market is shut:
                # earnings, mergers, guidance. Reading it while nothing can be
                # traded is exactly when reading is free. A refusal to trade is
                # a reason not to send an order, not a reason to stop looking.
                #
                # This applies to every gate, not just the closed market. On a
                # day the loss limit halts trading, what the system WOULD have
                # taken is worth recording — it costs nothing and it is the
                # only way to find out later whether the halt saved money.
                print(f"{YELLOW}not trading: {gate.reason}"
                      f" — evaluating only, no orders will be sent{RESET}")
                dry_run = True

                # WIDEN THE FRESHNESS WINDOW, OR THIS FINDS NOTHING.
                #
                # `fresh_bars` counts BARS, not time, and no bars are printed
                # while the market is shut. Four bars after the close means the
                # last four bars of yesterday's session, forever — so an
                # overnight scan would run every half hour and report zero
                # setups every time, looking like a quiet market rather than a
                # frozen clock.
                #
                # One session of 30-minute bars is 13. Widening to a session
                # asks "what set up while I was away", which is the question
                # worth asking overnight.
                #
                # These are NOT trade candidates and must never become them
                # without a fresh holdout. The measured entry is the close of
                # the trigger bar; entering the same setup at tomorrow's open
                # is a different trade at a different price with a stop that
                # may already be broken. This produces a WATCH list.
                session_bars = max(1, (390 // minutes) if minutes else 13)
                fresh = max(fresh, session_bars)
                print(f"{DIM}evaluating the last {fresh} bars "
                      f"(~one session) rather than {policy.fresh_bars}{RESET}")
        else:
            account = None
            held = pending = frozenset()
            print(f"{YELLOW}dry run — scanning only, no orders will be sent{RESET}")

        ex_dividends = {}
        if policy.block_ex_dividend_erosion:
            try:
                ex_dividends = dividends.by_symbol(await dividends.fetch_announcements(
                    client, settings.alpaca_key_id, settings.alpaca_secret_key))
            except Exception as exc:  # noqa: BLE001
                # A dividends outage must not kill a scan: an empty calendar
                # means "unknown, not clear", so no position is wrongly
                # refused or wrongly admitted.
                print(f"{YELLOW}! ex-dividend calendar unavailable: "
                      f"{type(exc).__name__} — treating as unknown{RESET}")
                ex_dividends = {}
            print(f"{DIM}ex-dividend calendar: {len(ex_dividends)} names with a "
                  f"declared ex-date ahead{RESET}")

        # PROBE BEFORE ANY CANDIDATE EXISTS. A scan that finds nothing never
        # calls the news gate, so per-candidate logging alone would stay silent
        # through an outage on exactly the quiet days that look normal.
        if news_gate:
            health = await probe_model(client)
            colour = DIM if health.ok else YELLOW
            print(f"{colour}{health.banner()}{RESET}")
            if not health.ok:
                append_event(settings.audit_log_path, AuditEvent(
                    event="news_gate_degraded", at=datetime.now().astimezone(),
                    symbol="", stage="preflight", reason=health.banner(),
                    policy=policy.fingerprint, build=build_id(),
                ))

        # WIND-DOWN. From Thursday no new positions are opened, so nothing is
        # bought that would have to be closed within a day or held over the
        # weekend. Existing positions keep their brackets and are flattened by
        # scripts/flatten.py before the Friday bell.
        if policy.flat_before_weekend and should_wind_down(
            datetime.now().astimezone().date(),
            from_weekday=policy.wind_down_from_weekday,
        ):
            print(f"{YELLOW}winding down for the weekend — evaluating only, "
                  f"no new positions will be opened{RESET}")
            dry_run = True
        elif policy.flat_before_weekend:
            today = datetime.now().astimezone().date()
            print(f"{DIM}capital budget today: "
                  f"{deployment_for(today, policy):.0f}% of the swing sleeve "
                  f"({position_cap_for(today, policy):.1f}% per position "
                  f"across {policy.max_positions} slots){RESET}")

        universe = await composite.load_universe(client, INDEXES[index])
        symbols = tuple(m.symbol for m in universe)
        sector_of = {m.symbol: m.sector for m in universe}
        # CLAMPED TO THE OPERATOR'S CORE LIST (window 16, 2026-10-06): when
        # watchlists/core.txt names any stocks, they are the ONLY names scanned.
        # The index universe is still loaded for sector context.
        core = _core_list()
        if core:
            symbols = core
            print(f"{YELLOW}CORE LIST: scanning only {len(core)} hand-picked name(s): "
                  f"{', '.join(core)}{RESET}")
        range_ = max_range(minutes)
        print(f"{DIM}scanning {len(symbols)} symbols on {minutes}m bars{RESET}")
        print(f"{DIM}policy: {policy.describe()}{RESET}")

        standing = watchlist.load()
        ready_again, still_waiting = watchlist.release(standing)
        if standing:
            print(f"\n{BOLD}WATCHLIST{RESET} {DIM}({len(standing)} deferred){RESET}")
            for entry in ready_again:
                print(f"  {entry.symbol:<6} {GREEN}eligible again{RESET} "
                      f"{DIM}— was: {entry.reason}{RESET}")
            for entry in still_waiting:
                print(f"  {entry.symbol:<6} {YELLOW}waiting{RESET} "
                      f"{DIM}— {entry.waiting_for}{RESET}")

        loaded = await asyncio.gather(
            *(fetch_intraday(s, client, minutes=minutes, range_=range_) for s in symbols),
            return_exceptions=True,
        )

        # DROP THE BAR STILL FORMING. Scans run three minutes past the half
        # hour, so the newest bucket is three minutes old with twenty-seven
        # left to change. The measured strategy enters at the CLOSE of the
        # trigger bar, and a partial bar has no close — only a current price.
        # Acting on one is a different trade at a different price from the one
        # the holdout measured, and nothing in the log would distinguish them.
        loaded = [
            b if isinstance(b, Exception)
            else drop_incomplete(b, minutes=minutes)
            for b in loaded
        ]

        # Exchange time, not local. The machine runs in Alaska; comparing an
        # Alaska date against Eastern bar timestamps would call the whole
        # morning stale.
        session_today = datetime.now(tz=EXCHANGE_TZ).date()

        candidates = []
        volumes_by_symbol: dict[str, float] = {}
        # The newest COMPLETED close per symbol (the forming bar was dropped
        # above). A setup first seen a scan late is checked against it before
        # sending -- see guards.late_signal.
        latest_close: dict[str, float] = {}
        funnel = Funnel()
        funnel.scanned(len(symbols))
        for symbol, bars in zip(symbols, loaded):
            if isinstance(bars, Exception) or not _liquid(bars):
                continue
            volumes_by_symbol[symbol] = _dollar_volume(bars)
            latest_close[symbol] = bars.closes[-1]
            # READ FROM THE POLICY, NOT HARDCODED.
            #
            # This was `require_value_sweep=True` while the policy said False,
            # so the change measured and shipped on 2026-09-28 never reached a
            # live scan: setup counts stayed at 73-76 when they should have
            # risen about 70%, and the fingerprint moved for a rule that had no
            # effect. Exactly the defect the 2026-09-26 audit found in
            # `min_price` -- fixed there, and its neighbour in the same call
            # was never checked.
            setups = scan(bars, k=k, min_reward_risk=min_rr,
                          min_risk_pct=min_risk_pct,
                          require_value_sweep=policy.require_value_sweep,
                          require_trend_context=trend_context,
                          require_rsi_context=rsi_context,
                          require_macd_context=macd_context)
            for setup in setups:
                funnel.setup_found()
                # Same two conditions as before, evaluated separately only so
                # the log can say WHICH one refused. The trading decision is
                # unchanged: both must hold.
                age = len(bars) - 1 - setup.trigger_index
                if age >= fresh:
                    funnel.dropped_as_stale(age=age, fresh_bars=fresh)
                    continue
                if not is_live_setup(bars, setup):
                    funnel.dropped_as_not_live()
                    continue
                # The trigger bar ended a session, so its close was never a
                # price anyone could have traded — the market was shut. See
                # TradingPolicy.block_overnight_entry.
                # BOTH predicates, because each misses a case the other
                # catches. crosses_session needs a later bar to exist, and
                # drop_incomplete removes the one that used to supply it —
                # leaving yesterday's final bar looking fresh with no
                # successor. is_stale_session answers the question a live scan
                # actually has: is this bar from today's session at all.
                if policy.block_overnight_entry and (
                    crosses_session(bars, setup.trigger_index)
                    or is_stale_session(bars, setup.trigger_index,
                                        session=session_today)
                ):
                    funnel.dropped_as_overnight()
                    continue
                # The trigger bar is from today's date but the session is
                # already over (after 16:00 ET): its close was never
                # tradeable at decision time, so refuse it even though
                # is_stale_session sees today's date.
                bar_at = bars.times[setup.trigger_index]
                if bar_at.date() == session_today and bar_at.hour >= 16:
                    funnel.dropped_as_overnight()
                    continue
                # Shadow-log the RSI gate WITHOUT filtering on it. It
                # measured best of nine gates (+0.294R vs +0.218R) but was
                # picked from nine tries, so it needs forward evidence,
                # not promotion. Recording costs nothing and settles it in
                # three months on data nobody chose it from.
                at = bars.times[setup.trigger_index]
                slot = session_slot(at)
                weekday = at.strftime("%a")
                if skip_open and slot == "open":
                    funnel.dropped_by_slot()
                    continue
                if weekday in skip_weekdays:
                    funnel.dropped_by_slot()
                    continue
                shadow = intraday_context.passes(
                    bars.upto(setup.trigger_index), require_rsi=True
                )
                funnel.became_candidate()
                candidates.append(
                    (setup, bars.times[setup.trigger_index], shadow)
                )

        # SAY HOW FRESH THE BARS ARE.
        #
        # On 2026-09-25 the bar cache was found renewing its own lease and
        # serving data up to three buckets stale, which with fresh_bars=2 can
        # lose a setup before the scanner ever sees it. It took four throwaway
        # scripts to establish that, because the funnel reports what was
        # REJECTED and never what it was looking at.
        #
        # A scan running on stale bars and a market with nothing in it produce
        # the same line. This is the difference between them.
        _times = [b.times[-1] for b in loaded
                  if not isinstance(b, Exception) and len(b)]
        if _times:
            newest = max(_times)
            lag_min = (datetime.now(tz=EXCHANGE_TZ) - newest).total_seconds() / 60
            # Only WARN while the market is open. Outside session hours the
            # newest bar is naturally hours old and a permanent yellow line
            # is a line nobody reads. `open_market` exists only on the
            # submitting path, so a dry run reports without judging.
            tint = YELLOW if (open_market and lag_min >= minutes * 1.5) else DIM
            spread = ""
            if len(set(_times)) > 1:
                spread = f", oldest {min(_times):%H:%M}"
            print(f"{tint}bars: newest {newest:%H:%M %Z}{spread} — "
                  f"{lag_min:.0f} min old ({lag_min / minutes:.1f} bars){RESET}")

        print(f"{DIM}{funnel.summary()}{RESET}")

        # Rank by what the study supported, not by which bar printed last.
        candidates.sort(key=lambda row: priority(row[0]), reverse=True)
        ranks = {row[0].symbol: i + 1 for i, row in enumerate(candidates)}
        print(f"\n{BOLD}{len(candidates)} fresh setups{RESET}\n")

        # CHEAP GATES BEFORE EXPENSIVE ADVICE.
        #
        # The advisory block below costs sector arithmetic, a round of headline
        # fetches and an LLM call. The WATCH gates that decide whether a
        # candidate can be acted on at all — already held, and no free slot —
        # used to run AFTER it, in the send loop. So a scan where every
        # candidate was already held, or where the five slots were full, paid
        # for advice on candidates it could not act on. With max_positions=5
        # and deployment at (100,80,60,40,0), a full book is not an edge case.
        #
        # This is the ordering the scan already gets right one level up, where
        # `_liquid()` runs before `scan()` because it is cheaper.
        #
        # The send loop below is deliberately UNCHANGED and still applies both
        # gates itself, so every refusal is recorded exactly as before and this
        # cannot alter a decision. `rankable` only decides what is worth
        # forming an opinion about.
        room = limits.max_positions - len(held)
        rankable = ([row for row in candidates
                     if check_symbol(row[0].symbol, held, pending)][:show]
                    if room > 0 else [])
        if candidates and not rankable:
            print(f"  {DIM}no candidate is actionable "
                  f"({'no free slot' if room <= 0 else 'all already held'}) — "
                  f"skipping news and ranking{RESET}")

        # Gemma's advisory ordering, recorded beside the deterministic one.
        llm_order: dict[str, ranking.SetupOpinion] = {}
        llm_rank: dict[str, int] = {}
        # MUST exist before the send loop. It is populated inside the advisory
        # block, which does not run when the gate is off or nothing is
        # actionable -- and the send loop reads it either way. An earlier
        # version of exactly this shape shipped undefined and only fired on a
        # branch the tests never reached; `test_no_undefined_names` was written
        # for it.
        trend_facts: dict[str, dict] = {}
        if news_gate and rankable:
            from scanner.entity import prefilter
            from scanner.sources import yahoo_rss
            sector_rs = await _sector_standing(client, universe)
            # FACTS THE SCAN ALREADY HAD AND THREW AWAY.
            #
            # On 2026-09-28 both positions opened were deep laggards -- MLM at
            # -29.2pp against the S&P and 14.4% below its 200-day average. The
            # screen has no opinion about trend and the model was never told,
            # so nothing in the pipeline could object. This hands the model the
            # arithmetic it cannot do for itself.
            trend_facts = await _trend_context(
                client, [row[0] for row in rankable])
            headlines = {}
            for setup, _, _ in rankable:
                try:
                    headlines[setup.symbol] = await yahoo_rss.fetch(setup.symbol, client)
                except Exception:
                    headlines[setup.symbol] = ()
            # Keep what the model was SHOWN, before asking what it thinks.
            #
            # Yahoo's feed returns only what is current, so until now every
            # headline the ranker read was discarded when the scan ended. The
            # slate records the model's OUTPUT, which is enough to score its
            # ranking — but re-running it, which is what testing a prompt
            # change needs, requires the INPUT. Nobody kept it, so the triage
            # prompt edited today could only be measured on hand-written
            # cases.
            #
            # Written before the model is called so a model failure still
            # leaves the evidence behind. It never raises: these headlines are
            # evidence ABOUT a decision, not an input to it, and a full disk
            # must not stop a trade.
            stored = news_store.append_items(
                item for items in headlines.values() for item in items
            )
            if stored:
                print(f"  {DIM}kept {stored} new headline(s) for replay{RESET}")

            opinions = await ranking.rank(headlines, client, sector_of,
                                          sector_rs, trend_facts)
            llm_order = {o.symbol: o for o in opinions}
            llm_rank = {s: i + 1 for i, s in enumerate(ranking.order(opinions))}
            if opinions:
                print(f"  {CYAN}Gemma's read (advisory, not acted on):{RESET}")
                for o in sorted(opinions, key=lambda x: llm_rank.get(x.symbol, 99)):
                    note = f" — {o.concern}" if o.concern and o.concern != "none" else ""
                    print(f"    {o.symbol:<6} {o.conviction:<8} driven by {o.driver:<8}"
                          f"{DIM}{note}{RESET}")

        earnings = {}
        # Honours policy.block_earnings_within_days: 0 means hold through
        # earnings, so the calendar is not consulted (window 16 runs at 0).
        if news_gate and candidates and policy.block_earnings_within_days:
            wanted = frozenset(setup.symbol for setup, _, _ in rankable)
            earnings, failed = await event_calendar.upcoming_earnings(
                wanted, client, horizon=policy.block_earnings_within_days)
            if failed:
                print(f"  {RED}! earnings calendar failed for {len(failed)} "
                      f"day(s): {', '.join(str(d) for d in failed)}{RESET}")
            if earnings:
                print(f"  {YELLOW}earnings inside the holding period: "
                      f"{', '.join(f'{s} {e.label}' for s, e in sorted(earnings.items()))}{RESET}")

        # Record the WHOLE slate, before anything is bought.
        #
        # Every other record here describes a trade that happened, so the only
        # outcomes this system can ever observe are the ones it already ranked
        # highly. That makes the ranking unfalsifiable: its third-choice calls
        # never become orders and so never become evidence, no matter how long
        # it runs. Writing the slate at decision time — taken and untaken
        # alike — is what lets the ranking be scored later against setups it
        # passed over, from bars already held.
        #
        # Written BEFORE the send loop deliberately. A slate written afterwards
        # would be missing exactly on the runs that failed partway, which are
        # the runs worth reading.
        # NEVER let recording a slate stop a trade.
        #
        # This is evidence ABOUT a decision, not an input to it — the same
        # reasoning `news_store` is built on, and it was missing here. The
        # block sits between the funnel and the send loop, so an exception
        # raised while building it would kill the scan AFTER finding setups
        # and BEFORE sending orders: trading would stop and the log would show
        # a scan that simply ended.
        #
        # That is the failure `test_no_undefined_names` was written for —
        # a branch that only runs under a condition the tests never reach,
        # firing on an unattended schedule. Slate recording had never once
        # executed when it shipped, because every scan that day found zero
        # candidates.
        try:
            _record_slate(candidates, ranks, llm_rank, llm_order,
                          volumes_by_symbol, sector_of, policy)
        except Exception as exc:                      # noqa: BLE001
            print(f"  {YELLOW}! slate not recorded: "
                  f"{type(exc).__name__}: {exc}{RESET}")

        sent = 0
        for setup, at, rsi_shadow in candidates[:show]:
            symbol_gate = check_symbol(setup.symbol, held, pending)
            if not symbol_gate:
                print(f"  {DIM}{setup.symbol:<6} skipped — {symbol_gate.reason}{RESET}")
                record(WATCH, setup.symbol, symbol_gate.reason)
                continue
            if sent >= room:
                print(f"  {DIM}{setup.symbol:<6} skipped — position limit reached{RESET}")
                record(WATCH, setup.symbol, "position limit reached")
                still_waiting = watchlist.defer(still_waiting, watchlist.Watched(
                    symbol=setup.symbol, reason="position limit reached",
                    entry_price=setup.entry_price, stop_price=setup.stop_price,
                    target_price=setup.target_price, trigger_at=at.isoformat(),
                    added_at=datetime.now().astimezone().isoformat(),
                ))
                continue

            # LATE SIGNAL. A trigger one bar old is still "fresh", and its old
            # close is still the limit. If the price has fallen since, that buy
            # limit is above the market and fills at once beside the stop --
            # APP and PSX, the only two fills >1% under their limits, both sent
            # one scan late, both stopped within the hour.
            late = late_signal(setup.entry_price, latest_close.get(setup.symbol),
                               policy.max_entry_drift_pct)
            if not late:
                print(f"  {RED}{setup.symbol:<6} SKIPPED — {late.reason}{RESET}")
                record(CONFIRM, setup.symbol, late.reason)
                continue

            # Size against the sleeve, never the balance. With capital parked
            # in income holdings, "1% of equity" measured on the full account
            # is more than 1% of the money this strategy actually has, and the
            # position cap inflates by the same factor with nothing reporting
            # it. Buying power is capped at free sleeve capital for the same
            # reason — the broker will happily lend against the income sleeve.
            equity = sleeves.swing_equity if account else 100_000.0
            power = min(account.buying_power, sleeves.swing_available) \
                if account else 100_000.0
            # The taper scales the position cap so a full book reaches the
            # day's deployment target. It can only ever reduce the cap — see
            # TradingPolicy.weekday_deployment_pct, which is a risk schedule
            # and explicitly not a weekday edge.
            day_cap = min(limits.max_position_pct,
                          position_cap_for(datetime.now().astimezone().date(), policy))
            sizing = size_position(
                equity, setup.entry_price, setup.stop_price,
                risk_pct=limits.risk_pct,
                max_position_pct=day_cap,
                buying_power=power,
            )
            if not sizing.is_tradeable:
                print(f"  {DIM}{setup.symbol:<6} skipped — {sizing.limited_by}{RESET}")
                record(CONFIRM, setup.symbol, sizing.limited_by)
                continue

            event = ex_dividends.get(setup.symbol)
            if event is not None and policy.block_ex_dividend_erosion:
                stop_pct = setup.risk / setup.entry_price * 100.0
                if event.erodes_stop(setup.entry_price, stop_pct,
                                     policy.ex_dividend_stop_threshold):
                    drop = event.drop_pct(setup.entry_price)
                    reason = (f"ex-dividend {event.ex_date} drops {drop:.2f}% "
                              f"= {drop / stop_pct * 100:.0f}% of a "
                              f"{stop_pct:.2f}% stop")
                    print(f"  {RED}{setup.symbol:<6} SKIPPED — {reason}{RESET}")
                    record(CONFIRM, setup.symbol, reason)
                    continue

            # TREND GATE. Enabled on the operator's decision, against a null
            # measurement that is recorded in policy.min_relative_strength.
            # A setup in a name lagging the index badly is admitted by the
            # screen exactly as readily as one in a leader; this is the only
            # place that distinction is made.
            facts = trend_facts.get(setup.symbol) or {}
            strength = facts.get("rs")
            if strength is not None and strength < policy.min_relative_strength:
                reason = (f"relative strength {strength:+.1f}pp below the "
                          f"{policy.min_relative_strength:+.1f}pp floor")
                print(f"  {RED}{setup.symbol:<6} SKIPPED — {reason}{RESET}")
                record(CONFIRM, setup.symbol, reason)
                continue

            # THE MODEL MAY NOW REFUSE. Only a GROUNDED veto blocks, and only
            # when the policy allows it; an outage produces no opinion at all,
            # so a dead model cannot become a silent halt.
            opinion = llm_order.get(setup.symbol)
            if policy.llm_veto_enabled and opinion is not None and opinion.veto:
                reason = f"model veto: {opinion.veto_reason or 'no reason given'}"
                print(f"  {RED}{setup.symbol:<6} SKIPPED — {reason}{RESET}")
                record(CONFIRM, setup.symbol, reason)
                continue

            if news_gate:
                flag = await check_news(setup.symbol, client, earnings=earnings)
                verdict = flag.verdict()
                if flag.headlines:
                    print(f"  {DIM}{setup.symbol:<6} news: "
                          f"{', '.join(sorted(set(flag.categories))) or 'none material'}{RESET}")
                if not verdict:
                    print(f"  {RED}{setup.symbol:<6} SKIPPED — {verdict.reason}{RESET}")
                    record(CONFIRM, setup.symbol, verdict.reason)
                    if flag.earnings and not flag.is_takeover_target:
                        # Temporary: eligible once the report is behind us.
                        eligible = ""
                        due = (earnings or {}).get(setup.symbol)
                        if due is not None:
                            eligible = (due.on + timedelta(days=1)).isoformat()
                        still_waiting = watchlist.defer(still_waiting, watchlist.Watched(
                            symbol=setup.symbol, reason=verdict.reason,
                            entry_price=setup.entry_price, stop_price=setup.stop_price,
                            target_price=setup.target_price, trigger_at=at.isoformat(),
                            added_at=datetime.now().astimezone().isoformat(),
                            eligible_on=eligible,
                        ))
                    for headline in flag.headlines[:2]:
                        print(f"         {DIM}{headline[:96]}{RESET}")
                    continue

            # SECTOR CONCENTRATION. max_per_sector caps how many positions one
            # sector may hold across the account. Ten of the 18 core names are
            # chips/AI hardware; eight open at once is one bet with 8% at risk,
            # not eight bets. 0 disables.
            sector_gate = sector_concentration(
                held, sector_of, setup.symbol, policy.max_per_sector
            )
            if not sector_gate:
                print(f"  {RED}{setup.symbol:<6} SKIPPED — {sector_gate.reason}{RESET}")
                record(CONFIRM, setup.symbol, sector_gate.reason)
                continue

            # Take-profit ladder: submit entry + per-tier sells + stop separately.
            # Single-target setups still use bracket_payload.
            if setup.targets:
                # Entry limit order (full shares)
                entry_payload = entry_payload(
                    setup.symbol, sizing.shares, setup.entry_price, at,
                    entry_offset_pct=policy.entry_limit_offset_pct)
                entry_payload["client_order_id"] = f"smc-{setup.symbol}-{int(at.timestamp())}"

                # Take-profit limit sells for each tier
                remaining_shares = sizing.shares
                tp_payloads = []
                for tier in setup.targets:
                    tier_shares = max(1, int(sizing.shares * tier.pct_of_shares))
                    remaining_shares -= tier_shares
                    tp_payloads.append(take_profit_payload(
                        setup.symbol, tier_shares, tier.target_price,
                        tier.label, at, entry_payload["client_order_id"]))

                # Stop loss for remaining shares after all tiers
                stop_shares = max(1, remaining_shares)
                sl_payload = stop_payload(
                    setup.symbol, stop_shares, setup.stop_price, at,
                    entry_payload["client_order_id"])

                intent = TradeIntent(
                    symbol=setup.symbol,
                    action="buy",
                    strategy="smc",
                    trigger_at=at,
                    entry_price=setup.entry_price,
                    stop_price=setup.stop_price,
                    target_price=0.0,  # multi-target: no single target
                    shares=sizing.shares,
                    risk_dollars=sizing.risk_dollars,
                    position_dollars=sizing.position_dollars,
                    reward_risk=0.0,
                    paper=True,
                    dry_run=dry_run,
                    client_order_id=entry_payload["client_order_id"],
                    rsi_gate_passed=rsi_shadow,
                    priority_rank=ranks.get(setup.symbol, 0),
                    priority_score=round(priority(setup), 2),
                    llm_conviction=llm_order[setup.symbol].conviction if setup.symbol in llm_order else "",
                    llm_concern=llm_order[setup.symbol].concern if setup.symbol in llm_order else "",
                    llm_driver=llm_order[setup.symbol].driver if setup.symbol in llm_order else "",
                    sector=sector_of.get(setup.symbol, ""),
                    dollar_volume=round(volumes_by_symbol.get(setup.symbol, 0.0), 2),
                    liquidity_tier=_liquidity_tier(volumes_by_symbol.get(setup.symbol, 0.0)),
                    llm_rank=llm_rank.get(setup.symbol, 0),
                    trigger_slot=session_slot(at),
                    trigger_weekday=at.strftime("%a"),
                    targets=setup.targets,
                )
                append_event(settings.audit_log_path, AuditEvent(
                    event="trade_intent", at=datetime.now().astimezone(),
                    symbol=setup.symbol, intent=intent,
                    stage=TRADE if not dry_run else WATCH,
                    policy=policy.fingerprint, build=build_id(),
                ))
                print(f"  {BOLD}{setup.symbol:<6}{RESET} {sizing.shares:>5} sh @ "
                      f"{setup.entry_price:>9,.2f}   "
                      f"stop {RED}{setup.stop_price:,.2f}{RESET}   "
                      f"targets {CYAN}{', '.join(f'{t.label}@{t.target_price:.2f}({t.pct_of_shares:.0%})' for t in setup.targets)}{RESET}")
                print(f"         {DIM}risking {sizing.risk_dollars:,.2f} "
                      f"({sizing.risk_dollars / equity * 100:.2f}% of "
                      f"{'swing sleeve' if policy.swing_allocation_pct < 100 else 'equity'}), "
                      f"position {sizing.position_dollars:,.2f}, "
                      f"limited by {sizing.limited_by}{RESET}")

                if dry_run:
                    continue
                try:
                    # Submit entry first
                    order = await broker.submit(entry_payload)
                    order_id = str(order.get("id", ""))
                    sent += 1
                    safety.reset_rejections(settings.kill_switch_path)
                    # Submit take-profit tiers
                    for tp in tp_payloads:
                        await broker.submit(tp)
                    # Submit stop loss for remaining shares
                    await broker.submit(sl_payload)
                    append_event(settings.audit_log_path, AuditEvent(
                        event="order_submitted", at=datetime.now().astimezone(),
                        symbol=setup.symbol, intent=intent,
                        order_id=order_id,
                        request_id=broker.last_request_id,
                        stage=TRADE, policy=policy.fingerprint, build=build_id(),
                    ))
                    print(f"         {GREEN}submitted{RESET} {DIM}id {order_id}{RESET}")
                except BrokerError as exc:
                    rej = safety.record_rejection(settings.kill_switch_path)
                    append_event(settings.audit_log_path, AuditEvent(
                        event="order_rejected", at=datetime.now().astimezone(),
                        symbol=setup.symbol, intent=intent,
                        request_id=broker.last_request_id, reason=str(exc),
                        stage=TRADE, policy=policy.fingerprint, build=build_id(),
                    ))
                    print(f"         {RED}not submitted: {exc}{RESET}"
                      f" {DIM}({rej} consecutive rejection(s)){RESET}")

        if not dry_run:
            print(f"\n{BOLD}{sent} order(s) sent to the paper account.{RESET}")
        watchlist.save(still_waiting)
        refused = rejections(tuple(decisions))
        if refused:
            print(f"\n{DIM}refused this run: "
                  f"{', '.join(f'{r} x{n}' for r, n in refused.items())}{RESET}")

        print(f"\n{YELLOW}Paper only. This strategy has ~200 trades of evidence from{RESET}")
        print(f"{DIM}86 days, with its stop threshold chosen from that same data. Every{RESET}")
        print(f"{DIM}paper trade here is an out-of-sample result the backtest did not{RESET}")
        print(f"{DIM}see — which is the point of running it.{RESET}")


#: Defaults come from the policy, so "what we test" and "what we trade"
#: cannot drift apart without a test failing.
VALIDATED = TradingPolicy()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--index", choices=list(INDEXES), default="sp500")
    p.add_argument("--k", type=int, default=VALIDATED.swing_k)
    p.add_argument("--minutes", type=int, default=VALIDATED.interval_minutes,
                   help="primary setup bars: 240 = 4H, 30 = 30m")
    p.add_argument("--fresh", type=int, default=VALIDATED.fresh_bars)
    p.add_argument("--min-rr", type=float, default=VALIDATED.min_reward_risk)
    p.add_argument("--min-risk-pct", type=float, default=VALIDATED.min_risk_pct)
    p.add_argument("--risk-pct", type=float, default=VALIDATED.risk_pct)
    p.add_argument("--max-positions", type=int, default=VALIDATED.max_positions)
    p.add_argument("--max-daily-loss-pct", type=float, default=VALIDATED.max_daily_loss_pct)
    p.add_argument("--max-position-pct", type=float, default=VALIDATED.max_position_pct)
    p.add_argument("--swing-allocation", type=float,
                   default=VALIDATED.swing_allocation_pct, metavar="PCT",
                   help="percent of the account this strategy may use; the "
                        "rest is the income sleeve (default 100 = all of it)")
    p.add_argument("--swing-cap", type=float, default=VALIDATED.swing_cap,
                   metavar="DOLLARS",
                   help="hold the swing sleeve at this size; everything above "
                        "it becomes income capital. Use this, not a percentage, "
                        "if profits are meant to accumulate into dividends")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="scan only (default)")
    mode.add_argument("--submit-paper", action="store_true",
                      help="explicitly submit bracket orders to Alpaca paper")
    mode.add_argument("--live", action="store_true",
                      help="LIVE MODE: hard-blocks on pre-flight failure "
                           "(model env, paper endpoint, session equity). "
                           "Extended-hours entries are refused.")
    p.add_argument("--show", type=int, default=10)
    p.add_argument("--trend-context", action="store_true")
    p.add_argument("--rsi-context", action="store_true")
    p.add_argument("--macd-context", action="store_true")
    p.add_argument("--skip-open", action="store_true",
                   help="ignore setups triggering 09:30-10:30 (NOT validated: "
                        "improves holdout but leaves only 21 trades)")
    p.add_argument("--skip-weekday", nargs="*", default=[], metavar="DAY",
                   help="e.g. Wed. NOT validated: 86 days is ~12 Wednesdays")
    p.add_argument("--no-news-gate", dest="news_gate", action="store_false",
                   help="skip the news check (it blocks mergers and litigation)")
    a = p.parse_args()

    asyncio.run(main(
        a.index, a.minutes, a.k, a.fresh, a.min_rr, a.min_risk_pct,
        RiskLimits(
            max_positions=a.max_positions,
            max_daily_loss_pct=a.max_daily_loss_pct,
            max_position_pct=a.max_position_pct,
            risk_pct=a.risk_pct,
        ),
        not a.submit_paper and not a.live, a.show,
        a.trend_context, a.rsi_context, a.macd_context,
        a.news_gate, a.skip_open, tuple(a.skip_weekday),
        TradingPolicy(
            interval_minutes=a.minutes, swing_k=a.k,
            min_risk_pct=a.min_risk_pct, min_reward_risk=a.min_rr,
            fresh_bars=a.fresh, risk_pct=a.risk_pct,
            max_position_pct=a.max_position_pct,
            max_positions=a.max_positions,
            max_daily_loss_pct=a.max_daily_loss_pct,
            block_structural_news=a.news_gate,
            swing_allocation_pct=a.swing_allocation,
            swing_cap=a.swing_cap,
        ),
        live=a.live,
    ))
