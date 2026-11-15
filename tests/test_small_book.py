"""The small-cap book (operator 2026-10-27: "penny stocks .. low caps .. a gate for it, 10% of the
account"; chose S&P 600 $5+, funded from the rules book, IWM above its 200-day as the gate)."""

from scanner.advisor.gemma_pool import PoolRow
from scanner.execution import ownership, small_book as sb
from scanner.execution.policy import TradingPolicy


def _row(symbol: str, mom: float, sector: str) -> PoolRow:
    return PoolRow(symbol=symbol, sector=sector, r1m=1.0, r3m=5.0, mom_12_1=mom, atr_pct=3.0)


def test_liquid_needs_the_price_and_the_dollar_volume():
    ok = sb.liquid([6.0] * 20, [400_000] * 20, min_price=5.0, min_dollar_volume=2_000_000)
    cheap = sb.liquid([4.9] * 20, [10_000_000] * 20, min_price=5.0, min_dollar_volume=2_000_000)
    thin = sb.liquid([6.0] * 20, [100_000] * 20, min_price=5.0, min_dollar_volume=2_000_000)
    assert ok and not cheap and not thin
    assert not sb.liquid([], [], min_price=5.0, min_dollar_volume=2_000_000)


def test_pick_counts_each_accepted_buy_toward_the_sector_cap():
    rows = [_row("A", 90, "Energy"), _row("B", 80, "Energy"), _row("C", 70, "Energy"), _row("D", 60, "Health")]

    def check(row, held):
        return "already 2" if held.get(row.sector, 0) >= 2 else None

    picks, refused = sb.pick(rows, check=check, held_sectors={}, slots=4)
    assert [r.symbol for r in picks] == ["A", "B", "D"]
    assert refused == {"C": "already 2"}


def test_pick_starts_from_the_books_own_holdings_and_stops_at_the_slots():
    rows = [_row("A", 90, "Energy"), _row("B", 80, "Health"), _row("C", 70, "Health")]
    picks, _ = sb.pick(rows, check=lambda r, held: "already 2" if held.get(r.sector, 0) >= 2 else None,
                       held_sectors={"Energy": 2}, slots=1)
    assert [r.symbol for r in picks] == ["B"]


def test_the_book_has_its_own_ids_and_the_sleeves_add_to_100():
    assert ownership.prefix_for(ownership.SMALL) == "sml-"
    assert ownership.strategy_of("sml-ABCD-123") == ownership.SMALL
    p = TradingPolicy()
    assert (p.small_allocation_pct, p.small_max_positions) == (10.0, 4)
    assert (p.gemma_allocation_pct, p.gemma_max_positions) == (20.0, 5)   # 30 -> 20 on 2026-11-02 (sweep)
    assert p.swing_allocation_pct == 0.0  # new sweep buys off 2026-11-10; open stops stay
    assert p.own_sleeves_pct == 55.0      # core 25 + rules 20 + small 10; the other 45% is cash


def test_queue_item_names_the_rule_and_the_plan_defaults():
    item = sb.queue_item(_row("A", 55.0, "Energy"), rank=3, passing=21)
    assert item["stop"] == "normal" and item["target"] == "none" and item["size"] == "normal"
    assert "small caps: #3 of 21" in item["reason"]
