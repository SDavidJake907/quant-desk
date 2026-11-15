# Going live with real money — criteria, decided in advance

**DRAFT for the operator's review, written 2026-10-05.** The operator plans to
fund the account in about six weeks (~2026-11-16). Window 15 ends 2026-12-04.
These criteria are set NOW, before window 15 produces a single trade, so that a
good or bad few weeks cannot write them after the fact. Change them only with a
dated note below explaining why — never quietly, and never after seeing results
that the change would rescue.

## Where things stand (2026-10-05)

- Two years of backtest, with costs and the Friday flatten as live runs it:
  **−0.059R per trade (live gates, 10bps), account −11.6%** (`position_size_test.py`,
  `sizing_research.py`). The last six months were positive (+0.185R); the
  eighteen before them negative.
- Live record: 15 closed trades, inconclusive. Two of the worst were an
  execution bug (late signals), fixed 2026-10-04.
- Open leads, all found on the same two years and so unconfirmed: minimum
  reward:risk 2.0, sector leadership, faster daily movers, and the separate
  momentum engine (t 1.89 after the overlap correction).

**On today's evidence the strategy should not be funded.** The six weeks are
for finding out whether that changes.

## Gate 1 — the strategy holds on data it was not built on

On the 2018-01 → 2024-08 Alpaca store (`data/intraday_alpaca_2018`, collected
2026-10-05; nothing here was found on it), with every live rule, the Friday
flatten and 10bps costs:

- [ ] expectancy per trade > 0
- [ ] account simulation (5 slots, compounding, cash-only) ends positive
- [ ] maximum drawdown under 25%
- [ ] positive in at least two of: 2018-19, 2020, 2021, 2022-23 — so it is not
      one good year carrying the rest

Any window-16 rule (reward:risk 2.0, sector leadership, …) is judged here
first. A rule that only works on 2024-26 is not a rule.

**RESULT 2026-10-05: FAIL.** 1,646 setups, 2018-01 → 2024-08:
−0.041R/trade ✗ · account +35.9% ✓ · DD 21.9% ✓ · positive in 3 of 4 periods ✓.
The account result is slot luck (taken +0.078R, skipped −0.354R; not repeated
on the 2y store). No window-16 candidate passed. WORKLIST §30.

## Gate 2 — live trading behaves like the backtest

Window 15, `69da227b506e`, by its end or by funding day, whichever is later:

- [ ] at least 20 closed trades (fewer cannot distinguish anything)
- [ ] mean R not below −0.25 — worse than that means live differs from the
      backtest, whatever the backtest says
- [ ] every fill within 0.5% of its limit, or the exception explained
- [ ] the news gate never failed open on a trading day
      (`news_gate_degraded` = 0 in session hours)
- [ ] no position carried over a weekend by a failed flatten

## Gate 3 — the machine is trustworthy

- [ ] two consecutive weeks with no failed scheduled task
      (`logs/scheduled.log`), including at least one Friday flatten
- [ ] `start_all.ps1` ALL CLEAR after a deliberate reboot
- [ ] the kill switch tested: `STOP_TRADING` created, a scan refuses to
      trade, file removed
- [ ] the archive (`archive_broker.py`) and outcome jobs ran every day
- [ ] **live bars from Alpaca real-time SIP** (Algo Trader Plus, $99/month),
      not Yahoo. Live scans read Yahoo 30m bars while every backtest reads
      Alpaca SIP bars -- the strategy is measured on one feed and traded on
      another. Yahoo also delivers late: PSX's 13:30 ET bar was missing at the
      14:03 ET scan and seen at 14:33, the late signal that filled beside the
      stop (2026-09-24). Alpaca's free tier cannot replace it: its SIP blocks
      the newest 15 minutes (later than Yahoo) and its real-time feed is IEX
      only (~2-3% of volume, breaks the $20M/bar floor).

## If all three pass

- Fund a **starter slice: 25% of the intended amount.**
- Same rules: 1% declared risk, 20% cap (≈0.43% real risk per trade).
- Hard stops, any one halts trading for a written review before restarting:
  - daily loss 3% (exists: `max_daily_loss_pct`)
  - week loss 6%
  - drawdown 15% from the funded high
- Scale to 50%, then 100%, each only after 30 further trades whose mean R is
  within one standard error of the backtest's.

## If any gate fails

Keep trading paper. Do not fund. Record which gate failed and why below; fix or
research; the next evaluation is the end of the following window.

## Change log

| date | change | why |
|---|---|---|
| 2026-10-05 | drafted | before window 15's first scan |
| 2026-10-05 | Gate 3: real-time SIP feed added | live/backtest data mismatch and Yahoo lag found |
| 2026-10-05 | −0.024R → −0.059R; Gate 2 fingerprint 9ed12e → 69da22 | −0.024R was ungated and pre-cost; window 15 was re-declared at 69da227b506e |
| 2026-10-05 | Gate 1 run: FAIL | WORKLIST §30 |
| 2026-11-10 | Deposit waits for one 10-7 hold. Sweep sleeve 20% → 0% for new buys. No funds moved. | Operator named the list. See the note below. The 2026-10-05 Gate 1 FAIL stays. |

## 2026-11-10 — do not fund yet

The 2026-10-05 gates above still stand, including the Gate 1 FAIL. This note
does not replace them.

The rules book bought on 2026-11-10 is the book that has to earn a deposit.
Those names finish one 15-session hold in early December. Judge that sleeve
against SPY over the same days. A deposit waits on that result. No other sleeve
is funded in its place.

Scorecard since the 2026-10-30 close, as of 2026-11-10: Rules −$1,602 (−8.27%)
versus SPY −0.77% (behind 7.51 pts). Small −$203 (−2.09%) versus IWM +0.45%
(behind 2.54 pts). Sweep −$243 (−1.26%) versus SPY −0.77% (behind 0.49 pts),
and its ghost is 0 for 11 closed trades (−10.97R). Core −$267 (−1.10%) is
ahead of SPY/QQQ −1.52% by 0.42 pts and is cash after the 2026-11-05 QQQ stop.
None of that earns a deposit. QQQ is bought again only on a new 20-day closing high.

New sweep buys are off (`policy.swing_allocation_pct` 0). Open sweep stops
stay. The books that can still buy are the rules book (20%), small caps (10%),
and QQQ (25%, only on that re-buy rule). The other 45% is cash.

Starter slice, only after a sleeve beats its benchmark: 25% of the intended
amount, then 50%, then 100%. Each step waits for 30 further trades whose mean
R is within one standard error of the measured book. Hard stops stay the ones
above: daily loss 3%, week loss 6%, drawdown 15% from the funded high.

The account stays on the paper host. Real-time SIP is still a Gate 3 item.
A read-only check on 2026-11-10 asked Alpaca for SPY's latest SIP quote and
got HTTP 403, "subscription does not permit querying recent SIP data". The
same quote on IEX returned HTTP 200. Paper scans stay on the working feed
(Yahoo for the scans, IEX where the code already asks Alpaca). 
`scripts/live_readiness_check.py` fails closed with "do not fund" until a SIP
quote succeeds. That refusal does not move the 10-7 hold onto SIP.
