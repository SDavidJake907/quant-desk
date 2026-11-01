"""The rules, in one place, versioned and attached to every decision.

WHY A POLICY OBJECT RATHER THAN SCATTERED FLAGS
The rules that decide a trade currently live in six places: constants in the
screen, defaults in argparse, thresholds in the guards, a liquidity floor in
the runner. That is fine while one person runs one command by hand. It stops
being fine the moment trades accumulate into evidence, because six months from
now the only question that matters about a losing month is "what rules were
running", and nothing today can answer it.

So: one frozen object, hashed, written beside every decision. A result can
always be traced back to the exact rules that produced it.

THE FOUR STAGES
    SCAN     the deterministic detector found a setup
    WATCH    it is a real candidate, ranked against its peers
    CONFIRM  the gates ran — liquidity, news, earnings, risk, duplicates
    TRADE    an order went to the broker

Every candidate reaches exactly one final stage and records why it stopped.
Rejections are data: "the screen found 40 setups and refused 38, here is the
breakdown by rule" is a far more useful record than two orders and silence.

WHAT THIS IS NOT
It is not a new entry rule. Entry is still the close of the trigger bar, which
is what the holdout measured. Adding a confirmation *delay* — wait for the next
bar, wait for a retest — would be a different strategy and would need its own
holdout before it could claim the same evidence.
"""

from __future__ import annotations

import hashlib
import json

from scanner.models import Frozen

SCAN = "scan"
WATCH = "watch"
CONFIRM = "confirm"
TRADE = "trade"
STAGES = (SCAN, WATCH, CONFIRM, TRADE)


class TradingPolicy(Frozen):
    """Every rule that can stop a trade. Hashed into the audit record."""

    # --- what counts as a setup ------------------------------------------
    #
    # THESE FOUR ARE THE VALIDATED CONFIGURATION. The +0.218R out-of-sample
    # result was measured at 30m, k=2, value sweep on, 1.5% stop floor. Any
    # entry point whose defaults differ is trading or testing something the
    # evidence does not cover — which is exactly what happened before this
    # object existed: the backtest defaulted to 4h with NO stop floor and the
    # live runner defaulted to 4h, while every result quoted came from 30m.
    # `test_policy.py` pins these so a default cannot drift again.
    interval_minutes: int = 30
    swing_k: int = 2
    #: TURNED OFF 2026-09-28 after being measured against itself.
    #:
    #: Same scan, flag toggled, 30m Alpaca store:
    #:
    #:     True   n=860   +0.054R  se 0.058   total +46.3R
    #:     False  n=1468  +0.039R  se 0.044   total +57.1R
    #:
    #: The effect on per-trade quality is +0.015R against standard errors near
    #: 0.05 -- indistinguishable from zero. It does not measurably improve the
    #: trades it keeps. It discards 41% of the sample to do it, and total return
    #: is 23% HIGHER without it.
    #:
    #: Setup scarcity is the binding constraint on everything here: 224 trades
    #: are needed to exclude zero and the scan finds about 1.2 a session. This
    #: buys 70% more evidence per unit time at no measurable cost in quality.
    #:
    #: WHAT IS GIVEN UP. This encoded the strategy's own thesis, that a sweep
    #: should occur at a volume-profile value area, so removing it removes part
    #: of the idea rather than a parameter. And the difference sits inside the
    #: error bars, so the filter is not proven useless -- only unproven useful.
    #: Set back to True to restore it.
    require_value_sweep: bool = False
    min_risk_pct: float = 1.5
    min_reward_risk: float = 1.5
    #: How many bars back a trigger may be and still be acted on. TWO, not
    #: four, because the measured entry is the CLOSE OF THE TRIGGER BAR — every
    #: bar of delay is slippage away from the thing the holdout measured, and
    #: on 30m bars four is a two-hour-old price. `trade.py` already used 2
    #: while this object claimed 4 and `find_setups.py` claimed 4; aligning on
    #: the tighter value leaves live behaviour unchanged and makes the
    #: declaration honest.
    fresh_bars: int = 2
    #: A setup older than the latest bar is refused when the latest close is
    #: already this far BELOW its entry. fresh_bars admits a trigger one bar
    #: old at its old close as the limit; when the price has fallen since, the
    #: buy limit is above the market and fills at once beside the stop. Live,
    #: the only two fills more than 1% under the limit (APP 2026-08-20, PSX
    #: 09-24) were both orders sent one scan late, both 0.5% from the stop,
    #: both stopped within the hour; every on-time order filled within 0.34%.
    #: A genuine bug, fixed 2026-10-04 before window 15's first scan.
    max_entry_drift_pct: float = 0.5
    #: The entry limit sits this far ABOVE the trigger close. The order lands
    #: ~3.5 minutes after the bar closes and these setups bounce fast: a limit
    #: AT the close filled 91% of 538 setups replayed on 1-minute bars (live:
    #: 15 of 18 -- META, LULU, SLB touched and never filled) and the misses
    #: were net winners. +0.30% filled 99% and earned the most total R (+46.7R
    #: vs +34.5R), beating a market order while still refusing to pay for a
    #: spike. Together with max_entry_drift_pct: down 0.5% -> skip, up to 0.3%
    #: -> fill, beyond -> let it go. limit_minute_test.py, 2026-10-05.
    entry_limit_offset_pct: float = 0.3

    # --- what is tradeable ------------------------------------------------
    min_price: float = 5.0
    #: Liquidity floor, in dollars traded per bar.
    #:
    #: RAISED FROM 5M TO 20M ON MEASUREMENT. This strategy trades liquidity
    #: SWEEPS — price running through a level where stops are clustered. In a
    #: thinly traded name there are not enough resting orders for a sweep to
    #: mean anything, so the pattern matches noise instead of an event.
    #:
    #: Measured over 125 setups, split at the repo's own existing tier
    #: boundary rather than one chosen after looking:
    #:
    #:     below 20M   n=62   +0.114R   (train -0.152R, holdout +0.508R)
    #:     at/above    n=63   +0.776R   (train +0.673R, holdout +1.053R)
    #:
    #: Same direction and similar size on both splits, and a permutation test
    #: puts the chance of a gap that large from a random split at 2.2% — the
    #: strongest result this project has produced. Against 18.9% for the
    #: weekday claim and 25.1% for time of day, both of which were noise.
    #:
    #: The cost is real: it removes 263 of 499 symbols and halves the setup
    #: count, so evidence accumulates at half the rate. That is a deliberate
    #: trade of quantity for quality.
    min_dollar_volume: float = 20_000_000.0

    #: Average true bar range over the last 20 bars, as a % of price. A floor
    #: here says "this name moves enough for a 1.5% stop to be a level rather
    #: than a rounding error".
    #:
    #: ZERO — DISABLED — ON PURPOSE. This number existed as a hardcoded 0.4 in
    #: `find_setups.py` alone, where it silently narrowed the PREVIEW to 230
    #: symbols while `trade.py` acted on 267. The two disagreed, and the 37 in
    #: the gap were names like MA, KO, PG and BRK.B — WBD averages 0.088%, so a
    #: 1.5% stop there is seventeen bars of ordinary movement away.
    #:
    #: The argument for enabling it is the same MECHANISM argument that carried
    #: the 20M floor: this trades sweeps of resting orders, and a name that
    #: barely moves does not sweep. But the floor was measured before it was
    #: enforced (p=0.022 over 125 setups) and this has never been measured at
    #: all. Thirteen untested ranking ideas have failed here, several after
    #: looking obvious. So it ships DECLARED and OFF: both entry points now
    #: read the same number, changing it moves the fingerprint, and live
    #: behaviour is exactly what the holdout measured until a split says
    #: otherwise.
    min_range_pct: float = 0.0

    #: Minimum 12-1 relative strength against ^GSPC, in percentage points, for
    #: a setup to be tradeable. -10.0 excludes laggards and deep laggards.
    #:
    #: WHY IT IS HERE. Both positions opened 2026-09-28 were deep laggards:
    #: MLM at -29.2pp and CMG at -21.2pp, each below its 200-day average and
    #: down ~18% on the year. The screen has no opinion about trend, so a
    #: liquidity sweep in a name that has been sold for twelve months scores
    #: identically to one in a leader. The mechanism argument is real: a sweep
    #: is supposed to be a SHAKEOUT, which needs someone defending the level,
    #: and in a falling knife the break of lows may just be the selling
    #: continuing.
    #:
    #: IT TESTED NULL, AND THAT IS RECORDED RATHER THAN HIDDEN.
    #: `scripts/relative_strength_test.py` over 731 setups:
    #:
    #:     band            train            holdout
    #:     leader          -0.114R n=155    +0.596R n=104
    #:     in line         -0.248R n=108    +0.169R n=36
    #:     laggard         -0.134R n=83     -0.204R n=31
    #:     deep laggard    -0.044R n=141    +0.169R n=73
    #:
    #: Holdout favours leaders strongly, but TRAIN ranked deep laggards best
    #: and leaders third. The halves disagree, the chosen gap is +0.000R at
    #: p=0.999, and that is the signature of noise. Nineteen ideas have now
    #: failed the same bar.
    #:
    #: It is enabled anyway, as a deliberate and reversible choice by the
    #: operator, on the mechanism argument rather than the measurement. It is a
    #: declared field so it moves the fingerprint, and the cost is legible:
    #: laggard and deep-laggard bands are 45% of setups, so this roughly halves
    #: the trade rate in exchange for an edge the data does not confirm. Set it
    #: to -100.0 to disable.
    #: DISABLED 2026-09-28, the same day it was enabled.
    #:
    #: It was turned on against its own measurement, on the operator's
    #: judgement and a mechanism argument. The measurement has not improved:
    #: holdout favoured leaders (+0.596R n=104) while TRAIN ranked deep
    #: laggards best and leaders third, the chosen gap was +0.000R at p=0.999,
    #: and the halves disagreed -- the signature of noise.
    #:
    #: What settled it is the cost. It discards 45% of setups, and setup
    #: scarcity is the binding constraint on every open question here: 224
    #: trades are needed to exclude zero and the scan finds about 1.2 a
    #: session. A filter with no measured benefit that halves the evidence rate
    #: makes every other question slower to answer.
    #:
    #: -100.0 disables it. The field stays declared and fingerprinted so
    #: enabling it remains one number and stays visible in the audit trail.
    min_relative_strength: float = -100.0

    # --- what stops a trade -----------------------------------------------
    block_structural_news: bool = True
    #: 0 = hold through earnings (operator's choice for window 16, 2026-10-06:
    #: the core list is hand-picked trending names). Was 7.
    block_earnings_within_days: int = 0

    #: Refuse a long whose stop would be consumed by the mechanical
    #: ex-dividend price drop. THIS IS NOT A PREDICTIVE FILTER. Every context
    #: gate rejected in this project claimed that some condition predicts
    #: better outcomes, and seven of nine made the holdout worse. This claims
    #: nothing about the future: on the ex-date the exchange reduces the open
    #: by the dividend, and MO's is 1.56% against a 1.5% minimum stop. It is a
    #: correction to a modelling error, not an optimisation — the backtest ran
    #: on Yahoo's ADJUSTED bars, where the drop has been smoothed away, so it
    #: measured a world in which this cost does not exist.
    block_ex_dividend_erosion: bool = True

    #: Share of the stop the drop may consume before a setup is refused.
    ex_dividend_stop_threshold: float = 0.33

    # --- how big -----------------------------------------------------------
    #: Share of the account this strategy may draw on. The rest is the income
    #: sleeve: held continuously, never sized or sold by this code. Sizing runs
    #: against the sleeve, not the balance — otherwise "1% of equity" quietly
    #: becomes 1.5% of the money the strategy actually has. 100 means the whole
    #: account, which is how this behaved before sleeves existed.
    swing_allocation_pct: float = 100.0

    #: Hard ceiling on the swing sleeve in dollars, or None for no ceiling.
    #: A percentage floats with the account, so profit stays where it was made;
    #: a cap holds the strategy at a fixed size and lets everything above it
    #: fall to the income sleeve. Only the cap makes income grow faster than
    #: the account, which is what "reinvest profits into dividends" means.
    swing_cap: float | None = None

    risk_pct: float = 1.0
    max_position_pct: float = 20.0
    max_positions: int = 12
    max_daily_loss_pct: float = 3.0
    min_equity: float = 1_000.0

    # --- when the market is shut --------------------------------------------
    #: Refuse a setup whose trigger bar ended a session.
    #:
    #: NOT A PREDICTIVE FILTER, and adopted on the same grounds as the
    #: ex-dividend gate: it corrects a modelling error rather than claiming an
    #: edge. `fresh_bars` counts BARS, so overnight is one bar and seventeen
    #: hours, and a setup from yesterday afternoon reads as fresh three minutes
    #: after today's open. The backtest measured entry at the CLOSE OF THE
    #: TRIGGER BAR; across a session boundary that price was never available,
    #: because the market was shut. Measured on 60 days of 30m bars: entries
    #: across the close are adverse 88% of the time, mean +0.785%, worst
    #: +3.20% -- more than twice the 1.5% minimum stop consumed before the
    #: position exists. trade.py already refuses these while the market is
    #: closed; this extends the same refusal three minutes past the open.
    block_overnight_entry: bool = True

    #: Close every position at the last bar before the weekend.
    #:
    #: OFF, AND EXPENSIVE. This one is not a correction, it is a risk purchase
    #: with a measured price: -0.213R per trade, taking expectancy from +0.447R
    #: to +0.233R against a validated holdout of +0.218R. It consumes
    #: essentially the whole edge, because max_hold_bars of 60 means 36% of
    #: setups span a weekend. Weekend gaps are ALREADY modelled -- a bar that
    #: opens through the stop fills at the open -- so this does not fix an
    #: unpriced risk; it removes an exposure no stop can cover, at that price.
    #: Turning it on is a deliberate choice, and it has been made: the
    #: operator judged an unmanageable weekend exposure worse than the cost.
    flat_before_weekend: bool = True

    #: Share of equity that may be committed to NEW positions, by weekday
    #: (Monday first). Falls toward the weekend and reaches zero where the
    #: wind-down already refuses entries, so the two rules cannot disagree.
    #:
    #: NOT A WEEKDAY EDGE. Expectancy by trigger day measured Mon +0.539R,
    #: Tue +0.054R, Wed -0.562R, Thu +0.780R, Fri +1.068R over 125 setups, and
    #: a permutation test puts the chance of a random five-way split producing
    #: a best day at least that good at 18.9%. Train and holdout disagree about
    #: which day wins. This is a risk schedule, and since exposure and return
    #: scale together here it costs return roughly in proportion.
    #: DOUBLED 2026-09-25, deliberately, to make the dollars readable.
    #:
    #: At the old schedule every position was sized by the cap rather than by
    #: risk — `limited by position cap` printed on all three trades of the
    #: last window — and actual risk came out at 0.108% against a declared
    #: `risk_pct` of 1.0%. Nine times too small. Three trades at +0.44R
    #: produced $1,860 on a $100k account, which is not a result anyone can
    #: read a drawdown from.
    #:
    #: This changes NO expectancy. R is scale-invariant: doubling exposure
    #: doubles dollars and doubles drawdown and leaves +0.248R exactly where
    #: it was. What it buys is visibility — dollar P&L and drawdown large
    #: enough to mean something, and fills under a size that can actually
    #: move against you.
    #:
    #: The taper SHAPE is unchanged and still not a weekday edge. Monday
    #: still deploys most and Friday still deploys nothing, for the same
    #: bars-available arithmetic. Only the scale moved.
    #:
    #: Monday now hits `max_position_pct` exactly: min(20, 100/5) = 20%. So
    #: this is the most the existing ceilings allow without raising one, and
    #: going further is a separate decision about single-name concentration.
    #:
    #: EXPECT THE DAILY-LOSS GUARD TO START BINDING. It is inert at 0.108%
    #: risk per trade and will not stay inert at twice the size. A scan that
    #: refuses to trade after a bad morning is the guard working, not a fault.
    #: FLAT AT 100% from 2026-09-28, was (100, 80, 60, 40, 0)
    #: averaging 56% of the sleeve. `taper.py` always described the
    #: schedule as a PREFERENCE that costs return in proportion, with
    #: return-over-drawdown stable between 4.7 and 5.1 at every level
    #: tested. Raising it therefore raises return ~1.79x and drawdown
    #: ~1.79x, leaving that ratio where it was. It expresses whatever
    #: edge exists more strongly, including an edge of zero.
    weekday_deployment_pct: tuple[float, ...] = (
        100.0, 100.0, 100.0, 100.0, 100.0)

    #: Weekday from which no NEW positions are opened, Monday being 0.
    #: Thursday, because flattening on Friday alone still allows a position to
    #: be opened on Friday morning with hours left to go wrong. Existing
    #: positions keep their brackets and are closed by scripts/flatten.py.
    #: FRIDAY ONLY, on arithmetic rather than on a weekday edge.
    #:
    #: Measured bars available before the Friday flatten, against a ~20 bar
    #: mean hold:
    #:
    #:     Mon 63   Tue 50   Wed 37   Thu 17   Fri 11
    #:
    #: Mon-Wed have more room than the strategy needs, so the flatten costs
    #: them nothing. Thursday is just short — the flatten costs it -0.313R and
    #: it still lands at +0.467R, above the overall average, so it trades.
    #: Friday has about half the time it needs and the flatten takes it from
    #: +1.068R to +0.247R.
    #:
    #: The weekday EXPECTANCY differences are noise (18.9% on a permutation
    #: test, and train and holdout disagree about which day wins). Bars
    #: available is not — it is arithmetic, true every week whatever the
    #: sample size. This rule rests on the second thing, not the first.
    #: RAISED TO 5 (no wind-down) 2026-09-28, on the operator's decision and a
    #: measurement that removed the original justification.
    #:
    #: Friday entries were refused because the Friday flatten was priced at
    #: -0.213R per trade. `scripts/flatten_cost_test.py` measured it at
    #: **-0.012R, se 0.020** -- eighteen times smaller. It touches 26% of trades
    #: and those average +0.528R: they are profitable when closed, not
    #: sacrificed. The reason for excluding Friday was an estimate that did not
    #: survive measurement.
    #:
    #: And Friday is not a worse day. With the flatten modelled, by trigger day:
    #:
    #:     Mon -0.011R n=175   Tue -0.050R n=233   Wed +0.066R n=171
    #:     Thu +0.015R n=162   Fri -0.002R n=120  (highest win rate, 50.8%)
    #:
    #: Friday sits mid-pack and indistinguishable from Monday, while being 14%
    #: of all setups. Excluding it removed a seventh of the evidence for nothing
    #: measurable. Set back to 4 to restore the wind-down.
    wind_down_from_weekday: int = 5

    # --- how it ends --------------------------------------------------------
    max_hold_bars: int = 60

    # --- which strategy each rule applies to (WORKLIST §37) -----------------
    #: The weekend flatten and the hold limit were written for the sweep
    #: scanner and used to act on EVERY position in the account. Ownership is
    #: read from the client_order_id prefix (execution/ownership.py). A
    #: strategy not named here is never closed by that rule; a position no
    #: prefix claims (bought by hand in the dashboard) is never closed by any.
    #: Manual picks hold over weekends: the operator's choice, 2026-10-06.
    #: "orb" closes itself every afternoon; here only as the Friday backstop.
    #: "core" added 2026-10-30 (operator: "we need to stop holding for the weekend", then
    #: chose SPY + day trades only): the SPY core is flat over every weekend-length closure
    #: and re-bought on the next session (plan_run.before_closure stops its buy that day).
    #: The rules book and small caps hold over weekends behind their stops: the real trades
    #: held 7+ days were the only ones that made money.
    flatten_strategies: tuple[str, ...] = ("smc", "orb", "core")
    max_hold_strategies: tuple[str, ...] = ("smc",)
    #: Held without a stop by design: the momentum backtest had none (its
    #: guard is the S&P trend filter), so protect.py must not "re-arm" one.
    #: "core" removed 2026-10-30 (operator: "every position should have a sl"):
    #: peak watch keeps its stop (plan_run.core_stop_pass) and the checks now flag it naked.
    stopless_strategies: tuple[str, ...] = ("momentum",)

    # --- the operator's own picks (scripts/manual_trade.py) -----------------
    #: Same 1% risk and the per-position cap as the scanner. Target defaults
    #: to this multiple of the stop distance when none is given: a CHOSEN
    #: number, not a measured one.
    manual_default_reward_risk: float = 2.0
    #: At most this many open positions per GICS sector across the account.
    #: 10 of the 18 core names are chips/AI hardware; 8 of them open at once
    #: is one bet with 8% at risk. 0 disables. Chosen, not measured.
    max_per_sector: int = 3

    # --- the shared sleeve: picks list + one-off manual trades ---------------
    #: Per-stock cap for the shared sleeve, FIXED rather than 100/max_positions.
    #: 2026-10-07: slots 8 -> 12 so one-off manual trades fit beside the 8
    #: picks; deriving the cap from the slot count would have shrunk every pick
    #: from 12.5% to 8.3%. Total spend is still bounded by the sleeve itself
    #: (buying room = sleeve - deployed), so a full book cannot exceed it.
    shared_position_cap_pct: float = 12.5

    # --- the operator's list, traded automatically (execution/picks.py) ----
    #: watchlists/picks.txt is kept in sync every 30 min in market hours:
    #: added names bought, removed names sold. Same 1% risk and position cap
    #: as manual picks, on the same half of the account. All CHOSEN, not
    #: measured (operator, 2026-10-06; WORKLIST §41).
    #: Safety stop this many daily ATRs under the entry: room for normal
    #: noise, there to stop a collapse.
    picks_stop_atr_multiple: float = 2.5
    #: Bracket target as a multiple of the entry: a ceiling for the order
    #: shape, not a profit target -- winners ride until delisted from the list.
    picks_target_ceiling: float = 2.0
    #: An unfilled list buy older than this is cancelled and re-sent at the
    #: current price on the next run.
    picks_stale_entry_minutes: float = 25.0
    #: SECTION 2, FAST MOVERS (operator 2026-10-21: "big caps-section 1,
    #: fast-movers-section2"): names under `# [fast]` in picks.txt. Same stop
    #: (2.5 ATR was the middle of the 1.5/2.5/3.5 grid on the Lotto trials
    #: names; tighter stopped out ~half the time), half the risk (operator
    #: chose 0.5%: gaps run through 17-29% stops), at most 2 at once, no buy
    #: with earnings today or in the next 7 calendar days. No profit target (targets cut the
    #: average in every row of that grid). CHOSEN, not measured: Lotto trials
    #: (TRIALS #79) grades fast movers forward.
    picks_fast_risk_pct: float = 0.5
    picks_fast_stop_atr_multiple: float = 2.5
    picks_fast_max_names: int = 2
    picks_fast_earnings_days: int = 7
    #: Sector cap for the list. 0 = off: the operator's list is a deliberate
    #: chip/AI bet and they chose to lift the cap for it (2026-10-06).
    picks_max_per_sector: int = 0

    # --- Gemma's own book (advisor/gemma_trader.py, WORKLIST §42) ----------
    #: Gemma chooses up to `gemma_max_positions` names from watchlists/core.txt
    #: each morning, with a quoted reason for each; the system prices, sizes,
    #: stops and sends, as for the picks list. Its own sleeve and slots.
    #: Operator, 2026-10-06: real paper orders, core list only, widen if it
    #: succeeds. 10% at first; raised to 40% on 2026-10-07 before its first
    #: order (operator chose 40/40/20 over 50/35/15: momentum needs ~35%+ to
    #: buy its $900-1,700 names in whole shares, and picks keep 20%).
    #: 60% from 2026-10-07 evening, when momentum stopped trading and its 40%
    #: was split Gemma +20 / picks + manual +20 (operator's choice).
    #: 50.0 from 2026-10-09 (operator: "both of us starting with 50/50"): Gemma
    #: 50%, picks + manual 50%. The quarterly 25% transfers start 2027 (§60).
    #: 100.0 / 10 names from 2026-10-22 (operator: "Gemma should take my position ... I need
    #: help catching up"): Gemma runs the whole account; the picks sleeve is 0. Slots 5 -> 10
    #: and risk halved, so each position stays ~10% / ~$1k risk -- more names, not bigger bets.
    #: 90.0 from 2026-10-23: 10% to the opening-range breakout book (operator "lets tie this
    #: in"; Gemma asked and agreed: "the 10% capital reduction is negligible").
    #: 40.0 / 6 names from 2026-10-26: the core-and-satellite plan (execution/satellite.py;
    #: operator + Gemma 10-23): 50% SPY core, Gemma 40% in 6 equal names, breakout 10%.
    #: From 2026-10-26 the rules book (scripts/rules_pick.py) picks these names, not Gemma.
    #: 30.0 / 5 names from 2026-10-27: 10% to the small-cap book (operator: "from the rules book").
    gemma_allocation_pct: float = 30.0
    gemma_max_positions: int = 5
    #: Risk per Gemma trade, as % of ITS sleeve. 2.0 from 2026-10-07 (operator:
    #: "push Gemma to max out profit"): at 1% with ~10% stops its five positions
    #: used ~$17.6k of a ~$61k sleeve. 2% lets it near-fully deploy (the 20%
    #: per-name cap binds). Twice the dollar swing; the competition score is %
    #: on money invested, so it stays comparable with the picks book (1%).
    #: 1.0 from 2026-10-22: the sleeve doubled (50 -> 100%), so the dollar risk is unchanged.
    gemma_risk_pct: float = 1.0
    #: GEMMA'S MENU (2026-10-08, operator: "let it pick its own TP and SL";
    #: WORKLIST §50). For each NEW buy Gemma names a stop, a target and a size
    #: from these lists; the system turns the name into a price. Stop: daily
    #: ATRs under the entry. Target: reward-to-risk multiple, 0 = no target
    #: (the picks ceiling). Size: risk as % of its sleeve; "normal" is
    #: `gemma_risk_pct`. CHOSEN, not measured: every choice is logged so it
    #: can be graded. The 20% per-name cap still binds a large size.
    #: One choice each from 2026-10-26 (the plan): stop 2.5 ATR, no fixed target (a trailing
    #: exit sells), size capped at an equal sixth. From satellite.WIDE_STOP_FROM (her B2) the
    #: 3% floor is off and the risk is a 3% stop's dollars on a full sixth, so a name moving more
    #: than 1.2% a day is bought smaller. She chooses WHICH names; the rules size and exit them.
    gemma_stop_atr_menu: tuple[tuple[str, float], ...] = (("normal", 2.5),)
    gemma_target_r_menu: tuple[tuple[str, float], ...] = (("none", 0.0),)
    gemma_size_risk_menu: tuple[tuple[str, float], ...] = (("normal", 1.0),)
    #: TradingAgents in Gemma's facts. OFF from 2026-10-07 at Gemma's own request
    #: (it wants its record to be its own judgement); TradingAgents keeps running
    #: as a graded shadow the operator still sees (second_opinion.py, weekly report).
    gemma_sees_tradingagents: bool = False
    #: The 03:00 daily brief in Gemma's facts (WORKLIST §52, operator 2026-10-08):
    #: coming events, the President's posts and schedule, the news, and which of
    #: its stocks each item touches -- dates and headlines, never its own earlier
    #: read of them. Context, labelled as such; not a tested edge.
    gemma_sees_brief: bool = True
    #: EXTENDED HOURS (WORKLIST §59, operator 2026-10-09: both books, every
    #: session -- premarket, after-hours, and overnight from Sunday 8 PM ET).
    #: Outside regular hours orders are plain limit orders (Alpaca accepts no
    #: stops then); the stop is armed by protect.py at the open.
    extended_hours_trading: bool = True
    #: An extended-hours sell is a limit this far under the live price, so it
    #: fills in a thin market without becoming a market order.
    extended_sell_offset_pct: float = 0.5

    # --- momentum engine sleeve ---------------------------------------------
    #: Share of equity the momentum book is sized against. 50% on 2026-10-06,
    #: 40% from 2026-10-07 (Gemma 40, picks + manual 20). Fixed into the paper
    #: book at its first rebalance; changing it later does not resize the book.
    momentum_allocation_pct: float = 0.0
    #: MOMENTUM IS A HELPER, NOT A BOOK (2026-10-07). Its first market-on-close
    #: orders were accepted and 8 of 9 expired unfilled on the paper account.
    #: The operator chose to stop it trading: the monthly dry run keeps its
    #: picks on record (Gemma's benchmark), TradingAgents rates them at 03:30,
    #: and Gemma may choose them as options. Its 40% went Gemma +20, picks +20.
    momentum_trades: bool = False

    # --- opening-range breakout day book (execution/orb.py, scripts/orb_trade.py) ----
    #: Operator 2026-10-23 ("lets tie this in, I think it will help"), from Lumibot's ORB
    #: example, written here. Rules, not Gemma: from her pool, names with 2x their usual
    #: opening volume break the first 15 minutes' high; stop the range low, target 1.5R,
    #: everything sold 10 minutes before the close. Its own 10%, taken from Gemma's share
    #: with her agreement. CHOSEN, not measured: graded forward like every book.
    orb_allocation_pct: float = 10.0
    orb_max_positions: int = 3
    #: Risk per trade as % of the ORB sleeve. The per-name cap (1/3 of the sleeve)
    #: usually binds first: a 1% stop on $3.3k risks ~$33.
    orb_risk_pct: float = 1.0
    orb_reward_risk: float = 1.5

    # --- the SPY core (execution/satellite.py, the plan of 2026-10-23) ------------
    #: Half the account sits in SPY: bought in two halves, topped up below 90% of its
    #: target, never sold by a rule, no stop. Operator + Gemma: "holding does work".
    core_allocation_pct: float = 50.0
    core_symbol: str = "SPY"
    #: Sold before the close only when losing AND all four signs confirm (execution/preclose.py,
    #: operator 2026-10-31: strict, SPY only). Every other book is scored and reported, not sold.
    preclose_sell_strategies: tuple[str, ...] = ("core",)

    # --- the small-cap book (execution/small_book.py, scripts/small_pick.py) --------
    #: Operator 2026-10-27 ("penny stocks .. low caps .. a gate for it, 10% of the account"):
    #: S&P SmallCap 600 members, $5+, $2M+ traded a day, while IWM is above its 200-day;
    #: the rules book's checks and exits. 4 equal names. CHOSEN, not measured: S&P 600
    #: momentum was +0.83 pts a hold over random, t 1.28 (WORKLIST §31).
    small_allocation_pct: float = 10.0
    small_max_positions: int = 4
    small_min_price: float = 5.0
    small_min_dollar_volume: float = 2_000_000.0
    small_gate_symbol: str = "IWM"

    def gemma_menu_value(self, kind: str, label: str) -> float:
        """The number behind one of Gemma's menu names. KeyError if unknown."""
        menu = {"stop": self.gemma_stop_atr_menu, "target": self.gemma_target_r_menu,
                "size": self.gemma_size_risk_menu}[kind]
        return dict(menu)[label]

    @property
    def own_sleeves_pct(self) -> float:
        """Equity claimed by the books with their own sleeve."""
        return (self.momentum_allocation_pct + self.gemma_allocation_pct + self.orb_allocation_pct
                + self.core_allocation_pct + self.small_allocation_pct)

    # --- recorded, never enforced ------------------------------------------
    #: Each of these improved the holdout and none has forward evidence. They
    #: are logged on every decision so the question can be settled later on
    #: data nothing was chosen from. Turning one on is a deliberate act.
    shadow_rsi_gate: bool = True
    shadow_session_slot: bool = True
    shadow_weekday: bool = True
    shadow_llm_ranking: bool = True

    # --- what the model may decide ------------------------------------------
    #: Whether a grounded `veto` from the ranking model BLOCKS a trade rather
    #: than merely colouring it.
    #:
    #: Until now the model has been advisory in the strict sense: it ranks
    #: candidates the screen already chose and nothing it says can stop an
    #: order. That was the right default while its judgement was unmeasured.
    #:
    #: What IS measured: `scripts/gemma_audit.py` gives it the project's own
    #: facts and marks its answers against conclusions established earlier. It
    #: scores 2/2 on the gradeable questions, and unprompted it identified the
    #: outlier trade skewing the mean and the optimistic fill assumption --
    #: both of which were independently confirmed here.
    #:
    #: What is NOT measured: whether its per-candidate calls predict outcomes.
    #: That needs the slate to accumulate, and there are three slates.
    #:
    #: A veto is refused unless `grounded` is true, so an ungrounded opinion
    #: cannot block a trade, and every veto is recorded with its reason. If the
    #: model is unreachable nothing is vetoed -- the same fail-open direction
    #: the news gate already takes, because a dead model must not become a
    #: silent halt.
    #: DISABLED 2026-09-28 on measurement, hours after being enabled.
    #:
    #: `scripts/llm_compliance.py` asked the same input five times at
    #: temperature 0. On a pending all-cash acquisition -- the clearest case in
    #: its own prompt -- the model called it `weak` 5/5 and escalated to a veto
    #: 1/5. The judgement is stable; the action is a coin flip.
    #:
    #: A gate that fires on 20% of identical inputs is not a gate, and this one
    #: also UNDER-fires on the case it reliably recognises. Leaving it on means
    #: a live trade is blocked or not by which sample was drawn.
    #:
    #: The fix is not more prompt text. Derive the veto from `conviction ==
    #: weak` plus a named category, so it is deterministic given the judgement
    #: -- and the judgement IS stable. Until that exists this stays off, and
    #: the opinion continues to be recorded in the slate where it can be scored.
    llm_veto_enabled: bool = False

    @property
    def fingerprint(self) -> str:
        """Short stable hash. Two runs with the same fingerprint ran the same
        rules; a change in results across different fingerprints is a change
        in policy, not in the market."""
        blob = json.dumps(self.model_dump(mode="json"), sort_keys=True)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]

    def as_prose(self) -> str:
        """The live rules in English, GENERATED from the fields.

        WHY GENERATED AND NOT WRITTEN
        A model needs the rules in language, and the obvious way to give it
        them is to type them into the prompt. That creates a second copy of
        every rule, and the copy goes stale silently. It has already happened
        three times in this repository on one day: this module's own taper
        described a schedule that had changed; `ranking.py` claimed the model
        "never sees prices" after it had been given four of them; and the
        ranking prompt told the model price was not its job in the same breath
        as asking it to judge price.

        A human reading a stale docstring is mildly misled. A MODEL reading a
        stale prompt reasons confidently from a false premise and cannot check.

        So nothing here is typed. Every number and every clause comes from the
        field beside it, which means turning a rule off turns its sentence off
        too, and `test_policy.py` pins the correspondence.

        This describes the RULES only. The measured behaviour of the strategy
        -- how often it stops out, where the money comes from -- is evidence
        about it rather than policy, changes without the policy changing, and
        belongs in the caller's prompt, not here.
        """
        lines = [
            f"The screen looks for a liquidity sweep on {self.interval_minutes}"
            f"-minute bars: price takes out a prior swing low, makes a higher "
            f"low, breaks character, then breaks structure. Swings are "
            f"confirmed {self.swing_k} bars either side. It is LONG ONLY -- "
            f"there is no short side, so a bearish setup is invisible to it "
            f"rather than rejected.",
            f"Entry is the close of the break bar. The stop is the higher low, "
            f"and must sit at least {self.min_risk_pct}% below the entry. The "
            f"target is the highest swing high before the sweep, and must pay "
            f"at least {self.min_reward_risk} times the risk.",
            f"A name is only tradeable above ${self.min_price:.0f} and above "
            f"${self.min_dollar_volume / 1e6:.0f}M of trading per bar.",
        ]
        if self.min_range_pct > 0:
            lines.append(
                f"It must also average at least {self.min_range_pct}% of range "
                f"per bar.")
        if self.min_relative_strength > -100:
            lines.append(
                f"And it must not lag the S&P 500 by more than "
                f"{abs(self.min_relative_strength):.0f} points of 12-1 "
                f"momentum.")
        if self.require_value_sweep:
            lines.append("The sweep must occur at a volume-profile value area.")
        closes = [f"the stop or the target, held by the broker",
                  f"a limit of {self.max_hold_bars} bars in the position"]
        if self.flat_before_weekend:
            closes.append("a forced close before the weekend")
        lines.append("A trade ends at " + ", or ".join(closes) + ".")
        lines.append(
            f"At most {self.max_positions} positions are open at once, each "
            f"risking {self.risk_pct}% of equity in principle and capped at "
            f"{self.max_position_pct}% of equity in size -- the cap usually "
            f"binds, so real risk per trade is smaller than the headline.")
        if self.block_earnings_within_days:
            lines.append(
                f"A name reporting earnings within {self.block_earnings_within_days} "
                f"days is refused.")
        return '\n'.join(lines)

    def describe(self) -> str:
        return (f"{self.interval_minutes}m k={self.swing_k} "
                f"stop>={self.min_risk_pct}% rr>={self.min_reward_risk} "
                f"risk={self.risk_pct}% max={self.max_positions} "
                f"[{self.fingerprint}]")


class Decision(Frozen):
    """One candidate's journey, and where it stopped."""

    symbol: str
    stage: str
    reason: str = ""
    priority_rank: int = 0

    @property
    def traded(self) -> bool:
        return self.stage == TRADE


def summarise(decisions: tuple[Decision, ...]) -> dict[str, int]:
    """Counts by final stage — the shape of the funnel for one run."""
    counts = {stage: 0 for stage in STAGES}
    for decision in decisions:
        counts[decision.stage] = counts.get(decision.stage, 0) + 1
    return counts


def rejections(decisions: tuple[Decision, ...]) -> dict[str, int]:
    """Why candidates were refused, most common first.

    This is the number that tells you whether a quiet day means a quiet market
    or a rule that has quietly stopped letting anything through.
    """
    counts: dict[str, int] = {}
    for decision in decisions:
        if decision.traded or not decision.reason:
            continue
        counts[decision.reason] = counts.get(decision.reason, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))
