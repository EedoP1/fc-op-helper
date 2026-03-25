"""Tests for ScannerService: tier classification, scan lifecycle, bootstrap, scheduling."""
import json
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from sqlalchemy import select

from src.server.db import create_engine_and_tables
from src.server.models_db import (
    PlayerRecord, PlayerScore,
    MarketSnapshot, SnapshotSale, SnapshotPricePoint,
    ListingObservation,
)
from src.server.circuit_breaker import CircuitBreaker, CBState
from src.config import (
    DEFAULT_SCAN_INTERVAL_SECONDS,
    MARKET_DATA_RETENTION_DAYS,
    LISTING_RETENTION_DAYS,
    LISTING_SCAN_BUFFER_SECONDS,
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


# ── scan_player tests ─────────────────────────────────────────────────────────

@patch("src.server.scanner.score_player_v2", new_callable=AsyncMock, return_value={
    "ea_id": 100, "buy_price": 20000, "sell_price": 24000,
    "net_profit": 2800, "margin_pct": 20, "op_sold": 5,
    "op_total": 50, "op_sell_rate": 0.1, "op_sales_per_hour": 2.0,
    "expected_profit_per_hour": 560.0, "efficiency": 0.028, "hours_of_data": 10.0,
})
async def test_scan_player_writes_score(mock_v2, scanner, db):
    """Test 6: scan_player writes a PlayerScore row built from v2 result."""
    svc, session_factory, mock_client = scanner
    _, _ = db  # unused directly

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
    assert row.buy_price == 20000
    assert row.expected_profit_per_hour == 560.0


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


# ── Market snapshot persistence tests ────────────────────────────────────────

def _seed_player_record(session, ea_id: int = 100) -> None:
    """Helper to insert a PlayerRecord for snapshot tests."""
    session.add(PlayerRecord(
        ea_id=ea_id, name="Snapshot Player", rating=88, position="ST",
        nation="Brazil", league="LaLiga", club="Real Madrid", card_type="gold",
    ))


@patch("src.server.scanner.score_player_v2", new_callable=AsyncMock, return_value={
    "ea_id": 100, "buy_price": 20000, "sell_price": 24000,
    "net_profit": 2800, "margin_pct": 20, "op_sold": 5,
    "op_total": 50, "op_sell_rate": 0.1, "op_sales_per_hour": 2.0,
    "expected_profit_per_hour": 560.0, "efficiency": 0.028, "hours_of_data": 10.0,
})
async def test_snapshot_created_on_scan(mock_score, scanner):
    """Test 15: scan_player creates a MarketSnapshot with correct fields."""
    svc, session_factory, mock_client = scanner
    market_data = make_player(ea_id=100, price=20000, num_sales=50, num_listings=30)
    mock_client.get_player_market_data = AsyncMock(return_value=market_data)

    async with session_factory() as session:
        _seed_player_record(session, 100)
        await session.commit()

    await svc.scan_player(100)

    async with session_factory() as session:
        result = await session.execute(
            select(MarketSnapshot).where(MarketSnapshot.ea_id == 100)
        )
        snap = result.scalar_one_or_none()

    assert snap is not None, "Expected a MarketSnapshot row"
    assert snap.ea_id == 100
    assert snap.current_lowest_bin == 20000
    assert snap.listing_count == 30
    prices = json.loads(snap.live_auction_prices)
    assert isinstance(prices, list)
    assert len(prices) == 30


@patch("src.server.scanner.score_player_v2", new_callable=AsyncMock, return_value={
    "ea_id": 101, "buy_price": 20000, "sell_price": 24000,
    "net_profit": 2800, "margin_pct": 20, "op_sold": 5,
    "op_total": 50, "op_sell_rate": 0.1, "op_sales_per_hour": 2.0,
    "expected_profit_per_hour": 560.0, "efficiency": 0.028, "hours_of_data": 10.0,
})
async def test_snapshot_sales_created(mock_score, scanner):
    """Test 16: scan_player creates SnapshotSale rows matching market data sales."""
    svc, session_factory, mock_client = scanner
    market_data = make_player(ea_id=101, price=20000, num_sales=50, num_listings=30)
    mock_client.get_player_market_data = AsyncMock(return_value=market_data)

    async with session_factory() as session:
        _seed_player_record(session, 101)
        await session.commit()

    await svc.scan_player(101)

    async with session_factory() as session:
        snap = (await session.execute(
            select(MarketSnapshot).where(MarketSnapshot.ea_id == 101)
        )).scalar_one()
        sales = (await session.execute(
            select(SnapshotSale).where(SnapshotSale.snapshot_id == snap.id)
        )).scalars().all()

    assert len(sales) == 50, f"Expected 50 SnapshotSale rows, got {len(sales)}"


@patch("src.server.scanner.score_player_v2", new_callable=AsyncMock, return_value={
    "ea_id": 102, "buy_price": 20000, "sell_price": 24000,
    "net_profit": 2800, "margin_pct": 20, "op_sold": 5,
    "op_total": 50, "op_sell_rate": 0.1, "op_sales_per_hour": 2.0,
    "expected_profit_per_hour": 560.0, "efficiency": 0.028, "hours_of_data": 10.0,
})
async def test_snapshot_price_points_created(mock_score, scanner):
    """Test 17: scan_player creates SnapshotPricePoint rows matching price history."""
    svc, session_factory, mock_client = scanner
    market_data = make_player(
        ea_id=102, price=20000, num_sales=50, num_listings=30, hours_of_data=10.0
    )
    mock_client.get_player_market_data = AsyncMock(return_value=market_data)

    async with session_factory() as session:
        _seed_player_record(session, 102)
        await session.commit()

    await svc.scan_player(102)

    async with session_factory() as session:
        snap = (await session.execute(
            select(MarketSnapshot).where(MarketSnapshot.ea_id == 102)
        )).scalar_one()
        points = (await session.execute(
            select(SnapshotPricePoint).where(SnapshotPricePoint.snapshot_id == snap.id)
        )).scalars().all()

    # make_player creates int(hours_of_data) + 1 price points
    assert len(points) == 11, f"Expected 11 SnapshotPricePoint rows, got {len(points)}"


async def test_no_snapshot_on_none_market_data(scanner):
    """Test 18: scan_player with None market_data creates no snapshot rows."""
    svc, session_factory, mock_client = scanner
    mock_client.get_player_market_data = AsyncMock(return_value=None)

    async with session_factory() as session:
        _seed_player_record(session, 103)
        await session.commit()

    await svc.scan_player(103)

    async with session_factory() as session:
        result = await session.execute(
            select(MarketSnapshot).where(MarketSnapshot.ea_id == 103)
        )
        snaps = result.scalars().all()

    assert len(snaps) == 0, "Expected no MarketSnapshot rows for None market data"


async def test_cleanup_deletes_old_snapshots(scanner):
    """Test 19: run_cleanup deletes snapshots older than retention and preserves recent ones."""
    svc, session_factory, _ = scanner
    now = datetime.utcnow()
    old_time = now - timedelta(days=MARKET_DATA_RETENTION_DAYS + 1)
    recent_time = now - timedelta(days=5)

    async with session_factory() as session:
        old_snap = MarketSnapshot(
            ea_id=200, captured_at=old_time,
            current_lowest_bin=10000, listing_count=20,
            live_auction_prices="[10000]",
        )
        session.add(old_snap)
        await session.flush()
        session.add(SnapshotSale(
            snapshot_id=old_snap.id, sold_at=old_time, sold_price=12000,
        ))

        recent_snap = MarketSnapshot(
            ea_id=200, captured_at=recent_time,
            current_lowest_bin=10000, listing_count=20,
            live_auction_prices="[10000]",
        )
        session.add(recent_snap)
        await session.flush()
        session.add(SnapshotSale(
            snapshot_id=recent_snap.id, sold_at=recent_time, sold_price=12000,
        ))
        await session.commit()

    await svc.run_cleanup()

    async with session_factory() as session:
        snaps = (await session.execute(select(MarketSnapshot))).scalars().all()
        sales = (await session.execute(select(SnapshotSale))).scalars().all()

    assert len(snaps) == 1, f"Expected 1 snapshot after cleanup, got {len(snaps)}"
    assert snaps[0].captured_at == recent_time
    assert len(sales) == 1, f"Expected 1 sale after cleanup (cascade), got {len(sales)}"


async def test_cleanup_preserves_recent_snapshots(scanner):
    """Test 20: run_cleanup preserves all snapshots within the retention window."""
    svc, session_factory, _ = scanner
    now = datetime.utcnow()

    async with session_factory() as session:
        for days_ago in [1, 10, 25]:
            snap = MarketSnapshot(
                ea_id=300, captured_at=now - timedelta(days=days_ago),
                current_lowest_bin=15000, listing_count=25,
                live_auction_prices="[15000]",
            )
            session.add(snap)
        await session.commit()

    await svc.run_cleanup()

    async with session_factory() as session:
        snaps = (await session.execute(select(MarketSnapshot))).scalars().all()

    assert len(snaps) == 3, f"Expected 3 snapshots preserved, got {len(snaps)}"


# ── Expiry-based scheduling tests ────────────────────────────────────────────

async def test_expiry_based_scheduling_uses_max_under_60min(scanner):
    """Test 21: _classify_and_schedule picks max(remaining under 60min) minus buffer.

    With auctions expiring in 30min and 50min (both under 60min), the interval
    should be based on the 50min one (max), minus 4min buffer = 46min = 2760s.
    """
    svc, session_factory, _ = scanner
    now = datetime.utcnow()

    async with session_factory() as session:
        session.add(PlayerRecord(
            ea_id=4001, name="Expiry Scheduling", rating=85, position="ST",
            nation="England", league="EPL", club="Arsenal", card_type="gold",
            last_scanned_at=now, is_active=True,
            listing_count=25, sales_per_hour=10.0,
        ))
        await session.commit()

    # Two auctions under 60 minutes: 30min and 50min
    expires_30 = (datetime.utcnow() + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    expires_50 = (datetime.utcnow() + timedelta(minutes=50)).strftime("%Y-%m-%dT%H:%M:%SZ")
    live_auctions_raw = [
        {"buyNowPrice": 20000, "expiresOn": expires_30},
        {"buyNowPrice": 25000, "expiresOn": expires_50},
    ]

    async with session_factory() as session:
        await svc._classify_and_schedule(
            4001, 25, 10.0, 200.0, session,
            live_auctions_raw=live_auctions_raw,
        )

    async with session_factory() as session:
        record = await session.get(PlayerRecord, 4001)

    # Expected: max(30min, 50min) - 4min buffer = 46min = 2760s (floor at 60s)
    expected_interval = max(int(50 * 60 - LISTING_SCAN_BUFFER_SECONDS), 60)
    actual_delta = (record.next_scan_at - now).total_seconds()
    assert abs(actual_delta - expected_interval) < 10, (
        f"Expected ~{expected_interval}s interval from max expiry, got {actual_delta:.0f}s"
    )


async def test_expiry_scheduling_defaults_when_no_listing_under_60min(scanner):
    """Test 22b: _classify_and_schedule uses DEFAULT_SCAN_INTERVAL_SECONDS when no listing < 60min.

    With one auction expiring in 90 minutes (over 60min threshold), the interval
    should fall back to DEFAULT_SCAN_INTERVAL_SECONDS (3360s = 56min).
    """
    svc, session_factory, _ = scanner
    now = datetime.utcnow()

    async with session_factory() as session:
        session.add(PlayerRecord(
            ea_id=4002, name="No Short Expiry", rating=82, position="CM",
            nation="Spain", league="LaLiga", club="Barcelona", card_type="gold",
            last_scanned_at=now, is_active=True,
            listing_count=20, sales_per_hour=5.0,
        ))
        await session.commit()

    # Only one auction expiring in 90 minutes — over the 60min threshold
    expires_90 = (datetime.utcnow() + timedelta(minutes=90)).strftime("%Y-%m-%dT%H:%M:%SZ")
    live_auctions_raw = [{"buyNowPrice": 20000, "expiresOn": expires_90}]

    async with session_factory() as session:
        await svc._classify_and_schedule(
            4002, 20, 5.0, 0.0, session,
            live_auctions_raw=live_auctions_raw,
        )

    async with session_factory() as session:
        record = await session.get(PlayerRecord, 4002)

    actual_delta = (record.next_scan_at - now).total_seconds()
    assert abs(actual_delta - DEFAULT_SCAN_INTERVAL_SECONDS) < 10, (
        f"Expected ~{DEFAULT_SCAN_INTERVAL_SECONDS}s default interval, got {actual_delta:.0f}s"
    )


async def test_expiry_scheduling_no_auctions_uses_default(scanner):
    """Test 22c: _classify_and_schedule uses DEFAULT_SCAN_INTERVAL_SECONDS when live_auctions_raw=None."""
    svc, session_factory, _ = scanner
    now = datetime.utcnow()

    async with session_factory() as session:
        session.add(PlayerRecord(
            ea_id=4003, name="No Auctions", rating=80, position="GK",
            nation="Germany", league="Bundesliga", club="Bayern", card_type="gold",
            last_scanned_at=now, is_active=True,
            listing_count=0, sales_per_hour=0.0,
        ))
        await session.commit()

    async with session_factory() as session:
        await svc._classify_and_schedule(4003, 0, 0.0, 0.0, session, live_auctions_raw=None)

    async with session_factory() as session:
        record = await session.get(PlayerRecord, 4003)

    actual_delta = (record.next_scan_at - now).total_seconds()
    assert abs(actual_delta - DEFAULT_SCAN_INTERVAL_SECONDS) < 10, (
        f"Expected ~{DEFAULT_SCAN_INTERVAL_SECONDS}s default interval, got {actual_delta:.0f}s"
    )


async def test_listing_purge(scanner):
    """Test 22: run_cleanup deletes resolved and orphaned ListingObservation rows older than retention."""
    svc, session_factory, _ = scanner
    now = datetime.utcnow()
    old_time = now - timedelta(days=LISTING_RETENTION_DAYS + 3)

    async with session_factory() as session:
        # Resolved observation — old resolved_at should be purged
        session.add(ListingObservation(
            fingerprint="old_resolved:1",
            ea_id=5001,
            buy_now_price=25000,
            market_price_at_obs=20000,
            first_seen_at=old_time,
            last_seen_at=old_time,
            scan_count=1,
            outcome="sold",
            resolved_at=old_time,
        ))
        # Orphaned unresolved — old last_seen_at should be purged
        session.add(ListingObservation(
            fingerprint="old_orphaned:1",
            ea_id=5001,
            buy_now_price=26000,
            market_price_at_obs=20000,
            first_seen_at=old_time,
            last_seen_at=old_time,
            scan_count=1,
            outcome=None,
            resolved_at=None,
        ))
        # Recent resolved — should be preserved
        session.add(ListingObservation(
            fingerprint="recent_resolved:1",
            ea_id=5001,
            buy_now_price=24000,
            market_price_at_obs=20000,
            first_seen_at=now - timedelta(days=2),
            last_seen_at=now - timedelta(days=1),
            scan_count=2,
            outcome="expired",
            resolved_at=now - timedelta(days=1),
        ))
        await session.commit()

    await svc.run_cleanup()

    async with session_factory() as session:
        obs = (await session.execute(select(ListingObservation))).scalars().all()

    assert len(obs) == 1, f"Expected 1 listing observation preserved, got {len(obs)}"
    assert obs[0].fingerprint == "recent_resolved:1"
