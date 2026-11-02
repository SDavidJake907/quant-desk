"""Pre-flight checks that must pass before any scan or order path runs.

THE IDEA
A scan that starts with Ollama down, the embedding model missing, or the
broker unreachable is trading blind. For paper trading we degrade gracefully
(fail open, print loudly). For live trading the same conditions must hard-block:
a dead model must never become a silent halt, and a missing model environment
means the dedup and news gate that were measured with it are not the ones
running.

Every check here is read-only. None of them place an order, change a position,
or touch the audit log beyond recording their own result.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from scanner.config import settings
from scanner.execution.safety import load_session_equity


class PreflightError(RuntimeError):
    """Raised when a live-trading pre-condition is not met."""


@dataclass(frozen=True)
class ModelEnv:
    """The model environment that gates dedup and the news/triage path."""

    triage_model: str
    embed_model: str
    ollama_reachable: bool
    triage_present: bool
    embed_present: bool

    @property
    def complete(self) -> bool:
        return self.ollama_reachable and self.triage_present and self.embed_present


@dataclass(frozen=True)
class BrokerEnv:
    """The broker connection and account state observed at pre-flight."""

    reachable: bool
    paper: bool
    account_status: str
    equity: float
    buying_power: float


@dataclass(frozen=True)
class PreflightReport:
    """The result of every pre-flight check. `blocked` names the reason."""

    model: ModelEnv
    broker: BrokerEnv
    blocked: bool
    reason: str = ""

    @property
    def ok(self) -> bool:
        return not self.blocked


async def _model_env(client: httpx.AsyncClient) -> ModelEnv:
    """Ask Ollama which models are installed. Never raises."""
    wanted_triage = settings.triage_model
    wanted_embed = settings.embed_model
    try:
        response = await client.get(
            f"{settings.ollama_host}/api/tags", timeout=10.0
        )
        response.raise_for_status()
        names = {m.get("name", "") for m in (response.json().get("models") or [])}
        # Ollama tags may carry ":latest" suffixes or digest-qualified names;
        # match on the prefix so "gemma4:12b" matches "gemma4:12b" exactly.
        triage_present = any(
            n == wanted_triage or n.startswith(wanted_triage + ":") for n in names
        )
        embed_present = any(
            n == wanted_embed or n.startswith(wanted_embed + ":") for n in names
        )
        return ModelEnv(
            triage_model=wanted_triage,
            embed_model=wanted_embed,
            ollama_reachable=True,
            triage_present=triage_present,
            embed_present=embed_present,
        )
    except Exception:
        return ModelEnv(
            triage_model=wanted_triage,
            embed_model=wanted_embed,
            ollama_reachable=False,
            triage_present=False,
            embed_present=False,
        )


async def _broker_env(client: httpx.AsyncClient) -> BrokerEnv | None:
    """Reach Alpaca and read the account. Returns None on any failure."""
    from scanner.execution.alpaca import Alpaca, PAPER_HOST

    try:
        broker = Alpaca(
            settings.alpaca_key_id, settings.alpaca_secret_key, client
        )
        account = await broker.account()
        return BrokerEnv(
            reachable=True,
            paper=broker.host == PAPER_HOST,
            account_status=account.status,
            equity=account.equity,
            buying_power=account.buying_power,
        )
    except Exception:
        return None


async def run(
    client: httpx.AsyncClient,
    *,
    live: bool = False,
) -> PreflightReport:
    """Run every pre-flight check.

    `live` hard-blocks when the model environment is incomplete, the broker
    is not the paper endpoint, API keys are missing, or session equity is not
    configured — conditions that must never silently pass when real money is
    at stake. Paper mode reports them loudly but continues.
    """
    model = await _model_env(client)
    broker = await _broker_env(client)

    blocked = False
    reasons: list[str] = []

    # API keys must be configured. Empty keys mean every broker call fails.
    keys_configured = bool(settings.alpaca_key_id and settings.alpaca_secret_key)
    if not keys_configured:
        reasons.append("Alpaca API keys not configured (SCANNER_ALPACA_KEY_ID / SECRET_KEY)")
        if live:
            blocked = True

    # Session start equity must be configured or captured — daily-loss guard
    # cannot detect a pre-session drawdown without it.
    session_equity = settings.session_start_equity or load_session_equity()
    if live and not session_equity:
        reasons.append("SCANNER_SESSION_START_EQUITY not set — daily-loss guard cannot baseline")
        blocked = True

    if not model.complete:
        missing: list[str] = []
        if not model.ollama_reachable:
            missing.append("Ollama unreachable")
        if not model.triage_present:
            missing.append(f"triage model {model.triage_model} not installed")
        if not model.embed_present:
            missing.append(f"embed model {model.embed_model} not installed")
        reasons.append("model environment incomplete: " + "; ".join(missing))
        if live:
            blocked = True

    if broker is None:
        reasons.append("broker unreachable")
        if live:
            blocked = True
    else:
        if not broker.paper:
            reasons.append("broker endpoint is LIVE, not paper — refusing")
            blocked = True   # live mode must never accidentally hit the live host
        if broker.account_status != "ACTIVE":
            reasons.append(f"account status is {broker.account_status}")
            if live:
                blocked = True

    # For live trading, session-start equity must be configured so the
    # daily-loss guard measures from a real baseline.
    if live and settings.session_start_equity <= 0:
        reasons.append("SCANNER_SESSION_START_EQUITY not set — daily-loss guard cannot baseline")
        blocked = True

    reason = "; ".join(reasons) if reasons else ""
    return PreflightReport(
        model=model,
        broker=broker or BrokerEnv(
            reachable=False, paper=False, account_status="UNKNOWN",
            equity=0.0, buying_power=0.0,
        ),
        blocked=blocked,
        reason=reason,
    )


def banner(report: PreflightReport) -> str:
    """One line suitable for a scheduled log."""
    parts: list[str] = []
    m = report.model
    if m.complete:
        parts.append(
            f"models OK — triage {m.triage_model}, embed {m.embed_model}"
        )
    else:
        bits: list[str] = []
        if not m.ollama_reachable:
            bits.append("Ollama unreachable")
        if not m.triage_present:
            bits.append(f"triage {m.triage_model} missing")
        if not m.embed_present:
            bits.append(f"embed {m.embed_model} missing")
        parts.append("MODEL ENV INCOMPLETE — " + "; ".join(bits))
    b = report.broker
    if b.reachable:
        parts.append(
            f"broker OK — {'paper' if b.paper else 'LIVE'}, "
            f"{b.account_status}, equity {b.equity:,.2f}"
        )
    else:
        parts.append("broker UNREACHABLE")
    if report.blocked:
        parts.insert(0, "BLOCKED")
    return "pre-flight: " + " | ".join(parts)
