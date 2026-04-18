"""End-to-end test: seed market_snapshots -> run strategies -> check results."""
import pytest
from datetime import datetime, timedelta
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import text
from src.server.db import Base


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    # Ensure both algo + server tables are registered on the metadata
    from src.algo.models_db import BacktestResult  # noqa: F401
    from src.server.models_db import MarketSnapshot, PlayerRecord  # noqa: F401
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    yield session_factory
    await engine.dispose()


async def seed_price_data(session_factory, num_players=3, num_hours=200):
    """Insert synthetic hourly snapshots with a mean-reverting sine-wave pattern."""
    import math
    base = datetime(2026, 1, 1)
    async with session_factory() as session:
        for pid in range(1, num_players + 1):
            base_price = 10_000 * pid
            for h in range(num_hours):
                price = int(base_price + base_price * 0.15 * math.sin(h / 12 * math.pi))
                await session.execute(
                    text(
                        "INSERT INTO market_snapshots (ea_id, captured_at, current_lowest_bin, listing_count) "
                        "VALUES (:ea_id, :captured_at, :price, 1)"
                    ),
                    {
                        "ea_id": pid,
                        "captured_at": (base + timedelta(hours=h)).isoformat(),
                        "price": price,
                    },
                )
        await session.commit()


@pytest.mark.asyncio
async def test_full_pipeline(db):
    from src.algo.engine import load_market_snapshot_data, run_sweep, save_result
    from src.algo.strategies.mean_reversion import MeanReversionStrategy

    # Seed
    await seed_price_data(db, num_players=3, num_hours=200)

    # Load
    price_data, _, _, _ = await load_market_snapshot_data(db, min_data_points=10)
    assert len(price_data) == 3

    # Run sweep with a small grid
    class SmallGridMR(MeanReversionStrategy):
        name = "mean_reversion_test"
        def param_grid(self):
            return [
                {"window": 12, "threshold": 0.10, "position_pct": 0.02},
                {"window": 24, "threshold": 0.10, "position_pct": 0.02},
            ]

    results = run_sweep(SmallGridMR, price_data, budget=100_000)
    assert len(results) == 2

    # Save
    for r in results:
        await save_result(db, r)

    # Verify in DB
    async with db() as session:
        rows = await session.execute(text("SELECT COUNT(*) FROM backtest_results"))
        count = rows.scalar()
        assert count == 2

    # Verify results have expected fields
    for r in results:
        assert r["started_budget"] == 100_000
        assert isinstance(r["total_pnl"], int)
        assert 0.0 <= r["win_rate"] <= 1.0
        assert 0.0 <= r["max_drawdown"] <= 1.0


@pytest.mark.asyncio
async def test_all_strategies_run(db):
    """Every discovered strategy can complete a backtest without crashing."""
    from src.algo.engine import run_backtest, load_market_snapshot_data
    from src.algo.strategies import discover_strategies

    await seed_price_data(db, num_players=2, num_hours=100)
    price_data, _, _, _ = await load_market_snapshot_data(db, min_data_points=10)

    strategies = discover_strategies()
    assert len(strategies) >= 4, f"Expected 4+ strategies, found {list(strategies.keys())}"

    for name, cls in strategies.items():
        grid = cls({}).param_grid()
        strategy = cls(grid[0])
        result = run_backtest(strategy, price_data, budget=100_000)
        assert result["strategy_name"] == name
        assert result["started_budget"] == 100_000
