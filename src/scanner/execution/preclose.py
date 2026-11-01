"""Before the close: four signs that a losing position should go now. Pure: decides only.

Operator 2026-10-31, after asking for an auto-sell of every loser at 3:50 and then stopping it
("how should we know? .. there should be a sign espcially in the news in the market" / "there
should be a 3-4 conformation to sell"), chose the STRICT version, SPY only ("go"):

    CHART     the price is under the 50-day AND under the 20-day low (support broken)
    MARKET    2 of 3: fear (VIXY up VIX_SPIKE_PCT), breadth (SECTORS_DOWN of 11 sectors down),
              credit (HYG down CREDIT_DROP_PCT) -- a broad sell-off, not a rotation
    NEWS      a headline today naming a catalyst (CATALYSTS), not just a price move
    MOMENTUM  MACD(12, 26) under its 9-day signal, today's price included

A position in `TradingPolicy.preclose_sell_strategies` (the SPY core) is sold only when it is
under its buy price AND all four confirm. Every other position is scored and reported, never
sold: their own stops and exits stand. On 2026-10-30 (SPY -1.8%) only momentum (and a news
headline, at most) confirms: SPY closed at 755.72 above its 750.36 20-day low, and the market
sign fails (VIXY +10.5% but 5 of 11 sectors down, HYG -0.2%: a rotation) -- so HOLD.
The thresholds are CHOSEN, not measured. scripts/preclose_check.py acts.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from scanner.execution.flatten import WORKING
from scanner.models import Frozen

VIX_SPIKE_PCT = 5.0
SECTORS_DOWN = 8
CREDIT_DROP_PCT = 0.3
SECTORS = ("XLK", "XLC", "XLY", "XLP", "XLE", "XLF", "XLV", "XLI", "XLB", "XLRE", "XLU")
MARKET = ("VIXY", "HYG", *SECTORS)

#: Events, not price words: "stocks tumble" is the move itself, not a reason for it.
CATALYSTS = ("strike", "attack", "missile", "invasion", "war ", "rate hike", "hikes rates",
             "raises rates", "tariff", "sanction", "default", "downgrade", "recession",
             "bank failure", "shutdown", "hot inflation", "inflation surge", "guidance cut",
             "cuts guidance", "profit warning", "investigation", "bankruptcy")


class Sign(Frozen):
    name: str
    ok: bool
    detail: str


class Score(Frozen):
    symbol: str
    book: str
    price: float
    entry: float
    signs: tuple[Sign, ...]
    sell: bool = False

    @property
    def losing(self) -> bool:
        return 0 < self.price < self.entry

    @property
    def confirmed(self) -> int:
        return sum(s.ok for s in self.signs)

    def line(self) -> str:
        pct = (self.price / self.entry - 1) * 100 if self.entry else 0.0
        marks = "  ".join(f"{s.name} {'YES' if s.ok else 'no'} ({s.detail})" for s in self.signs)
        verdict = "SELL" if self.sell else "hold"
        return (f"{self.symbol} [{self.book}] {pct:+.1f}% vs buy, {self.confirmed}/4 -> "
                f"{verdict}: {marks}")


def ema(xs: Sequence[float], n: int) -> list[float]:
    k, out = 2 / (n + 1), []
    e = xs[0]
    for x in xs:
        e = x * k + e * (1 - k)
        out.append(e)
    return out


def chart_sign(closes: Sequence[float], lows: Sequence[float], price: float) -> Sign:
    """`closes` / `lows`: completed sessions oldest first; `price`: now."""
    if len(closes) < 49 or len(lows) < 20:
        return Sign(name="chart", ok=False, detail="too few bars")
    sma50 = (sum(closes[-49:]) + price) / 50
    low20 = min(lows[-20:])
    ok = price < sma50 and price < low20
    return Sign(name="chart", ok=ok, detail=f"{price:.2f} vs 50d {sma50:.2f}, 20d low {low20:.2f}")


def momentum_sign(closes: Sequence[float], price: float) -> Sign:
    xs = [*closes, price]
    if len(xs) < 35:
        return Sign(name="momentum", ok=False, detail="too few bars")
    macd = [a - b for a, b in zip(ema(xs, 12), ema(xs, 26))]
    signal = ema(macd, 9)
    return Sign(name="momentum", ok=macd[-1] < signal[-1],
                detail=f"MACD {macd[-1]:+.2f} vs signal {signal[-1]:+.2f}")


def market_sign(day_pct: Mapping[str, float]) -> Sign:
    """`day_pct`: today's % change by symbol (VIXY, HYG, the 11 sector ETFs)."""
    vix = day_pct.get("VIXY")
    hyg = day_pct.get("HYG")
    down = sum(1 for s in SECTORS if day_pct.get(s, 0.0) < 0)
    fear = vix is not None and vix >= VIX_SPIKE_PCT
    broad = down >= SECTORS_DOWN
    credit = hyg is not None and hyg <= -CREDIT_DROP_PCT
    n = fear + broad + credit
    vix_s = f"{vix:+.1f}%" if vix is not None else "n/a"
    hyg_s = f"{hyg:+.2f}%" if hyg is not None else "n/a"
    return Sign(name="market", ok=n >= 2,
                detail=f"{n}/3: VIXY {vix_s}, {down}/11 sectors down, HYG {hyg_s}")


def catalyst(headline: str) -> bool:
    text = f" {headline.lower()} "
    return any(word in text for word in CATALYSTS)


def news_sign(headlines: Iterable[str]) -> Sign:
    hits = [h for h in headlines if catalyst(h)]
    if not hits:
        return Sign(name="news", ok=False, detail="no catalyst headline today")
    more = f" (+{len(hits) - 1} more)" if len(hits) > 1 else ""
    return Sign(name="news", ok=True, detail=f"'{hits[0][:90]}'{more}")


def score(*, symbol: str, book: str, price: float, entry: float, closes: Sequence[float],
          lows: Sequence[float], day_pct: Mapping[str, float], headlines: Iterable[str],
          sell_books: Iterable[str]) -> Score:
    signs = (chart_sign(closes, lows, price), market_sign(day_pct), news_sign(headlines),
             momentum_sign(closes, price))
    s = Score(symbol=symbol, book=book, price=price, entry=entry, signs=signs)
    sell = book in set(sell_books) and s.losing and s.confirmed == len(signs)
    return s.model_copy(update={"sell": sell})


def working_orders_for(orders: Iterable[Mapping[str, Any]], symbols: Iterable[str]) -> list[str]:
    """Ids of every working order (bracket legs included: status=all, nested) on `symbols`."""
    wanted = set(symbols)
    ids: list[str] = []
    for o in orders:
        for leg in (o, *(o.get("legs") or ())):
            if leg.get("symbol") in wanted and str(leg.get("status", "")).lower() in WORKING \
                    and str(leg["id"]) not in ids:
                ids.append(str(leg["id"]))
    return ids
