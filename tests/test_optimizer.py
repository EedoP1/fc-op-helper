"""Tests for the portfolio optimizer."""

from src.optimizer import optimize_portfolio
from src.models import Player


def _make_scored(ea_id, buy_price, net_profit, op_ratio):
    """Helper to create a scored player dict."""
    return {
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
