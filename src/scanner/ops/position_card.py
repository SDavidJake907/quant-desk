"""One card per open position: the numbers, the six peak readings, the headlines, the President feed.

Peak watch rewrites logs/positions/latest.md about every 30 minutes. Report only.
Nothing here sells, sizes, or moves a stop. The 15:40 four-sign check is unchanged:
its catalyst word list is reported beside the headlines, including the headlines it ignores.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path

from scanner.execution.news_gate import NewsFlag
from scanner.execution.preclose import catalyst
from scanner.models import Frozen
from scanner.ops.peak_sell import CHECKS, Readings, _fmt
from scanner.sources.index_history import IndexHistoryError, parse_members

PATH = Path("logs/positions/latest.md")
#: Written by scripts/held_news.py. Peak watch only reads it. A label never sells.
LABELS = Path("logs/positions/held_news.json")
#: Long enough that the 5-minute peak-watch task rewrites the card about every half hour.
REFRESH_S = 25 * 60
MAX_HEADLINES = 8
ENERGY = "energy"
ENERGY_WORDS = ("oil", "tanker", "iran", "hormuz")
# A campaign name. "oil and gas prices" and "oil companies halted exports" are the market, so they stay.
_CAMPAIGN_OIL = re.compile(
    r"(?<![A-Za-z])oil\s*(?:&|and)\s*gas(?:\s+industry)?(?![A-Za-z])"
    r"|(?<![A-Za-z])oil\s+company(?![A-Za-z])",
    re.IGNORECASE,
)
_MARKET_AFTER = re.compile(
    r"[\s,.:;–—-]*(?:(?:is|are|was|were|has|have|been|being)\s+){0,2}"
    r"(?:price|prices|fell|fall|falls|falling|drop|dropped|drops|dropping|"
    r"rose|rise|rises|rising|surge|surged|surges|output|production|export|exports|"
    r"futures|supply|demand|barrel|barrels|market|markets|shut|shutting|"
    r"halt|halted|halts)(?![A-Za-z])",
    re.IGNORECASE,
)
_MARKET_BEFORE = re.compile(r"(?:price|prices|crude)\s+(?:of\s+)?$", re.IGNORECASE)
_ENERGY_LABEL = {"oil": "oil", "tanker": "tanker", "iran": "Iran", "hormuz": "Hormuz"}
BOOKS = {
    "gemma": "Rules book", "small": "Small caps", "core": "Index core", "smc": "Sweep",
    "picks": "Picks", "orb": "ORB", "manual": "Manual", "momentum": "Momentum",
}


class Holding(Frozen):
    symbol: str
    book: str
    qty: float
    entry: float
    last: float
    upl: float
    upl_pct: float
    today: float
    stop: float | None = None
    atr_pct: float | None = None
    sector: str = ""
    #: The six peak-watch readings. None means this pass did not compute them.
    values: dict[str, float | None] | None = None
    #: Regular session (YYYY-MM-DD) the VWAP and 1-hour readings were taken from.
    session_day: str = ""


def due(path: Path, now: datetime, *, refresh_s: int = REFRESH_S) -> bool:
    """True when the card is missing or older than `refresh_s`."""
    try:
        age = now.timestamp() - path.stat().st_mtime
    except OSError:
        return True
    return age >= refresh_s


def sector_map(directory: Path = Path("data")) -> dict[str, str]:
    """Symbol -> GICS sector from the cached S&P 500, 400 and 600 pages. Empty when a page is missing."""
    out: dict[str, str] = {}
    for key in ("sp600", "sp400", "sp500"):
        path = directory / f"{key}_wikitext.txt"
        if not path.exists():
            continue
        try:
            members = parse_members(path.read_text(encoding="utf-8"))
        except (OSError, IndexHistoryError):
            continue
        for member in members:
            if member.symbol and member.sector:
                out[member.symbol.upper()] = member.sector.strip()
    return out


def _word(text: str, word: str, *, plural: bool = False) -> bool:
    ending = "s?" if plural else ""
    return re.search(rf"(?<![A-Za-z]){re.escape(word)}{ending}(?![A-Za-z])", text, re.I) is not None


def _without_campaign_oil(text: str) -> str:
    """Drop "Texas Oil & Gas" and "Middleton Oil Company". Keep a price, a fall, or output."""
    def keep(match: re.Match) -> str:
        before, after = text[:match.start()], text[match.end():]
        if _MARKET_BEFORE.search(before) or _MARKET_AFTER.match(after):
            return match.group(0)
        return " "
    return _CAMPAIGN_OIL.sub(keep, text)


def _energy_hit(text: str, word: str) -> bool:
    """The oil market, a tanker, Iran, or Hormuz. An oil-and-gas company name does not count."""
    if word == "oil":
        return _word(_without_campaign_oil(text), "oil")
    if word == "tanker":
        return _word(text, "tanker", plural=True)
    if word == "iran":
        return re.search(r"(?<![A-Za-z])iran(?:ian|ians)?(?![A-Za-z])", text, re.I) is not None
    return _word(text, word)


def names_symbol(text: str, symbol: str) -> bool:
    """A cashtag always counts. A bare word counts only at three letters or more.

    CI is a holding. A post that says "the CI of the matter" does not name it.
    A post that says "$CI" does.
    """
    ticker = str(symbol).strip().upper()
    if not ticker:
        return False
    if re.search(rf"(?<![A-Za-z0-9])\${re.escape(ticker)}(?![A-Za-z0-9])", text):
        return True
    if len(ticker) < 3:
        return False
    return _word(text, ticker)


def post_reason(text: str, symbol: str, sector: str) -> str | None:
    """Why a President or White House post belongs on this position, or None."""
    if _word(text, symbol):
        return "names the symbol"
    if sector.strip().lower() != ENERGY:
        return None
    hits = [word for word in ENERGY_WORDS if _energy_hit(text, word)]
    if not hits:
        return None
    return "energy book: the post says " + ", ".join(_ENERGY_LABEL[word] for word in hits)


def _money(value: float) -> str:
    sign = "-" if value < 0 else "+" if value > 0 else ""
    return f"{sign}${abs(value):,.2f}"


def _qty(qty: float) -> str:
    return str(int(qty)) if qty == int(qty) else f"{qty:.2f}"


def stop_line(last: float, stop: float | None, atr_pct: float | None) -> str:
    if not stop or not last:
        return "No live stop."
    pct = (last - stop) / last * 100
    where = f"{pct:.1f}% above it" if pct >= 0 else f"{abs(pct):.1f}% through it"
    if not atr_pct:
        return f"Stop {stop:,.2f} · {where} · daily ATR unavailable"
    return f"Stop {stop:,.2f} · {where} · {pct / atr_pct:.1f} daily ATR away"


def indicator_preface(session_day: str) -> str:
    """Which bars the six readings used. Empty when this pass has no session date."""
    if not session_day:
        return ""
    return (f"VWAP, Connors RSI, and the 1-hour Schaff are the {session_day} regular session, "
            f"measured on that session's last completed bars. "
            f"Daily Schaff and %B include the live price.")


def indicator_lines(symbol: str, values: Mapping[str, float | None] | None,
                    session_day: str = "") -> list[str]:
    if values is None:
        return ["The six readings were not computed this pass."]
    reading = Readings(symbol=symbol, pl_pct=0.0, values=dict(values))
    lines = []
    for key, met in reading.met().items():
        if met:
            flag = "MET"
        elif met is None and key == "trend_5m":
            flag = "no fresh 5-minute candle"
        elif met is None:
            flag = "missing"
        else:
            flag = "not met"
        value = reading.values.get(key)
        # Three decimals: 0.799 must not print as 0.80 and then say "not met".
        shown = f"{value:.3f}" if key == "pctb_1d" and value is not None else _fmt(key, value)
        label = CHECKS[key][0]
        if key == "vwap_5m" and session_day:
            label = f"price is at least +0.20% above the {session_day} regular-session VWAP"
        lines.append(f"{label}: now {shown} -> {flag}")
    return lines


def _headlines(heads: Sequence[str]) -> list[str]:
    shown = list(heads[:MAX_HEADLINES])
    if not heads:
        return ["No Alpaca headline tagged to this symbol since 04:00 ET."]
    matched = [h for h in heads if catalyst(h)]
    if matched:
        verdict = f"Word list matched {len(matched)} of {len(heads)}."
    else:
        verdict = (f"Word list matched none of {len(heads)}. "
                   "The 15:40 check does not count these as a catalyst.")
    extra = len(heads) - len(shown)
    more = [f"{extra} more not shown."] if extra else []
    return [verdict, *shown, *more]


def _posts_for(text_posts: Sequence[tuple[str, str, str]], symbol: str, sector: str) -> list[str]:
    """`text_posts` is (source label, when label, text)."""
    lines = []
    for source, when, text in text_posts:
        why = post_reason(text, symbol, sector)
        if why is None:
            continue
        stamp = f", {when}" if when else ""
        lines.append(f"{source}{stamp} ({why}): {text}")
    return lines


def _x_for(text_posts: Sequence[tuple[str, str]], symbol: str) -> list[str]:
    """`text_posts` is (when label, text). A post that does not name the symbol stays off."""
    lines = []
    for when, text in text_posts:
        if not names_symbol(text, symbol):
            continue
        stamp = f"{when}: " if when else ""
        lines.append(f"{stamp}{text}")
        if len(lines) == 3:
            break
    return lines


def label_lines(flag: NewsFlag) -> list[str]:
    """The open-book label for one name. A miss, and a hit, are both report-only."""
    if not flag.checked:
        return ["Not labeled. A missed label does not sell."]
    if flag.blocking:
        head = f"{flag.verdict().reason}. Report only. This does not sell."
    else:
        head = "No lawsuit, earnings date, or takeover. Report only. This does not sell."
    return [head, *list(flag.headlines[:3])]


def save_labels(path: Path, written: str, labels: Mapping[str, Sequence[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"written": written, "labels": {symbol: list(lines) for symbol, lines in labels.items()}}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    tmp.replace(path)


def load_labels(path: Path) -> tuple[str, dict[str, list[str]]] | None:
    """(when, symbol to lines). None when the file is missing or unreadable."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    found = raw.get("labels")
    if not isinstance(found, dict):
        return None
    labels: dict[str, list[str]] = {}
    for symbol, lines in found.items():
        if isinstance(symbol, str) and isinstance(lines, list):
            labels[symbol.upper()] = [str(line) for line in lines if str(line).strip()]
    return str(raw.get("written") or ""), labels


def _held_lines(labels: Mapping[str, Sequence[str]] | None, symbol: str) -> list[str]:
    if labels is None:
        return ["No company-news label yet. A label does not sell."]
    return list(labels.get(symbol.upper()) or ["Not labeled. A missed label does not sell."])


def render(*, written: datetime, holdings: Sequence[Holding],
           market: Sequence[str], headlines: Mapping[str, Sequence[str]],
           news_error: str | None, posts: Sequence[tuple[str, str, str]],
           feed_failed: int, feed_tried: int,
           held_labels: Mapping[str, Sequence[str]] | None = None,
           held_at: str = "",
           x_posts: Sequence[tuple[str, str]] = (),
           x_error: str | None = None) -> str:
    """Markdown for the command center. Headlines are listed whether or not the word list matches."""
    stamp = written.astimezone().strftime("%a %Y-%m-%d %H:%M %Z")
    lines = [
        f"Written {stamp} by peak watch. Report only: no order and no stop change.",
        "An X post does not sell. A post that names no holding is left off.",
        "",
        "## Market",
    ]
    if news_error:
        lines.append(f"Alpaca news unreadable ({news_error}).")
    elif market:
        lines.extend(f"- {h}" for h in market[:MAX_HEADLINES])
        if len(market) > MAX_HEADLINES:
            lines.append(f"- {len(market) - MAX_HEADLINES} more not shown.")
    else:
        lines.append("No SPY or QQQ headline since 04:00 ET.")
    if held_at:
        lines.extend(["", f"Company news labeled {held_at}. Report only. It does not sell."])
    if feed_tried and feed_failed == feed_tried and not posts:
        lines.extend(["", "President feed unreadable. Truth Social and the White House feeds returned nothing."])
    elif feed_failed:
        lines.extend(["", f"{feed_failed} of {feed_tried} President and White House feeds failed."])
    if not holdings:
        lines.extend(["", "No open positions."])
        return "\n".join(lines) + "\n"
    ordered = sorted(holdings, key=lambda h: (BOOKS.get(h.book, h.book), h.symbol))
    for h in ordered:
        book = BOOKS.get(h.book, h.book or "Unknown")
        sector = f" · {h.sector}" if h.sector else ""
        lines.extend([
            "",
            f"## {h.symbol} — {book}",
            (f"Last {h.last:,.2f} · entry {h.entry:,.2f} · {_qty(h.qty)} sh · "
             f"open P/L {_money(h.upl)} ({h.upl_pct:+.1f}%) · today {_money(h.today)}{sector}"),
            stop_line(h.last, h.stop, h.atr_pct),
            "",
            "### Indicators",
            *([indicator_preface(h.session_day)] if h.values is not None and h.session_day else []),
            *(f"- {line}" for line in indicator_lines(h.symbol, h.values, h.session_day)),
            "",
            "### Headlines",
        ])
        if news_error:
            lines.append("- Not read this pass.")
        else:
            lines.extend(f"- {line}" for line in _headlines(list(headlines.get(h.symbol, ()))))
        lines.extend(["", "### Company news"])
        lines.extend(f"- {line}" for line in _held_lines(held_labels, h.symbol))
        lines.extend(["", "### President and White House"])
        if feed_tried and feed_failed == feed_tried and not posts:
            lines.append("- Feed unreadable.")
        else:
            matched = _posts_for(posts, h.symbol, h.sector)
            if matched:
                lines.extend(f"- {line}" for line in matched)
            else:
                lines.append(f"- No matching post. {len(posts)} posts were read.")
        lines.extend(["", "### X"])
        if x_error:
            lines.append(f"- X feed unreadable ({x_error}).")
        else:
            matched_x = _x_for(x_posts, h.symbol)
            if matched_x:
                lines.extend(f"- {line}" for line in matched_x)
            else:
                lines.append("- No X post names this symbol.")
    return "\n".join(lines) + "\n"


def save(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def file_time(now: datetime | None = None) -> datetime:
    return now or datetime.now(timezone.utc)
