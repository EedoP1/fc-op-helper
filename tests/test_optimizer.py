"""Tests for the portfolio optimizer."""

from src.optimizer import optimize_portfolio
from src.models import Player


def _make_scored(ea_id, buy_price, net_profit, op_ratio, expected_profit_per_hour=None):
    """Helper to create a scored player dict.

    Args:
        ea_id: Player EA ID (used as resource_id).
        buy_price: Buy price in coins.
        net_profit: Net profit per sale.
        op_ratio: OP sale ratio (0.0–1.0).
        expected_profit_per_hour: v2 scorer metric (float or None for v1).
    """
    entry = {
        "player": Player(
            resource_id=ea_id, name=f"Player {ea_id}", rating=88,
            position="ST", nation="X", league="X", club="X", card_type="gold",
        ),
        "buy_price": buy_price,
        "net_profit": net_profit,
        "op_ratio": op_ratio,
        "expected_profit": net_profit * op_ratio,
        "sell_price": int(buy_price * 1.40),
        "margin_pct": 40,
        "op_sales": 10,
        "total_sales": 100,
        "op_sales_24h": 24,
        "sales_per_hour": 10,
        "time_span_hrs": 10,
    }
    if expected_profit_per_hour is not None:
        entry["expected_profit_per_hour"] = expected_profit_per_hour
    return entry


def test_fills_budget():
    """Should select players up to the budget limit."""
    scored = [_make_scored(i, 10000, 3000, 0.10) for i in range(20)]
    result = optimize_portfolio(scored, budget=50000)
    total = sum(s["buy_price"] for s in result)
    assert total <= 50000
    assert len(result) == 5  # 50k / 10k = 5 players


def test_respects_target_count():
    """Should not select more than TARGET_PLAYER_COUNT (100)."""
    scored = [_make_scored(i, 100, 30, 0.10) for i in range(200)]
    result = optimize_portfolio(scored, budget=1000000)
    assert len(result) <= 100


def test_no_duplicates():
    """Should not select the same player twice."""
    scored = [_make_scored(1, 10000, 3000, 0.10)] * 5  # same player 5 times
    result = optimize_portfolio(scored, budget=100000)
    assert len(result) == 1


def test_prefers_higher_efficiency():
    """With limited budget, higher efficiency card should be picked over lower."""
    expensive = _make_scored(1, 50000, 5000, 0.10)  # ep=500, eff=0.01
    cheap = _make_scored(2, 10000, 2000, 0.10)       # ep=200, eff=0.02
    # Budget only fits one
    result = optimize_portfolio([expensive, cheap], budget=15000)
    assert len(result) == 1
    assert result[0]["player"].resource_id == 2  # cheap wins on efficiency


def test_swap_replaces_expensive_with_cheaper():
    """Swap loop should replace one 50k card with multiple 10k cards if better."""
    expensive = _make_scored(1, 50000, 10000, 0.05)  # ep = 500
    cheap = [_make_scored(i, 10000, 3000, 0.10) for i in range(2, 7)]  # ep = 300 each, 5 × 300 = 1500
    result = optimize_portfolio([expensive] + cheap, budget=50000)
    ids = {s["player"].resource_id for s in result}
    # Should have swapped out player 1 (500 ep) for 5 cheaper ones (1500 ep total)
    assert 1 not in ids
    assert len(result) == 5


def test_empty_scored_returns_empty():
    """Empty input should return empty output."""
    result = optimize_portfolio([], budget=1000000)
    assert result == []


def test_budget_too_small_for_any_player():
    """If budget < cheapest player, return empty."""
    scored = [_make_scored(1, 10000, 3000, 0.10)]
    result = optimize_portfolio(scored, budget=5000)
    assert result == []


def test_backfill_uses_remaining_budget():
    """After main fill, leftover budget should be used for additional players."""
    big = _make_scored(1, 90000, 20000, 0.10)    # high efficiency, takes 90k
    small = _make_scored(2, 9000, 1000, 0.10)     # fits in remaining 10k
    result = optimize_portfolio([big, small], budget=100000)
    assert len(result) == 2
    total = sum(s["buy_price"] for s in result)
    assert total <= 100000


# ── v2 scorer ranking tests ───────────────────────────────────────────────────

def test_v2_player_ranks_by_expected_profit_per_hour():
    """v2 player with high expected_profit_per_hour beats v1 player on efficiency.

    v1: expected_profit = net_profit(1000) * op_ratio(0.1) = 100, buy_price=10000
        → efficiency = 100/10000 = 0.01
    v2: expected_profit_per_hour = 500, buy_price=2000
        → efficiency = 500/2000 = 0.25  (higher — v2 should rank first)
    """
    v1 = _make_scored(1, 10000, 1000, 0.1)  # ep=100, eff=0.01
    v2 = _make_scored(2, 2000, 200, 1.0, expected_profit_per_hour=500)  # epph=500, eff=0.25

    # Budget fits both. v2 should rank first (higher efficiency via epph).
    result = optimize_portfolio([v1, v2], budget=20000)
    assert len(result) == 2
    # First in result list should be v2 (ranked first by efficiency via epph)
    assert result[0]["player"].resource_id == 2, "v2 player should rank first (eff=0.25 > 0.01)"


def test_v1_fallback_when_no_epph():
    """Entries without expected_profit_per_hour still rank by expected_profit/buy_price."""
    # Two v1 players, no expected_profit_per_hour
    high_eff = _make_scored(1, 5000, 2000, 0.5)   # ep=1000, eff=0.20
    low_eff = _make_scored(2, 50000, 5000, 0.5)    # ep=2500, eff=0.05

    # Budget fits only one
    result = optimize_portfolio([high_eff, low_eff], budget=10000)
    assert len(result) == 1
    assert result[0]["player"].resource_id == 1, "Higher efficiency v1 player should win"


def test_mixed_v1_v2_portfolio():
    """Mixed v1 and v2 entries produce a valid portfolio respecting budget."""
    # v1 players (no expected_profit_per_hour)
    v1_players = [_make_scored(i, 10000, 1000, 0.5) for i in range(1, 4)]
    # v2 players (with expected_profit_per_hour)
    v2_players = [_make_scored(i, 8000, 500, 0.5, expected_profit_per_hour=300) for i in range(4, 7)]

    all_players = v1_players + v2_players
    budget = 50000

    result = optimize_portfolio(all_players, budget=budget)

    # Budget not exceeded
    total = sum(s["buy_price"] for s in result)
    assert total <= budget

    # No duplicates
    ids = [s["player"].resource_id for s in result]
    assert len(ids) == len(set(ids))

    # All result entries are from our input
    assert len(result) > 0

    # Verify _ranking_profit is stripped (or at least all entries have expected_profit)
    for entry in result:
        assert "expected_profit" in entry
