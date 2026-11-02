"""Immutable domain models.

Every model here is frozen. Transformations return new objects; nothing is
mutated in place.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class TradeIntent(Frozen):
    """The only shape allowed to cross from scanning into execution.

    Numeric values come from the deterministic setup and sizing code.  This
    model is deliberately broker-neutral and contains no LLM fields.
    """

    symbol: str
    action: str = Field(description="buy|hold|sell")
    strategy: str = "smc"
    trigger_at: datetime
    entry_price: float
    stop_price: float
    target_price: float
    shares: int
    risk_dollars: float
    position_dollars: float
    reward_risk: float
    paper: bool = True
    dry_run: bool = False
    client_order_id: str = ""
    #: Would the RSI 40-65 gate have passed? Recorded, never acted on —
    #: forward evidence for a filter chosen from nine candidates.
    rsi_gate_passed: bool | None = None
    #: Recorded, never filtered on. Each looked good on 86 days of data and
    #: none has enough forward evidence to act on. Logging them costs nothing
    #: and lets the question be settled later on data nobody chose them from.
    trigger_slot: str = ""       # open | morning | midday | close
    trigger_weekday: str = ""    # Mon..Fri
    #: Deterministic rank among this scan's candidates (1 = taken first).
    priority_rank: int = 0
    priority_score: float = 0.0
    #: Gemma's advisory read, recorded and compared later. It orders nothing
    #: today: acting on it would make the strategy unbacktestable and discard
    #: the only out-of-sample evidence this project has.
    llm_conviction: str = ""
    llm_concern: str = ""
    llm_driver: str = ""
    llm_rank: int = 0
    #: GICS sector. Shown and recorded, never filtered on — a sector gate is
    #: the same shape of idea as the seven context gates that made things worse.
    sector: str = ""
    #: Dollar volume per bar, and its band. The most liquid band showed the
    #: best holdout edge (+1.387R) on SIXTEEN trades, which is not a number.
    #: Recorded so the question can be asked later of a real sample.
    dollar_volume: float = 0.0
    liquidity_tier: str = ""     # 5-20M | 20-50M | over-50M


class AuditEvent(Frozen):
    """Append-only record of a decision, refusal, submission, or fill."""

    event: str
    at: datetime
    symbol: str = ""
    intent: TradeIntent | None = None
    order_id: str = ""
    request_id: str = ""
    reason: str = ""
    #: Which lifecycle stage this record belongs to: scan | watch | confirm |
    #: trade. A refusal says where it stopped.
    stage: str = ""
    #: Fingerprint of the TradingPolicy that produced this record.
    #:
    #: Without it a log is ambiguous about its own provenance. The rules here
    #: changed four times on 2026-08-06 — RSI shadow, session slots, the news
    #: gate, the earnings gate, Gemma's ranking — and the records written that
    #: day cannot say which version they ran under. Once a hundred records
    #: exist beneath drifting rules, nothing can un-mix them afterwards.
    policy: str = ""
    #: Which scan produced this record, so one run's funnel can be rebuilt.
    run_id: str = ""
    #: Event-specific data with no fixed shape: the news a gate read, the
    #: model's opinions, a run's counts. Text and labels, never a price the
    #: model produced.
    detail: dict = Field(default_factory=dict)


class NewsItem(Frozen):
    """A single raw article as ingested from a source. Never edited after birth."""

    id: str = Field(description="Stable dedup key — source GUID when available")
    source: str = Field(description="Ingestion source, e.g. 'yahoo_rss'")
    queried_ticker: str = Field(description="Ticker whose feed produced this item")
    title: str
    summary: str = ""
    url: str = ""
    published_at: datetime | None = None
    fetched_at: datetime = Field(description="Point-in-time stamp for backtests")


class TriageVerdict(Frozen):
    """Gemma 3's judgement on one item.

    Deliberately carries no numeric fields. The model never reports prices,
    percentages or ratios — those are rendered from market data downstream.
    """

    item_id: str
    role: str = Field(
        description="subject|supplier|customer|competitor|example|unrelated"
    )
    is_material: bool
    category: str = Field(
        description="earnings|guidance|mna|legal|product|analyst|macro|other"
    )
    direction: str = Field(description="positive|negative|neutral|unclear")
    deal_role: str = Field(
        default="none",
        description="target|acquirer|none — who is being bought, if anyone",
    )
    evidence_quote: str = Field(
        description="Verbatim span from title/summary justifying the verdict"
    )

    @property
    def keeps(self) -> bool:
        """Only stories genuinely ABOUT the company survive."""
        return self.role == "subject" and self.is_material


class TriagedItem(Frozen):
    """A news item paired with its verdict."""

    item: NewsItem
    verdict: TriageVerdict


class ScanReport(Frozen):
    """Result of one scan pass over a single ticker."""

    ticker: str
    company: str
    fetched: int
    after_prefilter: int
    after_roundup: int = 0
    after_dedup: int = 0
    kept: int
    items: tuple[TriagedItem, ...]
    filings: tuple[NewsItem, ...] = ()
    generated_at: datetime

    @property
    def noise_ratio(self) -> float:
        """Fraction of fetched items discarded. Sanity metric for feed quality."""
        if self.fetched == 0:
            return 0.0
        return 1.0 - (self.kept / self.fetched)
