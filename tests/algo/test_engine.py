"""Tests for the backtesting engine."""
import pytest
from datetime import datetime, timedelta
from src.algo.engine import run_backtest, run_sweep, run_sweep_single_pass
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


class ThresholdStrategy(Strategy):
    name = "threshold"

    def __init__(self, params: dict):
        self.params = params
        self.threshold = params.get("threshold", 0.05)

    def on_tick(self, ea_id, price, timestamp, portfolio):
        return []

    def param_grid(self):
        return [
            {"threshold": 0.05},
            {"threshold": 0.10},
            {"threshold": 0.15},
        ]


def test_sweep_runs_all_param_combos():
    price_data = make_price_data()
    results = run_sweep(ThresholdStrategy, price_data, budget=100_000)
    assert len(results) == 3
    assert all(r["strategy_name"] == "threshold" for r in results)
    # Each result should have different params
    params_set = {r["params"] for r in results}
    assert len(params_set) == 3


def test_single_pass_matches_sequential():
    """Single-pass sweep produces identical results to sequential run_sweep."""
    price_data = make_price_data()
    budget = 100_000

    # Sequential: run_sweep does one-at-a-time
    sequential_results = run_sweep(ThresholdStrategy, price_data, budget)

    # Single-pass: run_sweep_single_pass does all combos in one timeline walk
    single_pass_results = run_sweep_single_pass(
        [ThresholdStrategy], price_data, budget,
    )

    assert len(single_pass_results) == len(sequential_results) == 3
    for seq, sp in zip(
        sorted(sequential_results, key=lambda r: r["params"]),
        sorted(single_pass_results, key=lambda r: r["params"]),
    ):
        assert seq["total_pnl"] == sp["total_pnl"]
        assert seq["total_trades"] == sp["total_trades"]
        assert seq["win_rate"] == sp["win_rate"]
        assert seq["strategy_name"] == sp["strategy_name"]


def test_single_pass_with_multiple_strategies():
    """Single-pass with multiple strategy classes produces one result per combo."""
    price_data = make_price_data()
    results = run_sweep_single_pass(
        [AlwaysBuyStrategy, BuyAndHoldStrategy], price_data, budget=100_000,
    )
    # AlwaysBuyStrategy has 1 combo, BuyAndHoldStrategy has 1 combo = 2 total
    assert len(results) == 2
    names = {r["strategy_name"] for r in results}
    assert names == {"always_buy", "buy_and_hold"}


def test_single_pass_independent_portfolios():
    """Two combos with same strategy+params produce identical results (independent portfolios)."""
    price_data = make_price_data()

    # Run single-pass with AlwaysBuyStrategy listed twice
    # Each should get its own portfolio, so results should be identical
    results = run_sweep_single_pass(
        [AlwaysBuyStrategy, AlwaysBuyStrategy], price_data, budget=100_000,
    )
    assert len(results) == 2
    assert results[0]["total_pnl"] == results[1]["total_pnl"]
    assert results[0]["total_trades"] == results[1]["total_trades"]
    assert results[0]["win_rate"] == results[1]["win_rate"]


@pytest.mark.asyncio
async def test_run_and_save_results():
    """Test that run results can be saved to and loaded from DB."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from sqlalchemy import text
    from src.server.db import Base
    from src.algo.models_db import BacktestResult  # noqa: F401
    from src.algo.engine import save_result

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    result = {
        "strategy_name": "test",
        "params": "{}",
        "started_budget": 100_000,
        "final_budget": 110_000,
        "total_pnl": 10_000,
        "total_trades": 5,
        "win_rate": 0.6,
        "max_drawdown": 0.05,
        "sharpe_ratio": 1.2,
    }

    await save_result(session_factory, result)

    async with session_factory() as session:
        rows = await session.execute(text("SELECT * FROM backtest_results"))
        all_rows = rows.fetchall()
        assert len(all_rows) == 1
        assert all_rows[0].strategy_name == "test"

    await engine.dispose()
