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


@pytest.mark.asyncio
async def test_load_market_snapshot_data_hour_bucketing():
    """Multiple snapshots within the same hour collapse to the hourly median."""
    from datetime import datetime, timedelta
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from sqlalchemy import text
    from src.server.db import Base
    from src.server.models_db import MarketSnapshot, PlayerRecord  # noqa: F401
    from src.algo.engine import load_market_snapshot_data

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    base = datetime(2026, 1, 1, 12, 0, 0)
    async with session_factory() as session:
        # Three snapshots in the same hour, increasing prices
        for i, price in enumerate([100, 200, 300]):
            await session.execute(
                text(
                    "INSERT INTO market_snapshots (ea_id, captured_at, current_lowest_bin, listing_count) "
                    "VALUES (:ea_id, :captured_at, :price, 1)"
                ),
                {"ea_id": 1, "captured_at": (base + timedelta(minutes=i * 10)).isoformat(), "price": price},
            )
        # Six snapshots in the next six hours to meet min_data_points=6
        for h in range(1, 7):
            await session.execute(
                text(
                    "INSERT INTO market_snapshots (ea_id, captured_at, current_lowest_bin, listing_count) "
                    "VALUES (:ea_id, :captured_at, :price, 1)"
                ),
                {"ea_id": 1, "captured_at": (base + timedelta(hours=h)).isoformat(), "price": 500 + h},
            )
        await session.commit()

    price_data, _, _, _ = await load_market_snapshot_data(session_factory, min_data_points=6)

    assert 1 in price_data, "ea_id 1 should be present"
    # First hour collapses to the hourly median of [100, 200, 300] = 200
    first_ts, first_price = price_data[1][0]
    assert first_ts == base.replace(minute=0, second=0, microsecond=0)
    assert first_price == 200, f"Expected hourly median (200), got {first_price}"
    assert len(price_data[1]) == 7, "1 bucket for the first hour + 6 hourly rows after"

    await engine.dispose()


@pytest.mark.asyncio
async def test_load_market_snapshot_data_days_filter_sunday_aligned():
    """--days cutoff rolls back to the previous Sunday 00:00 UTC."""
    from datetime import datetime, timedelta
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from sqlalchemy import text
    from src.server.db import Base
    from src.server.models_db import MarketSnapshot, PlayerRecord  # noqa: F401
    from src.algo.engine import load_market_snapshot_data

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    # Reference "now" = Wednesday 2026-04-15 10:00:00 UTC
    now = datetime(2026, 4, 15, 10, 0, 0)
    # With days=5, naive cutoff = 2026-04-10 10:00:00 (a Friday).
    # Sunday-aligned cutoff rolls back to previous Sunday = 2026-04-05 00:00:00.
    # So anything at or after 2026-04-05 00:00 is kept; anything before is dropped.

    async with session_factory() as session:
        # Old row: 2026-04-04 23:59 — should be dropped
        await session.execute(
            text(
                "INSERT INTO market_snapshots (ea_id, captured_at, current_lowest_bin, listing_count) "
                "VALUES (:ea_id, :captured_at, :price, 1)"
            ),
            {"ea_id": 1, "captured_at": datetime(2026, 4, 4, 23, 59).isoformat(), "price": 100},
        )
        # Exactly on cutoff: 2026-04-05 00:00 — should be kept
        await session.execute(
            text(
                "INSERT INTO market_snapshots (ea_id, captured_at, current_lowest_bin, listing_count) "
                "VALUES (:ea_id, :captured_at, :price, 1)"
            ),
            {"ea_id": 1, "captured_at": datetime(2026, 4, 5, 0, 0).isoformat(), "price": 200},
        )
        # Pad with 5 more recent rows so ea_id 1 clears min_data_points=6
        for h in range(1, 6):
            await session.execute(
                text(
                    "INSERT INTO market_snapshots (ea_id, captured_at, current_lowest_bin, listing_count) "
                    "VALUES (:ea_id, :captured_at, :price, 1)"
                ),
                {"ea_id": 1, "captured_at": (datetime(2026, 4, 5, h, 0)).isoformat(), "price": 200 + h},
            )
        await session.commit()

    price_data, _, _, _ = await load_market_snapshot_data(
        session_factory, min_data_points=6, days=5, now=now,
    )

    assert 1 in price_data
    timestamps = [ts for ts, _ in price_data[1]]
    assert datetime(2026, 4, 4, 23, 0) not in timestamps, "Pre-cutoff row should be filtered out"
    assert datetime(2026, 4, 5, 0, 0) in timestamps, "Row at exact cutoff should be kept"

    await engine.dispose()


@pytest.mark.asyncio
async def test_load_market_snapshot_data_days_zero_means_no_filter():
    from datetime import datetime
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from sqlalchemy import text
    from src.server.db import Base
    from src.server.models_db import MarketSnapshot, PlayerRecord  # noqa: F401
    from src.algo.engine import load_market_snapshot_data

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        for h in range(6):
            await session.execute(
                text(
                    "INSERT INTO market_snapshots (ea_id, captured_at, current_lowest_bin, listing_count) "
                    "VALUES (:ea_id, :captured_at, :price, 1)"
                ),
                {"ea_id": 1, "captured_at": datetime(2020, 1, 1, h, 0).isoformat(), "price": 100 + h},
            )
        await session.commit()

    # days=0 → no filter; ancient data is returned
    price_data, _, _, _ = await load_market_snapshot_data(session_factory, min_data_points=6, days=0)
    assert 1 in price_data
    assert len(price_data[1]) == 6

    await engine.dispose()
