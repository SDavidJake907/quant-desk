"""Policy and decision-record tests.

The point of a policy object is that six months from now, a losing month can
be traced to the exact rules that produced it. That only works if the
fingerprint moves when a rule moves, and if refusals are recorded rather than
printed and forgotten.
"""

from __future__ import annotations

from scanner.execution.policy import (
    CONFIRM,
    SCAN,
    TRADE,
    WATCH,
    Decision,
    TradingPolicy,
    rejections,
    summarise,
)


def test_the_fingerprint_moves_when_a_rule_moves():
    """Otherwise two different rule sets could produce the same audit trail."""
    base = TradingPolicy()
    assert base.fingerprint == TradingPolicy().fingerprint
    assert base.fingerprint != TradingPolicy(min_risk_pct=2.0).fingerprint
    assert base.fingerprint != TradingPolicy(max_positions=3).fingerprint


def test_the_fingerprint_is_stable_across_field_order():
    a = TradingPolicy(min_risk_pct=1.5, max_positions=5)
    b = TradingPolicy(max_positions=5, min_risk_pct=1.5)
    assert a.fingerprint == b.fingerprint


def test_defaults_match_what_the_holdout_validated():
    """These four are the configuration the +0.218R out-of-sample result was
    measured on. Changing a default silently would orphan that evidence."""
    p = TradingPolicy()
    assert p.interval_minutes == 30
    assert p.swing_k == 2
    assert p.require_value_sweep is True
    assert p.min_risk_pct == 1.5


def test_the_hold_limit_matches_the_backtest():
    """60 bars is how the backtest closed trades; a bracket has no such limit,
    so the policy has to carry it."""
    assert TradingPolicy().max_hold_bars == 60


def test_shadow_settings_are_on_and_enforce_nothing():
    """Recorded, never acted on — each improved one 86-day window and none has
    forward evidence."""
    p = TradingPolicy()
    assert p.shadow_rsi_gate and p.shadow_session_slot
    assert p.shadow_weekday and p.shadow_llm_ranking


def test_the_funnel_counts_every_candidate_once():
    decisions = (
        Decision(symbol="A", stage=TRADE),
        Decision(symbol="B", stage=CONFIRM, reason="reports earnings Tue 08/11"),
        Decision(symbol="C", stage=CONFIRM, reason="structural news (mna)"),
        Decision(symbol="D", stage=WATCH, reason="position limit reached"),
    )
    counts = summarise(decisions)
    assert counts[TRADE] == 1 and counts[CONFIRM] == 2 and counts[WATCH] == 1
    assert sum(counts.values()) == len(decisions)


def test_refusals_are_counted_by_rule():
    """A quiet day should be distinguishable from a rule that quietly stopped
    letting anything through."""
    decisions = (
        Decision(symbol="A", stage=CONFIRM, reason="reports earnings"),
        Decision(symbol="B", stage=CONFIRM, reason="reports earnings"),
        Decision(symbol="C", stage=CONFIRM, reason="structural news (mna)"),
        Decision(symbol="D", stage=TRADE),
    )
    counts = rejections(decisions)
    assert counts["reports earnings"] == 2
    assert list(counts)[0] == "reports earnings"   # most common first


def test_a_traded_candidate_is_not_a_rejection():
    assert rejections((Decision(symbol="A", stage=TRADE),)) == {}


def test_an_empty_run_summarises_cleanly():
    assert sum(summarise(()).values()) == 0
    assert rejections(()) == {}


def test_describe_names_the_rules_and_the_fingerprint():
    text = TradingPolicy().describe()
    assert "30m" in text and "stop>=1.5%" in text
    assert TradingPolicy().fingerprint in text


def test_every_entry_point_defaults_to_the_validated_configuration():
    """The disconnect this catches was real: smc_backtest defaulted to 4h with
    NO stop floor, trade.py defaulted to 4h, and every result quoted came from
    30m with a 1.5% floor. A scheduled `trade.py --submit-paper` would have
    traded a configuration nobody measured."""
    import re
    from pathlib import Path

    policy = TradingPolicy()
    wanted = {
        "--minutes": str(policy.interval_minutes),
        "--min-risk-pct": str(policy.min_risk_pct),
        "--min-rr": str(policy.min_reward_risk),
        "--fresh": str(policy.fresh_bars),
    }
    for name in ("trade", "find_setups", "smc_backtest"):
        source = Path(f"scripts/{name}.py").read_text(encoding="utf-8")
        for flag, _ in wanted.items():
            match = re.search(rf'"{flag}"[^)]*?default=([^,)]+)', source)
            if match is None and flag == "--fresh" and name == "smc_backtest":
                continue      # the backtest evaluates every setup; no freshness
            assert match, f"{name}.py has no {flag}"
            default = match.group(1).strip()
            assert "VALIDATED" in default, (
                f"{name}.py {flag} defaults to {default!r}, not the policy — "
                f"evidence and execution can drift apart"
            )


def test_research_entry_points_default_to_the_validated_sweep():
    """`--value-sweep` was opt-in on the backtest and the live scan, so a
    bare run measured the raw pattern while trade.py traded the filtered
    one. Both now take the default from the policy."""
    from pathlib import Path
    for name in ("scripts/smc_backtest.py", "scripts/find_setups.py"):
        source = Path(name).read_text(encoding="utf-8")
        assert "default=VALIDATED.require_value_sweep" in source, name
        assert '"--value-sweep", action="store_true"' not in source, name


def test_the_time_exit_reads_its_limit_from_the_policy():
    from pathlib import Path
    source = Path("scripts/manage_positions.py").read_text(encoding="utf-8")
    assert "policy.max_hold_bars" in source
    assert "policy.interval_minutes" in source
