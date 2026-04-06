"""Tests for the backtesting engine."""
import pytest
from datetime import datetime, timedelta
from src.algo.engine import run_backtest
from src.algo.strategies.base import Strategy
from src.algo.models import Signal, Portfolio


class AlwaysBuyStrategy(Strategy):
    """Buys once per player, sells next tick. For testing."""
    name = "always_buy"

    def __init__(self, params: dict):
        self.params = params
        self._bought: set[int] = set()

    def on_tick(self, ea_id, price, timestamp, portfolio):
        if ea_id not in self._bought and portfolio.cash >= price:
            self._bought.add(ea_id)
            return [Signal(action="BUY", ea_id=ea_id, quantity=1)]
        if portfolio.holdings(ea_id) > 0:
            return [Signal(action="SELL", ea_id=ea_id, quantity=1)]
        return []

    def param_grid(self):
        return [{}]


def make_price_data():
    """Two players, 5 hours of data."""
    base = datetime(2026, 1, 1)
    return {
        1: [(base + timedelta(hours=h), 10_000 + h * 100) for h in range(5)],
        2: [(base + timedelta(hours=h), 20_000 + h * 200) for h in range(5)],
    }


def test_engine_basic_run():
    strategy = AlwaysBuyStrategy({})
    price_data = make_price_data()
    result = run_backtest(strategy, price_data, budget=100_000)
    assert result["strategy_name"] == "always_buy"
    assert result["started_budget"] == 100_000
    assert result["total_trades"] > 0
    assert "final_budget" in result
    assert "total_pnl" in result
    assert "win_rate" in result
    assert "max_drawdown" in result
    assert "sharpe_ratio" in result


class BuyAndHoldStrategy(Strategy):
    """Buys once, never sells. Tests force-sell at end."""
    name = "buy_and_hold"

    def __init__(self, params: dict):
        self.params = params
        self._bought = False

    def on_tick(self, ea_id, price, timestamp, portfolio):
        if not self._bought and ea_id == 1 and portfolio.cash >= price:
            self._bought = True
            return [Signal(action="BUY", ea_id=1, quantity=1)]
        return []

    def param_grid(self):
        return [{}]


def test_engine_force_sells_open_positions():
    strategy = BuyAndHoldStrategy({})
    price_data = make_price_data()
    result = run_backtest(strategy, price_data, budget=100_000)
    # Should have 1 trade from force-sell
    assert result["total_trades"] == 1
    # Final budget = 100000 - 10000 (buy at h0) + 10400 * 0.95 (sell at h4 with tax)
    expected_revenue = int(10_400 * 0.95)
    assert result["final_budget"] == 100_000 - 10_000 + expected_revenue


def test_engine_insufficient_funds_skips_buy():
    strategy = AlwaysBuyStrategy({})
    base = datetime(2026, 1, 1)
    price_data = {
        1: [(base, 90_000)],  # costs 90k
        2: [(base, 90_000)],  # can't afford second
    }
    result = run_backtest(strategy, price_data, budget=100_000)
    # Can only buy one player (90k), not enough for second
    assert result["total_trades"] <= 1
