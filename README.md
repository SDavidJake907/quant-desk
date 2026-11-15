# Quant Desk

Quant Desk is a paper-trading system for US stocks. It runs unattended on one Windows PC against one Alpaca paper account. Rules choose every symbol, price, size, stop, and sale. No model places or selects a live order.

The working folder, the Python package (`scanner`), and the `stock-scanner-*` task names are unchanged. Scheduled tasks and paths depend on them. The README as of 2026-10-27, including the earlier history of the research books, is in `docs/history/README-2026-10-27.md`.

Nothing in this repository is an offer to trade, and none of the live books is a finished result. Each book below is marked with the evidence it actually has.

| Mark | Meaning |
|---|---|
| Tested | Held up on data it was not built from |
| Chosen | A deliberate rule, not a tested edge |
| Weak | Tested, and only marginal |
| Failed | Tested, and it did not hold up |

| Book | Evidence |
|---|---|
| Index core | Buy-and-hold of the S&P 500 is the benchmark, not a result produced here. Through October 2026 it beat every picking method in this repository. The live sleeve is QQQ only, at 25%, by operator decision on 2026-11-02. |
| Rules book | **Chosen.** From 2026-11-03 the live book ranks the S&P 500 by the return from 10 months ago to 7 months ago. On 2007–2024 that cell was about +19% a year, Sharpe 0.78, against the index at +10%, Sharpe 0.58. The hidden 2025 year was +33.7%, Sharpe 1.31. The 2026 stub through 2026-10-23 does not confirm it: Sharpe 0.81 against the index at 1.17, and a deeper drawdown. A bullish filter on the same cell was worse on 2007–2024 and on 2025, so the buy stays the rank. The older twelve-month book beat random picks from 2016 to 2026 (t 1.49) and was flat for 2000–2015 once survivorship was removed. |
| Small-cap book | **Weak.** S&P 600 momentum beat random by 0.83 points over a three-month hold (t 1.28) and lost to it from 2017 to 2020. |
| Sweep scanner | **Failed** on 2018–2024 data it had not seen (−0.04R per trade). Its paper ghost made +14.1R in 28 trades from 7 October to 30 October 2026. New buys were on from 2026-11-02 and off again from 2026-11-10. Open positions keep their stops. |
| Opening-range book | **Off** from 2026-11-02. Eighteen trades, six winners, −$259. |
| Pre-close check | **Chosen.** Sells the index core only when the position is losing and all four signs agree. Not tested. |
| Penny list | **Shadow.** Records what it would buy. It does not order. |
| Acceleration study | **Shadow.** Records whether each rules-book candidate's momentum is speeding up or slowing down. Graded after 21 sessions. It does not order. |
| Claude and Gemma desks | **Shadow.** Daily calls are played in their own paper books and graded. Grok must approve a call before it is played. Grok keeps no book. |
| Gemma as a live picker | **Failed.** Names she chose did worse than names she passed over (−1.28 points, t −1.8). Retired 2026-10-26. The rules book replaced her. |

`FREEZE.md` records every rule change, with the reason and the evidence, newest at the bottom. `WORKLIST.md` and `TRIALS.md` record ideas already tested. Read them before testing another one.

Trading rules are frozen through the close of 2026-12-31.

## Feeds

Scans read the Yahoo daily cache. Orders go to the Alpaca paper account. The candidates-table close stays the Yahoo close. Nasdaq is the earnings page and the market-cap file. A name with no market cap stays in the pool. The $2 billion floor is unchanged. A 10-7 name is not sold because a report date appears.

## Capital

| Sleeve | Share | What it holds |
|---|---|---|
| Index core | 25% | QQQ only, since the sale of SPY on 2026-11-02. Bought from 10:00 ET in two daily halves, and only while the position is below 90% of its target. |
| Unassigned | 45% | The former SPY allocation, plus the sweep sleeve from 2026-11-10. No book buys it until it is assigned. |
| Rules book | 20% | Up to ten S&P 500 names. Order ids use the `gem-` prefix. |
| Small caps | 10% | Up to four S&P 600 names priced at $5 or more. Order ids use the `sml-` prefix. Buys only while IWM is above its 200-day average, and only with cash. |
| Sweep | 0% | New buys off from 2026-11-10. Open positions keep their stops. Order ids use the `smc-` prefix. |
| Opening-range book | 0% | Off from 2026-11-02. Its share went to the sweep, and that sleeve no longer buys. Any exit logic still runs. |
| Penny shadow | 0% | Records hypothetical buys after the close. |

From 2026-11-06, the index core, the sweep, and any opening-range position are sold at 15:52 ET on the last session before a weekend or a longer closure, and bought back on the next session. The rules book and the small-cap book stay open. Positions opened before 2026-11-03, and every small-cap position, stay behind their stops.

Each order's `client_order_id` prefix identifies its book. Every exit acts only on its own book.

## Entries

### Rules book

At 9:45 ET the book builds the S&P 500, after the scanner's price, range, and volume floors and a $2 billion market-cap floor. It ranks that pool by the return from 10 months ago to 7 months ago (210 sessions back to 147 sessions back). A name without that history is left out. There is no trend filter, no earnings filter, and no sector cap on the way in. On 2026-11-14 that same cell was checked with one bullish filter at a time: the close above its 20-day, 50-day, or 200-day average, the 50-day above the 200-day, a 10-day average above the 50-day, a daily SuperTrend(10, 3), MACD above its signal, a parabolic SAR, and the S&P above its 200-day. Each filter replaced a top-ranked name with a weaker one. None beat the plain rank on 2007–2024. The daily SuperTrend also lost the hidden 2025 year. The buy stays the rank.

The strongest names fill the free slots, up to ten. Each name is an equal slice of the 20% sleeve. The book looks for new names every five weekdays, starting 2026-11-03, and not on the days between. A name already held is not replaced because it fell down the ranking.

At 10:00 ET, peak watch buys the queue with a day limit. The stop is 2.5 ATR under the limit and never more than 8% under it. The buy order itself does not carry the stop; the next protect pass arms it once the shares are held. The buy still skips a name that has fallen 2% since 9:45, and a name with earnings inside five days. A queue written by the older 12-1 pick is not sent. A buy that is still due after the close can go out from 16:00 to 20:00 ET as the same day limit, marked for extended hours. Premarket and overnight do not buy.

A position opened before 2026-11-03 keeps the stop it was bought with. The sleeve is not trimmed to make room, so a new name is bought only when the 20% sleeve has room.

### Small-cap book

At 9:45 ET the book builds the S&P 600, at $5 or more, with at least $2 million of average daily dollar volume, and queues names for the open slots.

A name must pass every check:

- IWM is above its 200-day average.
- Top third of that pool by 12-1 momentum (the return from twelve months ago to one month ago).
- Positive six-month return.
- Price above the 50-day average.
- Up over the last month (the month that 12-1 momentum skips). Each name is checked on its own.
- No earnings within five days.
- No more than two names in the same sector.
- A name sold in the last 30 days is eligible again only on a new 20-day closing high.

Survivors are ranked by 12-1 momentum. The strongest names fill the free slots.

At 10:00 ET, peak watch buys the queue. It skips a name that has fallen 2% since 9:45, or that now has earnings inside the five-day buffer. From 2026-11-02 each buy takes its full equal slot. The stop is 2.5 daily ATRs under the entry and is never more than 8% under the entry. Buys use cash only. A buy that is still due after the close can go out from 16:00 to 20:00 ET as a day limit. Its stop is armed at the next regular session. Premarket and overnight do not buy.

### Index core

QQQ is bought toward 25% of equity. The order is a limit. A position already within 90% of its target is left alone. Otherwise the buy is the gap to the target, capped at half the target on that day. One GTC stop covers the shares, placed 2.5 ATRs under the buy. The stop is raised to break-even after a gain of 1 ATR and is never lowered. After a stop-out, QQQ is bought again only on a new 20-day closing high.

An index held above 1.25 times its target is sold down to the target. That is the only automatic trim in the system. A core buy, trim, or stop sale that is still due can go out from 16:00 to 20:00 ET as a limit. Premarket and overnight do not send those orders.

### Sweep

`scripts/trade.py` scans the S&P 500 on 30-minute bars during regular hours. The setup, in `src/scanner/screens/smc.py`, has four steps:

1. A swing low trades under the prior swing low.
2. Price breaks the swing high before that low.
3. A higher low forms.
4. Price breaks that high. The close of that bar is the entry.

The stop is the higher low. The target is the high the sweep traded through. A setup must also clear the price, range, and dollar-volume floors, offer at least 1.5 reward to risk, and pass the ex-dividend check and the news gate. The news gate refuses a name with a damaging headline. The sweep holds through earnings. A new trade would risk 1% of the sweep sleeve. That sleeve is 0% from 2026-11-10, so a new setup is not tradeable. Open positions keep the stop they were bought with. There are 12 slots and at most three names in one sector, counted on the sweep's own positions. It does not buy a name another book holds. A position still open after 60 bars, about five sessions, is closed. The book is flat before a weekend. `scripts/ghost_scanner.py` runs the same rules on paper beside the live book.

## Exits

Peak watch runs every five minutes. The pre-close check runs in the last 25 minutes of the session.

- **Rules book, from 2026-11-03.** A GTC stop sits 2.5 ATR under the entry and never more than 8% under it. The buy order does not carry that stop. `protect.py` arms it once the shares are held. From 2026-11-14, once a completed daily close after the entry day is at least 5% over the buy, a regular-session pass raises the stop to halfway from the entry to that best close. Today's price and a wick do not lift it. The stop is never lowered. A level at or above the live price is not sent, and a price already through the floor is not sold outside the close window. In the last ten minutes the book also sells when the S&P 500's daily bar is more than 3% above its own 20-day average, or when the position has been held 15 trading days. It does not sell on the day it was bought. A missing index bar does not sell. Those two exits can still go out from 16:00 to 20:00 ET. The same book sells at the regular close when a completed close was at least 5% over the buy and the price is back under halfway to the entry. A wick does not arm that lock, and the entry day's close cannot fire it. The extension and the 15-session hold are checked first. From 2026-11-14 the book also sells when it is up at least 2% with 4 of 6 peak readings and the 5-minute SuperTrend(10, 3) turns down while the position is still in profit. That sale can go out when the 5-minute price is fresh, including premarket and after hours. Overnight has no candle, so that session does not sell on trend. A sale before 15 sessions changes the grade against SPY. A position opened before 2026-11-03 is not on this exit.
- **Broker stop, small caps and older rules-book positions.** 2.5 ATRs under the entry, never more than 8% under it for positions bought from 2026-11-02. Positions bought before 2026-10-27 keep a 3% cap. The stop moves to break-even after a gain of 1 ATR.
- **Close-based exits for those same positions**, in the last ten minutes of the session: out before an earnings report; a profit lock, which sells after a 5% gain if the close gives back half of the best gain; a trail, which sells a close under the highest close minus 3 ATRs; a time stop, which sells when there has been no new high in 10 trading days and the gain is under 1 ATR.
- **Index core.** One GTC stop under the QQQ shares, using the same 2.5 ATR and break-even rules. It is never lowered.
- **Opening-range book.** Its stop is the opening-range low and its target is 1.5R. Anything still open is sold ten minutes before the close. The sleeve is zero, so it does not open new trades.
- **Pre-close check.** `scripts/preclose_check.py` scores every position on four signs. Chart: under the 50-day average and under the 20-day low. Market: at least two of three stress signs (VIXY up 5%, eight of eleven sectors down, HYG down 0.3%). News: an event headline since 04:00 ET, such as a strike, a tariff, or a downgrade. Momentum: MACD under its signal line. The check sells only the index core, only when that position is losing, and only when all four signs agree. It does not sell on missing data. Every other position is scored and written to `logs/preclose/latest.md`.

A book that grows past its sleeve is not trimmed, except for the index-core rule above. It simply stops buying until it is back under the sleeve.

## Scorecard

`scripts/book_scorecard.py` runs after the close, inside the outcomes task, and writes `logs/scorecard/latest.md`. The command center shows that page at the top. Every book is measured from the fresh start, the 2026-10-30 close:

> P&L = sales since the start − buys since the start + value now − value at the start

The result is shown in dollars and as a percent of the sleeve, next to the benchmark over the same days.

| Book | Benchmark |
|---|---|
| Index core | SPY and QQQ, half each |
| Rules book | SPY |
| Small caps | IWM |
| Sweep | SPY, and the ghost book, so the difference shows the cost of fills and the news gate |

Values are taken at the daily close, as the benchmarks are. One row per day is appended to `data/scorecard/daily.jsonl`. Read it over weeks and months.

The acceleration shadow records, at each 9:45 refill, every S&P 500 name with a 10-month to 7-month return, its acceleration (last month's return minus the average of the two months before), and whether it was queued (`data/shadow/accel.jsonl`). The scorecard grades that day 21 sessions later: did the names that were speeding up beat the names that were slowing down? After 30 graded days it can be considered as a tie-breaker for the rules book, and only after the freeze ends on 2026-12-31. Until then it records only.

## Controls

| Control | What it does |
|---|---|
| Broker stops | Small-cap positions, the rules book, the sweep, and QQQ have a live stop at Alpaca. A 10-7 stop is 2.5 ATR under the entry and never more than 8% under it. Once a completed close is at least 5% over the buy, peak watch raises it to halfway back to the entry and never lowers it. Momentum is the book held without a stop. |
| `protect.py` | Every 30 minutes, a position that should have a stop and does not have one is given the stop recorded on its buy. A working market sell is left alone. Momentum is left alone. |
| Daily loss limit | No new buys after a 3% down day. |
| Kill switch | A file named `STOP_TRADING` in the repository root blocks every new buy. |
| Ownership | Each closer acts only on its own order-id prefix. |
| Health check | 06:00 and 14:15 Alaska time: every share covered by a stop, every task's last result, and errors in the morning logs. The report is in `logs/health/`. |
| Audit log | `logs/trading_audit.jsonl` records every pick, order, refusal, and outcome. It is append-only. |

## Research desks

The rules trade. Gemma writes the only shadow plan, and those calls are graded. That shadow desk stays through the grade window that ends 2026-12-31. It is not replaced by a Grok book. Grok checks the calls and writes the Sunday read. Grok does not select live symbols and does not hold a book. Claude is retired. The engine file is still `scripts/claude_desk.py`. Claude's three tasks stay disabled. `data/claude/` is the old record and the schedule does not write it.

- **Plan**, before the open (`scripts/claude_desk.py --desk gemma plan`). Gemma reads one brief: the account, her shadow book, the candidates, and the last three days of headlines. She writes buy, hold, and sell calls with reasons. She runs locally and uses only the brief.
- **Verification** at 06:15 Alaska time (`scripts/claude_desk.py verify`). Grok approves or rejects each of Gemma's calls. A call that is not approved is not played. Grok cannot add a symbol or change a price. If Grok does not answer, the plan stays unsigned.
- **Settlement** after the close. Approved calls are played in Gemma's paper book (`data/gemma_desk/book.json`) and graded against SPY and QQQ over the same days. An unsigned plan is not played. At 13:15 Alaska time, `verify --check` confirms that her plan is signed and does not call the model.
- **Sunday read**, Sundays at 14:30 Alaska time. Grok writes the weekend news and the week ahead, with sources, to `logs/grok/sunday.md`. The page does not recommend trades. If Grok does not answer, no page is written.

A desk is not given live capital because it produced a plausible call. It earns that only by passing the same written test required of any rule.

## Schedule

Times are Alaska time. New York is four hours ahead.

| Task | When | What it runs |
|---|---|---|
| `stock-scanner-scan` | 05:33–12:03, every 30 min | `sell_at_open.py`, then `protect.py`, `picks_sync.py`, `manage_positions.py`, and `trade.py` |
| `stock-scanner-picks` | 05:45 weekdays | `rules_pick.py`, then `small_pick.py`. No model. The batch file is still `scheduled_gemma.bat`. |
| `stock-scanner-orb` | 05:46–11:56 weekdays | `orb_trade.py` |
| `stock-scanner-peakwatch` | Every 5 min, 24/5 | `peak_watch.py`: exits, the 10:00 buys, the index core, and a 10-7 stop raise during the regular session. A buy or sell that is already due can also go out from 16:00 to 20:00 ET as a limit. The 10-7 close lock stays in the regular close window. |
| `stock-scanner-held-news` | 07:15 and 11:15 weekdays | Labels news on the open book. Report only. No order. |
| `stock-scanner-polymarket` | 05:15 weekdays | Polymarket odds for the open book, plus Fed, oil, tariff, and S&P questions. Report only. No bet and no order. |
| `stock-scanner-position-watch` | 05:33–12:03 weekdays, every 30 min | Profit given back on each open position. Report only. No order. |
| `stock-scanner-health` | 06:00 and 14:15 | `health_check.py` |
| `stock-scanner-preclose` | 08:40 and 11:40 weekdays | `preclose_check.py --submit`. It acts only in the last 25 minutes, including early closes. |
| `stock-scanner-flatten` | 08:45–11:52, before closures | `flatten.py` for the index core (from 2026-11-06), the sweep, and the opening-range book |
| `stock-scanner-data` | 13:00 | Intraday bars for research |
| `stock-scanner-outcomes` | 13:30 | Trade outcomes, the sweep ghost, the scorecard, standings, the penny shadow, and the broker archive |
| `stock-scanner-gemma-desk-plan` | 04:30 weekdays | Gemma's shadow calls |
| `stock-scanner-grok-desk-plan` | 06:15 weekdays | Grok approves or rejects Gemma's calls |
| `stock-scanner-grok-desk-settle` | 13:15 weekdays | Confirms Gemma's plan is signed. No model call. |
| `stock-scanner-gemma-desk-settle` | 13:25 weekdays | Plays Gemma's approved calls |
| `stock-scanner-grok-sunday` | 14:30 Sundays | Grok's Sunday read |

`scripts/register_tasks.ps1`, `register_picks.ps1`, `register_orb.ps1`, `register_preclose.ps1`, `register_position_watch.ps1`, `register_polymarket.ps1`, and `register_claude_desk.ps1` register the tasks. Each script finishes with `fix_task_times.ps1`, which stores start times as local wall-clock time. Without that step, Task Scheduler pins the jobs to a fixed UTC offset and they run an hour off after a daylight-saving change. Tasks retired on 2026-10-26 are disabled, not deleted. Their scripts are in `scripts/archive/`.

## Everyday commands

```powershell
scripts\run_command_center.bat
.venv\Scripts\python.exe scripts\hub.py
.venv\Scripts\python.exe scripts\health_check.py
.venv\Scripts\python.exe scripts\rules_pick.py
.venv\Scripts\python.exe scripts\small_pick.py
.venv\Scripts\python.exe scripts\peak_watch.py --dry-run
.venv\Scripts\python.exe scripts\preclose_check.py
.venv\Scripts\python.exe scripts\claude_desk.py brief
.venv\Scripts\python.exe scripts\claude_desk.py verify --check
.venv\Scripts\python.exe scripts\book_scorecard.py
.venv\Scripts\python.exe scripts\trade.py
.venv\Scripts\python.exe scripts\penny_shadow.py
.venv\Scripts\python.exe scripts\competition.py standings
.venv\Scripts\python.exe scripts\protect.py
.venv\Scripts\python.exe scripts\manual_trade.py NVDA --stop 172.50 --target 195
```

`run_command_center.bat` opens the desk in a browser. `hub.py` is a single page of what ran, the account, and what needs attention. `health_check.py`, `rules_pick.py`, `small_pick.py`, `peak_watch.py --dry-run`, `preclose_check.py`, `trade.py`, and `protect.py` report only and send nothing. `verify --check` reports whether Gemma's plan is signed; omitting `--check` asks Grok to sign it. `manual_trade.py` previews unless `--submit` is passed.

## Repository map

| Path | Contents |
|---|---|
| `src/scanner/execution/policy.py` | Live rules and sleeve sizes |
| `src/scanner/execution/satellite.py` | Buy checks and exits |
| `src/scanner/execution/rules_pick.py`, `small_book.py`, `orb.py` | Per-book selection rules |
| `src/scanner/execution/plan_run.py` | The 10:00 buys, the exits, and the index core |
| `src/scanner/execution/preclose.py` | The four pre-close signs |
| `scripts/claude_desk.py`, `scripts/gemma_desk_prompt.md`, `scripts/grok_verify_prompt.md`, `scripts/grok_sunday_prompt.md` | Gemma's shadow plan, Grok's check, and Grok's Sunday read |
| `data/gemma_desk/`, `data/claude/` | Gemma's calls, shadow book, and grades. `data/claude/` is the retired record |
| `data/gemma/buy_queue.json`, `data/small/buy_queue.json` | The current queued buys |
| `logs/preclose/latest.md`, `logs/grok/sunday.md` | Latest pre-close report and Sunday read |
| `logs/scheduled_<task>.log` | Each task's output. `scheduled_rules_pick.log` contains both 9:45 picks. |
| `logs/scorecard/latest.md`, `data/scorecard/daily.jsonl` | Scorecard since the fresh start |
| `src/scanner/ops/book_scorecard.py`, `src/scanner/ops/accel_shadow.py` | Scorecard rules and the acceleration shadow |
| `data/shadow/accel.jsonl`, `data/shadow/penny.jsonl` | Acceleration and penny shadow records |
| `logs/trading_audit.jsonl` | Every decision and order |
| `FREEZE.md` | Every rule change, why, and the evidence |
| `WORKLIST.md`, `TRIALS.md` | Tests already run, and their verdicts |

## Changing a rule

Change the rule in `policy.py` or in the module that implements it, and add a test. Before the first order under the new rule, add a dated entry to `FREEZE.md` stating what changed, why, and what evidence it rests on. Judge a book over months against its index, SPY for the large-cap books and IWM for small caps, not by a single day.

## Setup

```powershell
uv venv --python 3.12
uv pip install -e ".[dev]"
Copy-Item .env.example .env
```

Set `SCANNER_SEC_USER_AGENT` in `.env` to an email address. The SEC requires one. Alpaca credentials must be paper keys:

```
SCANNER_ALPACA_KEY_ID=...
SCANNER_ALPACA_SECRET_KEY=...
```

The project sends orders only to the paper endpoint. Register the tasks once, from an elevated PowerShell window:

```powershell
scripts\register_tasks.ps1
scripts\register_orb.ps1
scripts\register_preclose.ps1
scripts\register_claude_desk.ps1
```

Gemma's desk needs Ollama with `gemma4:12b`. Grok's check and the Sunday read use the `grok` CLI. The health task has no register script. It runs `scripts\scheduled_health.bat` at 06:00 and 14:15 and was created by hand in Task Scheduler. Claude's three tasks are disabled.

Tests:

```powershell
.venv\Scripts\python.exe -m pytest tests -q
```
