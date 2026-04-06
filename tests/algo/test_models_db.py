import pytest
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from src.server.db import Base
import src.algo.models_db  # noqa: F401 — registers ORM models on Base.metadata before create_all


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    yield session_factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_price_history_table_exists(db):
    from src.algo.models_db import PriceHistory  # noqa: F401
    async with db() as session:
        conn = await session.connection()
        tables = await conn.run_sync(
            lambda sync_conn: inspect(sync_conn).get_table_names()
        )
        assert "price_history" in tables


@pytest.mark.asyncio
async def test_backtest_results_table_exists(db):
    from src.algo.models_db import BacktestResult  # noqa: F401
    async with db() as session:
        conn = await session.connection()
        tables = await conn.run_sync(
            lambda sync_conn: inspect(sync_conn).get_table_names()
        )
        assert "backtest_results" in tables
