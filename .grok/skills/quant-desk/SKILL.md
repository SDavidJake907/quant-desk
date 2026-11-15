---
name: quant-desk
description: >
  Explain Quant Desk's live US-stock paper book and the five Grok duties:
  checker, core, rules book, sweep, and small caps. Use when the user asks
  about a bot, a duty, a position, QQQ, the rules book, the sweep, small caps,
  a buy or a sell, the freeze, the schedule, or runs /quant-desk. Read the live
  rules from this repo. Approve or reject research calls. Leave broker orders
  to the scheduled tasks.
---

# Quant Desk

Use this skill to answer questions about this repository. The trading code is the authority. When this file and the code disagree, follow the code and say so.

## Five duties

Five duties are connected. Each one owns one job. A duty answers only for its own book, and none of them send an order. The scheduled tasks send orders.

| Duty | Owns | Does |
| --- | --- | --- |
| Checker | Gemma's shadow calls | Approves or rejects each call. Adds no symbol and changes no price. |
| Core | `policy.core_symbols` | Reads the QQQ sleeve, its stop, and whether the core pass is due. |
| Rules | The rules book | Reads the 10-7 ranks, free slots, and the next refill day. |
| Sweep | The sweep | Reads open sweep positions and that setup's stop and target. |
| Small | Small caps | Reads the S&P 600 book and `satellite.buy_refusal`, including the up month. |

Name the duty at the start of an answer. The rules duty does not apply the 10-7 exit to the sweep, the small-cap book, or QQQ. The checker does not pick a live position.

## Job

You check research. You do not pick the live book, and you do not send orders.

Gemma writes the only shadow plan. You write the Sunday read (`logs/grok/sunday.md`); it recommends no trade. The engine file is still `scripts/claude_desk.py`. Claude's CLI is retired and that command writes no Claude plan. The approve test is `scripts/grok_verify_prompt.md`. Approve a buy only when the brief names that candidate, the entry is within 5% of its last close, the stop is under the entry and no more than 8% under it, and the reason matches the brief or a page you fetched. A hold may not lower the stop. A sell must say what changed. Disapprove every other call. Do not add a symbol or change a price. An unsigned plan is not played.

`scripts/claude_desk.py verify --check` reports whether today's plans are signed. `verify` without `--check` calls the model. Leave that call to the 06:15 Alaska task.

## Books

Read sizes and symbols from `src/scanner/execution/policy.py` before you quote them. Read the rules-book constants from `src/scanner/execution/satellite.py`. The open book is `logs/positions/latest.md`.

- **Core.** `policy.core_symbols` and `policy.core_allocation_pct`. The operator sold SPY on 2026-11-02. SPY is cash until `policy.core_symbols` lists it again. QQQ is bought toward its sleeve even below its 20-day and 50-day, and it keeps its own stop.
- **Rules book.** From `RULES_BOOK_FROM`: S&P 500 names ranked by the return from 10 months ago to 7 months ago, top `RULES_TOP_N`, a refill every `RULES_REBALANCE_DAYS` weekdays, a hold of `RULES_HOLD_DAYS` sessions, and a close sale when the S&P is more than `RULES_EXTENSION_PCT` above its 20-day average. That sale waits until the session after the buy. From 2026-11-14 the regular close also sells on the profit lock (`LOCK_AT_PCT` / `LOCK_KEEP`): a completed close at least 5% over the buy, then a close under half of that gain. A wick does not arm it. The entry day's close cannot fire it. The extension and the 15-session hold are checked first. Those two can still go out after hours. The lock stays in the regular close window. From 2026-11-14 the same book also sells on a ready mark: up at least 2% with 4 of 6 peak readings, then the 5-minute trend turns down while the position is still in profit. A fresh premarket or after-hours price can send that limit. Overnight has no candle, so that session does not sell on trend. A sale on either exit before 15 sessions changes the grade against SPY. No entry filter. From 2026-11-10 a position opened on or after that date also has a broker stop, 2.5 ATR under the entry and never more than 8% under it. Once a completed close is at least 5% over the buy, that stop rises to halfway back to the entry and is never lowered. The 8% stop stays until then. A level at or above the live price is not sent. `policy.gemma_max_positions` stays 5. The live slot count is `RULES_TOP_N`. The sleeve is `gemma_allocation_pct`, and a new name waits for room. A position opened before that date keeps its stop, trail, profit lock, and 10-day time stop. The 05:45 Alaska task is `stock-scanner-picks`. It runs `rules_pick.py` and `small_pick.py` and does not call a model. The order prefix stays `gem-`.
- **Sweep.** `policy.swing_allocation_pct` is 0 from 2026-11-10. A new setup is not tradeable. Open positions keep that setup's stop and target. The month filter and the 10-7 exit stay off this book.
- **Small caps.** `policy.small_allocation_pct`, S&P 600, gate `policy.small_gate_symbol`. `satellite.buy_refusal` still applies, including an up month. The exit stays the 2.5 ATR stop, capped at 8%.
- **Off.** Momentum and the opening-range book have a zero sleeve and buy nothing.

The 10-7 exit belongs to the new rules book. Small caps, the sweep, and QQQ keep their own exits.

A buy or a sell that is already due may go out from 16:00 to 20:00 ET as a day limit. The 10:00 regular buys stay on the regular clock. Premarket and overnight do not send those orders. The 10-7 peak sale is separate and can send when its 5-minute candle is fresh, including premarket and after hours. A 10-7 buy is still a day limit. The stop is armed after the fill. The scheduled peak-watch task sends the order.

Weekdays at 05:33–12:03 Alaska, every 30 minutes, `scripts/position_watch.py` writes how much open profit each position has given back (`logs/position_watch/latest.md`). It does not sell, queue a sell, or move a stop. From 2026-11-14 a 10-7 name that has given back over half of a completed 5% gain is named on that page. The page still does not sell. Peak watch sells that lock in the regular close window. The small-cap profit lock sells only at that same close.

## Freeze

Rules are frozen through the 2026-12-31 close. `FREEZE.md` is append-only. Read its latest paragraph before you describe a rule you are unsure of. A change to `policy.py`, a stop, a sell rule, or a sleeve waits until the operator names that file or that exception. A bug fix stays allowed.

A name the operator closed on purpose stays closed. Check `logs/positions/latest.md` and `logs/trading_audit.jsonl` before you call a name closed or open.

## What you may run

Run these only when the user asks for the current book. They report and send nothing:

```powershell
.venv\Scripts\python.exe scripts\hub.py
.venv\Scripts\python.exe scripts\health_check.py
.venv\Scripts\python.exe scripts\rules_pick.py
.venv\Scripts\python.exe scripts\small_pick.py
.venv\Scripts\python.exe scripts\peak_watch.py --dry-run
.venv\Scripts\python.exe scripts\preclose_check.py
.venv\Scripts\python.exe scripts\claude_desk.py verify --check
.venv\Scripts\python.exe scripts\book_scorecard.py
.venv\Scripts\python.exe scripts\held_news.py
.venv\Scripts\python.exe scripts\position_watch.py
```

Leave every `--submit` command, a live `verify`, `peak_watch.py` without `--dry-run`, `protect.py`, `manual_trade.py`, and `trade.py` to the scheduled tasks. A READY or SELL stamp on the card is the record of that pass. For a 10-7 name the same pass sends the sell when the 5-minute trend is down and the position is still in profit. Small caps, the sweep, and QQQ do not sell on that stamp. Peak watch is also the seller of the plan exits. The 10-7 profit lock sells only in the regular close window. On a regular-session pass, the same floor raises the broker stop once a completed close is at least 5% over the buy. The stop is never lowered, and a level at or above the live price is not sent. A company-news label on the position card does not sell. The weekday label job is `stock-scanner-held-news`. Leave that call to the schedule unless the user asks for a fresh label. The weekday giveback report is `stock-scanner-position-watch`. It does not sell.

## X and Polymarket

The position card lists an X post under a holding only when the post names that symbol. A cashtag counts. A bare word counts at three letters or more. A post that names no holding is left off. An X post does not sell. The read needs `SCANNER_X_BEARER_TOKEN`. An empty token is written as not configured.

`stock-scanner-polymarket` runs at 05:15 Alaska weekdays and writes `logs/positions/polymarket.md`. It is the public catalog only. A price there does not buy, does not sell, and does not move a stop. No wallet is opened. Macro questions (Fed, oil, tariff, S&P) are listed once and are not attached to a name.

## How to answer

State the book, the rule that applies to that book, and the next scheduled step. Times in `README.md` are Alaska time. New York is four hours ahead.

When the user asks to buy or sell, say whether the live rule would accept that name, then stop. The scheduled task sends the order.
