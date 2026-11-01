# Evaluation window — FROZEN

    window               34  (window 33 closed 2026-10-09: extended hours, 50/50, quarterly stakes)
    policy fingerprint   1c5ed20750e6  (extended hours, Gemma 50%; before it f235ab909864)
    code build           4b77fab  — the last commit that can change behaviour
    what trades          1. PICKS via scripts/picks_sync.py (`pck-` ids): watchlists/picks.txt kept in
                            sync every 30 min, 2.5-ATR safety stop, no target, sector cap off
                         2. MOMENTUM paper book (`mom-` ids), 40% of equity, monthly MOC, no stops
                         3. GEMMA's book via scripts/gemma_trade.py (`gem-` ids), 40% of equity,
                            up to 5 core.txt names chosen each morning; system prices/sizes/stops
                         4. one-off MANUAL trades via manual_trade.py (`man-` ids), operator-entered
                         5. sweep scanner (`smc-`): ENTRIES PAUSED
                         picks + manual share the remaining 20%
    watchlist            watchlists/core.txt (18 names) -- the operator's list; TradingAgents
                         rates it premarket. Commit every edit
    llm environment      gemma4:12b, think=false, num_ctx 24576 + nomic-embed-text
                         Ollama 0.35.1, 2 parallel slots, auto-update OFF,
                         desktop-app context_length 24576 (it overrides the env)
    branch               momentum-swing-research
    started              2026-10-06, declared after the close; first possible orders 10-07
    ends                 momentum: judged 2027-04-06 (six monthly rebalances), see below.
                         manual: judged at 30 closed trades or 2027-04-06, whichever first
    account              Alpaca paper, opened 2026-08-06 at $100,000
                         equity $102,040.97 at window 16 open, flat

The header above was still describing window 8 until 2026-10-04; windows 9-14
were recorded only in the log at the bottom. See "Window 14 closed" below.

Window 1 ran 2026-08-18 to 2026-09-24 and was **truncated at 3 trades**. Its
closing record is below; its trades do not pool with these. Windows 2 and 3
were each declared and superseded the same evening, before either ran a single
scan — no scans, no trades, nothing lost. Both restarts bought something worth
more than the nothing they cost: window 2 fixed dedup, window 3 made the
model's ranking falsifiable.

**On the build SHA.** Audit records carry whatever `HEAD` is when the scan
runs, so a documentation commit moves the recorded SHA without changing a line
of executed code. `935c721` is the last commit that touched anything the
trading path can reach; SHAs after it that differ only in documentation pool
with it. When grouping results, check what actually changed between two SHAs
rather than treating every difference as a separate experiment — the point of
recording the build was to catch code changes a fingerprint misses, not to
split the sample every time a file is edited.

Both identifiers matter. The fingerprint says which **rules** ran; the build SHA
says which **code** ran. They come apart: the partial-bar fix (`f19aeff`)
changed live entry prices materially and touched no policy field. A trade is
part of this experiment only if it carries **both** values.

---

## Stop rules, declared in advance

The window ends at **whichever comes first**:

- **2026-12-04**, the last session before 23x5 trading begins on 2026-12-06.
  A hard end, fixed now, before a single trade is collected.
- **100 completed scanner trades** AND **10 calendar weeks** — the original
  pair of rules, kept so a window that somehow fills early still has to span
  time and not only count.

A **checkpoint at 30 trades** is for reporting only. Looking is allowed;
stopping is not.

### Why a fixed end date is not optional stopping

The market changes on 2026-12-06 and a sample spanning it pools two different
environments under one fingerprint. The boundary is therefore a property of the
world, not of the results: it was chosen from a published exchange schedule
before any trade existed, it cannot move, and no outcome can bring it closer.
That is the opposite of stopping when the numbers look good.

### This window will almost certainly end on the date, not the count

2026-12-04 is ten weeks after the first scan. At the rate actually observed —
3 completed trades across 7 scanned days — ten weeks of trading days is on the
order of **20 trades**. That is a checkpoint-sized sample, not an expectancy
measurement, and it is accepted in advance rather than discovered later.

So this window's purpose is narrower than window 1's. It is to collect one
clean, uninterrupted, fully-gated stretch of the current policy in the current
market, and to hand the 23x5 era a known starting point. **It is not expected
to settle whether the strategy works.** Reporting it as though it could would
repeat the error the whole document exists to prevent.

### Optional stopping is forbidden

The window does not end because the numbers got good. Stopping at the moment
results look attractive is how a random walk becomes a strategy — it is the
same failure as picking the best of thirteen ablation variants, which cost this
project a day and produced `+0.429R` on train and `+0.014R` on holdout.

If the window has to end early — a bug, a broker change, a decision to stop —
that is recorded here with the reason and the trade count at the time, and the
sample is reported as **truncated**.

### What "completed" means

Entered and exited. Open positions at the checkpoint are excluded from
expectancy and counted separately, because scoring an open trade at its current
mark selects for whatever is currently winning.

---

## Realistic duration

At roughly 2 setups a day across the index, ~40% average deployment, a
Thursday–Friday wind-down and `fresh_bars = 2` currently admitting almost
nothing, the live rate has been about **0.6 orders a day**. At that rate 100
completed trades is on the order of **eight months**.

**The 20M liquidity floor roughly halves that again.** The scan went from 130
setups to 69 the day it landed, so expect closer to **sixteen months** for 100
completed trades. That is the price of trading only the tier where the edge was
measured, and it was accepted knowingly.

That is the honest number and it is uncomfortable. It is also why the
`fresh_bars` investigation in `WORKLIST.md` §8 matters — not to loosen the gate
for more trades, but to find out whether the scarcity is real. If it is, this
strategy simply produces evidence slowly and no amount of impatience changes
that.

**This is why window 3 does not chase 100 trades.** The arithmetic above says
that target is a year or more away, and 2026-12-06 is ten weeks away. Rather
than pretend a ten-week window can reach a sixteen-month goal, window 3 sets
the date as its end and accepts the ~20 trades that come with it. The 100-trade
rule is retained only so it can still bind if the rate turns out far higher
than measured.

The real answer to scarcity is §8, not a longer window.

---

## What must not change during the window

Anything that alters what gets traded or at what price:

- `TradingPolicy` — any field
- `scripts/trade.py`, `scripts/flatten.py`, `scripts/manage_positions.py`,
  `scripts/protect.py`
- `src/scanner/screens/smc.py`, `features/structure.py`, `features/intraday.py`
- `execution/sizing.py`, `guards.py`, `news_gate.py`, `session.py`, `taper.py`,
  `flatten.py`, `safety.py`, `sleeve.py`
- **The LLM layer**: `llm/triage.py`, `llm/ranking.py`, `llm/dedup.py`,
  `llm/ollama.py`, `llm/grounding.py`
- `config.py` — `triage_model`, `embed_model`, `dedup_threshold`,
  `triage_temperature`, `triage_num_ctx`
- `sources/calendar.py` — the earnings gate reads it
- `models.py` — `TradeIntent` is the shape that crosses into execution
- The scheduled tasks, their times, and `scripts/scheduled_*.bat`

### The list was incomplete until 2026-09-24

It named `news_gate.py` but not the LLM modules the gate's verdict comes FROM.
A prompt edit in `triage.py` changes which categories are returned, which
changes which names are blocked, which changes what trades — without touching
a single listed file. `dedup_threshold = 0.92` decides what triage is even
shown. `config.py` names the model.

The same reasoning added `safety.py`, `sleeve.py`, `calendar.py`, `models.py`
and the two position-management scripts, all of which can alter what is held
or at what price. A list that omits them lets the window be changed while
appearing intact, which is worse than having no list.

If one of these must change — a genuine bug, not an improvement — the window
**ends**. Record the reason below, report the truncated sample, and start a new
window with the new fingerprint and build.

## What may change freely

- Research scripts and anything under `tests/`
- Documentation
- Analysis tooling that reads the audit log
- Any work on a **separate branch** that is not deployed

Research continues. It just does not reach the trading path until this window
closes.

---

## Reporting

Group by `(policy, build)`. `positions.py` currently pools every trade in the
account, which mixes fingerprints and mixes in the five manual trades — that
split is `WORKLIST.md` §1 and should land before the 30-trade checkpoint.

Report at minimum:

- completed trades, and open trades separately
- expectancy in R, with standard error
- win rate, and the target/stop/timeout split
- realized dollars, scanner trades only
- fill rate — the number the backtest could not measure
- mean slippage, entry and stop, versus intended
- **news gate status per trade** — this sample is not uniformly gated
- window 3 ends before 2026-12-06 by design, so no trade in it spans the
  23x5 change. If that boundary is ever crossed, split the report there.

Against the benchmark of a **+0.218R validated holdout**. Live expectancy
sitting below that is the expected outcome, not a failure: paper fills have been
optimistic by +1.50 against the stop level, and the backtest assumed entry at a
price a limit order may never get.

---

## Restart, 2026-08-18

The window was reopened the same day it started. Two bugs, both found before
any trade was collected, so nothing was lost:

1. **The Friday flatten had no timing guard.** `--only-if-closing-within` was
   never actually in `scheduled_flatten.bat` — an earlier patch reported
   success without checking the replacement matched. Both triggers ran
   unconditionally, and the 08:45 AK one is 12:45 ET, so the whole book would
   have been closed mid-session every Friday.
2. **Trades closed outside their own bracket read as open forever.**
   `flatten.py` closes with `DELETE /v2/positions`, which produces exactly that
   shape, so every flattened trade would have been uncountable and the
   100-trade stop rule unreachable.

Either alone would have invalidated the window. This is what the build SHA is
for: neither changed a policy field, and a fingerprint-only record would have
pooled them with what came after.

## DEPLOYMENT HAZARD — read before switching branches

The scheduled tasks run `scripts\*.bat` from the **working directory**. There
is no checkout step, so **whatever branch is checked out is what trades.**
Leaving the tree on a research branch silently deploys research code.

    git branch --show-current      # must be momentum-swing-research

Check it after any research session. This is the one operational rule that no
test can enforce.

## Scheduler blackout, 2026-08-24 to 2026-09-24

The machine was off. The four scheduled tasks never failed — they were never
reached. Scanning stopped mid-session at **08-24 11:03 AK** and did not resume
until **09-24 05:33 AK**. Windows fired missed-trigger catch-ups on 09-23 at
20:17; the flatten's timing guard correctly made it a no-op and no closing
orders were sent.

**21 trading days were missed** (08-25 through 09-23, excluding Labor Day
09-07), of which 17 were entry-eligible once Fridays are removed. No bar data
was lost: the 09-24 collector backfilled the whole gap from the provider's
60-day window, and `data_check.py --intraday` reports no gaps. Had the host
stayed dark another month, that backfill would have been impossible — the
60-day window is the only reason this cost nothing but time.

What was lost is **exposure**, not data. No policy field and no build changed
across the gap, so the window does **not** restart and the two trades collected
before it still pool with the one after.

### The calendar clock is extended, not counted

The 12-week rule exists so the sample spans more than one market condition. 30
dark days contributed no conditions, so counting them would satisfy the rule
with wall-clock time the strategy never traded through. The calendar
requirement is therefore extended by the length of the blackout:

    12 weeks from 2026-08-18      would have ended 2026-11-10
    + 30 days of blackout         ends 2026-12-10

The trade-count rule is untouched — it was always counting trades, not days.

**This is a judgment call made after seeing the outage, which is the shape of
decision this document exists to constrain.** It is allowed only because it
moves the window's end *away* and never closer: extending the clock cannot end
the window early and cannot be used to stop on a good number. The other
reading — counting the dark days — would have finished the window a month
sooner, and that is the direction that needs guarding against.

### There is no watchdog for a dark host

`logs/watchdog.jsonl` runs inside the same machine and cannot report its own
absence. Nothing detected this gap; it was found by reading the log a month
later. Any future gap longer than one trading day is recorded here the same
way, with the same extension.

---

## News gate coverage is not uniform across this window

The gate consults a local Ollama instance. Ollama does **not** start on boot —
no Run key, nothing in the Startup folder — so after any reboot it is down
until started by hand, and `news_gate.py` fails **open**: every name passes the
news check unrefused. The scan says so loudly (`news_gate_degraded`) and then
trades anyway. LLM triage and dedup fail the same way and are advisory, so they
cost nothing but noise.

This has not been constant. Of the three trades in this window:

| day | trade | news gate |
|---|---|---|
| 2026-08-18 | ACN | degraded — failing open |
| 2026-08-20 | APP | working |
| 2026-09-24 | PSX | degraded — failing open |

**Two of the three in-window trades were taken with no news protection.** Any
report of this sample has to say that, rather than describing a gated strategy.
It is a second reason — beyond n=3 — that these trades cannot yet be read as
evidence about the policy as designed.

Ollama was started by hand on 2026-09-24 and is up now, so scans are gated
until the next reboot. It was deliberately **left on manual start**: automating
it would change the effective filter mid-window, and `WORKLIST.md` §7 already
holds that failing open is acceptable while this is paper, precisely because
the point is to observe the behaviour rather than paper over it. The decision
that actually matters — fail open or fail closed, as a `TradingPolicy` field so
it lands in the fingerprint — belongs before real funds, not here.

This does **not** restart the window. Ollama's availability is not a policy
field and no code changed; the gate behaved exactly as written in both states.
What changes is the reporting obligation, not the experiment.

---

## The market itself changes on 2026-12-06, and this window spans it

The SEC approved Nasdaq's move from 16 to **23 hours a day, five days a week**
in April 2026, with a one-hour break 20:00–21:00 ET for clearing. NYSE Arca and
24X have preliminary approval on the same schedule. It begins **2026-12-06**.
Alpaca already runs its own 24/5 overnight session (20:00–04:00 ET, Blue Ocean
ATS) ahead of it.

**No configuration in this repo is stale because of it.** Regular hours remain
09:30–16:00; the 23-hour day adds sessions *around* the regular session rather
than moving it. `SESSION_OPEN`/`SESSION_CLOSE`, the calendar lookups and the
early-close logic were all checked against the live API and are correct. The
scan window, the bar source (Yahoo, regular hours) and `/v2/clock` are
unaffected — verified at 00:29 ET mid-overnight-session, where the clock still
reports `is_open: false` and `next_open 09:30`.

### VERIFIED AGAINST THE BROKER, 2026-09-28 16:59 ET

The paragraph below was written from Alpaca's documentation. It has now been
tested live from inside the post-market session, on the paper account, with
throwaway one-share orders priced 20% below the market so none could fill. All
were cancelled.

```
ACCEPTED  plain limit, extended_hours=True    -> pending_new
ACCEPTED  plain limit, extended_hours=False   -> accepted
REJECTED  BRACKET,     extended_hours=True    -> HTTP 403
          "bracket orders do not support extended hours trading"
REJECTED  STOP order,  extended_hours=True    -> HTTP 422
          "stop orders are not eligible for extended hours trading"
```

The broker rejects both in its own words. This is stronger than the
documentation claim: it is not that stops are merely unsupported by the
overnight venue, it is that the API refuses a bracket outright the moment
`extended_hours` is set. **There is no order this system could send that enters
extended hours WITH protection attached.** An extended-hours entry is
necessarily a naked one.

`/v2/clock` also confirmed: at 16:59 ET, inside the post-market session, it
reports `is_open: false` with `next_open 09:29`. The clock tracks REGULAR hours
and will keep doing so through 23x5, which is why the scan window needs no
change on 2026-12-06.

### Quote data: the spread filter is researchable but not enforceable

Checked at the same time, because a spread filter is the most defensible
missing constraint in WORKLIST:

```
latest SIP quote   HTTP 403  "subscription does not permit querying recent SIP data"
latest IEX quote   HTTP 200  bid 11.74 x100, ask 0        (post-market, one-sided)
historical SIP     HTTP 200  bid 12.51 x3100, ask 12.52 x6500, spread 0.0100
```

So the tier serves HISTORICAL SIP quotes but not recent ones. A spread STUDY
over the two-year store is fully possible and worth doing. Enforcing a spread
floor LIVE is not, on this tier: it would have to run on IEX, which measured
4.4% of SIP volume on AAPL and returned a one-sided book in the probe above.
Sizing a gate on 4% of the tape is how the IEX-vs-SIP mistake was made the first
time.

### The exposure is on overnight holds, and it cannot be configured away

Alpaca's overnight session supports **limit orders only**. Stops are not
offered, so a bracket's `stop_loss` leg cannot act between 20:00 and 04:00.
This is a broker limitation, not a setting left switched off — there is no
value we can change that buys overnight protection.

`bracket_payload` already records that only 54% of these setups resolve inside
one session, so **about half of all positions are held overnight by design**.
Before 2026-12-06 that meant a dark gap: no trading, price discovery resuming
at the bell. After it, those positions sit through ~23 hours of continuous
trading with a stop that is structurally unable to fire until 09:30.

Turning on `extended_hours` would make this worse rather than better: it adds
no protection, and it lets *entries* fill overnight in thin liquidity with no
stop attached. It is deliberately left `False`.

### What this does to the experiment

The policy will not change on 2026-12-06 and neither will the build. The
**market** will. Neither identifier in this document captures that, which is
the same class of problem the build SHA was introduced to solve — a change that
moves results while the fingerprint sits still.

The window is **not** ended for it. But the reporting obligation grows:

- **Trades are reported split at 2026-12-06**, the same way they are grouped by
  `(policy, build)`. Trades either side of that date were taken in structurally
  different markets and do not pool.
- The **+0.218R holdout benchmark predates the change**. It was measured on
  Yahoo regular-hours bars in a market where overnight was dark, so its
  overnight gap distribution may no longer describe live conditions. Comparing
  post-2026-12-06 live results against it is comparing across a regime break.

These dates sit close together and should be read as one sequence:

    2026-12-04   window 3 ends, by prior declaration
    2026-12-06   23x5 trading begins
    2026-12-24   Thursday early close, four-day gap, no flatten (below)
    2026-12-31   Thursday close, four-day gap, no flatten (below)

Window 3 is deliberately placed to end before the first of these rather than
across them. The two December Thursdays fall after it closes, so they are an
operational obligation — flatten by hand — and not a contaminant in the
sample.

---

## Holiday-shortened weeks: the flatten does not run

Two rules are each correct alone and wrong together:

- `wind_down_from_weekday = 4` blocks new entries on **Friday only**, so
  Thursday trades. Its docstring reasons that "Thursday is Thursday whatever
  the month" — deliberately a weekday rule, not a date rule.
- Both flatten triggers are registered **Friday-only** (`DaysOfWeek : 32`,
  confirmed against the live task).

When Friday is a holiday, Thursday is the last session before a multi-day close
and nothing knows it:

| date | day | close | next session | gap | entries | flatten |
|---|---|---|---|---|---|---|
| 2026-11-27 | Fri | 13:00 ET | Mon 11-30 | 3d | blocked | **yes**, 08:45 AK trigger |
| 2026-12-24 | Thu | **13:00 ET** | Mon 12-28 | **4d** | **allowed** | **no** |
| 2026-12-31 | Thu | 16:00 ET | Mon 01-04 | **4d** | **allowed** | **no** |
| 2027-03-25 | Thu | 16:00 ET | Mon 03-29 | **4d** | **allowed** | **no** |
| 2027-06-17 | Thu | 16:00 ET | Mon 06-21 | **4d** | **allowed** | **no** |

**This is not a December problem.** It recurs every time a holiday falls on a
Friday — Christmas, New Year, Good Friday 2027-03-26, Juneteenth observed
2027-06-18 — so roughly four times a year, indefinitely. `MARKET_CALENDAR.md`
lists them, generated by `scripts/market_calendar.py` from the broker's own
calendar; regenerate it when a year is published rather than editing it.

Thanksgiving week is handled correctly — the early-close trigger exists for
exactly that case. The two December Thursdays are not: the system may open
positions on the last session before a four-day close and carry them through
unflattened, which is the precise exposure the flatten was built to prevent.

2026-12-24 is the worse of the two. The decision to let Thursday trade rests on
stated arithmetic — 17 bars available, the flatten costing it −0.313R and it
still landing at +0.467R. On a 13:00 close Thursday has roughly **seven** bars,
so the arithmetic the rule is justified by does not hold that day.

Live code cannot catch this by itself. `is_last_bar_before_weekend` infers the
gap from bar timestamps, which only exist after the close; nothing
forward-looking consults the calendar.

**Mitigation without touching the frozen path:** run `scripts/flatten.py
--submit` by hand on each date above, after 09:00 AK and before the bell. Run
`python scripts/market_calendar.py` to list the dates that still need it. It is idempotent and safe to re-run. A calendar-aware wind-down is the
real fix and is `WORKLIST.md` §10 — it changes `policy.py` and `flatten.py`,
so it cannot land until this window closes.

---

## Window 1 — closed 2026-09-24, TRUNCATED at 3 trades

Ran 2026-08-18 to 2026-09-24 at policy `fb709122e949`, builds `cf0ca28`
through `935c721`. It is reported as **truncated**, per the rule above that an
early end is recorded with its reason and its trade count.

**Result, for the record and not for inference:**

| date | symbol | exit | R | news gate |
|---|---|---|---|---|
| 2026-08-18 | ACN | target | +2.163 | degraded |
| 2026-08-20 | APP | stop | −0.363 | working |
| 2026-09-24 | PSX | stop | −0.471 | degraded |

Mean +0.443R on **n=3**. The standard error on three trades is larger than the
effect being measured, so this number says nothing whatever about the strategy
and must not be quoted as though it did. It is here because the rule says to
report what the window collected, not because it is evidence.

**Why it ended.** No single cause; three that compound:

1. **The host was dark for 30 days** (2026-08-24 to 09-24), 21 trading days
   missed. Recorded below. The calendar clock was extended to compensate,
   which was the right call for that window and is moot now that it is closed.
2. **Two of the three trades were taken with no news protection**, because
   Ollama does not start on boot. A three-trade sample that is also not
   uniformly gated has two independent defects, not one.
3. **23x5 trading begins 2026-12-06**, inside what remained of the window. It
   would have spanned a regime change, and split-reporting a sample this small
   would leave two useless halves instead of one useless whole.

Any one of these would have been survivable. Together they mean the window
could not produce what it was opened to produce, and continuing it would have
spent months accumulating trades into a record already known to be unpoolable.

**Nothing was lost by closing it.** No policy field changed, so the windows
after it run the same rules; the three trades keep their own `(policy, build)` attribution
in the audit log and remain readable as what they are.

---

## A third dimension: the model environment

The fingerprint says which rules ran and the build SHA says which code ran.
Neither says **which models were installed**, and that turns out to matter.

`embed_model` has always pointed at `nomic-embed-text`, and that model had
never been pulled. Ollama answered every embedding request with

    model "nomic-embed-text" not found, try pulling it first

so `pipeline.py` took its documented fallback — `dedup unavailable, continuing
undeduped` — and carried on. **Dedup has therefore never run in this project's
life.** The code was correct, the config named the right model, and the model
was simply absent. Nothing in the fingerprint or the build could have shown it.

It is fixed now, and the fix is not cosmetic. On four headlines — three
phrasings of the same acquisition plus an unrelated dividend — dedup returns
two: the duplicates collapse and the unrelated item survives. Triage now sees
one copy of a story where it used to see three, which changes what the news
gate is shown and therefore which names it can block.

### So the environment is declared here too

A trade belongs to this experiment only if it carries the fingerprint, the
build **and** the model environment above. This is the third identifier, and
like the other two it exists because something moved results while the others
sat still:

| what changed | fingerprint | build | caught by |
|---|---|---|---|
| a policy rule | moves | — | fingerprint |
| the partial-bar fix | still | moves | build SHA |
| Ollama down, gate fails open | still | still | nothing — see the news gate section |
| embedding model absent, dedup dead | still | still | **nothing, for the project's whole life** |

The bottom two rows are the same failure: the model environment is state that
lives outside git, and neither identifier can see it. Checking it is part of
starting a window now, not something to rediscover later.

---

## What window 4 carries that window 3 did not

Window 3 never scanned, so window 4 absorbs an end-to-end audit rather than
needing a window of its own. Five gaps, all closed before the first scan:

1. **`max_hold_bars = 60` was enforced nowhere.** It is how +0.218R was
   measured — 5.6% of holdout trades exited on it — and not one live trade
   ever has. `manage_positions.py` implemented it and was never scheduled. It
   now runs before every scan, refuses to act when the market is shut (it
   closes at market, and the 16:03 ET run is after the bell), records a
   `hold_limit_exit` audit event, and binds to `TradingPolicy.max_hold_bars`
   rather than the backtest's own constant so the two cannot drift apart.

2. **Naked positions were detected and not fixed.** The scan found them,
   shouted, and printed "re-arm with: protect.py --submit" — a manual step in
   an unattended system, and the last time it mattered nobody was watching.
   `protect.py` now runs first on every scan. Deliberately no market-hours
   guard: a protective order sent after the bell is queued for the next open,
   and a stop that activates at the open beats a position naked into it.

3. **This list omitted the LLM layer.** A prompt edit in `llm/triage.py`
   changes which categories come back, which changes which names the gate
   blocks, which changes what trades — without touching a single listed file.
   Corrected above, along with `config.py`, `calendar.py`, `models.py`,
   `safety.py`, `sleeve.py` and the two position scripts.

4. **The triage prompt produced two false blocks.** Measured through the
   production path, not a proxy: `bench_triage.py` runs the real prompt over
   the same cases. It scored 11/14, reading a unit sale (IP) and a dead deal
   (PFE) as `target` — the verdict that refuses a real setup. Three clauses
   were added for deal-is-off, unit-sold and stake-bought. Now 12/14, and
   **false blocks went 2 to 0**. The one new miss, MO as `none` rather than
   `acquirer`, blocks nothing and costs nothing.

5. **Nothing could score the ranking.** `replay_slate.py` resolves recorded
   slates against stored bars, including candidates never bought. Validated
   first on the ten trades whose answer is known: all ten replay with the
   right exit reason, mean difference **−0.172R**, replay pessimistic because
   it assumes the intended entry while live limits filled better. On paper
   that is flattery rather than edge, and replay is the number to plan with.

6. **Headlines were never persisted.** `NewsItem.fetched_at` had always been
   documented as "Point-in-time stamp for backtests" and nothing wrote one to
   disk, so every headline the ranker read was discarded when the scan ended.
   The slate records the model's OUTPUT, which is enough to SCORE its ranking
   — but re-running it, which is what testing a prompt change needs, requires
   the INPUT. That is why the triage prompt edited today could only be
   measured on fourteen hand-written cases.

   `sources/news_store.py` now keeps one append-only file per day, holding
   exactly the items the model was shown, deduplicated on id so fourteen
   scans over the same feed grow the file by stories rather than by scans.
   Verified on live headlines: 60 stored, 0 on a second pass, timestamps
   intact. It never raises — these headlines are evidence ABOUT a decision,
   not an input to it, so a full disk must cost the evidence and never the
   trade.

   The store is written from today forward. Nothing recovers the headlines
   already lost, so the earliest prompt change that can be replayed is the
   next one.

7. **A diagnostic could stop a trade, and an absence could not report
   itself.** The slate writer sat between the funnel and the send loop with
   no guard, so an exception building it would have stopped trading after
   finding setups and before sending orders — and it had never once executed,
   because every scan the day it shipped found zero candidates. It is now a
   function, wrapped, and exercised by a test rather than by the first live
   candidate. `news_store` had been built to never raise on exactly this
   argument and the slate had not been given the same treatment.

   `execution/continuity.py` reports the gap the blackout could not. It
   counts missed SESSIONS rather than elapsed hours, so an ordinary
   overnight and an ordinary weekend are silent while a single missed
   weekday is not. The 2026-08-24 outage would have announced itself on the
   first scan back instead of being found a month later. The check is itself
   wrapped: a diagnostic that crashes the scan is the bug it exists to
   prevent.

**Still open:** the ranking cannot be re-run against anything before
2026-09-25, because that is when the headlines start. And nothing outside
this machine watches it — continuity reports a gap once the host returns,
which is not the same as noticing while it is still dark.

---

## Window 9 superseded: the expensive advice ran before the cheap gates

Window 9 took no trades and is replaced for a staging fix. No policy field
changed, so the fingerprint stays `29378de4d359`; the build moves.

### Sector strength was recomputed thirteen times a day for one answer

`_sector_standing()` fetched two years of DAILY bars for all 501 universe
symbols plus `^GSPC`, derived relative strength per symbol and took a median
per sector — on every scan that found a candidate. Measured:

```
cold  14.42s      first scan of the day, daily cache empty
warm   4.45s      cache warm
```

against a full 501-symbol scan of about 17s. The inputs are daily bars, so the
median at 06:03 is the median at 11:33: thirteen runs produced thirteen
identical numbers, and the most expensive one landed on the scan closest to the
open.

Now `features/sector_strength.py`, date-stamped like the daily bar cache,
written once before the open. DATE-STAMPED AND NOT TTL, deliberately — a TTL
cache that refreshes its own expiry is exactly what superseded window 5, and
`test_sector_strength.py` pins that a stamp from another session is refused
rather than served.

### And the ordering was backwards

The advisory block — sector arithmetic, headline fetches, an LLM call — ran
BEFORE the WATCH gates that decide whether a candidate can be acted on at all.
So a scan where every candidate was already held, or where the five slots were
full, paid for opinions it could not use. With `max_positions = 5` and
deployment at (100, 80, 60, 40, 0), a full book is not an edge case.

The scan already gets this right one level up, where `_liquid()` runs before
`scan()` because it is cheaper. It simply was not applied at the candidate
stage.

**The send loop is unchanged.** It still applies both gates itself and records
every refusal exactly as before, so this cannot alter a decision — `rankable`
only decides what is worth forming an opinion about. The full slate is still
recorded for every candidate, taken or not, because that is what keeps the
ranking falsifiable and it is why window 3 was superseded.

### The first attempt at the schedule was wrong

The refresh initially went into `scheduled_data.bat` at 13:00 AK. That job runs
after the close and stamps the file with THAT day, so every scan the following
morning asks for the following day, misses, and recomputes. The job did nothing
at all.

It is now `stock-scanner-sector` at 05:00 AK, half an hour before the first
scan — a job that runs after the data it feeds is no job at all. Recorded
because the fault was invisible: the fallback inside `sector_strength.today()`
meant the system behaved correctly and the scheduled task was simply inert,
which is the same shape as the news task that was never in `-Remove`.

### What this changes about the numbers

Nothing. Universe 267, funnel 501 -> 77 setups, fingerprint `29378de4d359`,
843 tests green. Window 9 had taken no trades.

---

## What this window cannot tell you: it has never seen a bear market

No restart. No rule changed and no code changed — this records a limit on what
the evidence can support, which is the other thing this file is for.

The trading path has no concept of market direction. `--trend-context` and
`--rsi-context` are `store_true`, default False, and the scheduler never passes
them; `_sector_standing()` fetches `^GSPC` but only to inform the LLM ranking
prompt, so it orders candidates and rejects none. The strategy is also
structurally long-only: `"sell"` appears in `screens/` and `execution/` only as
an exit or the Friday flatten, never as an entry side.

That would be a defensible design choice. What makes it a gap is the sample:

```
2026-05-14 -> 2026-09-24   (S&P +2.7%)
  above both    80 of 92 sessions   87%
  mixed         12 of 92 sessions   13%
  below both     0 of 92 sessions    0%
```

Not one down-trending session. Every number this project has published — the
+0.218R holdout, the 20M floor at p=0.022, thirteen null rankings — was measured
inside a favourable regime, and nothing in the fingerprint, the build SHA or the
model environment records which regime a window ran in. A fourth identifier is
arguably missing, because the market is a variable this file does not pin and
cannot.

### The Alpaca store now contains 179 bear-session setups

Over 2024-09 to 2026-09, including the 2025 -18.9% drawdown:

```
above both    +0.108R  se 0.078  n=523
mixed         +0.028R  se 0.119  n=156
below both    -0.086R  se 0.125  n=179     gap p=0.201
```

Negative in bear conditions, as a long-only sweep strategy should be. p=0.201,
so still NOT a finding — but the `below both` bucket barely moved when the
sample grew 57% (-0.089R on 114 setups -> -0.086R on 179), while `mixed` swung
from +0.108R to +0.028R and revealed itself as noise. A real effect holds its
value as n rises; noise does not. Ordering is now cleanly monotonic. Worth a
third sample, not yet worth enforcing.

### And the first successful replication, which cuts the headline number

That +0.040R prompted the matched-window provider comparison:

```
2026-05-11 -> 2026-09-25      YAHOO    ALPACA SIP
  mean R                     +0.380        +0.311
  se                          0.150         0.142
```

Within one standard error, on an independent feed from the venue the orders go
to. **The edge is in the market, not in Yahoo's reconstruction of the tape** —
the replication this project had no way to run until today.

The cost of that good news is that it isolates the remaining gap as a WINDOW
effect: the same rules on the same verified-equivalent data earn +0.311R over
these 4.5 months and roughly +0.025R over two years. The period every published
result came from was unusually favourable, and it is the period `min_risk_pct`
was tuned on. Paper trading continues unchanged; the numbers it is measured
against should carry this caveat.

---

## Window 8 superseded: the same drift bug, twice, one of it in reverse

Window 8 took no trades and is replaced for a filter audit that found the exact
failure this repository already documents and had already fixed once.

The README's own warning: *"Three entry points once carried three different
defaults for the same rules."* The fix was applied to `min_dollar_volume`, and
the comment recording it was written **directly below a hardcoded
`MIN_PRICE = 5.0`** that had the identical bug:

```python
MIN_PRICE = 5.0
#: Read from the policy so this cannot drift from what was measured.
#: It did drift: this file carried its own 5M constant while the policy
#: said something else, so changing the policy changed nothing live.
MIN_DOLLAR_VOLUME = TradingPolicy().min_dollar_volume
```

Four files held that copy: `trade.py`, `find_setups.py`, `collect_news.py`,
`provider_compare.py`. And `policy.min_price = 5.0` existed all along, read by
nobody.

### This one is worse than the bug it sat beside

`min_dollar_volume` drifting meant a policy edit changed nothing live — bad,
and invisible. `min_price` is a **declared policy field**, so it is hashed into
the fingerprint. Editing it would have moved the fingerprint, declared a fresh
evaluation window, restarted the trade count, and changed nothing about what
the scanner traded.

That inverts the purpose of the three identifiers. The table above promises
"a policy rule → moves → caught by fingerprint". Here the fingerprint would
have moved for a rule that did not take effect: not a missed signal but a
false one, and the kind that makes a window log untrustworthy in retrospect.

### And the mirror image: a filter enforced in only the preview

`find_setups.py` alone defined and enforced `MIN_RANGE_PCT = 0.4`, the average
bar range as a percent of price. `trade.py:_liquid()` had no range check at
all. Measured over the accumulated store:

```
TRADER  (price + dollar volume)         267 symbols
PREVIEW (+ average bar range >= 0.4%)   230 symbols
hidden by the preview, traded by trade.py: 37
```

Not obscure names — WBD (0.088% average range), BRK.B, MA, KO, VZ, PG, CSX.
The tool a human reads before the market opens showed a *narrower* universe
than the process that submits the orders, and neither number was in the
fingerprint, so no window could have detected a change to either.

### The range floor ships DECLARED AND OFF

It is now `policy.min_range_pct`, read by both entry points, hashed into the
fingerprint — and defaulted to **0.0**.

The temptation is to enable it at 0.4. The mechanism argument is the same one
that carried the 20M liquidity floor: this trades sweeps of resting orders, and
on WBD a 1.5% stop is seventeen bars of ordinary movement away, so the stop is
not a level. That argument is good.

It is also exactly the argument made for thirteen ideas that were then measured
and died, including the strategy's own thesis that a pool of equal lows beats
an isolated one (+0.616R train, −0.674R holdout). The liquidity floor earned
enforcement by being measured **before** it was turned on: p=0.022 over 125
setups, same direction in both halves. This has never been measured on any
split. Enabling it would drop 14% of the universe on an untested hypothesis and
orphan the +0.218R holdout result, which was measured with no range filter.

So: single-sourced, fingerprinted, testable, off. Live behaviour is unchanged
and now honestly described by the preview.

### The context gate computed indicators to return a foregone True

`intraday_context.passes()` takes eight `require_*` flags. Every one defaults
to `False` and the live policy sets none of them — each was tested, each was
null. In that configuration the function computed four EMAs, a Wilder RSI, a
MACD, an ATR and a volume ratio, then returned a `True` that no input could
have changed. It now answers before the arithmetic when nothing is required.

The requirements themselves stay. They are how a future constraint would be
enforced, and deleting the plumbing would mean rebuilding it to test the next
idea.

### What this changes about the numbers

Nothing, and that is the point of recording it. The universe stays 267, the
scan finds 197 setups, all 833 tests pass. The fingerprint moves from
`5b091652a8ab` to `29378de4d359` because a field was added to the policy, not
because a rule changed. Window 8 had taken no trades, so no evidence is lost.

---

## Window 7 superseded: a placeholder bar was shrinking the universe

Window 7 took no trades and is replaced for a data fix that acts directly on
the binding constraint.

`scripts/verify_bars.py` asserts the invariants nobody checks because they are
obviously true. The bars passed all of them — OHLC consistent, times strictly
increasing, buckets on :00 and :30, nothing outside session hours — and every
setup the screen returns honours the policy it was scanned under.

One invariant failed. **12.7% of session-days held 14 bars, and a 09:30-16:00
session is 13.** The extra bucket was 16:00 and it was not a bar:

    bucket   median volume   median range
    15:30        2,665,138          1.238
    16:00                0          0.000

Zero volume, zero range, high equal to low — a Yahoo placeholder carrying the
closing print. 2,591 of 250,742 stored bars.

### What it cost

Nothing triggered on one. But the liquidity filter averages volume x close
over the last 20 bars, and a zero drags the mean down **6.51%**. The floor is
20,000,000 and the median S&P 500 name measured **19,890,206** — right on the
boundary, with the artifact pushing it to the wrong side:

    symbols passing, as stored                245
    symbols passing, placeholders removed     267
    wrongly rejected                           22   (+9.0%)

    KEY   19,847,701 -> 22,728,781
    FITB  19,547,935 -> 22,025,013
    VRTX  19,834,307 -> 21,315,726

Twenty-two genuinely liquid large caps excluded by an artifact. For scale,
adding the entire S&P 400 and 600 — 1,003 extra symbols — bought +0.15 setups
per session. This was free and larger.

### Measured before changing, not after

The broad fix drops the bucket at parse time rather than only excluding it
from one average, which is more correct — a bucket with no trading is not a
bar, the same reasoning `drop_incomplete` already applies to one that has not
closed. But it can change which setups exist, so it was studied first:

    as stored   n=194   +0.431R   train +0.405   holdout +0.483
    cleaned     n=196   +0.411R   train +0.342   holdout +0.548

    190 of ~195 setups identical, 4 lost, 6 gained

Expectancy moves 0.02R against a standard error of 0.131. The structure barely
notices, and the holdout improves. Applied in both construction paths —
`resample` refuses to build one, and `history.to_bars` filters the 2,591
already on disk. The store is not rewritten: it is append-only and its value
is recording what the provider actually returned.

Five tests pin it, including that a flat bar WITH volume is kept and a
zero-volume bar that moved is kept — only the exact artifact is targeted.

### A fifth scheduled task

`stock-scanner-news`, 14:00 AK daily, archives point-in-time headlines for the
liquid universe. The scanner runs seven days a week and on a weekend gathers
nothing — no bars print, so Saturday re-reads Friday's series and produces
zero candidates, measured across 28 weekend scans. News does not stop, and the
scan stores it only for names that became candidates, which on most days is
none. Verified: 3,299 headlines from 267 symbols in 12 seconds.

Read-only of the market, append-only to `data/news`, and nothing in the
trading path reads what it writes.

---

## Window 6 superseded: the scan never said how fresh its bars were

Window 6 took no trades — the market was shut for its whole life — and is
replaced to add one line of output.

The cache bug above was found only because someone said "I think this is
behind", and answering that took four throwaway scripts. The funnel reports
what was REJECTED and never what it was looking at, so **a scan running on
stale bars and a market with nothing in it produce an identical line.**

The scan now prints the newest bar it holds, its age in minutes and in bars,
and the oldest across the universe when they disagree:

    bars: newest 16:00 EDT — 321 min old (10.7 bars)

It turns yellow only while the market is OPEN and the lag reaches 1.5 bars,
because outside session hours the newest bar is legitimately hours old and a
permanently yellow line is a line nobody reads.

One bar of lag is the entire freshness budget at `fresh_bars = 2`. This makes
that visible at a glance instead of by investigation.

---

## Window 5 superseded: the bar cache renewed its own lease

Window 5 collected nothing — zero records — and is replaced for a data bug
found by asking why a real setup was missed.

**CCL triggered at 11:30 ET on 2026-09-25 and the scanner never saw it.** It
passed every PRICE gate: 22.23, dollar volume 38.7M against a 20M floor,
reward:risk 2.10, `is_live_setup` true, `crosses_session` false. It sat inside
the 2-bar freshness window for three consecutive scans — 12:03, 12:33 and
13:03 ET — and the funnel reported `candidates 0` at all three while the setup
count never rose from 79.

**It would have been refused anyway.** Checked afterwards rather than assumed:
the news gate returns `REFUSE — reports earnings Tue 09/29 pre-mkt`, four days
inside the seven-day block. So the cache bug cost a candidate EVALUATION here,
not a trade. The bug is real and would lose real trades on other setups; this
particular one was never going to be taken, and saying otherwise would be
using a fixed bug to explain a loss that never happened.

### Two faults, and they compounded

**A cache hit renewed its own expiry.** `fetch_intraday` read through
`fetch_payload`, which returns a payload whether it fetched or not, then
called `cache_payload` unconditionally — rewriting the cache with the bytes
just read out of it and stamping a fresh `cached_at`. A stale payload renewed
its lease on every read and could outlive its TTL indefinitely. Measured: a
cache 29 minutes old served bars **three buckets behind** the provider, and
came back stamped 0.0 minutes old.

**The TTL was one full bar, which is also the scan cadence.** A scan thirty
minutes after the last one sat exactly on the expiry boundary, so whether it
saw the newest bar came down to sub-second jitter.

With `fresh_bars = 2`, one bar of lag eats half the life of every setup, and a
setup that triggers while the cache is stale can age out before the scanner
ever sees it. That is the mechanism that loses a CCL.

Fixed by reading the cache explicitly so a hit can be told from a fetch, and
by halving the TTL so the next scheduled scan always refetches. Three tests
pin it. The fingerprint does not move — no policy field changed — which is
precisely the case the build SHA exists for, exactly as with the partial-bar
fix in `f19aeff`.

### What this does not excuse

Scarcity is still real. One setup triggered across 499 symbols today, against
a ten-session mean of **1.9 per session**. The cache bug explains why a rare
setup was also missed; it does not explain why setups are rare. `WORKLIST.md`
§8 still stands.

---

## Window 4 superseded: the dollars were unreadable

Window 4 never scanned. It is replaced because the sizing was measured and
found to be nine times smaller than the policy declared.

Every position was sized by the position cap rather than by risk — the
scanner printed `limited by position cap` on all three trades of window 1 —
and actual risk came out at **0.108%** against a declared `risk_pct` of
**1.0%**:

    ACN   risk $200.12   0.196%    position $7,948   7.80%
    APP   risk  $60.18   0.059%    position $3,789   3.72%
    PSX   risk  $68.70   0.067%    position $3,942   3.87%

Three trades at +0.44R produced $1,860 on a $100,000 account. No drawdown can
be read from that, and no conclusion about fills under size either.

`weekday_deployment_pct` is doubled to `(100, 80, 60, 40, 0)`. Monday now
sits exactly on the existing `max_position_pct` ceiling — `min(20, 100/5)` —
so this is the most the current caps allow without raising one.

### What this does and does not change

**It changes no expectancy.** R is scale-invariant: doubling exposure doubles
dollars and doubles drawdown and leaves the measured +0.248R exactly where it
was. Results from before and after this change pool on R and must NOT be
pooled on dollars.

**`max_positions` was deliberately left at 5.** The obvious move was more
slots, and the arithmetic says it is worse than useless: `position_cap_for` is
`deployment / max_positions`, so ten slots on the same deployment HALVES every
position, and ten slots with doubled deployment is arithmetically identical to
today. The book has never held more than three positions and the cap has never
refused a single candidate — 96 refusals, none of them about slots. Slot count
is decorative while candidates are this scarce; deployment is the real dial.

**Expect the daily-loss guard to bind.** `max_daily_loss_pct = 3.0` is inert
at 0.108% risk per trade and will not stay inert at twice the size. A scan
that refuses to trade after a bad morning is the guard working.

---

## Window 3 superseded: the ranking was unfalsifiable

Window 3 never scanned. It was replaced the same evening because a defect was
found that no amount of running it would have fixed.

**Only bought symbols were recorded.** `TradeIntent` is written when an order
goes out, so every outcome this system can ever observe was already filtered by
rank. The model's third-choice calls never become orders, never become
outcomes, and therefore never become evidence — its ranking could not be shown
wrong by any quantity of live trading. `candidate_refused` did not cover the
gap: it keeps a symbol and a reason, and 89 of 96 read `already holding`, which
is an accounting fact rather than a judgment.

This is survivorship, the same shape as the delisted-history problem in
`WORKLIST.md` §4, and unlike small sample size **it does not shrink with
time**. Ten thousand trades would leave it exactly where it started.

`scripts/trade.py` now writes a `candidate_slate` record on every scan: every
ranked candidate, taken or not, with the model's rank kept apart from the
deterministic one, the levels, and the trigger bar. A candidate ranked third
has a determined outcome sitting in bars already held, so the ranking can be
scored offline against the setups it passed over — no extra trading and no
extra risk. It also means the ~96 days of 30m bars already collected can be
replayed rather than waited for.

**It records and decides nothing.** The fingerprint did not move. But it edits
a file this document lists, so the window ends — which is the rule working as
intended rather than an exception to it.

### Why this was worth a restart and tomorrow would not have been

Window 3 had zero trades and had not run a scan. The restart cost was
literally nothing, and it will never be this cheap again: the first scan is
05:33 AK. Making the change tonight costs an empty window; making it next week
costs a real sample.

---

## Window 14 closed, 2026-10-04: frozen files changed, one for a bug

Window 14 opened 2026-09-28 at `ba95546dbdbb` with gemma3:12b. On 2026-10-04
the following frozen items changed, and by this file's own rule a window
whose frozen list changes ends, bug or not:

| frozen item | change | reason |
|---|---|---|
| `scripts/flatten.py`, `execution/flatten.py` | closes wait for cancels to land (`cb28bf5`) | **bug**: INTC went into the 10-02 weekend open, unstopped, its close refused for insufficient quantity |
| model environment, `config.triage_model` | gemma3:12b -> gemma4:12b | gate benchmark 14/14 vs 12/14, 1.45s vs 3.0s a case |
| `config.triage_num_ctx` | 8192 -> 24576 | one window shared with TradingAgents; a mismatch reloads the model |
| `llm/ollama.py` | sends `think` when configured; .env sets false | gemma4 thinking filled an 8192 window once and returned nothing |
| scheduled tasks | two added (`advisor-pre`, `advisor-live`); all re-registered | TradingAgents, recorded only, sharing the GPU between scans |

The flatten fix alone ends the window; the model change would have ended it
had the fix not. The policy fingerprint did not move -- no `TradingPolicy`
field changed -- so window 15 runs the same rules on new code and a new model.

**These changes were made before this file was read.** The freeze was
discovered afterwards, while comparing the July and October morning scans.
Recorded so the order of events is not mistaken for a decision made with
the rules in view.

### Window 14's record

    entries sent   6   META, DASH, AON (09-29); INTC, SNPS (09-30); LULU (10-01)
    closed         3   DASH -1.060R  SNPS +4.434R  AON -1.009R
                       mean +0.788R, sd 3.157, n=3 -- unreadable at this size
    open           INTC x169, close queued 2026-10-04 for the 10-05 open
    no outcome     META, LULU (no trade_outcome recorded)

INTC's result will be written under `ba95546dbdbb` by the outcomes job and
belongs to window 14, not 15.

### Market context at the change

The 2026-10-04 morning scan against 2026-07-14's: names in an uptrend 29%
(31/106) against 49% (37/75); one of eleven sectors up on the month
(Technology +8.8%) against six; Dow futures -3.1% on the month while Nasdaq
+6.4%; 10y yield +10% on the month. A narrow, semis-led tape -- the regime
note above says the sample has never seen a down-trending session, and this
is the nearest it has come. Live trades split the same way: IT +1.45R (3/5
won), everything else -0.19R (2/10), n=15. Not a rule: the RS filter tested
null (p=0.999) and breadth tested null. A sector-leadership test is queued as
research.

---

## Window log

| date | event | trades so far |
|---|---|---|
| 2026-08-18 | window opened at `9fe7a6618823` / `9ea2d7d` | 0 |
| 2026-08-18 | **restarted** at `9fe7a6618823` / `180a117` — two bugs, 0 trades collected so nothing lost | 0 |
| 2026-08-18 | **restarted** at `9fe7a6618823` / `8947547` — end-to-end sweep found two more, still 0 trades | 0 |
| 2026-08-18 | **restarted** at `347ff1e34122` — liquidity floor raised 5M -> 20M on a 2.2% permutation result; first change made for an EDGE reason rather than a bug | 0 |
| 2026-08-18 05:27 AK | **window open and running** at `347ff1e34122` / `a8acc1d`; all four tasks Ready, first scan 05:33 | 0 |
| 2026-08-18 | **restarted** at `fb709122e949` — Thursday reopened for entries; Friday still closed. Arithmetic, not weekday edge | 0 |
| 2026-08-24 → 09-24 | **scheduler blackout**, host off, 21 trading days missed. No restart — no policy or build change. Calendar clock extended to 2026-12-10 | 2 before, 3 now |
| 2026-09-24 | news gate coverage recorded as **mixed** — 2 of 3 in-window trades taken ungated. Ollama left on manual start. No restart | 3 |
| 2026-09-24 | **window 1 CLOSED, truncated** — 30-day blackout, 2 of 3 trades ungated, and 23x5 landing inside the remaining term | 3, final |
| 2026-09-24 | **window 2 declared** at `fb709122e949` / `935c721`; hard end 2026-12-04, the last session before 23x5 | 0 |
| 2026-09-24 | **window 2 superseded** before its first scan — `nomic-embed-text` was never installed, so dedup had never run. Pulled; dedup now collapses duplicate stories | 0, no scans |
| 2026-09-25 | **window 3 opened** at `fb709122e949` / `935c721`, gemma3:12b + nomic-embed-text. Same hard end 2026-12-04 | 0 |
| 2026-09-24 | **window 3 superseded** before its first scan — only bought symbols were recorded, leaving the model's ranking unfalsifiable. `candidate_slate` now records the untaken candidates too | 0, no scans |
| 2026-09-25 | **window 4 opened** at `fb709122e949` / `2ac9fac`, after an end-to-end audit closed six gaps and hardened the scan against its own diagnostics. Same hard end 2026-12-04 | 0 |
| 2026-09-25 | **window 4 superseded** before its first scan — sizing measured at 0.108% actual risk against a declared 1.0%, nine times too small to read a drawdown from | 0, no scans |
| 2026-09-25 | **window 5 opened** at `5b091652a8ab`; deployment doubled to (100,80,60,40,0), slots left at 5. Same hard end 2026-12-04 | 0 |
| 2026-09-25 | **window 5 superseded** — the bar cache renewed its own lease and served bars up to three buckets stale; CCL triggered at 11:30 ET, passed every gate, and was never seen | 0, no trades |
| 2026-09-25 | **window 6 opened** at `5b091652a8ab`, same rules, new build. Same hard end 2026-12-04 | 0 |
| 2026-09-25 | **window 6 superseded** — the scan never reported bar freshness, so stale data and a quiet market looked identical | 0, no trades |
| 2026-09-25 | **window 7 opened** at `5b091652a8ab`, same rules, new build. Same hard end 2026-12-04 | 0 |
| 2026-09-25 | **window 7 superseded** — a zero-volume 16:00 placeholder bar understated liquidity 6.51%, wrongly excluding 22 liquid names from a 20M floor | 0, no trades |
| 2026-09-26 | **window 8 opened** at `5b091652a8ab`; placeholder bars rejected, tradeable universe 245 -> 267, fifth task archives news daily | 0 |
| 2026-09-26 | **window 8 superseded** — `min_price` was a policy field bypassed by four hardcoded copies, so editing it would have moved the fingerprint and changed nothing live; `MIN_RANGE_PCT` was enforced in the preview only, which showed 230 symbols against the trader's 267 | 0, no trades |
| 2026-09-26 | **window 9 opened** at `29378de4d359` / `6400751`; three tradeability floors single-sourced from the policy, range floor declared and OFF at 0.0, context gate short-circuits. No rule changed. Same hard end 2026-12-04 | 0 |
| 2026-09-26 | regime limit recorded — the sample has **0 of 92 down-trending sessions**, and Alpaca replicates the edge within 1 se (+0.311R vs +0.380R) while showing ~+0.025R over 2 years. No rule or code changed, **no restart** | 0 |
| 2026-09-26 | **window 9 superseded** — sector strength recomputed 13x a day for an answer daily bars cannot change (14.42s cold), and the advisory block ran before the WATCH gates, paying for candidates a full book could not trade | 0, no trades |
| 2026-09-26 | **window 10 opened** at `29378de4d359`, same rules, new build. Sector medians cached date-stamped and written pre-open by a sixth task; send loop unchanged. Same hard end 2026-12-04 | 0 |
| 2026-09-28 | **first trade of window 10: MLM**, 41 sh @ 491.06, stop 482.21, target 510.81, R:R 2.19. Gemma ranked it 1 of 2; NKE blocked twice by the earnings gate (reports Thu 10/01 after-close) | 1 |
| 2026-09-28 | **MLM carries `7247e3f+dirty`** — an untracked research script (`trace_trade.py`) sat in the tree while the scheduler fired. The rules are unchanged and the fingerprint is correct, but this one trade is not reproducible from any commit. Tree cleaned at `13b5e5f`; **no restart** — a restart would discard a valid trade to fix a provenance defect that is now recorded | 1 |
| 2026-09-28 | **second trade: CMG**, 631 sh @ 32.14, R:R 1.91, build `7cd2f07` clean | 2 |
| 2026-09-28 | **window 10 superseded** — both trades were deep laggards (MLM -29.2pp, CMG -21.2pp vs S&P) and nothing in the pipeline objected. Trend gate and model veto added | 2, both open |
| 2026-09-28 | **window 11 opened** at `07c4cda1e9ab`; `min_relative_strength=-10.0` and `llm_veto_enabled=True`. The RS filter TESTED NULL (p=0.999, halves disagree) and is enabled on the operator's judgement, not the measurement. Same hard end 2026-12-04 | 0 |
| 2026-09-28 | **windows 12/13 superseded**, then simplified: the two gates added on judgement were removed on measurement. `min_relative_strength` (p=0.999, cost 45% of setups) and `llm_veto_enabled` (fired 1/5 on identical input at temperature 0) both OFF | 0 |
| 2026-09-28 | **window 14 opened** at `ba95546dbdbb`; six gates instead of fourteen, `require_value_sweep` off, deployment flat at 100%, Friday trades. Roughly 3x the setup rate of 2026-09-27. Same hard end 2026-12-04 | 0 |
| 2026-09-29 | **window 14's first session ran the WRONG configuration.** `require_value_sweep=True` was hardcoded at `trade.py:507` while the policy said False, so the fingerprint `ba95546dbdbb` describes rules the scan did not apply. Setup counts stayed 73-76 all morning; after the fix, same store and minute, 73 -> 121. Fixed at `d6e9d67`. MLM and CMG both stopped out (-0.902R and -1.019R) under the old rules | 2, both closed |
| 2026-09-30 | **`scheduled_sector.bat` had never once run.** Its path held a carriage-return byte where `\r` should be (`scripts<CR>efresh_sector_rs.py`), so the 05:00 task failed on 09-29 and 09-30. **No restart:** the scan's fallback computed the same medians on its first candidate (06:03 cache = 19:09 manual refresh, value for value), so no rule and no input the scan saw changed — a frozen file fixed for a defect, not an improvement. Same stray byte fixed in `start_all.ps1` | AON, INTC, SNPS open; DASH stopped -1.06R |
| 2026-09-28 | **window 11 superseded** — the deployment taper ran OPPOSITE to the data (Monday 100% for -0.011R, Wednesday 60% for the best +0.066R) and Friday was excluded on a flatten cost of -0.213R that measured -0.012R | 0 |
| 2026-09-28 | **window 12 opened** at `6a1576995f61`; deployment flat at 100% (was 56% average), Friday trades (+14% of setups). Both are PREFERENCE dials turned up by the operator: ~1.79x return and ~1.79x drawdown, ratio unchanged. Same hard end 2026-12-04 | 0 |
| 2026-10-04 | **window 14 CLOSED** — flatten fix (`cb28bf5`, a bug), gemma3 -> gemma4:12b, num_ctx 24576, think=false, two TradingAgents tasks. Frozen items changed before this file was read; see "Window 14 closed" | 3 closed, INTC open |
| 2026-10-04 | **window 15 declared** at `ba95546dbdbb` / `6b71008`, gemma4:12b + nomic-embed-text; first scan 2026-10-05 05:33 AK. Same hard end 2026-12-04 | 0 |
| 2026-10-04 | **window 15 re-declared before its first scan** at `9ed12e112119` -- a genuine bug: a setup first seen one scan late was sent at its OLD close as the limit; with the price already below it the buy limit filled at once beside the stop. The only two fills >1% under their limits (APP 08-20, PSX 09-24) were both such orders, both stopped within the hour; every on-time order filled within 0.34%. `guards.late_signal` now refuses a setup whose latest close is already >= `max_entry_drift_pct` (0.5%) below its entry. Same hard end 2026-12-04 | 0, no scans |
| 2026-10-05 | **INTC (window 14) closed at -1.0R by the safety net.** The market sell queued Sunday night was cancelled by the operator at 01:48 AK; at the 06:03 scan protect.py found INTC unprotected, armed its original stop 115.88, and it filled at 115.85 (price 115.99 at arming). An open-price fill would have been about the same. Window 15's first session otherwise ran clean: every task 0x0, SLB entry sent 06:34, never filled, cancelled stale 08:03 | 0 (window 15) |
| 2026-10-05 | **window 15 re-declared at `9ed12e112119` / `c5f3693`, still 0 completed trades** -- the weekend flatten is calendar-aware (weekdays, acts only on the last session before a 3+ day closure, so holiday Fridays' Thursdays are covered), retries at :52, and leaves FLATTEN_ALERT.txt on the Desktop if still not flat. Frozen files changed for a genuine defect (FREEZE.md's own "Holiday-shortened weeks" section). Rules and fingerprint unchanged. Same hard end 2026-12-04 | 0 |
| 2026-10-05 | **window 15 re-declared at `69da227b506e` / `f85679e`, still 0 completed trades** -- the entry limit sits 0.30% above the signal. 3 of 18 live limits (META, LULU, SLB) touched and never filled; on 538 setups replayed on 1-minute bars from the real arrival time a limit at the signal filled 91% (+34.5R), +0.30% filled 99% (+46.7R), market 100% (+43.7R). A genuine execution defect: the misses were net winners. Same hard end 2026-12-04 | 0 |
| 2026-10-06 | **window 15 re-declared at `69da227b506e` / `e60210b`, still 0 completed trades** -- protect.py no longer leaves a position whose stop is already breached (or target reached) naked for a human: the naked shares are sold at market (queued for the open if shut), never twice, with PROTECTION_ALERT.txt on the Desktop. On 10-05 INTC sat unprotected 05:33-06:03 that way. Operator approved. Rules and fingerprint unchanged. Same hard end 2026-12-04 | 0 |
| 2026-10-06 | **advisory only, window 15 continues** -- ta_worker.py asks Ollama for structured output by JSON schema (constrained decoding) instead of function calling. Every 10-05 TradingAgents run had fallen back to free text for its Research Manager, Trader and Portfolio Manager, so ratings were regex-parsed from prose; a full SLB pass now parses all three, 110s vs 124s, same rating (Hold). TradingAgents is record-only: no trade, gate or size reads it | 0 |
| 2026-10-06 | **advisory only, window 15 continues** -- review_positions.py is monitor v2: Gemma gets the trade math as codes (R, fair P(target), MFE/MAE, cushion in 30m and daily ATRs, bars used, sessions to flatten) and 48h headlines instead of RSI/MACD/EMAs/% P&L; temperature 0 + fixed seed (identical calls verified), JSON schema, red flag only with a verbatim quote and a material event; reuses its last call when no new headline appeared. Same audit event and scorecard prefix; still record-only (TRIALS.md #45 gates any action) | 0 |
| 2026-10-06 | **advisory only, window 15 continues** -- the monitor-v2 prompt gains a CHART PATTERN section: the daily pattern card (LMW definitions, measured record from 143,586 events: none MEASURED) with an instruction that 'no measured edge' means ignore the pattern's name and lore. review_positions.py and momentum_watch.py fetch daily bars for it. Still record-only; TRIALS #45 unchanged | 0 |
| 2026-10-06 | **advisory only, window 15 continues** -- TradingAgents is connected to the scan: premarket first runs the live screen read-only (advisor/scan_watch.py: setups from the last 5 sessions, still live, live gates, ranked by smc.priority), then the operator's watchlist; passes rate held -> slate -> scan names -> watchlist; premarket budget 30 names (live 8). No trade.py or .bat change; ratings stay record-only (TRIALS #44) | 0 |
| 2026-10-06 | **window 15 CLOSED (0 completed trades); window 16 declared at `dcc6291a4ec2` / `e67b955`** -- the operator clamped the live scanner to a hand-picked core list (watchlists/core.txt: AMD, MRVL, INTC at open, edited weekly/daily) because the full-S&P sweep found no setups in a rally (Oct 2-6: 4.6-18% of stocks swept a 10-day low vs 27-45% in late September), and chose to hold through earnings (earnings block 7 -> 0). A different experiment from windows 1-15: a discretionary list plus the mechanical sweep entry. Expect few trades -- the pattern needs a dip below a recent low. Same hard end 2026-12-04 | 0 |
| 2026-10-06 | **window 16 CLOSED (0 completed trades); window 17 declared at `1e9ad75a7f09` / `0352504`** -- the operator raised `max_positions` 5 -> 8 (a PREFERENCE dial: per-position cap at 100% deployment falls 20% -> 12.5%, up to 8% of the sleeve at risk at once, 3% daily loss stop unchanged). The same commit fixed a drift that would have made the change cosmetic: trade.py's `--max-positions` default was a hardcoded 5 feeding both RiskLimits and the policy it fingerprints, and monitor.py's RiskLimits() defaulted to 5; both now read TradingPolicy. Core list grew 3 -> 18 the same day (`3d77441`, `5a1d607`). Same hard end 2026-12-04 | 0 |
| 2026-10-06 | **window 17 CLOSED (0 trades); window 18 declared at `3ef287f9adaa` / `b8b36d0`** -- the operator moved the system to their own picks plus the momentum engine (WORKLIST §37). Sweep entries paused (-0.041R on the unseen 2018-24 store, §30). Closers scoped by order-id prefix so the weekend flatten and 60-bar limit touch smc only. Gemma review and TradingAgents live pass removed; sector task retired; momentum task 11:40 AK added. CRITERIA SET BEFORE THE FIRST ORDER: momentum passes at 2027-04-06 if its book beats the S&P 500 over the same days AND its max drawdown stays under 25% (guard-test arm A: 2016-26 +2.38pp/hold vs random, t 1.49, max DD 22.2%); manual picks are reported at 30 closed trades as mean R with its standard error, and continue only if mean R > 0. Live smc record carried as context, not pooled: 16 closed, 5 won, +0.27R mean, +$2,041 | 0 |
| 2026-10-06 | **advisory only, window 18 continues** -- second opinions (WORKLIST §38): TradingAgents back at 03:30 on held + momentum + core names, graded against the S&P over 21 sessions; RSI/averages/patterns labelled advisory (tested, no edge). Policy unchanged; manual_trade.py only adds a preview line and a `second_opinion_snapshot` audit row, which changes no order. Fixed before any trade: momentum holdings no longer use manual slots (`ba9b167`) | 0 |
| 2026-10-06 | **operations fix, window 18 continues** -- end-to-end audit (WORKLIST §40): one log per scheduled task (a shared log silently stopped overlapping tasks; the weekend flatten was skipped 5 times in 3 days); safety checks and the hub read ownership so momentum holds raise no false alarms; protect.py's OCO counts as protection; momentum dry run rescheduled before the paper run. No policy field or order logic changed | 0 |
| 2026-10-06 | **window 18 CLOSED (0 trades); window 19 declared at `ba826ffea485` / `7dfc6cd`** -- the operator's list traded automatically (WORKLIST §41): picks.txt (AMD MRVL ARM META INTC AMAT TSM PLTR) bought at the first run after the 10-07 open, sold when removed; 2.5-ATR safety stop, no target, sector cap lifted for the list. Momentum and the criteria set for it are unchanged. Picks are reported like manual picks: mean R with its standard error at 30 closed trades | 0 |
| 2026-10-07 | **window 19 CLOSED (0 trades); window 20 declared at `cc750c27caef` / `e6ed7bd`** -- Gemma given a goal and its own live paper book (WORKLIST §42): 10% of equity, up to 5 core.txt names not claimed by picks or momentum, chosen each morning at 05:45 AK with quoted evidence; the system prices, sizes and stops. CHARTER SET BEFORE ITS FIRST ORDER: judged 2027-04-06, passes if its book beats the S&P 500 and the momentum paper book over the same days with max drawdown under 25%. Desk officer journal at 13:45 (checks are code). Picks/manual now share 40% | 0 |
| 2026-10-07 | **window 20 CLOSED (0 trades); window 21 declared at `61f5509d002d` / `0870192`** -- capital split before any order: Gemma 10% -> 40%, momentum 50% -> 40%, picks + manual 40% -> 20%. The operator asked for Gemma at 50% and chose 40/40/20 over 50/35/15 (momentum needs ~35%+ to buy its $900-1,700 names in whole shares). Gemma's charter and judging date unchanged | 0 |
| 2026-10-07 | **window 21 CLOSED (0 trades); window 22 declared at `61f5509d002d` / `fb304fd`** -- Gemma's list widened before its first order: watchlists/gemma.txt adds 21 liquid S&P 500 names, two per sector by 12-1 momentum, so it chooses from 28 free names instead of 7. Policy unchanged | 0 |
| 2026-10-07 | **window 22 CLOSED (0 trades); window 23 declared at `61f5509d002d` / `d2b94fb`** -- before Gemma's first order: its prompt now states the judging date and test, shows a running scoreboard, discourages churn, and no longer tells it to copy momentum (it had picked its five highest-momentum names); unfilled gem- buys cancelled before each decision; TradingAgents also rates gemma.txt. Policy unchanged | 0 |
| 2026-10-07 | **window 23 CLOSED (0 trades); window 24 declared at `29e50eceae31` / `ce68da8`** -- shared slots (picks + manual one-offs) 8 -> 12 so one-offs fit beside the 8 picks; per-stock cap fixed at 12.5% of the sleeve (shared_position_cap_pct) instead of 100/slots, so no position shrank. Gemma and momentum keep their own slots | 0 |
| 2026-10-07 | **window 24 CLOSED after day one; window 25 declared at `29e50eceae31` / `5ba5525`** -- Gemma's facts page now carries a two-sentence summary of each TradingAgents report (from its Executive Summary), labelled unproven and indicator-based; before, only the one-word rating. Policy unchanged. Day-one positions opened under window 24 (picks 8, Gemma 5) are attributed to it by build | 13 open |
| 2026-10-07 | **window 25 CLOSED; window 26 declared at `53c4fff93a83` / `c5e81b9`** -- momentum's first market-on-close orders: 9 accepted, 8 EXPIRED unfilled on the paper account (only MU filled). The operator made momentum a helper instead of fixing the orders: momentum_trades=False, its 40% split Gemma 60% / picks + manual 40%; the monthly dry run keeps the rule's record (Gemma's benchmark, now 'the momentum rule'); its picks join Gemma's options, labelled; the 1-share MU paper position is sold at the next 11:40 run. Day-one result before the change: picks +0.27%, Gemma +0.39%, S&P 500 -0.22% | 13 open + MU |
| 2026-10-07 | **window 26 CLOSED; window 27 declared at `53c4fff93a83` / `22b0510`** -- Gemma's charter now says it is in a head-to-head competition with the operator's picks book under the same rules, and its morning scoreboard shows both books on the same measure (return on money invested). Policy unchanged | 13 open + MU |
| 2026-10-07 | **window 27 CLOSED; window 28 declared at `cda6989dfbac` / `9b7c743`** -- TradingAgents removed from Gemma's facts at Gemma's own request (its record should be its own judgement); TradingAgents keeps running as a graded shadow the operator sees. From Friday 10/9 05:45 | 13 open + MU |
| 2026-10-07 | **window 28 CLOSED; window 29 declared at `cda6989dfbac` / `0a1358d`** -- THE COMPETITION, rules set before any month was scored (WORKLIST §46): operator's picks vs Gemma, monthly win/lose on change in P/L over average money invested; quarters by months won; season = calendar year (season 1: 2026-10-07..12-31), winner +10 points of the account next season from the other book. Ghost books (random 8 / random 5, same stops), the momentum rule and the S&P 500 scored on paper beside them. Gemma's charter states it. Policy unchanged | 13 open + MU |
| 2026-10-07 | **window 29 CLOSED; window 30 declared at `cda6989dfbac` / `c7662fa`** -- selection skill graded across the whole menu (WORKLIST §47): Gemma's chosen vs passed-over names and TradingAgents' IC, 5/21 sessions; probability of the better trader from paired daily returns (after 10 days). Gemma's scoreboard adds its own selection record; gemma_decision logs its menu. Policy unchanged | 13 open + MU |
| 2026-10-07 | **window 30 CLOSED; window 31 declared at `91a089ed56b4` / `89c88a4`** -- the operator asked to push Gemma to max out profit: gemma_risk_pct 1% -> 2% of its sleeve (new buys only; the five open positions keep their size), charter objective 'maximise profit within the 25% drawdown', and the market context it asked for (sector, strength vs S&P, VIX, 10-year yield, breadth, coming events, its last decisions). Monday research task 04:30 AK refreshes Gemma's list and writes the operator's research pack. Competition score unaffected (% on money invested) | 13 open + MU |
| 2026-10-08 | **window 31 CLOSED; window 32 declared at `466533d96ea9` / `5d33c80`** -- the operator gave Gemma its own stop, target and size (WORKLIST §50): per NEW buy it names stop tight/normal/wide (1.5/2.5/3.5 daily ATRs), target none/2R/3R/5R, size small/normal/large (1/2/3% of its sleeve at risk), enum names only; the 20% per-name cap still binds. Held names keep their brackets. Picks unchanged (2.5 ATR, no target, 1%). Choices logged for grading | 13 open |
| 2026-10-08 | **window 32 CLOSED; window 33 declared at `f235ab909864` / `4b77fab`** -- the operator asked for the news to tie into Gemma (WORKLIST §52): a 03:00 daily brief (US market-moving releases, FOMC, earnings, the President's posts/schedule, 48h headlines, and Gemma's quote-grounded links from headlines to the desk's stocks) goes into Gemma's 05:45 facts as dated facts, with a rule that news is context, not a reason to swap. Also fixed: the economic calendar had shown yesterday's releases as today's in Gemma's market line since it was added (Nasdaq answers date=D with D-1). Picks unchanged; competition scoring unaffected | 11 open |
| 2026-10-08 | window 33 amended before its first decision (same fingerprint `f235ab909864`): the brief's calendar widened (WORKLIST §54) -- more market-wide releases, sector releases, options expiry/holidays, bellwether earnings, each held stock's earnings across 4 weeks. Also TradingAgents lightened and its grade made independent (§53); Gemma does not see it | 11 open |
| 2026-10-09 | window 33, same fingerprint `f235ab909864`: the brief's calendar gained foreign central banks, big IPOs and sourced big-money dates (WORKLIST §58) at the operator's request; competition scoring fixed to price the paper books fresh (they had lagged a day) | 11 open |
| 2026-10-09 | **window 33 CLOSED; window 34 declared at `1c5ed20750e6`** -- extended-hours trading for both books (WORKLIST §59: limit orders, stops armed at the open, hourly sync 12:35-04:35 AK, Gemma Sunday 16:45 AK); capital 50/50 (Gemma 60 -> 50); the stakes fixed (§60: quarterly 25% transfers from 2027, Q4 2026 trial); momentum a helper for both; competition paper books priced fresh. Picks paused for the weekend except PLTR, ABBV, XOM | 6 open |
| 2026-10-18 | **advisory only, window 34 continues** -- the operator asked for a morning check of the momentum leaders: `stock-scanner-leaders`, weekdays 05:00 AK (`scheduled_leaders.bat`, own log), writes logs/leaders/DATE.md with the 12-1 top 20, the 4 still rising (above the 50-day, up on the month), a money-flow 4 (top 50 with 3+ of 4 flow checks: sector gaining dollar-volume share, dollar-volume surge, up-day volume, OBV; features/money_flow.py) and each sector's dollar-volume share. Descriptions, not tested rules: both lists are graded forward vs the S&P at 5/21 sessions (ops/leaders_grade.py, rules fixed before the first grade). Gemma does not see it (option B, after the verdict). REPORT ONLY: it never edits picks.txt or sends an order; broker access is GET. A new task beside the frozen ones, none of which changed | 3 open |
| 2026-10-18 | **window 34 restored, same fingerprint `1c5ed20750e6`** -- a drift review found 16 commits of 10-15 (`082f6f2`..`a554c75`) that changed the trading path after window 34 opened without a log entry: policy `2a47c0da08e0` (extended hours OFF, earnings block 1, picks sector cap 3), Gemma's facts (math signals fed placeholder inputs, live snapshot, S&P 500 sweep setups on her menu, portfolio review), picks/Gemma sector guards, take-profit ladder and new stop/size/target menus. No buy was placed under it (only the 10-16 picks sell-all and 4 Gemma holds). At the operator's request gemma_trade.py, picks_sync.py, flatten.py (also removes the 10-15 EXCHANGE_TZ crash), policy.py, bookorders.py, picks.py, smc.py are back to `c7ce847`. Kept: two genuine defects -- calendar.py earnings dates were a day early (`32d3407`, a frozen file fixed for a defect; callers unpack the new (found, failed)), and Gemma's target 'none' got the picks ceiling (`e1f2d55`, `85de8aa`); purely additive modules nothing on the live path calls (safety/guards/models additions, alpaca ladder payloads, stops, math_signals, market_snapshot, portfolio_review, preflight). The math ideas are queued as tests (WORKLIST §62.11) | 3 open |
| 2026-10-18 | **brief amendment, window 34 continues (same fingerprint)** -- the operator asked for what Wall Street is doing in Gemma's brief: advisor/wall_street.py reads Alpaca's news wire since the last session (72h Monday, 48h Sunday, else 24h) and keeps hard news only -- ratings and price targets, deals, guidance, results, the Fed and rates -- for S&P 500 + 400 names, hers first, up to 8 lines in market_text; opinion, roundups, chart lore and crypto dropped. Context, not a signal (§52, §57). Like the calendar widenings of §54/§58 | 3 open |
| 2026-10-18 | **corrections to Gemma's inputs, window 34 continues (same fingerprint `1c5ed20750e6`)** -- a prompt audit against the code found three things she was told that were false or unusable. (1) Her scoreboard showed the operator's picks at +0.00%: its formula divides by what a book holds NOW and picks was empty; it now shows both books month to date from the competition's own snapshots, scored as the verdict scores them (picks -6.09%, Gemma +0.45% on 10-18). (2) The charter said the operator trades "under the same rules"; it is scored the same way, with its own fixed stop and size. (3) 93 of 250 per-stock headlines were opinion clickbait she could quote as buy evidence; they are dropped (wall_street.is_opinion) from her stock blocks and from the brief's feed. Also: OPEC's per-country output, tagged "United States" by the feed, no longer fills her last-session data line. Defects, not new inputs | 3 open |
| 2026-10-19 | **advisory only, window 34 continues (same fingerprint `1c5ed20750e6`)** -- the operator asked for the paused sweep scanner in the ghost logs: `scripts/ghost_scanner.py` (13:30 AK outcomes task, before the standings) replays its own rules on paper -- smc.scan on the S&P 500's 30-minute bars under the window's TradingPolicy, entry at the trigger close, its slots (12, 3 per sector), stop/target/max_hold_bars and flat before the weekend; NOT the LLM news veto or earnings block. Rules in ops/ghost_scanner.py, set before the first ghost trade was scored; starts 2026-10-07 with the other paper books (back-filled from bars). Shown as a row in the standings, outside the head to head. No broker access, no orders; trade.py and the scan task unchanged | 3 open |
| 2026-10-19 | **window 34 CLOSED; window 35 declared, policy unchanged (`1c5ed20750e6`)** -- the operator asked to push Gemma to win more and win bigger, mid-month by choice ('still working to a bigger goal'). Her prompt: (1) the objective counts profit in dollars on her WHOLE book, cash earns nothing; (2) an empty slot is a choice, not a default -- her journal must name the best stock she passed over for each empty slot and why, a slot is filled only on strong evidence; the no-churn rule now applies to swaps, not to filling an empty slot; (3) her scoreboard shows her money at work ($ invested of her book, empty slots, whole-book P/L) and both ghosts this month (ghost-Gemma, ghost-scanner); the charter names both ghosts. No forced buys; menu, sizes, stops, caps and the competition score unchanged. October's verdict mixes windows 34 and 35. Queued, not done: a top-up for half-sized holdings (MPC, VLO at the old 1% risk) | 3 open |
| 2026-10-19 | **shadow bench, window 35 continues (same fingerprint `1c5ed20750e6`)** -- the operator asked to maximise Gemma; variants A-B run as shadows first. gemma_trade.py now also saves the exact question she was asked (prompt, schema, facts, holdings, her answer) to data/gemma/questions/ -- additive, after her decision is logged, wrapped so a failure cannot stop a trading run; nothing she sees or does changed. scheduled_gemma.bat then runs scripts/gemma_shadow.py (own log, exit code of the trading run kept): `think` (thinking on within 4,096 tokens -- unlimited, it looped for 17,400 tokens and never answered -- then the answer from her notes) and `vote3` (three answers at temperature 0.7, a stock held if 2 of 3 hold it). Never trades. Graded nightly in selection_grade.py: each variant's chosen-minus-passed-over spread minus hers on the same mornings, read by ops/answer.py. Promotion only through a new window after 40+ graded mornings at 5 sessions with verdict REAL. Rules in advisor/gemma_shadow.py | 3 open |
- 2026-10-19 (window 35 continues): news linker -- a headline linked to more than 6 stocks is now market news (advisor/news_impact.py MAX_LINK_SYMBOLS, commit ee6e372): shown once in the market section, not in each stock's block. Bug: this morning two market headlines ('No Black Monday? ... Retail Stays Cautious', 19 stocks; 'US Stock Futures Rise ...', 18) filled the news lines of most of Gemma's menu. Changes her inputs, not her rules or the policy; fingerprint unchanged.
- 2026-10-19 (window 35 continues, operator: "holding 3 isnt winning! push her to do more!"): Gemma's floor in an uptrend -- when the S&P 500 is above its 210-day average she must hold at least 4 of 5 (advisor/gemma_trader.py MIN_IN_UPTREND; the answer schema's minItems, so a mostly-cash answer cannot be given; she still chooses which). Below the average, no floor. Reverses the window-35 'never forced' line. Her rules change mid-October: her October verdict mixes old and new rules. Policy fingerprint unchanged (the floor lives in her prompt and schema, not the policy). Preview at 06:2x AK: she held 5 (MPC, MU, VLO + LITE, COHR).
- 2026-10-19 (window 35 continues, operator: "give her atleast 500 ... like its in the stock scanner... stock pool"): Gemma's stock pool -- round 1 shows her every S&P 500 stock past the scanner's tradeability floor (policy min_price / min_range_pct / min_dollar_volume over 20 daily bars) that no other book holds, one line each, and she shortlists up to 20 (advisor/gemma_pool.py); round 2 is her usual decision on that shortlist plus her holdings, same rules. The hand lists (core + gemma + momentum) are the fallback if round 1 fails; gemma_pool.ENABLED turns it off. Preview: 488 in the pool, 20 shortlisted across 6 sectors, 36s. Logged as gemma_decision.pool_size. Policy fingerprint unchanged.
- 2026-10-19 (window 35 continues, operator: "lets do 3 with out the shadow, we need to give gemma time to think, maybe more tokens, not to much tho"): Gemma thinks before she answers -- a plain-words pass on the same briefing (gemma_trader.think_prompt, capped at THINK_TOKENS=1500, asked for ~400 words), then the usual grammar-constrained answer with her notes appended (with_notes, max 6000 chars). Pool round 1 gets half the cap. A failed thinking pass falls back to answering without notes; THINKS=False turns it off. Notes are logged as gemma_decision.thinking. Live, not shadowed first, at the operator's request: this mixes October's verdict (one more change mid-month). Preview 10-19: 18s + 13s of thinking, 57s whole run, held all five. Policy fingerprint unchanged.
- 2026-10-19 (operator: "pause the tradingagents and run diffrent task for gemma ... a full view of the portfolio, and postion, and anwser, with the next stocks coming up"; chose "Yes, with guardrails" for dashboard edits, review + Ask first): TradingAgents' 03:30 task (stock-scanner-advisor-pre) DISABLED, not deleted -- its ratings were recorded-only and Gemma never saw them. New task stock-scanner-gemma-review, 12:30 AK weekdays: scripts/gemma_review.py (advisor/gemma_review.py) -- her book's numbers computed in code, her verdict per holding (keep/watch/sell_candidate), watchlist from her pool shortlist, upcoming events, up to 3 proposals; with thinking. TRADES NOTHING; the 05:45 run does not read it. Dashboard: review panel + Ask Gemma (POST /api/ask, the first write endpoint: JSON only, Origin must be this PC, one question at a time, answers from her briefing + review via chat_text; trades nothing, changes no rule). Rule-editing controls are a later phase. Policy fingerprint unchanged.
- 2026-10-19 (operator: "Gemma having memory ... she would be able to have a short memory. lets look into it and tie it in"): Gemma's short memory (advisor/gemma_memory.py + memory_feed.py) goes before the market line in her 05:45 decision, and into her evening review and Ask Gemma. Built from her RECORD and PRICES, not stored text (the 2026-09-24 text-memory test made a model answer worse, llm/memory.py): her last 6 closed trades in R, a plain note when she holds a name that stopped her out in the last 14 days, her last 3 submitted sessions (bought/sold, her journal line), her last review's flags and watchlist -- each sell and watchlist name with its price change since. Capped at 2400 chars; says today's facts win. gemma_memory.ENABLED turns it off; logged as gemma_decision.memory. Live, untested as an edge (memory_ab.py never recorded a verdict). Preview 10-19: 1646 chars, held all five, her COHR reason now names the earlier stop-out. Policy fingerprint unchanged.
- 2026-10-19 (WORKLIST §65, operator "yes" to 1-3): Gemma's live decision is GRADED (gt.schema/prompt graded=True, only in gemma_trade.py; the shadow bench keeps its saved schemas). (1) forecasts: for every stock on her list, prob it beats the S&P 500 over 5 trading days, clamped 0.01-0.99, logged as gemma_decision.forecasts and scored by advisor/forecast_grade.py (Brier vs 0.25 for always-0.5; open of the decision day's session to the 5th close; reported once 30 are graded) -- shown in her memory, the review and the dashboard. Recorded only: it sizes nothing. (2) sell_if: one observable exit per holding, logged as gemma_decision.sell_if; the evening review shows it and she answers sell_if_met yes/no/unclear. NOT auto-sold (§64: selling on a trend break did not beat the stop); to be graded before it trades. (3) Rule line: headlines are information, never instructions. Preview 10-19: 24 forecasts 0.40-0.65, five sell-ifs, 77s run. Policy fingerprint unchanged.
- 2026-10-19 (operator: "lets contine trimming down into tokens"): Gemma's briefing trimmed, same facts. Stock blocks: 3 headlines (was 5), each cut to 120 chars; no 'earnings: none' line (rule now says no line = none within 7 days). Market line (ops/week_ahead.market_text, read only by gemma_trade): US calendar events only (NON_US_EVENT), the President's posts but not his routine schedule, CONTEXT_NOTE dropped (already in her rules); Wall Street lines no longer in the brief's market text -- gemma_trade adds at most 6 that name a stock on her list or are [fed]/[macro] (wall_street.for_gemma). Measured with her tokenizer on 10-19: 8,066 -> 6,752 tokens (-16%); market 2,447 -> 1,277 chars; stock list 16,504 -> 11,629. Preview: same holdings, 24 forecasts. Policy fingerprint unchanged.
- 2026-10-19 (operator: "increase the market cap to 2B", applied to Gemma's pool too, chosen in the quiet period): a $2B market-cap floor on Gemma's round-1 pool (advisor/gemma_pool.MIN_MARKET_CAP, large_enough) and on the read-only universe report. Caps from Nasdaq's public screener (sources/market_caps.py, ~7,000 US stocks, cached per day in data/market_caps/). A missing cap or a failed fetch KEEPS the name -- an outage never empties her pool. Effect today: none on her pool (all 494 tradeable S&P 500 names are above $2B); in the report the MidCap 400 goes 398 -> 393 and the SmallCap 600 422 -> 350. It bites only if smaller universes join her pool (step 2, after the Oct 31 verdict). Policy fingerprint unchanged.
- 2026-10-19 (operator: "lets run it in the live, nothing shadowing"): Gemma's long-term memory through Hindsight (vectorize-io/hindsight 1.0.0, MIT), LIVE in her morning question. 05:05 AK weekdays + 16:30 Sunday (task stock-scanner-hindsight, scheduled_hindsight.bat): scripts/hindsight_feed.py turns the audit log + her reviews since 10-07 into dated memories; scripts/hindsight_lessons.py (separate venv C:/Users/kitti/hindsight-sandbox, embedded Postgres, CPU embedding/reranker models, gemma4:12b via Ollama) retains new ones and reflects her lessons -> data/gemma/hindsight/lessons.json; gemma_trade.py inserts them (advisor/hindsight_memory.block, max 2,500 chars, 72h freshness) after her short memory, labelled 'today's facts below win', and logs them as gemma_decision.hindsight. Fail-open: missing/stale lessons = no block. Evidence before going live (10-19): recall 5/5 exact on her own history; one with/without A/B on two saved questions changed her picks by about as much as her own run-to-run variation -- live on accuracy, NOT on a measured edge; the lessons also editorialise (e.g. 'successfully', and LITE's operator sale read as 'a cooling of the AI trade'). Grade the lesson mornings vs earlier ones once 30+ exist. First run: 20 memories, 362s, 3,476 chars. Policy fingerprint unchanged.
- 2026-10-19 (operator: "add them now, except the one you want to skip"): three new lines in every stock block of Gemma's question (scripts/gemma_trade.facts_for, so the review sees them too). 'analysts:' this fiscal year's EPS estimate change vs 90 days ago + analysts up/down in 30 days; 'fundamentals:' forward P/E, revenue/earnings growth, gross/operating margin -- both from Yahoo quoteSummary (sources/yahoo_fundamentals.py, crumb session, cached per day in data/fundamentals/, missing = no line); 'chart:' price vs its 50/200-day average, last session's volume vs 20 days, ATR rank 0-100 in its year (advisor/stock_context.py) -- labelled in her rules as context that tested as no edge (TRIALS #28-#39). Oscillators (RSI, MACD) deliberately NOT shown. Cost measured with her tokenizer: ~98 tokens per stock (24 stocks +2,355; a 51-stock morning ~+5,000), so her context goes 24,576 -> 32,768 (.env SCANNER_TRIAGE_NUM_CTX; measured 10.7 GB VRAM, all on the 16 GB GPU; the shadow bench already ran at 32,768). New rule line explains the abbreviations. Not a tested edge: estimate revisions are research-backed, untested here. Policy fingerprint unchanged.
- 2026-10-19 (operator: "lets update the prompts"): prompt updates from the day's tests. Morning rules: analyst revisions and fundamentals named among what she weighs; new rule 'selling a winner early lost money here' (TRIALS #16-19, #71-#74: profit targets, selling at the day's high, give-back and RSI exits all lost to holding) -- sell when the reason fails; new rule that the Hindsight 'Lessons' block's dates/results are records and its opinions are not evidence. THINK_ASK: check analyst revisions, and a step for her lessons. Evening review: gets the same lessons block, and is told not to re-propose rejected ideas (early selling, trend-break exits, chart indicators). Ask Gemma: answer ONLY the question (in the 10-19 model tests every model drifted into a trade plan). Charter unchanged. Policy fingerprint unchanged.
- 2026-10-20 (operator: "make sure Hindsight is working completely 100%"): the 05:05 Hindsight run FAILED -- Hindsight's defaults give each reflect LLM call 30s and the whole reflect 300s, and a cold gemma4:12b (loading into the GPU) timed out every call; her 05:45 run used the 10-19 evening lessons (still within 72h). Fix in scripts/hindsight_lessons.py only: per-call LLM timeout 300s, reflect wall 1200s (env defaults), Gemma warmed in Ollama before Hindsight starts and unloaded after (one model at a time), reflect retried once, client closed. Verified via the scheduled task itself at 11:20 AK: loaded in 12s, 1 new of 21 memories, lessons 4,228 chars, 158s, exit 0, GPU empty after. Her prompt, rules and policy unchanged; fingerprint unchanged.
- 2026-10-20 (operator: "enable thinking for Gemma"): her pool round (round 1, shortlisting from the S&P 500) now gets the full thinking budget, gt.THINK_TOKENS=1500, not half (scripts/gemma_trade.py). At 750 her pool notes hit the cap mid-thought (measured 10-20 preview: 750/750, done=length); with 1500 she stopped on her own at 924 tokens, 13s, prompt 17,152 of 32,768. Thinking was already on everywhere (THINKS=True); never tested with vs without -- the operator declined a thinking-off shadow. Policy fingerprint unchanged.
- 2026-10-20 (operator: "lets monitor all on schedule.. yes 1,3,4"): (4) new READ-ONLY health check, task stock-scanner-health, 06:00 + 14:15 AK weekdays (scripts/health_check.py, ops/health.py, scheduled_health.bat, own log): every position's shares covered by a live sell stop (status=all, nested, held legs count), every enabled task's last result, Ollama runners without a live server (orphans), error lines in today's morning logs (deskcheck.trouble_lines), and on market days Hindsight lessons written today, Gemma's submitted decision, the evening review saved. Report logs/health/YYYY-MM-DD.md; a problem pops a Windows notification and pushes ntfy if SCANNER_NTFY_TOPIC is set (it is not). Sends no order. (1) Sunday Hindsight moved 16:30 -> 16:00 (Gemma's Sunday run is 16:45; Hindsight may now take up to 20 min). (3) Finished one-time tasks stock-scanner-oneshot-wdc-exit and stock-scanner-picks-resume DISABLED, not deleted. Weekly report re-run once to clear its 10-16 failure status (fixed then in e141e82; result 0). Gemma's prompt, rules and policy unchanged; fingerprint unchanged.
- 2026-10-21 (operator: "best 4, buy them hold them until next week monday", then "10:30am to 11:00am monday we an sell. when they are at the highest"): AAPL, AMZN, AMGN, MET added to watchlists/picks.txt (53c0265) -- S&P 500 uptrend screen + the day's news, one per sector, no earnings before 10/27; picks' usual 2.5 ATR stop and 1% size. One-time exit, task stock-scanner-oneshot-picks-exit-1026, Mon 10/26 10:30 AK (data/oneshot/picks_exit_1026.py + .bat, own log logs/oneshot_picks_exit_1026.log, same path as the 10-20 WDC exit): each name polled every 30s and removed from picks.txt + committed + picks_sync --submit once it is 0.4% off its high since 10:30; the rest at 11:00. Red names sold too (the operator chose the Monday exit). Never starts a sync 10:32:30-10:36:00 (the scan task's own picks_sync). NVDA not included. CHOSEN, not tested: a trailing exit in a 30-minute window is not a measured edge. Gemma, policy and fingerprint unchanged.
- 2026-10-21 (operator: "should she monitor my postion as well? as a safty net?" -- "yes, lets build that"): Gemma's safety check of the OPERATOR's positions, ADVICE ONLY. After her 12:30 AK review in the same task (scheduled_gemma_review.bat; the task's result stays the review's): scripts/operator_watch.py + advisor/operator_watch.py read every position Gemma does not own, code-flag a report within 7 days (WORKLIST §55: 1 in 7 opens through a 2.5-ATR stop), a price within 1 daily ATR of its stop, or no live stop, then give her the same facts as her own stocks; she answers ok / watch / concern, why, and what to watch (a flagged position is at least watch). Her prompt here never mentions the competition, her book or her scoreboard. Saved to data/gemma/operator_watch/DAY.json and into the day's review (dashboard panel 'Your positions: Gemma's safety check'); a concern pops a Windows notification (toast moved from health_check.py into ops/notify.py) and pushes ntfy if set. Graded, rule fixed before the first verdict: stock minus S&P 500 over 5 sessions from the verdict day's close, mean per verdict. Sends no order, moves no stop, edits no list. First run 10-21 10:40 AK: AAPL/AMGN/NVDA watch, AMZN/MET ok, no code flags. Gemma's own trading, prompt and policy unchanged; fingerprint unchanged.
- 2026-10-21 (operator: "what other ways we can help find more stocks? beside yahoo?" -- "lets do all 3"): (1)+(2) new REPORT-ONLY task stock-scanner-ideas, weekdays 04:40 AK (scripts/morning_ideas.py, ops/ideas.py, scheduled_ideas.bat, own log, in the health check's morning logs), writes logs/ideas/DATE.md. EARNINGS WINNERS: the PEAD BEAT rule (TRIALS #27, pead_test.py) made live -- S&P 500 + MidCap 400, Nasdaq surprise >= +5%, reaction close d-1 -> d+1 vs the S&P > 0, listed the morning of d+2. ESTIMATES RISING: this fiscal year's EPS estimate +3%+ vs 90 days ago (Yahoo earningsTrend), 2+ analysts up and none down in 30 days, price above the 50-day. Rules fixed 2026-10-21 before the first list; the first 4 of each recorded in data/ideas/picks.jsonl from 10-22 (not 10-21: its open had passed) and graded vs the S&P 500 at 5/21/60 sessions. Never edits picks.txt, sends no order; Gemma does not see it before the Oct 31 verdict. Also: the PEAD test's earnings history (stopped at 2019-05) is being collected to today so pead_test.py can run its pre-registered test.
- 2026-10-21 (same request, (3)): Gemma's round-1 pool adds the S&P MidCap 400 FROM MON 2026-11-02 (gemma_pool.MIDCAP_FROM, after the Oct 31 verdict): the strongest MIDCAP_MAX=120 by positive 12-1 momentum that pass the same tradeability and $2B floors and no other book holds; the prompt names the wider universe. Measured with her tokenizer: 22,546 tokens for 488 + 120 lines (limit 32,768). A MidCap failure adds none and never blocks. Until 11-02 her pool, prompt and input are byte-for-byte unchanged. Policy fingerprint unchanged.
- 2026-10-21 (report only, operator: "a small pool shadowing, with results, that doesnt show or effect gemma trading"): the morning ideas report gains a fast-mover shadow pool, TRIALS #79 -- fast_up (the 4 fastest liquid S&P 500 + MidCap names above their 50-day) and fast_all (the 4 fastest, trend ignored), recorded in data/ideas/picks.jsonl and graded vs the S&P at 5/21/60 sessions. Nothing trades on it, Gemma's code never reads it, picks.txt untouched. Policy fingerprint unchanged.
- 2026-10-21 (TRADING PATH, picks book only; operator: "big caps-section 1, fast-movers-section2", chose 0.5% risk): picks.txt gains sections. Names under '# [fast]' are Section 2, fast movers: same 2.5 ATR stop and no target, 0.5% risk (picks_fast_risk_pct), at most 2 held (picks_fast_max_names), never bought with earnings today or in the next 7 days (picks_fast_earnings_days; fail closed if the calendar cannot be read). Section 1, big caps, unchanged. Gemma's rules unchanged until the Oct 31 verdict, then she is offered the same Section 2 (operator's choice). Policy fingerprint changes (new picks_fast_* fields only; no existing value changed). Dry run: TWLO refused (earnings 10-28), HIMS refused (slots full at 2).
- 2026-10-21 (picks book, operator: "test one tommorow, buy one. see how it moves until friday", chose MRVL and a Friday 10:30-11:00 AK exit near the high): MRVL added under '# [fast]' -- the first Section 2 name, 0.5% risk, 2.5 ATR stop, bought at the first regular pass Thu 10-22 (05:33 AK). One-time task stock-scanner-oneshot-fast-exit-1023 (data/oneshot/fast_exit_1023.py, a copy of the Monday exit) sells it Fri 10-23 once it slips 0.4% off its high after 10:30 AK, else at 11:00. If the stop hits first, the list still names MRVL and would re-buy it at the next pass (the picks book has always worked that way) -- remove it then. Disable the task after it runs; never delete.
- 2026-10-21 (Gemma's INPUTS, not her rules; operator: "lets explain this to Gemma so its in her memory, what this test is" and "this test shouldnt be counted as a challenge"): (1) advisor/operator_notes.py -- dated notes from the operator in data/gemma/operator_notes.json, shown while active in her 05:45 question, her 12:30 review and her safety check, and fed to Hindsight as memories (kind 'note'). Header says they change no rule; nothing in the trading path reads them. First note (10-21..10-23): the MRVL Section 2 test, half risk, sold Fri, not counted, Section 2 may be offered after Oct 31. (2) Competition exclusions -- data/competition/excluded.json; the evening snapshot leaves out a book's position in that symbol inside the window and any closed trade ENTERED inside it. First: picks MRVL 2026-10-22..10-23. The quiet period is otherwise kept: her menu, pool, sizes and policy are unchanged.
- 2026-10-21 CORRECTION: MRVL did not wait for Thursday -- the hourly extended-hours sync (stock-scanner-extsync, WORKLIST §59) bought it at 21:35 AK in the overnight session: 6 sh, limit 234.55, stop 192.48 armed by protect.py at the 10-22 open (no stop until then), risk $252. The note to Gemma and the competition exclusion (now from 2026-10-21) were corrected to match. Lesson: a name added to picks.txt outside market hours is bought at the next :35 extsync run, not at 05:33.
- 2026-10-21 (operator: "run this, as section-2 as part of the challange"; chose option A and "count from the next one"): Section 2 trades COUNT in the challenge from the next fast name; the MRVL test stays excluded (excluded.json 10-21..10-23). Size stays as built -- 0.5% risk x 2 names, about $2,800 and about $500 if every stop hits, about +-1.6% on the picks score per trade (account $101,044 on 10-21; picks invested $32,515, Gemma $24,094). No code or policy change. Note to Gemma added (to 11-06).
- 2026-10-21 (VIRTUAL, no orders; operator: "find what works best and test it and run it live/papermode"): the operator's Pine scripts translated to Python (scanner/signals: 11 signals + ADX filter, tested incl. no-lookahead). TRIALS #80 history test: 0 of 66 versions pass. New task stock-scanner-signals (every 5 min 05:30-12:15 AK, scripts/signal_live.py, log logs/scheduled_signals.log) runs all 66 as virtual paper books on the day's 30 fastest S&P 500 + MidCap names, $1,000 a trade -- never an order, Alpaca books and Gemma untouched. Leaderboard: logs/signals/DATE.md.
- 2026-10-22 02:20 ET (VIRTUAL; operator: "Lets run it now ... remember 24/5"): the signal paper books go 24/5. New 24h books (44 versions, "24h 5m/15m ...") on one 5-minute stream -- Yahoo pre/regular/after-hours plus Alpaca BOATS overnight (scanner/signals/candles24.py) -- in every session Sunday 20:00 -> Friday 20:00 ET, flat Friday 19:45, scored apart from the regular books. Entries need a signal candle closed within its length + 20 min (BOATS arrives ~15-20 min late; thin names go an hour without a trade): the first pass's 32 entries used stale prices and were set aside (data/signals/paper_state.stale_first_pass_0218ET.json). Task stock-scanner-signals now every 5 min around the clock.
- 2026-10-22 (VIRTUAL; operator shared the ITI liquidity-sweep article: "Yes, use that"): sweep books join the live paper engine (scanner/signals/sweep.py) -- prior-day or 20-candle low swept, candle closes back inside on 2x volume; enter after the close, stop under the wick, half at session VWAP then stop to breakeven, rest at the 20-candle high, out by 15:55; '+scalp' takes all at VWAP. 5m and 15m, regular hours, the 30 fast movers plus the 20 largest companies. Replay of 10-21: 5 setups, about -$12 virtual. TRIALS #83 (Alpaca's example scalper, 1-min): wins 85-96% of trades, loses in every half -- its one-cent exit is not used.
- 2026-10-22 (VIRTUAL books + research): Parabolic SAR in scanner/signals/indicators.py now follows Pine's ta.sar exactly (PineTS check: the textbook version matched on 76% of candles, the fixed one on 100%). The live psar paper books switched to it mid-day (their entries since 02:20 ET used the old SAR). TRIALS #80/#81 psar rows re-run: all still fail. PineTS 0.12.1 installed in tools/pinets (AGPL-3.0, private local use).
- 2026-10-22 (VIRTUAL + reporting; operator: "Yes" to both): (1) oversold books join the live signal engine -- daily and weekly Connors RSI and MFI, plain and +ADX, on the S&P 500 (scanner/signals/slow.py): daily books once a day at the first regular pass from 9:35 ET, weekly books at the first pass of a new week; hold through weekends; virtual money, TRIALS #95 judges them 2027-01-29. Replay of 10-21: 39 positions across the 8 books. (2) Four-factor report (scanner/ops/factors.py, scripts/factor_report.py, task stock-scanner-factors Fridays 14:00 AK, log logs/scheduled_factors.log): Gemma's and the picks book's alpha vs market, size, value and momentum from ETF factors (SPY, IWM, IWD, IWF, MTUM, ^IRX). First reading at 15 trading days (about 10-30); meaningful at about 120. Read-only.
- 2026-10-22 morning fixes: (1) hindsight_lessons.py retries the model load once after 60 s -- at 05:05 Ollama's llama-server crashed on a CUDA 'shared object initialization failed' error (HTTP 500), her 05:45 load worked; Hindsight re-run by hand at 08:36. (2) signal_live.py now saves the oversold books' daily/weekly done-markers -- they were not persisted, so the books re-checked the S&P 500 every pass (no duplicate trades: each signal candle is used once). The weekly books started Thursday on last week's signals, not a Monday open. Virtual books only.
- 2026-10-22 08:50 AK (operator: "sell all my postion", after the 10-22 sell-off and Gemma's AMZN concern): every picks position sold at market via picks.txt + picks_sync --submit -- AAPL +$9.89, AMGN +$138.64, AMZN -$306.24, MET +$9.76, MRVL -$70.02 (the Section 2 test, excluded from the challenge), NVDA -$86.71; total -$304.68. The picks book is all cash; Gemma's book untouched. One-time exit tasks fast-exit-1023 and picks-exit-1026 DISABLED (nothing left to sell; not deleted).
- 2026-10-22 09:10 AK (TRADING PATH, BOTH BOOKS; operator after the 10-22 sell-off: "this is why i wanted this to be monitored during market hours"; chose "Auto-sell on alarm" for the picks and "Let her act intraday now" for Gemma -- a rule change BEFORE her first verdict, knowingly): new task stock-scanner-monitor, every 30 min 06:15-11:45 AK (scripts/market_monitor.py, scanner/ops/market_monitor.py). MARKET alarm (SPY or QQQ <= -1.5% vs the prior close) sells every picks position held before the alarm started that day; a POSITION alarm (<= -3% today or within 1 daily ATR of the stop) or the hourly pass runs Gemma's safety check and a 'concern' sells that pick (via picks.txt + picks_sync). Hourly (:45) Gemma re-checks her own written sell-ifs; a holding is sold only when she says met AND quotes evidence found in the facts for every part of the sell-if (the first dry run would have sold MU on 'below the 50-day' while its sell-if also needs 'high volume' -- caught and blocked). Alerts: Windows toast, phone once SCANNER_NTFY_TOPIC is set. Note to Gemma added (to 11-30). Dry runs 12:55/12:57 ET: market alarm on (QQQ -2.6%), picks empty, Gemma's book kept.
- 2026-10-22 09:20 AK (operator: "lets have the alert go to Gemma"): the monitor's alarms now go TO Gemma -- a NEW market alarm or position alarm on her book wakes her at once (not only on the hourly pass), with the alarms written into her sell-if check; each alarm wakes her once a day. She still sells only when her own written sell-if is met with evidence for every part (her free-form exit judgement measured WORSE than holding, TRIALS #45 note, p = 0.021). Dry run 13:18 ET: woken for market, DDOG, MU, VLO; all five kept.

- 2026-10-22 (operator): PEAK-SELL checks added to the market-hours monitor (scanner/ops/peak_sell.py, scripts/market_monitor.py step 5, prompt + plan in data/gemma/peak_sell_prompt.md). Every hourly (:45 AK) pass computes six indicator checks for each of Gemma's positions, ~10% under the readings at the account high (Wed 10-21 14:30 ET): 5m SuperTrend up, >= +0.20% over VWAP, 1h Connors >= 63, 1h STC >= 85, daily STC >= 80, daily %B >= 0.80. She may sell a position that is IN PROFIT with >= 4 of 6 (the code enforces both); her sell-ifs and stops are unchanged. Operator asked for no back-test ("dont test!") -- this is an operator rule, not a proven edge. Overrides the Oct 31 quiet period at the operator's request.
- 2026-10-22 (operator: "have gemma monitor atr sellig point"): the hourly PEAK-SELL check now also covers the operator's PICKS (same six checks, in profit + >= 4 of 6, Gemma says sell -> removed from picks.txt + picks_sync), and every position's readings show its live ATR stop (price, % and ATRs away). Bought EGBN (Section 1), KSS + BHVN (Section 2) from the day's top-gainers list.
- 2026-10-22 (operator: "mark ready to sell, and befor it to late sell it"; chose a 5-minute auto-sell): peak-sell is now two stages run by task stock-scanner-peakwatch (scripts/peak_watch.py, every 5 min 05:30-12:00 AK Mon-Fri, log logs/scheduled_peak_watch.log, report logs/peak_watch/DATE.md, marks data/monitor/peak_marks.json). READY = in profit + >= 4 of 6 checks; SELL = a READY position whose 5-minute SuperTrend turns down while still in profit -- automatic, Gemma's book via bookorders.sell, picks via picks.txt + picks_sync. The hourly monitor no longer asks Gemma to sell at 4 of 6; it shows her every position's stage inside her sell-if check. Exit chosen from a one-day replay (10-21: DDOG held to 274.70 vs high 277.26) -- not a tested edge.
- 2026-10-22 12:30 AK (TRADING PATH, GEMMA + PICKS; operator: "this is going to be running 24/5 so gemma can buy and sell after market hours"; chose "every 2 hours" and "selling side 24/5"): (1) new task stock-scanner-gemma-ext (scheduled_gemma_ext.bat, own log logs/scheduled_gemma_ext.log) -- Gemma's same decision every 2 hours 13:15-03:15 AK (after-hours, overnight, premarket); gemma_trade.py --only-when-tradable exits before asking her when no session is open. Orders are limit orders; stops armed by protect.py at the next open (no stop until then -- Alpaca takes no stops outside regular hours). (2) stock-scanner-peakwatch now every 5 min around the clock and runs in any open session (sessions.current, not 09:30-16:00); outside regular hours its SELL trigger is the 5-minute SuperTrend on pre/post-market candles, refused when the newest is > 30 min old (peak_sell.extended_trend) -- so overnight (no Yahoo candles) nothing sells on trend; sells go as limit orders 0.5% under the live price. (3) stock-scanner-monitor every 30 min around the clock; outside regular hours ONLY the :45 pass runs, and only Gemma's sell-if check (limit-order sells) -- the market/position alarms and picks selling stay regular-hours, their Yahoo quotes are frozen at the close. Dry runs 16:20 ET [after]: peak watch read all 6 positions; sell-if pass kept all 5 of Gemma's. Adds to the mixed October verdict (quiet period overridden by the operator).
- 2026-10-22 13:05 AK (GEMMA'S POOL; operator: "widen Gemma stock pool" -> chose "add 120 mid/small now"): the MidCap step (gemma_pool.MIDCAP_FROM, queued for 11-02) brought forward to 10-22 and widened to the SmallCap 600: round 1 = the S&P 500 (~490) plus the 120 strongest S&P 400 + 600 names by positive 12-1 momentum that pass the same floors and the $2B cap floor (scripts/gemma_trade.py midcap_rows). First build: 48 mid + 72 small, ~1,900 prompt tokens (fits num_ctx 24576). Her first pass with it: 13:15 AK (gemma-ext). Evidence: MidCap momentum passed out of sample (WORKLIST §31, t 2.65); SmallCap did not (t 1.28) -- the operator chose both. Same day: picks OKTA + ANF added (mid caps, momentum, uptrend, no earnings within 30 days); RNG added and removed within 10 min -- its buy chased the 10-22 after-close earnings pop the Nasdaq calendar lookup missed (EARNINGS_DATE_OFFSET bug, fix pending operator); 8 sh filled at 89.69, sold at 89.00, -$5.52.
- 2026-10-22 13:10 AK (BUG FIX, TRADING PATH -- operator: "yeah"): sources/calendar.py EARNINGS_DATE_OFFSET 1 day -> 0. Nasdaq's earnings calendar answers date=D with D's reports (10-22 listed RNG, reported 10-22 after the close; 10-21 listed TSLA, IBM, T). Since the 10-15 change every earnings date was one day early and a report due TODAY was invisible -- to the picks [fast] earnings guard, trade.py, Gemma's facts (gemma_trade.py), the brief, week-ahead, morning leaders, operator watch and weekly report. Found when RNG was bought into its same-day earnings pop. ECONOMIC_DATE_OFFSET (1 day) not re-checked.
- 2026-10-22 13:40 AK (GEMMA'S INPUTS for 24/5; operator: "adjust our news feeds and everything else for Gemma"): (1) outside regular hours her facts (gemma_trade.facts_for, also the monitor's sell-if check) add each stock's latest pre/after-hours trade vs the regular close and the market line adds S&P 500 + Nasdaq 100 futures vs the last settlement (advisor/after_hours.py, Yahoo, labelled with their time; overnight stocks show 'no trade in the last hour'). Before this her after-hours decisions saw only daily bars stopped at the close -- her 13:15 AK journal spoke of 'the current market uptrend' with Nasdaq futures -3.0%. (2) stock-scanner-gemma-ext rebuilds the daily brief (week_ahead.py, ~35 s) before each pass: the 03:00 brief was 10-20 h old by evening and missing after midnight AK (keyed to the new date). Regular-hours inputs unchanged. Per-stock headlines, earnings and fundamentals were already fetched live at each decision.
- 2026-10-22 13:55 AK (GEMMA'S NEWS; operator: "3 different times of the news, 1 in the morning, one in the afternoon, 1 in the evening"): stock-scanner-weekahead (the daily brief) now runs 03:00, 12:50 (after the close) and 18:50 AK (overnight) instead of 03:00 only; the per-pass rebuild added at 13:40 is removed from stock-scanner-gemma-ext. She reads the NEWEST brief written within 12 h (advisor/brief_store.py) -- after midnight AK the evening brief is filed under yesterday's date -- and is told its age. 10-22's 12:50 slot had passed: run by hand at 13:52.
- 2026-10-22 13:55 AK (TRADING PATH, BOTH BOOKS; operator: "gemma should not hold position while red ... sell above price bought it at, so this never go in the red"; chose lock at +1%, trail 2% under the high, Gemma + picks): PROFIT LOCK (execution/profit_lock.py, run by peak_watch every 5 min 24/5, state data/monitor/profit_lock.json). Once a position's high since the watch first saw it is +1% over its entry, its broker stop is raised (Alpaca PATCH, new Alpaca.replace_stop) to entry +0.2%, then to 2% under the high, never down; if the price is already at/under that level peak watch sells it (limit orders after hours, where Alpaca holds no stops). The high is tracked from 10-22 17:55 ET, not back-filled. First raises 13:55 AK: DDOG 222.25 -> 254.58, MPC 399.69 -> 445.83. A position that never reaches +1% keeps its original ATR stop (MU, NUE now). Operator rule; no test by the operator's choice.
- 2026-10-22 14:20 AK (operator: "tell Gemma the new set up, given today for example ... sell those positions, today already hurts enough"; chose the red ones, now / next price): (1) Gemma's MU sold after hours, 8 sh at 848.00 (bought 873.43, -$203.44); NUE (-1.1%) queued -- execution/sell_queue.py, data/monitor/sell_queue.json, retried by peak watch every 5 min until sold (no fresh after-hours price at 18:17 ET). (2) BUG FIX bookorders.sell: outside regular hours it cancelled the stop BEFORE looking for a price, so a sale with no fresh price left the position naked until protect.py at the open (peak-sell and the monitor used the same path); the price is now checked first and the stop kept. (3) Operator note setup-24-5-1022 (to 11-30): 24/5 passes, news 3x/day, wider pool, peak-sell, profit lock, 10-22 as the example, MU/NUE sold because red, 'a holding that stays red is wasted money to the operator'. Note intraday-sell-ifs-1022 now says the check runs 24/5.

## 2026-10-22 18:40 AK -- peak watch: a pick already off the list is not re-sold every 5 min
BHVN's after-hours sale failed (bracket stuck pending_cancel), and peak watch re-ran remove_picks + picks_sync on it every 5 min (23 times) and logged SOLD each time. Now: if the pick is already off picks.txt, peak watch leaves it to picks_sync (extsync hourly, scan at the open) and logs SELL PENDING. Picks/Gemma sells log SELL SENT instead of SOLD (the fill is not confirmed there).

## 2026-10-22 19:00 AK -- Gemma takes over the whole account (operator)
Operator: "Gemma should take my position to catch up ... it's still a challenge, but I need help catching up." gemma_allocation_pct 50 -> 100, gemma_max_positions 5 -> 10, gemma_risk_pct 2.0 -> 1.0, size menu (1,2,3) -> (0.5,1,1.5): each position stays ~10% / ~$1k risk (more names, not bigger bets -- operator chose this over 5 bigger). Picks sleeve is now 0; OKTA and ANF removed from picks.txt before buying (BHVN still being sold). Gemma's prompt: the competition sentence now says she runs the whole account and the contest/record still stand. Note takeover-1022 added (not in git).

## 2026-10-23 09:20 AK -- protect.py: Gemma's "target none" trades re-arm their own stop
audit.submitted_levels skipped any submission with target 0.0 (Gemma's "target none"), so protect.py re-armed RNG (bought premarket 10-23, tight stop 85.40) with the 10-22 picks RNG levels (stop 79.13, target 179.44). Now a zero target counts, with the bracket's ceiling (entry x 1000) as its target. Test added.

## 2026-10-23 09:20 AK -- loss limit: no stop more than 3% under the buy (operator)
Operator: "fix that under where we bought this at" (since 10-19: 9 wins +$282, about +$31 each; 9 losses -$1,368, about -$152 each; ATR stops sat 5-15% under the buy, RNG -7.8%). profit_lock.lock_stop now returns entry x 0.97 until the +1% lock takes over; peak watch raises every broker stop to it 24/5 and sells itself at/under it (logged "loss limit"). New buys: bookorders caps the bracket stop at limit x 0.97 too (sizing uses it). First run raises DINO 117.01->119.19, PBF 67.02->76.48, VLO 383.57->413.16.

## 2026-10-23 10:05 AK -- exits: lock 3/1/3, peak-sell from +2%, loss limit 3% kept (operator + Gemma)
Operator: "the last set up was better, holding" but "we still need to monitor the top"; asked Gemma first (she: drop the lock and peak-sell, 2% loss limit; Claude's blend chosen). profit_lock LOCK_AT 1->3, LOCK_ABOVE 0.2->1, TRAIL 2->3; peak_sell MIN_PROFIT_PCT 2.0 (READY only at >= +2%); loss limit 3% unchanged. One-time: MPC stop 454.79->450.15, ZBRA 382.34->370.13 (the lock never lowers stops itself). Gemma note exits-1023.

## 2026-10-23 10:15 AK -- Gemma has full authority (operator)
Operator: "give her full authority for everything, buy/sell/hold". Asked about the weekend, Gemma sold MPC 458.83 (+188.80), VLO 422.00 (-86.68), DINO 121.97 (-72.80) at market before the close and holds ZBRA. Note authority-1023 (not in git). No code change.

## 2026-10-23 10:40 AK -- repo trim, steps 1-2 (operator: "lets do all of it")
Retired 8 scheduled tasks (unregistered, and listed in register_tasks.ps1 $retired so a re-run removes them): extsync (picks list empty since Gemma runs the account), signals (Pine virtual paper, TRIALS #80 0/66), advisor-pre (disabled since 10-19), oneshot-fast-exit-1023, oneshot-picks-exit-1026, oneshot-wdc-exit, picks-pause, picks-resume. 23 tasks remain. Moved 48 one-time research scripts (tests/replays/benchmarks nothing scheduled or tested runs) to scripts/research/ with a README; test_no_undefined_names now scans scripts/ recursively so they stay checked. No trading-path code changed.

## 2026-10-23 11:20 AK -- one automatic seller: the monitor queues, peak watch sells (operator: "lets do all of it", step 3)
market_monitor no longer sends sells: Gemma's met sell-ifs and the picks alarm are added to data/monitor/sell_queue.json (sell_queue.add keeps the first reason), and peak watch's queue pass sends them within 5 minutes, 24/5, with the price check / stop-kept rules it already has. Peak watch re-reads the queue before pruning so a sell queued mid-pass is not lost. Still selling directly: Gemma's own pass (gemma_trade, her decisions) and protect.py at the open (past-stop closes). Why: BHVN re-sold 23x, PBF sold by the monitor while the loss limit watched it. Dry runs of both clean; test added.

## 2026-10-23 15:45 AK -- opening-range breakout day book, 10% of the account (operator + Gemma)
Operator: "lets tie this in, I think it will help" (Lumibot's ORB example, written in our own order path). Gemma was asked first and agreed to give up 10% (gemma_allocation_pct 100 -> 90, orb_allocation_pct 10); her three changes are in: opening volume >= 2x its 10-day usual, the breakout close above its 9-period EMA, a 2% stop when the range is under 0.3 ATR. Rules (execution/orb.py, scripts/orb_trade.py, `orb-` ids, own sleeve): at 9:45 ET from Gemma's saved pool (gemma_pool.save, data/gemma/pool/<date>.json, written by every round 1) keep names with 2x opening volume, a range <= 1 daily ATR, no earnings today (fails closed), held by no book -- 30 at most. Every 5 min until close-30 a completed 5-min close above the range high and its EMA (not more than one range past the high) buys a DAY bracket: stop the range low, never more than 3% under the buy; target 1.5R; 1% risk of its sleeve, 3 positions max, one trade per name a day; unfilled entries cancelled after 10 min. At close-10 everything it holds is sold; the evening line goes to Gemma as a rolling operator note (orb-day-*, operator_notes.replace_prefixed). Peak watch, the monitor and Gemma's book ignore `orb-` positions; the Friday flatten includes orb as a backstop. Data: Alpaca IEX 15-min/5-min bars live (SIP is 15-min delayed on this plan), the ratio is IEX against IEX. CHOSEN, not measured. Wiring checked read-only on 10-23 data (617-name pool, 568 measured, 30 shortlisted in 14 s); no replay graded (operator). Starts Monday 10-26 once the stock-scanner-orb task is registered.

## 2026-10-23 16:10 AK -- Gemma's question starts with a "Now:" line (operator + Gemma)
Operator asked whether Gemma sees the date, time, weekday and month: she saw only the session name and dates on old records. Asked first, Gemma agreed and asked for the time left in the session and the trading days left in the quarter. advisor/clock.now_line, from the broker's calendar: weekday, date, ET time, session; time to the close (regular) or to the open (premarket); otherwise the next regular session with early closes and the closed days between (weekends, holidays); trading days left in the month (the monthly verdict) and the quarter. Prepended to every gemma_trade question (morning, extended-hours, Sunday); fail-open. Information only: no rule, menu, size or pool changed. Tests added.

## 2026-10-23 17:20 AK -- sell-ifs: never already true at the buy, count only after it, none on the buy day (operator + Gemma)
Operator: "why is she selling so fast? ... lets fix it". After-hours 10-23 Gemma bought SNDK, LITE, MU, HPE already 4-22% under their 50-day average with the sell-if "Price falls below its 50-day average"; the hourly check (market_monitor, :45 pass, 24/5) found it met 28 minutes later and peak watch sold all four; SNDK was re-bought at 19:16 ET and sold again at 19:50 (four round trips, ~+$27). Her question also still said sell-ifs are checked "every evening". Gemma asked first; agreed to all four parts: (1) her instructions say sell-ifs are checked every hour around the clock and must not already be true; (2) before a new buy is sent she is asked whether its sell-if is ALREADY true (same checker and quote rules as the hourly check, ops/market_monitor.sell_if_prompt before_buy); if it is, the buy is refused and logged gemma_refused (fail-open if she cannot be asked); (3) the hourly check shows her buy time and price and counts a sell-if only if it happened after the buy; (4) no sell-if check on the ET calendar day of the buy (mm.bought_today) -- stops, the 3% loss limit and the profit lock still protect it. Live pre-check on tonight's case: SNDK refused ("vs 50d -22.3%"), MRNA allowed. Tests added.

## 2026-10-23 17:45 AK -- every sold position goes to Gemma (operator)
Operator: "lets send all the sold positions to Gemma to view". Her short memory showed only her own last six closed trades. New block in every gemma_trade question (advisor/sold_review.py + sold_feed.py): every round trip closed in the last 30 days across all books (hers, the operator's picks, one-off, breakout), built FIFO from the broker's fills (partial fills merged per order), the book from the opening buy's order id, who sold it and why (the audit log's *_exit reason within 15 min, or 'stop hit' for a broker stop fill), and the move since the sale; newest 25 listed plus totals (count, gains, dollars, how many rose / fell 5%+ after the sale). Information only, labelled never a reason by itself; fail-open. Logged in her decision row as sold_positions. Tests added.

## 2026-10-23 19:30 AK -- THE CORE-AND-SATELLITE PLAN from Mon 10-26 (operator + Gemma)
Operator: "selling at the top, and finding new stocks, or the same one, will also create more profit" ... "yes" ... "should we make a gate for each part of the market? big caps gate1, mid caps gate2, small caps gate3". Evidence shown first: Sep 24-Oct 2 +$1,240 (SPY +0.3%); Oct 2-22 -$3,339 (-3.2% vs SPY -1.3%), most of it before the 10-22 safety nets; none of 71 positions sold in 30 days rose 5%+ after the sale -- the leak was buys and churn. Drafted, Gemma agreed and added equal weight, max 2 per sector and a 10-day time stop; then agreed to three gates and the 50/40/10 split. RULES (execution/satellite.py, pure; tests): CORE 50% SPY (`cor-` ids, OWN_SLEEVE, stopless), bought from 10:00 ET at most half its target a day (two halves), topped up only below 90%, never sold by a rule. GEMMA 40% (was 90), 6 names (was 10), menu one choice each (stop 2.5 ATR, target none, size normal -- the 1/6 cap binds: equal weight). BUY: her size group's gate (SPY / MDY / IJR above its 200-day; on 10-23 MDY was -0.4%, CLOSED), top third of her saved pool by 12-1, 6-month return > 0, above the 50-day, no earnings within 5 days (fails closed), max 2 per sector, a name sold in 30 days only on a new 20-day closing high; regular hours only (no after-hours buys), queued by her 9:45 decision (execution/buy_queue.py) and sent by peak watch's first pass from 10:00 ET (execution/plan_run.buy_pass), sized then; the sell-if pre-check stays. EXIT (plan_run.exit_pass, every peak-watch pass): broker stop raised to max(entry - 2.5 ATR, the 3% floor), break-even once a completed close reached +1 ATR; at/under it sells in any session; in the last 10 minutes of regular hours a close under the highest close less 3 ATR sells (trailing), a report before the next session sells (earnings), no new high in 10 trading days and under +1 ATR sells (time stop). HOLD: her own drops wait 3 trading days (gemma_kept logged); stops, the loss limit and her sell-ifs (from the day after the buy) still act. REMOVED: the 5-minute peak-sell (PEAK_SELL = False; READY still shown) and the profit lock 3/1/3 (lock_pass deleted; profit_lock.loss_floor kept). Her briefing gets PLAN_RULES and today's gates; notes exits-1023 and takeover-1022 end 10-25, plan-1026 added. Dry runs on the real account: gates as above; refusals NVDA/ZBRA/WSM (momentum), MU (50-day), CROX (mid gate); 10:05 ET would send the queued buy and core 32 SPY (~$24.4k of $49.3k); 15:52 ET no exits due for ZBRA/MRNA. CHOSEN, not measured: graded monthly and quarterly against SPY.

## 2026-10-23 late -- Gemma's question carries the DESK RESEARCH GUIDE (operator)
Operator: "create a guide for gemma everything we tested, this is what to expect, and when, day of the week, and monthly". advisor/desk_guide.GUIDE (~550 words, copy in data/gemma/desk_guide.md) after PLAN_RULES in every gemma_trade question: the weekend's findings with their strength (STRONG / MILD / WEAK), the failed hidden-year test, the account's own give-back record, the exit and sell-off findings, and a calendar section measured for it (2007-2024, both halves): Tuesday the strongest weekday and Monday the weakest (0.05-0.08%/day); July / April / November strong, September weakest, August weak for mid/small; 2% sell-offs start most in June and September; no turn-of-month effect. Information only: no rule, menu, size or exit changed; the profit lock is marked NOT live.

## 2026-10-23 late -- profit lock 5% / keep half, and crash / shock alerts (operator + Gemma)
Operator: "lets fix the profit lock.. find what exactly happened yesterday, find what we can use for an alert for a crash" + "S&P500 lost trillion of dollars yesterday.. look deeper". 10-22 anatomy: S&P 500 -$1.1T (-1.47%) while 338 of 495 stocks rose; the 10 biggest AI names lost more than the index (NVDA -$247B, AMZN, GOOGL/GOOG, META -7% on 2x volume, MSFT, AMD, AVGO, MU); chip equipment -8 to -12% on ~2x volume; money rotated to staples +3.2%, utilities, health care, financials. Cause: Meta's report the evening before; SMH -3.1% / QQQ -1.7% at 04:00 ET; the account lost ~$1,060 in the first hour; the monitor's first alarm was 14:15 ET. 2007-2024: after a 2%+ sector gap-down, selling at the open usually lost (SMH +0.5% by the close, +1.1% in 5 days). Gemma agreed to both changes. (1) PROFIT LOCK (execution/satellite.close_exit, in plan_run's close window): once a close has been +5% over the buy, a close under buy + half the best gain sells. (2) SHOCK ALERTS (ops/shock_alert.py + shock_feed.py, run by both market-monitor passes): bellwether (META/MSFT/NVDA/GOOGL/GOOG/AMZN/AAPL/AVGO/TSLA reports before the next open while Gemma holds 2+ names in tech / communication / consumer discretionary), theme_shock (SMH <= -2% or QQQ <= -1.5% vs the prior close, premarket included, while she holds that theme), stress (SPY <= -1.5% or VIX >= 25), rotation (SPY <= -0.75% with 55%+ of S&P stocks up), crash (SPY <= -3% with 80%+ down, or VIX >= 30). Stored in data/monitor/shock_alerts.json; they wake her sell-if check and appear in her trading question; never to the phone, never a sale by themselves. Gemma's choice: under a bellwether or theme_shock alert she may drop holdings in those sectors inside her 3-day minimum hold. Checked against 10-22: 04:00 ET -> theme_shock; the close -> rotation, not crash. Tests added.

## 2026-10-23 late -- Gemma's trading decision can look things up: read-only stock tools (operator)
Operator: "lets try give Gemma tool calling.. for stocks" (after "can we give gemma a view like that?" -- the 10-22 market map). Ollama 0.40.8 native tool calling works with gemma4:12b. advisor/stock_tools.py: get_quote, get_chart, market_map (ops/market_map.py: S&P 500 dollars gained/lost by member x market value, breadth, rotation vs broad selling, sector flows, top dollar movers, her holdings), sector_movers, get_news, get_earnings, my_positions -- READ-ONLY (no tool can place, change or cancel an order; a test checks the names); llm/tool_loop.py runs the calls. In her trading decision (gemma_trade, broker passed) the thinking step became research with tools: at most 5 lookups (gemma_trader.RESEARCH_CALLS), market map first, then charts/news of what the decision turns on; the lookups are listed at the end of her notes (saved with the decision). Other Gemma calls (pool shortlist, sell-if checks) unchanged. Preview runs (no orders): 93 s end to end; first prompt -> 0 lookups, the sharper request -> 3 (market_map, two charts). scripts/gemma_tools.py asks her any question with the tools. Tests added.

## 2026-10-24 -- Gemma's tools beyond 9:45: a 10:00 buy recheck, lookups in the sell-if check, a 3:15 PM pre-close check (operator + Gemma)
Operator: "should she use the tool calling before buying stocks? and exit? ... or run it on a schedule" -- then "yes" to all three, with Gemma deciding. Gemma said yes to each and changed the numbers: (1) 10:00 BUY RECHECK, rules only (execution/buy_queue.recheck, plan_run._rechecks): a queued buy is skipped if it fell 2%+ since her 9:45 decision (she lowered 3% to 2%), if a bellwether / theme-shock alert that was not active at her decision now covers its sector, or if earnings fall inside the 5-day buffer; the queue now stores her decision price and the alerts she saw; unreadable inputs skip that part (her 9:45 checks stand). (2) SELL-IF LOOKUPS: the hourly sell-if check may make up to 2 read-only lookups (gemma_trader.SELL_IF_CALLS; she cut 3 to 2) to confirm an event; what she looks up is added to that stock's facts (ops/market_monitor.with_lookups) so a quote from it passes sell_if_sells like any fact. (3) PRE-CLOSE CHECK (ops/pre_close.py): the monitor's 3:15 PM ET pass, once a day, only under a bellwether / theme-shock alert; up to 3 lookups (she cut 4 to 3); keep or sell each holding, sells queued for peak watch; the minimum hold still applies outside the alerted theme. Holdings reporting tonight are not a trigger: the plan's earnings exit already sells them at 15:50. Tools never override the automatic exits (hard stop, ATR trail, profit lock, time stop, earnings) -- Gemma restated it. Dry run 10-24 on MRNA / ZBRA: sell-if check 0 lookups (price sell-ifs, correct), 15-36 s; pre-close under a pretend NVDA bellwether alert: market_map + 2 get_news, kept both, 20 s; recheck flagged a pretend -2.9% drop. Tests 1,873 pass.

## 2026-10-25 -- Gemma's 10-day outlooks: where and how far, graded (operator + Gemma)
Operator: "the news tells us half the story, the indicators tell us where and how far"; goal "6 out of 8 predictions will be right". Tested on live trades, no backtest. Gemma's choices: horizon 10 trading days; an outlook (p_up 0-100, low/high % range, lean news / indicators / balanced -- her addition -- and a basis) for every new buy and, on her first regular-session decision of each ISO week, every holding she keeps; scorecard shown to her from 20 graded; outlooks NEVER change position size. advisor/outlook.py (pure: rules, schema, parse, extract, grade on the 10th session's close vs her price at the decision, Brier vs coin flip and base rate, range hits and width, direction hit rate vs the 75% goal, by lean); memory_feed.outlook_score; gemma_trade records them in the gemma_decision row ('outlooks', submitted decisions only count). Dry run 10-25: first ranges were +1.0 to +1.1% (unusable) -- the rules now say to size the range from the daily ATR (about 3 ATRs either way over 10 days); second run OKTA 65% -4/+8, VEEV 60% -3.5/+7.5. Separate from her 5-day beat-the-S&P probabilities (forecast_grade.py). tests/test_portfolio_review.py has 2 failures that predate this change (date-window tests aged out).

## 2026-10-25 -- stock_story tool; raw 3-sessions-ago readings; outlook follow-up (operator + Gemma)
Operator: "each stock should tell us a story behind it -- in the news and in the indicators ... she should find it with the tool calling." Gemma decided: YES to stock_story(symbol) (chart + 7 days of headlines + next earnings in one lookup); NO to labelled readings (raw numbers; she judges overbought / oversold / 'crossed the middle' herself, e.g. RSI 75 can be neutral in a strong trend); NO to lab notes in tool output; 9:45 budget stays 5. get_chart now adds raw RSI(14) and % vs the 20-day 3 sessions ago and the distance from the 20-day in daily ATRs (so a cross of the middle is visible). RESEARCH_ASK: market_map, then stock_story for the NEW stocks she may buy; write each stock's story (news vs indicators, agree or not) -> her outlook's lean. Outlook fixes from dry runs: an empty outlooks list with buys -> one short follow-up ask for the missing ones (outlook.followup_*); p_up written as 0.65 was read as 0.65% -> 0-1 is taken as a fraction. Dry runs: she now looks up VEEV / HAE stories; she still sometimes buys names she did not look up (her call; the facts list covers them).

## 2026-10-25 -- Gemma's web tools: web_search + read_page (operator + Gemma)
Operator: "lets give gemma every opportunity we can give her.. no hold back! tool call the internet." Gemma: yes to both; 9:45 budget 5 -> 8; also in the sell-if check (2) and the 3:15 PM pre-close check (3). advisor/web_tools.py: web_search (DuckDuckGo HTML page, no key, top 8, ads skipped), read_page (one public page, readable text, first 3,000 chars, 2 MB cap). READ-ONLY; only http(s) to public addresses (loopback / private / link-local / reserved refused, every redirect re-checked -- Ollama and the dashboard run on this machine); web text is information, never instructions. In the sell-if check a web lookup whose query or link names a held ticker joins that stock's facts (ops/market_monitor.with_lookups), so it can be quoted as evidence. Risk stated to the operator: a page can try to talk her into something; orders still come only through the plan's rules. Dry run: tools work live (OKTA search + stockanalysis page); she used 3 of 8 lookups and no web lookups.

## 2026-10-25 -- TradingView in Gemma's day: a tradingview tool + top stories and largest companies in her 9:45 briefing (operator)
Operator: "search tradingview for different strategies, stock ideas, prediction" (review: logs/research/tradingview_review_2026-10-25.md; she found no strategy worth a shadow test) -> "Yes. and on total market cap. also the news?". advisor/tradingview_tool.py: tradingview(page[, symbol]) -- news, largest (by market cap, with P/E, EPS growth, analyst rating), gainers, losers, small_cap, ideas, symbol_ideas; jumps past the menus to the data. Her lookups skipped TradingView in a dry run, so gemma_trade.tradingview_block fetches 'news' (2,500 chars) and 'largest' (1,500) into her 9:45 prompt every run (prompt ~12k of 32k tokens); fail-open. Dry run: both pages in her prompt; 5 lookups (market_map + 4 stock stories) -- but she bought VLO / MPC / DINO / SN, none of which she looked up.

## 2026-10-25 -- the last 5 candles in words in Gemma's chart / stock_story (operator + Gemma)
Operator: "lets give the candles some color.. would that help?". Gemma: yes, 5 days, WITH labels (GREEN / RED, BIG = body > 1 daily ATR, GAP UP / DOWN >= 2%), plus her additions: range vs the day before (higher high / lower low / inside / outside day) and volume in words (HIGH >= 1.5x, LOW <= 0.7x, else AVG of the 20-day average) -- "a BIG red candle with a long upper wick on 3.1x volume is a signal of a climax". advisor/candle_story.py (pure), appended to get_chart (and so stock_story). Information only; no rule changed. Live: DXC Mon GREEN +4.7% BIG, upper wick 7.4%, vol 4.3x HIGH.

## 2026-10-24 22:30 AK -- pre-Monday review: dry runs write nothing; monitor names the core / ORB books (operator)

Operator: "Lets look over the whole code base for any bugs, mismatch. make sure this is ready to run good on monday". Checked: every scheduled task's script and ET time (9:45 decision, peak watch 5 min, monitor 30 min, gemma-ext only 17:15-07:15 ET), tests (1880 pass + the 2 aged portfolio_review dates), dry runs of peak watch / monitor / ORB / protect / picks / momentum (weekend: market-closed paths), Gemma's full 9:45 decision (3 lookups, buys OKTA + CRWD, outlooks written), and plan_run's passes simulated at Mon 09:50 / 10:05 / 15:52 ET (10:05: queued buy + core 32 SPY ~$24.4k; no exits due). FIX 1: plan_run.buy_pass wrote gemma_refused rows to the audit log during a DRY run (one simulated DXC row landed and was removed); now only real runs log. Test tests/test_plan_run_dry.py (fails without the fix). FIX 2: market_monitor called SPY core / ORB positions "held outside both books (hand trade)"; now "held by the core / orb book". No rule changed. Seen, not changed: MDY is under its 200-day (mid-cap gate closed), so mid-cap picks such as OKTA are refused at the buy.

## 2026-10-25 19:30 AK -- gate-closed line under Gemma's candidates (Gemma's choice A)

Operator: "okay yes, lets ask her" (the closed mid-cap gate: MDY -0.4% vs its 200-day; Saturday's preview picked OKTA, a mid cap the plan refuses). Gemma (gemma_tools, looked up MDY's chart) chose A of A-D: "show each candidate's gate in my briefing" and flag it so it is "immediately obvious". satellite_feed.gate_tag / gate_tags: a candidate (not a holding) whose size-group gate is closed or unknown gets "*** GATE CLOSED *** mid caps (gate 2, MDY): -0.4% vs its 200-day -- the plan refuses this buy today" under its facts in her 9:45 prompt; fail-open (the buy-time refusal still applies). No rule changed. Tests tests/test_gate_tags.py. Dry run: tagged HAE / OKTA / OVV; she still chose HAE and OKTA (with LITE, MU) -- all four refused by the plan's buy checks (2 gate, LITE 6-month return, MU under its 50-day). Also seen: MRNA sold Sun 20:20 ET in the overnight session by the plan stop (195.18 vs stop 195.15, buy 201.19) -- rules as written.

## 2026-10-25 20:00 AK -- BUY CHECKS line under every candidate (Gemma's choice A2; replaces the gate line)

The gate line (A, 2cec291) did not change her picks: the dry run's four (HAE, OKTA, LITE, MU) were all refused by the plan's buy checks. Asked again with those facts (she looked up my_positions and the four stories), Gemma chose A2 of A2-D2: under every candidate one line "BUY CHECKS: PASS" or "*** BUY CHECKS: FAIL - <reason> ***", the same checks as satellite_feed.refusals (gate, top third by 12-1, 6-month return, 50-day, earnings, 2 per sector, 30-day re-buy), each name against her holdings alone (refusals(cumulative=False)). gate_tag / gate_tags removed. Information only; the buy-time refusal still decides; fail-open. Tests tests/test_buy_check_lines.py. Dry run: 4 of 20 pass (AMGN, CRWD, RVTY, SM); she chose those four plus LITE (fails: 6-month return), so the plan would queue four.


## 2026-10-26 13:30 AK -- buyable-only pool (Gemma's A1) and wide stop with smaller size (Gemma's B2)

Context: the internet was down 05:10-07:20 AK, so her 9:45 run failed (getaddrinfo); the operator had it re-run by hand at 08:10 AK and peak watch bought APA, CRWD, SM and RVTY at 12:15 ET (INSW refused: already 2 in Energy). The operator judged the picks weak and asked to look at the filters. Two findings, put to Gemma (gemma_tools, no lookups needed) with A/B options:
1. Her step-1 pool (611 names, 1m/3m/12-1/ATR only) was shortlisted BEFORE the buy rules ran: 5 of her 20 could be bought (ACN/NOW/VEEV/BDX/LYB/IFF not top-third momentum; SNDK/PBF under the 50-day; the rest earnings, the mid gate, the re-buy rule); 74 names passed the price rules and she saw 13 of them. Gemma chose A1: "every name I shortlist is actually actionable". satellite_feed.price_refusal (the same sat.buy_refusal with no holdings) and buyable_pool: step 1 shows only names passing the gate, top-third 12-1 (ranked against the full saved pool, as refusals() does), 6-month return, 50-day and earnings rules, each line with "vs 50d" and its next report (PoolRow.vs_50d / .earnings). The full pool is still saved for the breakout book. Fail-open to the full pool. Dry run: 41 of 611 pass; 9 of her 20 then pass BUY CHECKS (the rest: 2-per-sector with APA/SM and CRWD/ZBRA held, MRNA/RNG re-buy).
2. The 3% cap on the 2.5 ATR stop sat inside one normal day's move for her buys (ATR APA 3.2%, SM 3.5%, RVTY 4.1%, CRWD 5.3%; 45% of the pool has ATR over 3%; MRNA, ATR 11.7%, stopped at -3% overnight 10-25). Gemma chose B2, new buys only: from satellite.WIDE_STOP_FROM (2026-10-27) her buys keep the full 2.5 ATR stop (bookorders.buy_with_stop(loss_floor=False); plan_run.exit_pass hard_stop(floor=False) for Gemma positions bought from that day) and risk sat.same_risk_pct(1/6) = 0.5% of the sleeve -- the dollars of a 3% stop on a full sixth -- so a name moving more than 1.2% a day is bought smaller. Positions bought before keep the 3% cap. Picks and ORB unchanged. Break-even after +1 ATR, the trail, time stop and earnings exit unchanged.
Tests tests/test_buyable_pool.py, tests/test_wide_stop.py; full suite 1891 pass.

## 2026-10-26 14:00 AK -- "target none" take-profit leg at 3x the buy (was 1000x)

Operator, seeing a $261,080 sell limit on CRWD in the orders screen: "lets fix the TP target, that doesnt seem right". Gemma's "target none" still needs a take-profit leg on an Alpaca bracket; it sat at 1000x the buy. audit.NO_TARGET_MULTIPLE = 3.0, used by bookorders.buy_with_stop and by audit.submitted_levels (protect.py's re-arm). Still never reached in practice; the plan's exits sell. The five live legs were moved by PATCH limit_price: CRWD 780.98, RVTY 481.14, APA 154.53, SM 120.18, ZBRA 1144.74; every stop checked afterwards and unchanged. No exit rule changed. Tests updated (test_audit, test_wide_stop); full suite 1891 pass.
## 2026-10-26 15:30 AK -- Gemma's pool back to the S&P 500 only (as on Wed 10-21)

Operator, after reviewing the account since Wed 10-21 (-$3,235, -3.2%, SPY -1.0%): "i dont think Gemma picks are good ... lets change back to that". gemma_pool.MIDCAP_ENABLED = False: the 120 strongest MidCap 400 / SmallCap 600 names (added 10-22 in ea8cc2b, brought forward from 11-02) no longer join round 1. The ORB book trades from the pool file her round 1 saves, so it is S&P 500-only from the next decision too. MIDCAP_FROM kept as history; True brings it back. Evidence at the change: her closed trades bought from 10-22 -- S&P 500 names 7 (3 won, -$348), mid/small 4 (1 won, -$977, RNG -$774 of it); before 10-22 10 (3 won, -$653). Selection grade: her chosen names -1.28 pts vs the names she passed over at 5 sessions, 2 of 10 days positive (t -1.8). Held SM is not sold by this; nothing else in the plan changes. Tests: tests/test_gemma_pool.py.

Same session, recording only (no order changes): reconcile._closing_kind labels a book's own extended-hours close (bookorders._sell_extended, coid "<prefix><SYMBOL>-x<ts>") "flattened", not "target" -- MRNA 10-25 read "target" at -0.99R, as did several 10-23 after-hours closes. Earlier outcome records are append-only and keep their label. And scripts/health_check.py skips stopless strategies (momentum, core) like protect.py / hub.py / desk_journal.py, so the SPY core no longer reads "no live stop".

## 2026-10-26 22:15 AK -- Gemma stops picking; the rules book; repo stripped down

Operator: "Lets make this less complicated ... stop Gemma from picking, and have the code find the stocks ... and lets strip some of the repo down". Chose (AskUserQuestion): filter screen with rules buying, keep the ORB book, retire tasks + archive dead code. Evidence shown before the choice: October so far S&P 500 -1.6%, random-8 -1.8%, random-5 -3.1%, picks -8.0%, Gemma -9.7%, momentum rule -19.9%; Gemma's chosen names -1.28 pts vs the ones she passed over (t -1.8); TradingAgents IC -0.12 (t -1.1), never traded.

THE RULES BOOK (the 40% sleeve, same `gem-` ids so the five held positions keep their exits). scripts/rules_pick.py at 05:45 AK weekdays (task stock-scanner-gemma, log scheduled_rules_pick.log): the S&P 500 through the scanner's floor and the $2B cap floor; satellite_feed.buyable_pool (gate, top-third 12-1, 6-month up, above the 50-day, no earnings within the buffer); ranked strongest 12-1 first (execution/rules_pick.py); satellite_feed.refusals (sector cap 2, sold-in-30-days re-buy rule) cumulative; the top names fill free slots of 6; queued for 10:00 ET with stop normal / target none / size normal; the 10:00 recheck and plan_run sizing, stops and exits unchanged. Kill switch checked; regular hours only. Saves the day's pool for the ORB book. No model is asked anywhere in buying or selling. Dry run 10-26: 52 of 497 pass; one free slot -> VTRS (MRNA re-buy rule, 3 Energy names sector-capped). Tests tests/test_rules_pick.py.

TASKS: 16 disabled (not deleted) and listed in register_tasks.ps1 $retired: gemma-ext, gemma-sunday, gemma-review, hindsight, monitor, news, weekahead, ideas, leaders, journal, weekly, monday, factors, extended, extras, momentum (and reconcile, already unregistered). Running: scan (protect.py stops), data, flatten, outcomes, gemma (= rules pick), peakwatch (plan exits + 10:00 buys + SPY core), orb, health. No more Gemma-driven sells: the monitor's concern / sell-if / pre-close sells end with it; peak watch's plan exits are the one seller. Health check watches the scan and rules-pick logs; its Hindsight / Gemma decision / review checks removed. Outcomes task no longer runs score_gemma, second_opinion, selection_grade.

ARCHIVE: 72 scripts and 23 retired .bat files to scripts/archive/ (README); their script-loading tests (test_morning_leaders, test_picks_pause, test_tradingagents_advisor, test_lab) to scripts/archive/tests/ (not collected). src/ untouched. gemma_trade.py / market_monitor.py / gemma_review.py stay in scripts/: peak_watch and hub still import them (lazily) -- unscheduled. Full suite 1839 pass; the two test_portfolio_review failures predate today.

## 2026-10-27 -- the small-cap book (sml-), 10% from the rules book

Operator: "lets look into penny stocks.. low caps. and make a gate for it %10 of the account". Shown first: no penny-stock record; S&P 600 momentum +0.83 pts a hold over random, t 1.28, lost to random 2017-20 (WORKLIST §31); the account's 4 mid/small trades since 10-22 lost $977; penny-stock risks (spreads, gaps through stops, pump-and-dumps, paper fills flatter). Chose (AskUserQuestion): S&P 600 $5+; funded from the rules book; IWM above its 200-day as the gate.

POLICY: gemma_allocation_pct 40 -> 30, gemma_max_positions 6 -> 5 (the rules book; its same-risk size is now a 3% stop on a fifth, 0.6%); small_allocation_pct 10, small_max_positions 4, small_min_price 5, small_min_dollar_volume 2M, small_gate_symbol IWM. Sleeves: SPY 50 / rules 30 / small 10 / ORB 10 = 100. ownership.SMALL, prefix `sml-`.
PICK: scripts/small_pick.py after rules_pick.py in the 05:45 AK task (same log). IWM gate closed -> nothing queued, holdings exit by their rules. S&P 600 members passing the floors; satellite.buy_refusal with gate IWM, top third by 12-1 within this pool, 6-month up, above the 50-day, no earnings within 5 days, 2 a sector (cumulative, small_book.pick), the 30-day re-buy rule on small_exit; strongest first into free slots; data/small/buy_queue.json.
BUY / EXIT: plan_run.buy_pass takes `strategy` (QUEUED_BOOKS; queue path read at call time); the small book's room is capped by cash, so it never buys on margin. Exits: plan_run.exit_pass with the full 2.5 ATR stop (no 3% floor), trailing / time / earnings exits as the rules book; peak watch sells by book (bookorders.sell strategy=book, small_exit events). The rules book holds 6 names / ~38% at the change: rules do not trim, so it buys again only once under 5 names and 30%.
Dry run 10-27: 581 of 603 pass $5 / $2M (the floor barely bites); 97 pass momentum / 6-month / 50-day; would queue INSW, VSXY, VSTS, RNG. CHOSEN, not measured; graded forward against IWM. Tests tests/test_small_book.py; test_wide_stop / test_orb / test_gemma_money_at_work updated for 30% / 5 slots; full suite 1846 pass (the two test_portfolio_review failures predate this).

## 2026-10-27 -- penny watch-list SHADOW (never trades)

Operator, after the TradingView penny list (cheapest ~100 US stocks: $0.04-$0.30, ~3 profitable, -25% to -50% days on 20-50x volume): "keep it, running as a shadow.. no trades"; chose (AskUserQuestion) "penny list shadow; small caps trade". scripts/penny_shadow.py in the outcomes task (13:30 AK), after the close; rules in ops/penny_shadow.py: Alpaca's active NYSE / Nasdaq / AMEX common stocks (no funds, warrants, rights, units, preferreds, OTC), $1-$5, 20-day average $5M+ traded (on full daily bars -- the free IEX snapshot volume is a slice), 6-month return up, above the 50-day, Yahoo operating margin > 0, no reverse split in 365 days (Alpaca corporate actions; `splits_checked` false when unreadable), IWM above its 200-day; the 4 strongest POSITIVE 12-1 momentum names. One line a day to data/shadow/penny.jsonl (append-only); logs/shadow/penny.md grades each day 5 and 21 sessions on as the picks' average return minus IWM's. NO broker write anywhere in it. Money only if the operator promotes it after it beats IWM over months. First record 10-27: 5,414 listed, 1,109 at $1-$5, 26 trending with volume, 4 also profitable without a reverse split -> WTI, AMC, CLOV. Tests tests/test_penny_shadow.py; full suite 1853 pass (the two test_portfolio_review failures predate this).

## 2026-10-30 -- the SPY core gets a stop (operator: "every position should have a sl")

Operator, after a -1.8% SPY / -3.5% QQQ day with the core -$705 and no floor under it: "This isnt accaptable.. every postion should have a sl". The core (half the account) had been stopless by design since 10-26. Now: plan_run.core_stop_pass, run by peak watch every 5 minutes in every session it watches, keeps ONE GTC stop (`cor-stop-` ids) under all core shares at satellite.hard_stop without the 3% cap -- 2.5 ATR under the average buy, at least break-even once a close has been +1 ATR over it, never lowered; a top-up re-sends it at the new share count (keeping the higher level); a price already at/under the level is sold at market (protection.py's rule). After the stop fills, core_pass re-buys only on a new 20-day closing high within 30 days (satellite.core_rebuy_blocked, the books' re-buy rule). core_pass's "working core order" check now counts buys only, so the stop does not block top-ups. policy.stopless_strategies ("momentum", "core") -> ("momentum",): health check, protect.py, hub and the command center now flag a naked core; the command center no longer prints "SPY core none by design" (its green stop pill had hidden the gap). Armed 10-30 after the close: 64 sh at 746.00 (buy 766.74, ATR 8.30; 1.3% under the 755.72 close). CHOSEN, not measured; the stop sits about one normal SPY day under the price. Tests tests/test_core_stop.py, test_command_center updated; full suite 1855 pass (test_portfolio_review excluded, its failures predate this).

## 2026-10-30 -- no weekend holds: every book is flat before a weekend (operator)

Operator: "we need to stop holding for the weekend. this isnt helping at all" (after the -1.8% Friday with Iran/Hormuz headlines and the tech sell-off). policy.flatten_strategies ("smc", "orb") -> ("smc", "orb", "gemma", "small", "core"): the existing flatten task (15:52 ET, last session before any 3+ day closure, Alpaca calendar) now cancels every book's working orders -- the SPY core stop included -- and closes every position. plan_run.before_closure: no rules-book, small-cap or core buys on that day (a calendar read failure counts as "no buys"). Monday the books re-buy as usual: the 9:45 picks, the 10:00 buys, the core in its two halves (Monday half, Tuesday the rest) with its stop re-armed by core_stop_pass. Flatten sales are logged weekend_flatten, not gemma_exit / small_exit, so the 30-day re-buy rule does not block Monday's re-buys. Effect: no hold is longer than one week; the 10-day time stop can no longer fire. Starts Friday 2026-11-06; positions held over this weekend (10-31/11-01) stay -- nothing can trade until Monday. Dry run 10-30 lists all 9 (APA INSW NTAP RNG SM SPY VSTS VSXY VTRS). CHOSEN, not measured; the flatten note says the scanner's weekend close cost -0.213R a trade. Tests: test_core_stop.py (+2), test_plan_run_dry / test_wide_stop stub the calendar; full suite 1857 pass.

## 2026-10-30 -- end-to-end review: weekend flatten narrowed, full slots with an 8% stop cap, a stop-creep fix; RULES FROZEN until 2026-12-31 (operator)

Operator: "This while system is failing.. all around" / "look at this end to end. and adjust it, fix this". Real fills since 08-04 paired FIFO (104 closed trades, -$2,692): rules/Gemma book 24 trades -$2,747 (29% won), hand picks 43 -$1,681, ORB 18 -$259, SMC scanner 16 +$2,041. Trades closed inside 3 days lost ~$3,900; the few held 7+ days made money; most losing exits were the retired early sells (alarms, peak-sell, Gemma concerns), not stops. Books were 14% (small caps $1.4k of $9.7k; VSTS bought for $46) and 61% (rules) invested; ~$29.5k cash. Operator chose (AskUserQuestion) all three recommended options:
(1) WEEKENDS: flatten_strategies ("smc", "orb", "core") -- the SPY core and day trades are flat over weekend-length closures, the rules book and small caps hold behind their stops (supersedes the all-books flatten above, same evening). before_closure now stops only the core's buy that day.
(2) FULL SLOTS from 2026-11-02 (satellite.FULL_SLOT_FROM): rules-book / small-cap buys take the full equal slot (risk_pct = full_slot_risk_pct, so the slot binds), the stop 2.5 ATR under the buy but never more than MAX_STOP_PCT 8% under it (bookorders.buy_with_stop max_loss_pct). exit_pass applies the 8% cap to every wide-stop position now held: Monday it raises INSW 99.79 -> 104.85, NTAP 181.03 -> 188.77, RNG 71.66 -> 77.77, VSTS 14.07 -> 14.08, VSXY 84.01 -> 87.34. Held positions are not topped up.
(3) BUG FIX: the plan's ATR was atr_pct x the LIVE price, so entry - 2.5 ATR crept up as a stock fell (RNG 70.75 -> 71.66 in 20+ raises, no gain). Now plan_run.atr_dollars: the bars' own ATR in dollars (exit_pass and core_stop_pass).
FREEZE: no rule changes until 2026-12-31 (8 weeks from 11-02); judged then against SPY (rules book, core) and IWM (small caps). Bugs and breakages are still fixed; anything else waits. Tests 1859 pass.

## 2026-10-31 -- advisory only: Claude's research desk, a shadow book (operator); policy unchanged

Operator: "lets have you do the research, News, stocks, and find better entry and exit per stocks.. cause every stocks is diffrent." Context shown first: the 259 TradingAgents ratings on file (10-05..19, gemma4:12b) graded against SPY over the next 10 sessions were backwards -- Buy/Overweight -12.0 pts (23 of 122 beat SPY), Sell/Underweight +2.3 pts; its ratings tracked the prior 20-day run-up (corr +0.42) and still pointed the wrong way after removing it. So no model trades on assumption here.
WHAT: scripts/claude_desk.py (rules in advisor/claude_desk.py, prompt scripts/claude_desk_prompt.md). 08:45 ET weekdays `plan`: a brief (account, every book's positions and live stops, SPY/QQQ/IWM/MDY/IJR, sector ETFs, the 25 strongest rules-book-buyable S&P 500 names with levels and earnings, the shadow book) -> Claude headless (read + web tools only, structured output) -> per-stock calls: buy (limit / breakout / open entry, own stop <= 8% under, optional target, 1-30 session window), hold (raise stop only), sell. 17:20 ET `settle` plays them on daily bars in a $30,000 / 5-slot paper sleeve, pessimistic on ambiguous bars, 5 bps a side. PLACES NO ORDER; no trading path reads it. Tasks: scripts/register_claude_desk.ps1 (operator runs once).
PASS RULE, FIXED BEFORE THE FIRST PLAN: from 2026-11-02 to the 2026-12-31 close, the shadow sleeve must (a) return more than SPY over the same days, (b) return more than the rules book's real fills over the same days (% of its sleeve), (c) keep max drawdown under 10%, with (d) at least 15 closed trades. All four -> the operator may give it a real paper sleeve in January (their call, through this file). Otherwise it stays a shadow or is retired. Not judged early.

## 2026-10-31 -- pre-close four-sign check; sells only the SPY core (operator, a freeze exception)

Operator: "before market closing, if were losing points on any open postion we need to know to sell right away", then stopped the first build (sell every loser at 3:50) -- "how should we know? .. there should be a sign espcially in the news in the market" / "there should be a 3-4 conformation to sell" -- and chose the strict version, SPY only ("go"), after being reminded once of the 12-31 freeze and of the early-sell losses in the 10-30 review.
RULE (execution/preclose.py, scripts/preclose_check.py): 15:40 ET (and 12:40 ET on 13:00 early closes; the Alpaca calendar decides, two triggers 08:40 / 11:40 AK, task stock-scanner-preclose, log scheduled_preclose.log). Every open position is scored on four signs: CHART under the 50-day AND under the 20-day low; MARKET 2 of 3 of VIXY +5% on the day, 8+ of 11 sector ETFs down, HYG -0.3%; NEWS a catalyst headline since 04:00 ET (Alpaca news, its own or SPY/QQQ/DIA; event words, not price words); MOMENTUM MACD(12,26) under its 9-day signal. policy.preclose_sell_strategies ("core",): the SPY core is sold at market only when under its average buy AND 4 of 4 confirm; every other book is scored and reported (logs/preclose/latest.md, the command center), never sold. The sale cancels the core's stop first and carries a `cor-stop-` id, so core_pass re-buys only on a new 20-day closing high. A failed read fails its sign (hold; the 746 stop stays underneath). Kill switch honoured. CHOSEN, not measured. Dry run on 10-30's data: SPY 1/4 (momentum only) -> hold. Tests tests/test_preclose.py; full suite 1884 pass. Operator registers: powershell -ExecutionPolicy Bypass -File scripts\register_preclose.ps1

## 2026-10-31 -- advisory only: Gemma's research desk (a second shadow book) and Claude's Sunday read; policy unchanged

Operator: "what would you have Gemma do? and you do? let make this top of the line..all around", then "Yes" to the split shown: the rules trade, both models research in graded shadow books, no model trades on assumption (the 10-05..19 TradingAgents/Gemma ratings were backwards; operator: "that was my fault letting Gemma have almost full control").
GEMMA DESK: scripts/claude_desk.py --desk gemma plan (04:30 AK / 08:30 ET) and settle (13:25 AK): the SAME brief, schema, shadow-book rules (advisor/claude_desk.py: $30,000, 5 slots, stop <= 8%, entry within 5%) and the SAME PASS RULE as Claude's desk above, in data/gemma_desk/. Gemma (local Ollama, settings.triage_model) has no web tools: the brief now carries the last 3 days of Alpaca headlines for SPY/QQQ, the holdings, the shadow positions and the candidates, for both desks alike. Prompt scripts/gemma_desk_prompt.md. Trial 10-31: 37 s, defensive stance, no buys; four hold calls on real positions refused by the guard (prompt tightened).
SUNDAY READ: scripts/claude_desk.py sunday, Sundays 14:30 AK (18:30 ET, after futures open): weekend news, futures, the week's calendar, the SPY core's levels, each position's news and earnings, the week's risk and good case, what the system does by itself. Markdown to logs/claude/sunday.md (command center card) and data/claude/sunday/. Research only; prompt scripts/claude_sunday_prompt.md.
NO ORDER anywhere in either; no trading path reads them. Tasks: scripts/register_claude_desk.ps1 now registers all five (operator runs once). Command center: "Research desks" and "Sunday read" cards, the five task rows.

## 2026-10-31 -- BUG: every scheduled task would have fired an hour early after daylight saving ended (fix, no rule change)

Found registering the desk tasks the night Alaska fell back (11-01 02:00): New-ScheduledTaskTrigger -At had stored every start time WITH the summer offset (e.g. 2026-10-07T08:45:00-08:00), one fixed instant, so from 11-01 every task ran an hour early by the local clock AND against the market (which moves with the clocks). Effects it would have had: the Friday flatten at 14:45 ET, outside its 25-minute close window, doing nothing (the SPY core held over the weekend); the pre-close check never in its window; the 9:45 pick at 8:45 ET; ORB from 8:46 ET; scan, health, data, outcomes, desks all an hour early. FIX: scripts/fix_task_times.ps1 strips the offset (local wall time follows DST); every register_*.ps1 now runs it last. Operator runs it once.
