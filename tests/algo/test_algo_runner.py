"""Tests for algo_runner — DB integration for signal engine."""
import pytest
import asyncio
from datetime import datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from src.server.db import Base
from src.server.models_db import (
    AlgoConfig, AlgoSignal, AlgoPosition, PlayerRecord, MarketSnapshot,
)
from src.server.algo_runner import run_signal_engine


@pytest.fixture
async def db():
    """Async in-memory SQLite for integration tests."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    yield sf
    await engine.dispose()


async def _seed_promo_batch(sf, num_cards=12, hours=250):
    """Seed a Friday promo batch into players + market_snapshots."""
    release = datetime(2026, 4, 3, 18, 0)  # Friday

    async with sf() as session:
        for i in range(num_cards):
            ea_id = 200_000 + i
            session.add(PlayerRecord(
                ea_id=ea_id,
                name=f"Player {i}",
                rating=88,
                position="CM",
                nation="Test",
                league="Test",
                club="Test",
                card_type="TOTS",
                created_at=release,
            ))

            for h in range(hours):
                ts = release + timedelta(hours=h)
                if h < 48:
                    price = int(30000 * (1.0 - 0.5 * h / 48))
                elif h < 72:
                    progress = (h - 48) / 24
                    price = int(30000 * (0.5 + 0.25 * progress))
                elif h < 200:
                    price = int(30000 * (0.75 + 0.02 * (h - 72) / 128))
                else:
                    price = int(30000 * 0.77)

                session.add(MarketSnapshot(
                    ea_id=ea_id,
                    captured_at=ts,
                    current_lowest_bin=price,
                    listing_count=50,
                ))

        session.add(AlgoConfig(
            budget=5_000_000,
            is_active=True,
            strategy_params=None,
            created_at=datetime(2026, 4, 3),
            updated_at=datetime(2026, 4, 3),
        ))
        await session.commit()


@pytest.mark.asyncio
async def test_runner_generates_signals(db):
    await _seed_promo_batch(db)
    await run_signal_engine(db)

    async with db() as session:
        signals = (await session.execute(select(AlgoSignal))).scalars().all()

    assert len(signals) > 0, "Should generate at least one signal"
    buy_signals = [s for s in signals if s.action == "BUY"]
    assert len(buy_signals) > 0, "Should have BUY signals"
    for s in signals:
        assert s.status == "PENDING"
        assert s.quantity > 0


@pytest.mark.asyncio
async def test_runner_skips_when_inactive(db):
    await _seed_promo_batch(db)
    # Deactivate
    async with db() as session:
        config = (await session.execute(select(AlgoConfig))).scalar_one()
        config.is_active = False
        await session.commit()

    await run_signal_engine(db)

    async with db() as session:
        signals = (await session.execute(select(AlgoSignal))).scalars().all()
    assert len(signals) == 0


@pytest.mark.asyncio
async def test_runner_deduplicates_signals(db):
    await _seed_promo_batch(db)
    await run_signal_engine(db)

    async with db() as session:
        count_1 = len((await session.execute(select(AlgoSignal))).scalars().all())

    # Run again — should not create duplicate signals
    await run_signal_engine(db)

    async with db() as session:
        count_2 = len((await session.execute(select(AlgoSignal))).scalars().all())

    assert count_2 == count_1, "Should not create duplicate signals"
