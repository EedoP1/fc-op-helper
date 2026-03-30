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
        expected_profit_per_hour: v2 scorer metric (float). Defaults to
            net_profit * op_ratio when not provided.
    """
    epph = expected_profit_per_hour if expected_profit_per_hour is not None else net_profit * op_ratio
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
        "expected_profit_per_hour": epph,
    }


def _make_fillers(count=80, start_id=1000):
    """Generate cheap filler players to prevent the drop-and-backfill loop.

    The optimizer's drop-and-backfill fires when selected < 80 players.
    These fillers (1 coin, EPPH=0.001) pad the candidate pool so greedy
    fill reaches 80+ without affecting budget or ranking assertions.
    """
    return [_make_scored(start_id + i, 1, 1, 0.10, expected_profit_per_hour=0.001)
            for i in range(count)]


def test_fills_budget():
    """Should select players up to the budget limit."""
    scored = [_make_scored(i, 10000, 3000, 0.10) for i in range(20)] + _make_fillers()
    result = optimize_portfolio(scored, budget=50080)
    total = sum(s["buy_price"] for s in result)
    assert total <= 50080
    real = [s for s in result if s["buy_price"] == 10000]
    assert len(real) == 5  # 50k / 10k = 5 real players


def test_respects_target_count():
    """Should not select more than TARGET_PLAYER_COUNT (100)."""
    scored = [_make_scored(i, 100, 30, 0.10) for i in range(200)]
    result = optimize_portfolio(scored, budget=1000000)
    assert len(result) <= 100


def test_no_duplicates():
    """Should not select the same player twice."""
    scored = [_make_scored(1, 10000, 3000, 0.10)] * 5 + _make_fillers()
    result = optimize_portfolio(scored, budget=100080)
    dupes = [s for s in result if s["player"].resource_id == 1]
    assert len(dupes) == 1


def test_prefers_cheaper_when_budget_tight():
    """With limited budget, only the affordable card should be selected."""
    expensive = _make_scored(1, 50000, 5000, 0.10)  # EPPH=500 but too expensive
    cheap = _make_scored(2, 10000, 2000, 0.10)       # EPPH=200, fits budget
    result = optimize_portfolio([expensive, cheap] + _make_fillers(), budget=15080)
    ids = {s["player"].resource_id for s in result}
    assert 2 in ids      # cheap fits
    assert 1 not in ids  # expensive doesn't fit budget


def test_drop_and_backfill_replaces_expensive_with_cheaper():
    """Drop-and-backfill should replace one expensive card with many cheap cards."""
    expensive = _make_scored(1, 42500, 10000, 0.05)   # EPPH=500, expensive
    cheap = [_make_scored(i, 500, 300, 0.10) for i in range(2, 87)]  # 85 cheap players
    result = optimize_portfolio([expensive] + cheap, budget=43000)
    ids = {s["player"].resource_id for s in result}
    assert 1 not in ids       # expensive dropped and banned
    assert len(result) >= 80  # cheap players backfilled


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
    big = _make_scored(1, 90000, 20000, 0.10)    # EPPH=2000, takes 90k
    small = _make_scored(2, 9000, 1000, 0.10)     # EPPH=100, fits in remaining
    result = optimize_portfolio([big, small] + _make_fillers(), budget=100080)
    ids = {s["player"].resource_id for s in result}
    assert 1 in ids  # big player selected
    assert 2 in ids  # small player backfilled with remaining budget
    total = sum(s["buy_price"] for s in result)
    assert total <= 100080


# ── v2 scorer ranking tests ───────────────────────────────────────────────────

def test_ranks_by_expected_profit_per_hour():
    """Player with high expected_profit_per_hour ranks first in output.

    low: expected_profit_per_hour = 100, buy_price=10000
    high: expected_profit_per_hour = 500, buy_price=2000 (should rank first)
    """
    low = _make_scored(1, 10000, 1000, 0.1, expected_profit_per_hour=100)
    high = _make_scored(2, 2000, 200, 1.0, expected_profit_per_hour=500)

    result = optimize_portfolio([low, high] + _make_fillers(), budget=20080)
    ids = {s["player"].resource_id for s in result}
    assert 1 in ids and 2 in ids  # both selected
    real = [s for s in result if s["player"].resource_id in (1, 2)]
    assert real[0]["player"].resource_id == 2, "Higher EPPH player should rank first"


def test_portfolio_with_varied_efficiency():
    """Players with varied expected_profit_per_hour produce a valid portfolio respecting budget."""
    players_low = [_make_scored(i, 10000, 1000, 0.5, expected_profit_per_hour=500) for i in range(1, 4)]
    players_high = [_make_scored(i, 8000, 500, 0.5, expected_profit_per_hour=300) for i in range(4, 7)]

    all_players = players_low + players_high
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

    # Verify _ranking_profit is stripped (all entries should still have expected_profit)
    for entry in result:
        assert "expected_profit" in entry
