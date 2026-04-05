"""Tests for the portfolio optimizer (v3 weighted scorer)."""

from src.optimizer import optimize_portfolio
from src.models import Player


def _make_scored(ea_id, buy_price, net_profit, sell_ratio, expected_profit_per_hour=None):
    """Helper to create a scored player dict for v3 optimizer.

    Args:
        ea_id: Player EA ID (used as resource_id).
        buy_price: Buy price in coins.
        net_profit: Net profit per sale at chosen margin.
        sell_ratio: Sell ratio (0.0–1.0), stored in op_ratio column.
        expected_profit_per_hour: v3 weighted score. Defaults to
            sell_ratio * net_profit * 10 (simulated sph=10).
    """
    score = expected_profit_per_hour if expected_profit_per_hour is not None else sell_ratio * net_profit * 10
    return {
        "player": Player(
            resource_id=ea_id, name=f"Player {ea_id}", rating=88,
            position="ST", nation="X", league="X", club="X", card_type="gold",
        ),
        "buy_price": buy_price,
        "net_profit": net_profit,
        "op_ratio": sell_ratio,
        "expected_profit": score,
        "sell_price": int(buy_price * 1.40),
        "margin_pct": 40,
        "op_sales": 0,
        "total_sales": 0,
        "card_type": "gold",
        "sales_per_hour": 10,
        "expected_profit_per_hour": score,
    }


def _make_fillers(count=80, start_id=1000):
    """Generate cheap filler players to prevent the drop-and-backfill loop.

    The optimizer's drop-and-backfill fires when selected < 80 players.
    These fillers (1 coin, min profit) pad the candidate pool so greedy
    fill reaches 80+ without affecting budget or ranking assertions.
    """
    return [_make_scored(start_id + i, 1, 2000, 0.5, expected_profit_per_hour=0.001)
            for i in range(count)]


def test_fills_budget():
    """Should select players up to the budget limit."""
    scored = [_make_scored(i, 10000, 3000, 0.80) for i in range(20)] + _make_fillers()
    result = optimize_portfolio(scored, budget=50080)
    total = sum(s["buy_price"] for s in result)
    assert total <= 50080
    real = [s for s in result if s["buy_price"] == 10000]
    assert len(real) == 5  # 50k / 10k = 5 real players


def test_respects_target_count():
    """Should not select more than TARGET_PLAYER_COUNT (100)."""
    scored = [_make_scored(i, 100, 3000, 0.80) for i in range(200)]
    result = optimize_portfolio(scored, budget=1000000)
    assert len(result) <= 100


def test_no_duplicates():
    """Should not select the same player twice."""
    scored = [_make_scored(1, 10000, 3000, 0.80)] * 5 + _make_fillers()
    result = optimize_portfolio(scored, budget=100080)
    dupes = [s for s in result if s["player"].resource_id == 1]
    assert len(dupes) == 1


def test_prefers_cheaper_when_budget_tight():
    """With limited budget, only the affordable card should be selected."""
    expensive = _make_scored(1, 50000, 5000, 0.80)  # too expensive
    cheap = _make_scored(2, 10000, 3000, 0.80)       # fits budget
    result = optimize_portfolio([expensive, cheap] + _make_fillers(), budget=15080)
    ids = {s["player"].resource_id for s in result}
    assert 2 in ids      # cheap fits
    assert 1 not in ids  # expensive doesn't fit budget


def test_drop_and_backfill_replaces_expensive_with_cheaper():
    """Drop-and-backfill should replace one expensive card with many cheap cards."""
    expensive = _make_scored(1, 42500, 10000, 0.50)
    cheap = [_make_scored(i, 500, 3000, 0.80) for i in range(2, 87)]  # 85 cheap players
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
    scored = [_make_scored(1, 10000, 3000, 0.80)]
    result = optimize_portfolio(scored, budget=5000)
    assert result == []


def test_backfill_uses_remaining_budget():
    """After main fill, leftover budget should be used for additional players."""
    big = _make_scored(1, 90000, 20000, 0.80)
    small = _make_scored(2, 9000, 3000, 0.80)
    result = optimize_portfolio([big, small] + _make_fillers(), budget=100080)
    ids = {s["player"].resource_id for s in result}
    assert 1 in ids  # big player selected
    assert 2 in ids  # small player backfilled with remaining budget
    total = sum(s["buy_price"] for s in result)
    assert total <= 100080


def test_ranks_by_score():
    """Player with higher weighted score ranks first in output."""
    low = _make_scored(1, 10000, 3000, 0.50, expected_profit_per_hour=100)
    high = _make_scored(2, 5000, 4000, 0.90, expected_profit_per_hour=500)
    result = optimize_portfolio([low, high] + _make_fillers(), budget=20080)
    ids = {s["player"].resource_id for s in result}
    assert 1 in ids and 2 in ids
    real = [s for s in result if s["player"].resource_id in (1, 2)]
    assert real[0]["player"].resource_id == 2, "Higher scored player should rank first"


def test_portfolio_with_varied_scores():
    """Players with varied scores produce a valid portfolio respecting budget."""
    players_high = [_make_scored(i, 10000, 5000, 0.80, expected_profit_per_hour=500) for i in range(1, 4)]
    players_low = [_make_scored(i, 8000, 3000, 0.60, expected_profit_per_hour=300) for i in range(4, 7)]
    all_players = players_high + players_low
    budget = 50000
    result = optimize_portfolio(all_players, budget=budget)
    total = sum(s["buy_price"] for s in result)
    assert total <= budget
    ids = [s["player"].resource_id for s in result]
    assert len(ids) == len(set(ids))
    assert len(result) > 0


def test_min_profit_filter():
    """Players with net_profit < 2000 should be excluded."""
    low_profit = _make_scored(1, 10000, 1500, 0.90, expected_profit_per_hour=1000)
    high_profit = _make_scored(2, 10000, 5000, 0.90, expected_profit_per_hour=1000)
    result = optimize_portfolio([low_profit, high_profit] + _make_fillers(), budget=100080)
    ids = {s["player"].resource_id for s in result}
    assert 1 not in ids  # below 2k profit
    assert 2 in ids


def test_exclude_card_types():
    """Excluded card types should not appear in portfolio."""
    totw = _make_scored(1, 10000, 5000, 0.90, expected_profit_per_hour=1000)
    totw["card_type"] = "Team of the Week"
    rare = _make_scored(2, 10000, 5000, 0.90, expected_profit_per_hour=1000)
    rare["card_type"] = "Rare"
    result = optimize_portfolio([totw, rare] + _make_fillers(), budget=100080, exclude_card_types=["Team of the Week"])
    ids = {s["player"].resource_id for s in result}
    assert 1 not in ids  # TOTW excluded
    assert 2 in ids


def test_upgrade_swaps_weakest():
    """Upgrade loop should swap weakest player for a stronger unselected one."""
    weak = _make_scored(1, 10000, 3000, 0.50, expected_profit_per_hour=50)
    strong = _make_scored(2, 10000, 5000, 0.90, expected_profit_per_hour=500)
    # Budget only fits one expensive player + fillers. Greedy picks weak first,
    # upgrade loop should swap it for strong.
    result = optimize_portfolio([weak, strong] + _make_fillers(), budget=10081)
    real = [s for s in result if s["player"].resource_id in (1, 2)]
    assert len(real) == 1
    assert real[0]["player"].resource_id == 2, "Upgrade loop should swap in the stronger player"
