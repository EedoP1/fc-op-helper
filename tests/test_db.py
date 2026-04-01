"""Tests for the database layer: engine creation, WAL mode, CRUD, session."""
import pytest
import sys
import tempfile
import os
from datetime import datetime
from sqlalchemy import text, inspect
from src.server.db import create_engine_and_tables, get_session
from src.server.models_db import PlayerRecord, PlayerScore, ListingObservation, DailyListingSummary


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


@pytest.mark.skipif(sys.platform == "win32", reason="WAL mode file locking on Windows")
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


async def test_listing_observation_table_created(db):
    """Test 6: ListingObservation table created with correct columns."""
    engine, _ = db
    async with engine.connect() as conn:
        table_names = await conn.run_sync(
            lambda sync_conn: inspect(sync_conn).get_table_names()
        )
        assert "listing_observations" in table_names

        columns = await conn.run_sync(
            lambda sync_conn: {
                c["name"]: c
                for c in inspect(sync_conn).get_columns("listing_observations")
            }
        )
        assert "fingerprint" in columns
        assert "ea_id" in columns
        assert "buy_now_price" in columns
        assert "market_price_at_obs" in columns
        assert "first_seen_at" in columns
        assert "last_seen_at" in columns
        assert "scan_count" in columns
        assert "outcome" in columns
        assert "resolved_at" in columns


async def test_daily_listing_summary_table_created(db):
    """Test 7: DailyListingSummary table created with correct columns."""
    engine, _ = db
    async with engine.connect() as conn:
        table_names = await conn.run_sync(
            lambda sync_conn: inspect(sync_conn).get_table_names()
        )
        assert "daily_listing_summaries" in table_names

        columns = await conn.run_sync(
            lambda sync_conn: {
                c["name"]: c
                for c in inspect(sync_conn).get_columns("daily_listing_summaries")
            }
        )
        assert "ea_id" in columns
        assert "date" in columns
        assert "margin_pct" in columns
        assert "op_listed_count" in columns
        assert "op_sold_count" in columns
        assert "op_expired_count" in columns
        assert "total_listed_count" in columns


async def test_player_score_has_new_columns(db):
    """Test 8: PlayerScore has expected_profit_per_hour and scorer_version columns."""
    engine, _ = db
    async with engine.connect() as conn:
        columns = await conn.run_sync(
            lambda sync_conn: {
                c["name"]: c
                for c in inspect(sync_conn).get_columns("player_scores")
            }
        )
        assert "expected_profit_per_hour" in columns
        assert "scorer_version" in columns
        assert columns["expected_profit_per_hour"]["nullable"] is True
        assert columns["scorer_version"]["nullable"] is True


async def test_listing_observation_crud(db):
    """Test 9: ListingObservation insert and query round-trip."""
    _, session_factory = db
    async with session_factory() as session:
        obs = ListingObservation(
            fingerprint="fp_abc123",
            ea_id=99999,
            buy_now_price=50000,
            market_price_at_obs=45000,
            first_seen_at=datetime.utcnow(),
            last_seen_at=datetime.utcnow(),
            scan_count=1,
        )
        session.add(obs)
        await session.commit()

        from sqlalchemy import select
        stmt = select(ListingObservation).where(ListingObservation.ea_id == 99999)
        result = await session.execute(stmt)
        row = result.scalar_one()
        assert row.fingerprint == "fp_abc123"
        assert row.buy_now_price == 50000
        assert row.outcome is None


async def test_daily_listing_summary_crud(db):
    """Test 10: DailyListingSummary insert and query round-trip."""
    _, session_factory = db
    async with session_factory() as session:
        summary = DailyListingSummary(
            ea_id=99999,
            date="2026-03-25",
            margin_pct=10,
            op_listed_count=5,
            op_sold_count=3,
            op_expired_count=2,
            total_listed_count=20,
        )
        session.add(summary)
        await session.commit()

        from sqlalchemy import select
        stmt = select(DailyListingSummary).where(DailyListingSummary.ea_id == 99999)
        result = await session.execute(stmt)
        row = result.scalar_one()
        assert row.date == "2026-03-25"
        assert row.margin_pct == 10
        assert row.op_sold_count == 3


async def test_player_score_new_columns_nullable(db):
    """Test 11: PlayerScore new columns accept None values."""
    _, session_factory = db
    async with session_factory() as session:
        score = PlayerScore(
            ea_id=77777, scored_at=datetime.utcnow(),
            buy_price=50000, sell_price=60000, net_profit=7000,
            margin_pct=20, op_sales=5, total_sales=50,
            op_ratio=0.1, expected_profit=700.0, efficiency=0.014,
            sales_per_hour=10.0, is_viable=True,
            expected_profit_per_hour=None,
            scorer_version=None,
        )
        session.add(score)
        await session.commit()

        from sqlalchemy import select
        stmt = select(PlayerScore).where(PlayerScore.ea_id == 77777)
        result = await session.execute(stmt)
        row = result.scalar_one()
        assert row.expected_profit_per_hour is None
        assert row.scorer_version is None
