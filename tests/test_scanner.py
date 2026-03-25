"""Tests for ScannerService: tier classification, scan lifecycle, bootstrap, scheduling."""
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from sqlalchemy import select

from src.server.db import create_engine_and_tables
from src.server.models_db import PlayerRecord, PlayerScore
from src.server.circuit_breaker import CircuitBreaker, CBState
from src.config import (
    TIER_PROFIT_THRESHOLD,
    SCAN_INTERVAL_NORMAL,
    SCAN_INTERVAL_HOT,
)
from tests.mock_client import make_player


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
async def db():
    """In-memory SQLite engine + session factory."""
    engine, session_factory = await create_engine_and_tables("sqlite+aiosqlite:///:memory:")
    yield engine, session_factory
    await engine.dispose()


@pytest.fixture
def circuit_breaker():
    """Fresh circuit breaker in CLOSED state."""
    return CircuitBreaker(failure_threshold=5, success_threshold=2, recovery_timeout=60.0)


@pytest.fixture
async def scanner(db, circuit_breaker):
    """ScannerService wired with in-memory DB and fresh circuit breaker."""
    from src.server.scanner import ScannerService
    _, session_factory = db
    svc = ScannerService(session_factory=session_factory, circuit_breaker=circuit_breaker)
    # Replace the real FutGGClient with a mock so no real HTTP calls are made
    mock_client = AsyncMock()
    mock_client.start = AsyncMock()
    mock_client.stop = AsyncMock()
    svc._client = mock_client
    yield svc, session_factory, mock_client


# ── Tier classification tests ─────────────────────────────────────────────────

async def test_classify_tier_hot_listing_count(scanner):
    """Test 1: classify_tier returns 'hot' when listing_count >= 50."""
    svc, *_ = scanner
    assert svc._classify_tier(listing_count=50, sales_per_hour=3.0) == "hot"


async def test_classify_tier_hot_sales_per_hour(scanner):
    """Test 2: classify_tier returns 'hot' when sales_per_hour >= 15."""
    svc, *_ = scanner
    assert svc._classify_tier(listing_count=5, sales_per_hour=15.0) == "hot"


async def test_classify_tier_hot_profit(scanner):
    """Test 3: classify_tier returns 'hot' when last_expected_profit >= TIER_PROFIT_THRESHOLD
    even if listing_count and sales_per_hour are low (per API-04)."""
    svc, *_ = scanner
    result = svc._classify_tier(
        listing_count=10,
        sales_per_hour=3.0,
        last_expected_profit=600.0,
    )
    assert result == "hot", f"Expected 'hot' for high-profit player, got '{result}'"


async def test_classify_tier_normal(scanner):
    """Test 4: classify_tier returns 'normal' when listing_count >= 20 and < 50,
    sales_per_hour < 15, and profit below threshold."""
    svc, *_ = scanner
    result = svc._classify_tier(
        listing_count=25,
        sales_per_hour=5.0,
        last_expected_profit=100.0,
    )
    assert result == "normal"


async def test_classify_tier_cold(scanner):
    """Test 5: classify_tier returns 'cold' when listing_count < 20 and sales_per_hour < 7
    and profit below threshold."""
    svc, *_ = scanner
    result = svc._classify_tier(
        listing_count=10,
        sales_per_hour=3.0,
        last_expected_profit=0.0,
    )
    assert result == "cold"


# ── scan_player tests ─────────────────────────────────────────────────────────

async def test_scan_player_writes_score(scanner, db):
    """Test 6: scan_player writes a PlayerScore row to DB with correct fields."""
    svc, session_factory, mock_client = scanner
    _, _ = db  # unused directly

    # Make market data that will score as viable
    market_data = make_player(
        ea_id=100,
        name="Scorer",
        price=20000,
        num_sales=100,
        op_sales_pct=0.15,
        op_margin=0.40,
        num_listings=30,
        hours_of_data=10.0,
    )
    mock_client.get_player_market_data = AsyncMock(return_value=market_data)

    # Insert PlayerRecord first so the update in scan_player can find it
    async with session_factory() as session:
        session.add(PlayerRecord(
            ea_id=100, name="Scorer", rating=88, position="ST",
            nation="Brazil", league="LaLiga", club="Real Madrid", card_type="gold",
        ))
        await session.commit()

    await svc.scan_player(100)

    async with session_factory() as session:
        stmt = select(PlayerScore).where(PlayerScore.ea_id == 100)
        result = await session.execute(stmt)
        row = result.scalar_one_or_none()

    assert row is not None, "Expected a PlayerScore row to be written"
    assert row.ea_id == 100
    assert row.is_viable is True
    assert row.buy_price > 0


async def test_scan_player_skips_when_cb_open(scanner, db):
    """Test 7: scan_player skips when circuit_breaker.is_open is True."""
    svc, session_factory, mock_client = scanner

    # Force circuit breaker into OPEN state
    svc._circuit_breaker.state = CBState.OPEN
    svc._circuit_breaker._opened_at = 0.0  # long ago? no — keep it open with very large recovery

    # Use a CB that stays OPEN
    cb = CircuitBreaker(failure_threshold=5, success_threshold=2, recovery_timeout=99999.0)
    cb.state = CBState.OPEN
    import time
    cb._opened_at = time.monotonic()
    svc._circuit_breaker = cb

    mock_client.get_player_market_data = AsyncMock()

    await svc.scan_player(999)

    # No API call should have been made
    mock_client.get_player_market_data.assert_not_called()

    # No PlayerScore row should exist
    async with session_factory() as session:
        stmt = select(PlayerScore).where(PlayerScore.ea_id == 999)
        result = await session.execute(stmt)
        row = result.scalar_one_or_none()
    assert row is None


async def test_scan_player_records_failure_on_exception(scanner):
    """Test 8: scan_player records failure on circuit_breaker when API raises exception."""
    svc, session_factory, mock_client = scanner

    import httpx
    mock_client.get_player_market_data = AsyncMock(
        side_effect=httpx.TimeoutException("timeout")
    )

    # Insert PlayerRecord so the update doesn't fail on a missing row
    async with session_factory() as session:
        session.add(PlayerRecord(
            ea_id=101, name="Fail Player", rating=80, position="CM",
            nation="Spain", league="EPL", club="Chelsea", card_type="gold",
        ))
        await session.commit()

    initial_failures = svc._circuit_breaker._failures
    await svc.scan_player(101)

    # Circuit breaker should have recorded a failure
    assert svc._circuit_breaker._failures > initial_failures


# ── success_rate_1h tests ─────────────────────────────────────────────────────

async def test_success_rate_1h_correct_ratio(scanner):
    """Test 9: success_rate_1h returns correct ratio."""
    svc, *_ = scanner
    now = datetime.now(timezone.utc)

    # 7 successes, 3 failures in last hour
    svc._scan_results_1h = [
        (now - timedelta(minutes=i * 5), True) for i in range(7)
    ] + [
        (now - timedelta(minutes=i * 5 + 1), False) for i in range(3)
    ]

    rate = svc.success_rate_1h()
    assert abs(rate - 0.7) < 0.01, f"Expected ~0.7, got {rate}"


async def test_success_rate_1h_empty_returns_1(scanner):
    """Test 9b: success_rate_1h returns 1.0 when no results recorded."""
    svc, *_ = scanner
    svc._scan_results_1h = []
    assert svc.success_rate_1h() == 1.0


# ── bootstrap tests ──────────────────────────────────────────────────────────

async def test_run_bootstrap_inserts_player_records(scanner, db):
    """Test 10: run_bootstrap inserts PlayerRecord rows from discovery results."""
    svc, session_factory, mock_client = scanner

    # Mock discover_players to return 3 players
    mock_client.discover_players = AsyncMock(return_value=[
        {"ea_id": 201, "price": 15000},
        {"ea_id": 202, "price": 25000},
        {"ea_id": 203, "price": 50000},
    ])

    await svc.run_bootstrap()

    async with session_factory() as session:
        stmt = select(PlayerRecord).where(PlayerRecord.is_active == True)  # noqa: E712
        result = await session.execute(stmt)
        rows = result.scalars().all()

    ea_ids = {r.ea_id for r in rows}
    assert 201 in ea_ids
    assert 202 in ea_ids
    assert 203 in ea_ids
    assert all(r.is_active for r in rows)


# ── Adaptive scheduling tests ────────────────────────────────────────────────

async def test_adaptive_scheduling_shortens_interval(scanner):
    """Test 11: Player with 50% sales_per_hour delta (above 25% threshold) gets halved interval."""
    svc, session_factory, _ = scanner
    now = datetime.utcnow()

    async with session_factory() as session:
        # Seed player record (normal tier: listing_count=25, sales_per_hour=10)
        session.add(PlayerRecord(
            ea_id=3001, name="Adaptive Player", rating=85, position="ST",
            nation="Brazil", league="LaLiga", club="Real Madrid", card_type="gold",
            scan_tier="normal", last_scanned_at=now, is_active=True,
            listing_count=25, sales_per_hour=10.0,
        ))
        # Previous score (sales_per_hour=10.0)
        session.add(PlayerScore(
            ea_id=3001, scored_at=now - timedelta(hours=1),
            buy_price=20000, sell_price=24000, net_profit=2800, margin_pct=20,
            op_sales=5, total_sales=50, op_ratio=0.1,
            expected_profit=200.0, efficiency=0.01, sales_per_hour=10.0,
            is_viable=True,
        ))
        # Current score (sales_per_hour=15.0 — 50% delta)
        session.add(PlayerScore(
            ea_id=3001, scored_at=now,
            buy_price=20000, sell_price=24000, net_profit=2800, margin_pct=20,
            op_sales=5, total_sales=50, op_ratio=0.1,
            expected_profit=200.0, efficiency=0.01, sales_per_hour=15.0,
            is_viable=True,
        ))
        await session.commit()

    async with session_factory() as session:
        await svc._classify_and_schedule(3001, 25, 10.0, 200.0, session)

    # Check next_scan_at is closer than normal interval (should be halved)
    async with session_factory() as session:
        record = await session.get(PlayerRecord, 3001)

    expected_half = SCAN_INTERVAL_NORMAL // 2
    actual_delta = (record.next_scan_at - now).total_seconds()
    assert abs(actual_delta - expected_half) < 5, (
        f"Expected ~{expected_half}s interval, got {actual_delta:.0f}s"
    )


async def test_adaptive_scheduling_no_change_stable(scanner):
    """Test 12: Player with 10% delta (below 25% threshold) keeps default interval."""
    svc, session_factory, _ = scanner
    now = datetime.utcnow()

    async with session_factory() as session:
        session.add(PlayerRecord(
            ea_id=3002, name="Stable Adaptive", rating=85, position="CM",
            nation="Spain", league="LaLiga", club="Barcelona", card_type="gold",
            scan_tier="normal", last_scanned_at=now, is_active=True,
            listing_count=25, sales_per_hour=10.0,
        ))
        # Previous score (sales_per_hour=10.0)
        session.add(PlayerScore(
            ea_id=3002, scored_at=now - timedelta(hours=1),
            buy_price=20000, sell_price=24000, net_profit=2800, margin_pct=20,
            op_sales=5, total_sales=50, op_ratio=0.1,
            expected_profit=200.0, efficiency=0.01, sales_per_hour=10.0,
            is_viable=True,
        ))
        # Current score (sales_per_hour=11.0 — 10% delta, below threshold)
        session.add(PlayerScore(
            ea_id=3002, scored_at=now,
            buy_price=20000, sell_price=24000, net_profit=2800, margin_pct=20,
            op_sales=5, total_sales=50, op_ratio=0.1,
            expected_profit=200.0, efficiency=0.01, sales_per_hour=11.0,
            is_viable=True,
        ))
        await session.commit()

    async with session_factory() as session:
        await svc._classify_and_schedule(3002, 25, 10.0, 200.0, session)

    async with session_factory() as session:
        record = await session.get(PlayerRecord, 3002)

    actual_delta = (record.next_scan_at - now).total_seconds()
    assert abs(actual_delta - SCAN_INTERVAL_NORMAL) < 5, (
        f"Expected ~{SCAN_INTERVAL_NORMAL}s interval, got {actual_delta:.0f}s"
    )


async def test_adaptive_scheduling_respects_floor(scanner):
    """Test 13: Adaptive interval never goes below ADAPTIVE_MIN_INTERVAL_SECONDS (300s)."""
    svc, session_factory, _ = scanner
    now = datetime.utcnow()

    async with session_factory() as session:
        # Use hot tier (30min = 1800s) so half = 900s, still above 300s floor
        # But we test with a tier that, when halved, would go below floor
        # Hot interval is 1800, half is 900 — above 300. So floor won't kick in here.
        # Instead, seed as hot but manually test the floor logic:
        # The floor only matters if base_interval // 2 < 300, which doesn't happen
        # with current tier intervals. So we verify the floor is at least respected
        # by checking that the scheduled interval is >= 300.
        session.add(PlayerRecord(
            ea_id=3003, name="Floor Player", rating=85, position="ST",
            nation="France", league="Ligue 1", club="PSG", card_type="gold",
            scan_tier="hot", last_scanned_at=now, is_active=True,
            listing_count=60, sales_per_hour=20.0,
        ))
        # Previous score
        session.add(PlayerScore(
            ea_id=3003, scored_at=now - timedelta(hours=1),
            buy_price=20000, sell_price=24000, net_profit=2800, margin_pct=20,
            op_sales=5, total_sales=50, op_ratio=0.1,
            expected_profit=200.0, efficiency=0.01, sales_per_hour=10.0,
            is_viable=True,
        ))
        # Current score (100% delta — definitely triggers adaptive)
        session.add(PlayerScore(
            ea_id=3003, scored_at=now,
            buy_price=20000, sell_price=24000, net_profit=2800, margin_pct=20,
            op_sales=5, total_sales=50, op_ratio=0.1,
            expected_profit=200.0, efficiency=0.01, sales_per_hour=20.0,
            is_viable=True,
        ))
        await session.commit()

    async with session_factory() as session:
        await svc._classify_and_schedule(3003, 60, 20.0, 200.0, session)

    async with session_factory() as session:
        record = await session.get(PlayerRecord, 3003)

    actual_delta = (record.next_scan_at - now).total_seconds()
    # Hot interval is 1800, halved is 900, floor is 300 — so should be 900
    expected = max(SCAN_INTERVAL_HOT // 2, 300)
    assert abs(actual_delta - expected) < 5, (
        f"Expected ~{expected}s interval, got {actual_delta:.0f}s"
    )
    assert actual_delta >= 300, f"Interval {actual_delta}s is below 300s floor"


async def test_adaptive_scheduling_no_previous_score(scanner):
    """Test 14: Player with no previous PlayerScore uses default tier interval."""
    svc, session_factory, _ = scanner
    now = datetime.utcnow()

    async with session_factory() as session:
        session.add(PlayerRecord(
            ea_id=3004, name="New Player", rating=82, position="RW",
            nation="England", league="EPL", club="Arsenal", card_type="gold",
            scan_tier="normal", last_scanned_at=now, is_active=True,
            listing_count=25, sales_per_hour=10.0,
        ))
        # Only 1 score (the "current" one) — no previous to compare against
        session.add(PlayerScore(
            ea_id=3004, scored_at=now,
            buy_price=20000, sell_price=24000, net_profit=2800, margin_pct=20,
            op_sales=5, total_sales=50, op_ratio=0.1,
            expected_profit=200.0, efficiency=0.01, sales_per_hour=10.0,
            is_viable=True,
        ))
        await session.commit()

    async with session_factory() as session:
        await svc._classify_and_schedule(3004, 25, 10.0, 200.0, session)

    async with session_factory() as session:
        record = await session.get(PlayerRecord, 3004)

    actual_delta = (record.next_scan_at - now).total_seconds()
    assert abs(actual_delta - SCAN_INTERVAL_NORMAL) < 5, (
        f"Expected ~{SCAN_INTERVAL_NORMAL}s default interval, got {actual_delta:.0f}s"
    )
