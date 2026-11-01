# Quant-desk

A paper-trading system for US stocks that runs itself on one Windows PC, in one Alpaca
**paper** account. Rules decide every pick, price, size, stop and sale. No AI model places
or chooses a trade.

*(The folder, the Python package `scanner` and the `stock-scanner-*` task names keep their
old names on purpose: every task and path points at them. The README as it stood until
2026-10-27, with the full history of Gemma's book, the sweep scanner and the research, is in
`docs/history/README-2026-10-27.md`.)*

## Read this first

**Paper money, and nothing here is proven yet.** Each part below says what it rests on:

- **tested**: held up on data it was not built from
- **chosen**: a reasonable rule, not tested
- **weak**: tested and only marginal
- **failed**: tested and did not hold up

| Part | Evidence |
|---|---|
| SPY core | **tested** by everyone: buy-and-hold S&P paid ~11%/yr since 2007. In October 2026 it beat every picking method here. |
| Rules book (picks by momentum + buy checks) | **chosen**. Momentum on the S&P 500 beat random picks 2016-26 (t 1.49) but was flat once survivorship was removed for 2000-15. |
| Small-cap book | **weak**. S&P 600 momentum beat random by +0.83 pts a 3-month hold (t 1.28) and lost to it 2017-20. |
| ORB day book | **chosen**. Not tested here. |
| Pre-close check (SPY core) | **chosen**. Sells the core only when it is losing and all four signs confirm. Not tested. |
| Penny watch-list | **shadow only**: records what it would buy, never trades. |
| Claude / Gemma research desks | **shadow only**: daily per-stock calls played in paper books of their own, graded, never order. |
| Gemma (local AI) picking | **failed** here: her chosen names did worse than the ones she passed over (-1.28 pts, t -1.8). Retired 2026-10-26. |

`FREEZE.md` holds every rule change with its reason and evidence, newest at the bottom.
`WORKLIST.md` and `TRIALS.md` hold the ideas already tested. Read them before testing a new
one, because most ideas have been tried.

## The account

```
SPY core        50%   SPY, bought in two halves, topped up below 90% of target.
                      One GTC stop at 2.5 ATR under the buy (from 10-30); re-bought only on a new 20-day high.
Rules book      30%   up to 5 S&P 500 names (`gem-` order ids)
Small caps      10%   up to 4 S&P 600 names, $5+ (`sml-`), only while IWM is above its 200-day
ORB day book    10%   opening-range breakouts (`orb-`), everything sold 10 min before the close
Penny shadow     0%   records its would-buys after the close; never orders
```

**Weekends (from 2026-11-06):** the SPY core (and ORB) is sold at 15:52 ET on the last session before
a weekend or longer closure and re-bought on the next; the rules book and small caps hold behind their stops.
**Sizing (from 2026-11-02):** each rules-book / small-cap buy takes its full equal slot; its stop is 2.5 ATR
under the buy, never more than 8% under it. **Rules frozen until 2026-12-31** (FREEZE.md).

Each order's `client_order_id` prefix says which book owns it, and every rule acts only on
its own book.

### How a stock is picked (rules book and small caps)

At 9:45 ET, each picker:

1. builds its universe:
   - rules book: S&P 500, the scanner's price / range / volume floors, $2B+ market cap
   - small caps: S&P 600, $5+, $2M+ traded a day
2. keeps the names that pass every buy check:
   - gate ETF above its 200-day (SPY for the rules book, IWM for small caps)
   - top third of the pool by 12-1 momentum
   - 6-month return up
   - above the 50-day average
   - no earnings within 5 days
   - no more than 2 a sector
   - a name sold in the last 30 days is re-bought only on a new 20-day high
3. ranks the survivors strongest 12-1 momentum first and queues the top ones for the book's
   free slots.

At 10:00 ET, peak watch rechecks each queued name and buys it. The recheck skips a name
that fell 2% since 9:45 or now has earnings inside the buffer. From 2026-11-02 each buy
takes its full equal slot. Before then, a volatile name was bought smaller so that its stop lost
no more than a 3% stop would on a full slot, which left the books mostly in cash.
The small-cap book buys with cash only, never on margin.

### How a position is sold (peak watch every 5 minutes, plus the pre-close check)

- **Stop**: 2.5 ATR under the buy, held at the broker. From 2026-11-02 it is never more than
  8% under the buy. It moves to break-even once the stock is up 1 ATR. Positions bought
  before 10-27 keep a 3% cap.
- **Close-based exits** (last 10 minutes of the session):
  - before an earnings report
  - profit lock: once up 5%, a close that gives back half the best gain
  - trailing exit: a close under the highest close less 3 ATR
  - time stop: no new high in 10 trading days and under +1 ATR
- **ORB**: its own stop (the opening-range low) and a 1.5R target. Everything is sold at
  close-10.
- **SPY core**: one GTC stop (`cor-stop-`) under all its shares. It uses the books' rule: 2.5 ATR,
  then break-even from +1 ATR, and it is never lowered. After a stop-out the core is re-bought
  only on a new 20-day closing high.
- **Pre-close check** (`preclose_check.py`, 25 min before the close): scores every position on
  four signs:
  - **chart**: under the 50-day average and under the 20-day low
  - **market**: 2 of 3 of fear (VIXY +5%), breadth (8 of 11 sectors down) and credit (HYG -0.3%)
  - **news**: an event headline since 04:00 ET, such as a strike, a tariff or a downgrade
  - **momentum**: MACD under its signal line

  It sells **only the SPY core**, only when it is losing, and only when all four signs confirm.
  It never sells on missing data. Every other position is scored and reported, never sold.
  Report: `logs/preclose/latest.md`.

The rules never trim a book that is over its size. It simply buys nothing until it's back
under.

## What keeps it safe

| Layer | What it does |
|---|---|
| Broker-side stops | Every position has a live stop at Alpaca, so it holds even when this PC or its internet is down. |
| `protect.py` | Every 30 min: a position without a live stop gets one. Since 10-30 every book has a stop, the SPY core included. |
| Daily loss stop | No new buys after a 3% day. |
| Kill switch | A file named `STOP_TRADING` in the repo root blocks every new buy. |
| Ownership | Every closer acts only on its own book's prefix. |
| Health check | 06:00 and 14:15 AK: every share covered by a stop, every task's last result, errors in the morning logs. Report in `logs/health/`. |
| Audit log | `logs/trading_audit.jsonl`: every pick, order, refusal and outcome, append-only. |

## Research desks (shadow, no orders)

The rules trade. Claude and Gemma do research and get graded, the same way the books are.

- **Desk plans** (`scripts/claude_desk.py plan --desk claude|gemma`, before the open):
  - Each desk reads one brief: the account, its own shadow book, the candidates, and the
    last 3 days of headlines.
  - It writes buy / hold / sell calls with reasons.
  - Claude can search the web. Gemma (local) uses only the brief.
- **Settle** (after the close): plays the day's calls in the desk's own paper book
  (`data/claude/book.json`, `data/gemma_desk/book.json`) and grades it against SPY and QQQ over the same days.
- **Sunday read** (Sundays 14:30 AK): Claude's written read of the weekend news and the week
  ahead, with sources, in `logs/claude/sunday.md`. It never suggests hand trades.

A desk earns a say in real money only by passing the same written test as any rule. The
command center shows both books and the Sunday read.

## The schedule (Alaska time; New York is +4 hours)

| Task | When | Runs |
|---|---|---|
| `stock-scanner-scan` | 05:33-12:03, every 30 min | `protect.py` (stops), `picks_sync.py`, `manage_positions.py` |
| `stock-scanner-gemma` | 05:45 weekdays | `rules_pick.py`, then `small_pick.py` (the name is historical) |
| `stock-scanner-orb` | 05:46-11:56 weekdays | `orb_trade.py` |
| `stock-scanner-peakwatch` | every 5 min, 24/5 | `peak_watch.py`: exits, the 10:00 buys, the SPY core |
| `stock-scanner-health` | 06:00, 14:15 | `health_check.py` |
| `stock-scanner-preclose` | 08:40, 11:40 weekdays | `preclose_check.py --submit` (acts only 25 min before the close, so early closes too) |
| `stock-scanner-flatten` | 08:45-11:52, before closures | `flatten.py` (SPY core from 11-06, ORB, the paused sweep book) |
| `stock-scanner-data` | 13:00 | intraday bars for research |
| `stock-scanner-outcomes` | 13:30 | trade outcomes, standings, the penny shadow, the broker archive |
| `stock-scanner-gemma-desk-plan` / `claude-plan` | 04:30 / 04:45 weekdays | the research desks' calls (shadow) |
| `stock-scanner-claude-settle` / `gemma-desk-settle` | 13:20 / 13:25 weekdays | play the calls in each shadow book |
| `stock-scanner-claude-sunday` | 14:30 Sundays | the Sunday read |

`scripts/register_tasks.ps1`, `register_orb.ps1`, `register_preclose.ps1` and
`register_claude_desk.ps1` register them. Each one ends by running `fix_task_times.ps1`, which
stores the start times as local wall-clock time. Without it, Task Scheduler pins them to a
fixed UTC offset, and every task runs an hour off after a daylight-saving change (bug found
2026-10-31). The Gemma / AI tasks,
news, briefs and reports retired on 2026-10-26 are disabled, not deleted; their scripts are in
`scripts/archive/`.

## Everyday commands

```powershell
scripts\run_command_center.bat                          # the desk on one HTML page, opened in the browser
.venv\Scripts\python.exe scripts\hub.py                 # one page: what ran, the account, what needs you
.venv\Scripts\python.exe scripts\health_check.py        # the health check, any time (read-only)
.venv\Scripts\python.exe scripts\rules_pick.py          # what the rules book would queue now (dry run)
.venv\Scripts\python.exe scripts\small_pick.py          # same for the small-cap book
.venv\Scripts\python.exe scripts\peak_watch.py --dry-run  # what the exits would do now
.venv\Scripts\python.exe scripts\preclose_check.py      # score every position on the four signs (dry run)
.venv\Scripts\python.exe scripts\claude_desk.py brief   # build the desks' brief; plan / settle / sunday, --desk gemma
.venv\Scripts\python.exe scripts\penny_shadow.py        # record / grade the penny shadow
.venv\Scripts\python.exe scripts\competition.py standings  # books vs the index and random picks
.venv\Scripts\python.exe scripts\protect.py             # which positions lack a stop (report)
.venv\Scripts\python.exe scripts\manual_trade.py NVDA --stop 172.50 --target 195  # preview; --submit sends
```

## Where things live

| Path | What |
|---|---|
| `src/scanner/execution/policy.py` | every rule and size, in one place |
| `src/scanner/execution/satellite.py` | the buy checks and exits |
| `src/scanner/execution/rules_pick.py`, `small_book.py`, `orb.py` | each book's picking rules |
| `src/scanner/execution/plan_run.py` | the 10:00 buys, the exits, the SPY core |
| `src/scanner/execution/preclose.py` | the pre-close check's four signs |
| `scripts/claude_desk.py`, `scripts/*_desk_prompt.md`, `scripts/claude_sunday_prompt.md` | the research desks and the Sunday read |
| `data/claude/`, `data/gemma_desk/` | each desk's calls, shadow book and grades |
| `logs/preclose/latest.md`, `logs/claude/sunday.md` | the last pre-close report, the last Sunday read |
| `src/scanner/ops/penny_shadow.py` | the penny shadow's rules |
| `data/gemma/buy_queue.json`, `data/small/buy_queue.json` | today's queued buys |
| `data/shadow/penny.jsonl`, `logs/shadow/penny.md` | penny shadow records and its grade vs IWM |
| `logs/scheduled_<task>.log` | each task's output; `scheduled_rules_pick.log` has both 9:45 picks |
| `logs/peak_watch/YYYY-MM-DD.md` | every peak watch pass |
| `logs/competition/standings.md` | the books against SPY / IWM and random picks |
| `logs/trading_audit.jsonl` | every decision and order |
| `FREEZE.md` | every rule change, why, and its evidence |
| `WORKLIST.md`, `TRIALS.md` | what has been tested, and the verdicts |

## Changing a rule

Change it in `policy.py` or the rule module, with a test. Add a dated `FREEZE.md` entry before
the first order under it, saying what changed, why, and what it rests on. Judge each book
over months against its index (SPY, or IWM for small caps), not by day.

## Setup

```powershell
uv venv --python 3.12
uv pip install -e ".[dev]"
Copy-Item .env.example .env      # set SCANNER_SEC_USER_AGENT to your email (SEC needs it)
```

Put Alpaca **paper** keys in `.env`:

```
SCANNER_ALPACA_KEY_ID=...
SCANNER_ALPACA_SECRET_KEY=...
```

This project talks only to the paper endpoint. Then register the tasks once from an
elevated PowerShell: `scripts\register_tasks.ps1`, `register_orb.ps1`, `register_preclose.ps1`
and `register_claude_desk.ps1`. The desks need Ollama running `gemma4:12b`, plus the `claude`
CLI signed in. The health
task has no register script; it runs `scripts\scheduled_health.bat` at 06:00 and 14:15 and was
set up by hand in Task Scheduler.

Tests: `.venv\Scripts\python.exe -m pytest tests -q`
