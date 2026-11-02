"""Gemma looks back at a closed trade: what it expected, and what happened.

WHAT THIS IS FOR
At entry Gemma records a conviction, a driver and a concern. Once the trade
closes there is an answer. This asks the model to write, in plain language,
what its entry read implied, what the news since suggests happened, and what
it missed — so a person reading the trade history can see the reasoning beside
the result rather than a bare label.

WHAT IT IS NOT
It is not evidence, and it never feeds back into trading. A model asked to
explain an outcome it already knows will always produce a convincing story;
that is what language models are good at, and it is exactly why hindsight
narratives are dangerous as inputs. The judgement of whether Gemma's calls are
any good is the scoreboard: realized R grouped by the conviction it gave
BEFORE the outcome. That is arithmetic, computed here by `call_verdict`, not by
the model.

SAME GUARANTEES AS EVERY OTHER MODEL CALL
- The schema has no numeric field. The R-multiple is shown to the model as
  context and rendered on the page from the broker's fills, never from text.
- Any quote must occur verbatim in the headlines supplied, or it is dropped
  and the review is marked ungrounded.
- A model outage returns None. Nothing waits on this.
"""

from __future__ import annotations

import json

import httpx

from scanner.llm.ollama import LLMError, chat_json

DRIVERS = ("company", "sector", "market", "none")

_SCHEMA = {
    "type": "object",
    "properties": {
        "expected": {"type": "string"},
        "what_happened": {"type": "string"},
        "missed": {"type": "string"},
        "driver_in_hindsight": {"type": "string", "enum": list(DRIVERS)},
        "evidence_quote": {"type": "string"},
        "lesson": {"type": "string"},
    },
    "required": ["expected", "what_happened", "missed",
                 "driver_in_hindsight", "evidence_quote", "lesson"],
}

_PROMPT = """You are reviewing one of your own past calls on a stock trade, \
after the trade has closed. Be plain and specific. Do not flatter the call and \
do not invent news.

Company: {symbol}
How the trade ended: {outcome}

WHAT YOU SAID AT ENTRY
  conviction: {conviction}
  what you thought was driving the stock: {driver}
  your concern: {concern}
  the text you cited: {evidence}

HEADLINES YOU HAD AT ENTRY
{entry_headlines}

HEADLINES AVAILABLE NOW
{later_headlines}

Write:
- expected: one or two sentences on what your entry read implied would happen.
- what_happened: one or two sentences on what the headlines suggest actually \
moved the stock. If they do not explain it, say so plainly — price can move \
for reasons no headline records.
- missed: what, if anything, was visible at entry that you underweighted. \
"Nothing identifiable" is an acceptable answer.
- driver_in_hindsight: company, sector, market, or none.
- evidence_quote: an exact phrase copied from one of the headlines above that \
supports what_happened, or an empty string.
- lesson: one sentence a person could use when reading your next call.

Do not state prices, percentages or R values. They are shown separately."""


def call_verdict(conviction: str, r: float | None) -> str:
    """Did the call agree with the outcome? Arithmetic, not the model's opinion.

    Only strong and weak are calls. Neutral says nothing, so it can be
    neither right nor wrong.
    """
    if r is None or conviction not in ("strong", "weak"):
        return "no call"
    won = r > 0
    if conviction == "strong":
        return "right" if won else "wrong"
    return "right" if not won else "wrong"


def outcome_text(state: str, r: float | None) -> str:
    ending = {"target": "hit its target", "stop": "was stopped out",
              "closed": "was closed another way (time limit or manual)"
              }.get(state, state)
    if r is None:
        return ending
    return f"{ending}, a {'gain' if r > 0 else 'loss'} of {r:+.2f}R"


def _lines(headlines) -> str:
    items = [h for h in headlines if h]
    return "\n".join(f"  - {h}" for h in items[:10]) or "  (none recorded)"


def build_prompt(trade: dict, entry: dict, later_headlines: list[str]) -> str:
    return _PROMPT.format(
        symbol=trade["symbol"],
        outcome=outcome_text(trade["state"], trade.get("r")),
        conviction=trade.get("conviction") or "not recorded",
        driver=trade.get("driver") or "not recorded",
        concern=trade.get("concern") or "none given",
        evidence=entry.get("evidence") or "(none)",
        entry_headlines=_lines(entry.get("headlines", [])),
        later_headlines=_lines(later_headlines),
    )


def ground(review: dict, headlines: list[str]) -> dict:
    """Drop a quote that does not occur in the headlines the model was shown."""
    quote = str(review.get("evidence_quote", "")).strip()
    found = not quote or any(quote.lower() in h.lower() for h in headlines)
    driver = str(review.get("driver_in_hindsight", "none"))
    return {
        "expected": str(review.get("expected", ""))[:400],
        "what_happened": str(review.get("what_happened", ""))[:400],
        "missed": str(review.get("missed", ""))[:300],
        "driver_in_hindsight": driver if driver in DRIVERS else "none",
        "evidence_quote": quote if found else "",
        "lesson": str(review.get("lesson", ""))[:300],
        "grounded": found,
    }


async def review(trade: dict, entry: dict, later_headlines: list[str],
                 client: httpx.AsyncClient) -> dict | None:
    """One hindsight review, or None if the model is unavailable."""
    prompt = build_prompt(trade, entry, later_headlines)
    try:
        raw = await chat_json(prompt, _SCHEMA, client)
    except (LLMError, json.JSONDecodeError):
        return None
    shown = list(entry.get("headlines", [])) + list(later_headlines)
    return ground(raw, shown)
