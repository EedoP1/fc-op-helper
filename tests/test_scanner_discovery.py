"""Tests for run_discovery — cold-reset and mark-cold semantics."""
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select

from src.server.db import create_engine_and_tables
from src.server.models_db import PlayerRecord
from src.server.scanner_discovery import run_discovery


@pytest.fixture
async def db():
    engine, session_factory = await create_engine_and_tables("sqlite+aiosqlite:///:memory:")
    yield engine, session_factory
    await engine.dispose()


def _discovery_row(ea_id: int) -> dict:
    """Shape a dict matching what FutGGClient.discover_players returns."""
    return {
        "ea_id": ea_id,
        "commonName": f"Player {ea_id}",
        "firstName": "",
        "lastName": "",
        "overall": 85,
        "position": "ST",
        "rarityName": "Rare Gold",
    }


async def test_cold_player_reappears_gets_reset(db):
    """Player cold-marked to +24h and returned in discovery → next_scan_at resets to ~now."""
    _, session_factory = db
    now = datetime.utcnow()
    cold_time = now + timedelta(hours=24)

    # Seed: one cold-marked active player
    async with session_factory() as session:
        session.add(PlayerRecord(
            ea_id=111, name="Cold", rating=85, position="ST",
            nation="", league="", club="", card_type="Rare Gold",
            scan_tier="", next_scan_at=cold_time, is_active=True,
            listing_count=0, sales_per_hour=0.0,
        ))
        await session.commit()

    # Mock client: player 111 re-appears in discovery
    client = MagicMock()
    client.discover_players = AsyncMock(return_value=[_discovery_row(111)])

    await run_discovery(session_factory, client)

    async with session_factory() as session:
        result = await session.execute(
            select(PlayerRecord).where(PlayerRecord.ea_id == 111)
        )
        rec = result.scalar_one()
        # Should have been reset to ~now (within a few minutes), NOT still +24h
        assert rec.next_scan_at < now + timedelta(minutes=5), (
            f"expected next_scan_at near {now}, got {rec.next_scan_at}"
        )


async def test_normal_schedule_player_untouched(db):
    """Player with next_scan_at +3min must NOT be reset — preserves 5-min dispatch schedule."""
    _, session_factory = db
    now = datetime.utcnow()
    normal_next = now + timedelta(minutes=3)

    async with session_factory() as session:
        session.add(PlayerRecord(
            ea_id=222, name="Normal", rating=85, position="ST",
            nation="", league="", club="", card_type="Rare Gold",
            scan_tier="", next_scan_at=normal_next, is_active=True,
            listing_count=0, sales_per_hour=0.0,
        ))
        await session.commit()

    client = MagicMock()
    client.discover_players = AsyncMock(return_value=[_discovery_row(222)])

    await run_discovery(session_factory, client)

    async with session_factory() as session:
        result = await session.execute(
            select(PlayerRecord).where(PlayerRecord.ea_id == 222)
        )
        rec = result.scalar_one()
        # Reset threshold is +1h; +3min is well below → must remain near normal_next.
        # Allow 60s of slack for the test run itself.
        assert abs((rec.next_scan_at - normal_next).total_seconds()) < 60, (
            f"expected next_scan_at ~{normal_next}, got {rec.next_scan_at}"
        )


async def test_cold_player_not_rediscovered_stays_cold(db):
    """Player cold-marked to +24h and NOT in discovery result stays cold."""
    _, session_factory = db
    now = datetime.utcnow()
    cold_time = now + timedelta(hours=24)

    async with session_factory() as session:
        session.add(PlayerRecord(
            ea_id=333, name="StillCold", rating=85, position="ST",
            nation="", league="", club="", card_type="Rare Gold",
            scan_tier="", next_scan_at=cold_time, is_active=True,
            listing_count=0, sales_per_hour=0.0,
        ))
        await session.commit()

    # Discovery returns a DIFFERENT player (444) — 333 is NOT rediscovered
    client = MagicMock()
    client.discover_players = AsyncMock(return_value=[_discovery_row(444)])

    await run_discovery(session_factory, client)

    async with session_factory() as session:
        result = await session.execute(
            select(PlayerRecord).where(PlayerRecord.ea_id == 333)
        )
        rec = result.scalar_one()
        # Existing "mark cold" loop will push it to a new cold time — either way,
        # it should still be > now + 1h (well beyond normal schedule horizon).
        assert rec.next_scan_at > now + timedelta(hours=1), (
            f"expected player 333 to remain cold, got next_scan_at={rec.next_scan_at}"
        )
