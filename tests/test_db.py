"""Tests for the database layer: engine creation, WAL mode, CRUD, session."""
import pytest
import tempfile
import os
from datetime import datetime
from sqlalchemy import text
from src.server.db import create_engine_and_tables, get_session
from src.server.models_db import PlayerRecord, PlayerScore


@pytest.fixture
async def db():
    """Create in-memory engine and tables, yield session factory, dispose after."""
    engine, session_factory = await create_engine_and_tables("sqlite+aiosqlite:///:memory:")
    yield engine, session_factory
    await engine.dispose()


async def test_create_engine_and_tables_succeeds(db):
    """Test 1: Engine and table creation succeeds on in-memory SQLite."""
    engine, session_factory = db
    assert engine is not None
    assert session_factory is not None


async def test_wal_mode_enabled():
    """Test 2: WAL mode is enabled on file-based SQLite."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        db_url = f"sqlite+aiosqlite:///{db_path}"
        engine, sf = await create_engine_and_tables(db_url)
        async with sf() as session:
            result = await session.execute(text("PRAGMA journal_mode"))
            mode = result.scalar()
            assert mode == "wal", f"Expected WAL mode, got {mode}"
        await engine.dispose()


async def test_player_record_crud(db):
    """Test 3: PlayerRecord insert and query round-trip."""
    _, session_factory = db
    async with session_factory() as session:
        record = PlayerRecord(
            ea_id=12345, name="Test Player", rating=88,
            position="ST", nation="Brazil", league="LaLiga",
            club="Real Madrid", card_type="gold",
        )
        session.add(record)
        await session.commit()
        result = await session.get(PlayerRecord, 12345)
        assert result is not None
        assert result.name == "Test Player"
        assert result.rating == 88
        assert result.is_active is True


async def test_player_score_crud(db):
    """Test 4: PlayerScore insert and query round-trip."""
    _, session_factory = db
    async with session_factory() as session:
        score = PlayerScore(
            ea_id=12345, scored_at=datetime.utcnow(),
            buy_price=50000, sell_price=60000, net_profit=7000,
            margin_pct=20, op_sales=5, total_sales=50,
            op_ratio=0.1, expected_profit=700.0, efficiency=0.014,
            sales_per_hour=10.0, is_viable=True,
        )
        session.add(score)
        await session.commit()
        from sqlalchemy import select
        stmt = select(PlayerScore).where(PlayerScore.ea_id == 12345)
        result = await session.execute(stmt)
        row = result.scalar_one()
        assert row.buy_price == 50000
        assert row.op_ratio == 0.1


async def test_get_session_yields_async_session(db):
    """Test 5: get_session yields a working AsyncSession."""
    _, session_factory = db
    async for session in get_session(session_factory):
        result = await session.execute(text("SELECT 1"))
        assert result.scalar() == 1
        break
