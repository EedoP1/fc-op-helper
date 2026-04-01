"""Tests for ScannerService: tier classification, scan lifecycle, bootstrap, scheduling."""
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from sqlalchemy import select

from src.server.db import create_engine_and_tables
from src.server.models_db import (
    PlayerRecord, PlayerScore,
    MarketSnapshot,
    ListingObservation,
)
from src.server.circuit_breaker import CircuitBreaker, CBState
from src.config import (
    SCAN_INTERVAL_SECONDS,
    MARKET_DATA_RETENTION_DAYS,
    LISTING_RETENTION_DAYS,
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
    # Replace the real FutGGClient with a mock so no real HTTP calls are made.
    # Use MagicMock (not AsyncMock) because scanner calls get_player_market_data_sync
    # synchronously via run_in_executor — AsyncMock would return a coroutine object.
    mock_client = MagicMock()
    mock_client.start = AsyncMock()
    mock_client.stop = AsyncMock()
    svc._client = mock_client
    yield svc, session_factory, mock_client


# ── scan_player tests ─────────────────────────────────────────────────────────

@patch("src.server.scanner.score_player_v2", new_callable=AsyncMock, return_value={
    "ea_id": 100, "buy_price": 20000, "sell_price": 24000,
    "net_profit": 2800, "margin_pct": 20, "op_sold": 5,
    "op_total": 50, "op_sell_rate": 0.1,
    "expected_profit_per_hour": 280.0, "efficiency": 0.014,
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
    mock_client.get_player_market_data_sync = MagicMock(return_value=market_data)

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
    assert row.expected_profit_per_hour == 280.0


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

    mock_client.get_player_market_data_sync = MagicMock()

    await svc.scan_player(999)

    # No API call should have been made
    mock_client.get_player_market_data_sync.assert_not_called()

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
    mock_client.get_player_market_data_sync = MagicMock(
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
    "op_total": 50, "op_sell_rate": 0.1,
    "expected_profit_per_hour": 280.0, "efficiency": 0.014,
})
async def test_snapshot_created_on_scan(mock_score, scanner):
    """Test 15: scan_player creates a MarketSnapshot with correct fields."""
    svc, session_factory, mock_client = scanner
    market_data = make_player(ea_id=100, price=20000, num_sales=50, num_listings=30)
    mock_client.get_player_market_data_sync = MagicMock(return_value=market_data)

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
    assert snap.listing_count == 30


async def test_no_snapshot_on_none_market_data(scanner):
    """Test 18: scan_player with None market_data creates no snapshot rows."""
    svc, session_factory, mock_client = scanner
    mock_client.get_player_market_data_sync = MagicMock(return_value=None)

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
        )
        session.add(old_snap)

        recent_snap = MarketSnapshot(
            ea_id=200, captured_at=recent_time,
            current_lowest_bin=10000, listing_count=20,
        )
        session.add(recent_snap)
        await session.commit()

    await svc.run_cleanup()

    async with session_factory() as session:
        snaps = (await session.execute(select(MarketSnapshot))).scalars().all()

    assert len(snaps) == 1, f"Expected 1 snapshot after cleanup, got {len(snaps)}"
    assert snaps[0].captured_at == recent_time


async def test_cleanup_preserves_recent_snapshots(scanner):
    """Test 20: run_cleanup preserves all snapshots within the retention window."""
    svc, session_factory, _ = scanner
    now = datetime.utcnow()

    async with session_factory() as session:
        for days_ago in [1, 10, 25]:
            snap = MarketSnapshot(
                ea_id=300, captured_at=now - timedelta(days=days_ago),
                current_lowest_bin=15000, listing_count=25,
            )
            session.add(snap)
        await session.commit()

    await svc.run_cleanup()

    async with session_factory() as session:
        snaps = (await session.execute(select(MarketSnapshot))).scalars().all()

    assert len(snaps) == 3, f"Expected 3 snapshots preserved, got {len(snaps)}"


# ── Fixed scan interval test ──────────────────────────────────────────────────

@patch("src.server.scanner.score_player_v2", new_callable=AsyncMock, return_value={
    "ea_id": 5001, "buy_price": 20000, "sell_price": 24000,
    "net_profit": 2800, "margin_pct": 20, "op_sold": 5,
    "op_total": 50, "op_sell_rate": 0.1,
    "expected_profit_per_hour": 280.0, "efficiency": 0.014,
})
async def test_fixed_5min_scan_interval(mock_v2, scanner):
    """Test 21: scan_player schedules next_scan_at at exactly SCAN_INTERVAL_SECONDS (300s) from now."""
    svc, session_factory, mock_client = scanner
    now = datetime.utcnow()

    market_data = make_player(
        ea_id=5001, name="Fixed Interval", price=20000, num_sales=50, num_listings=25,
    )
    mock_client.get_player_market_data_sync = MagicMock(return_value=market_data)

    async with session_factory() as session:
        session.add(PlayerRecord(
            ea_id=5001, name="Fixed Interval", rating=85, position="ST",
            nation="England", league="EPL", club="Arsenal", card_type="gold",
            last_scanned_at=now, is_active=True,
            listing_count=25, sales_per_hour=10.0,
        ))
        await session.commit()

    await svc.scan_player(5001)

    async with session_factory() as session:
        record = await session.get(PlayerRecord, 5001)

    assert record is not None, "PlayerRecord should exist after scan"
    assert record.next_scan_at is not None, "next_scan_at should be set"
    actual_delta = (record.next_scan_at - now).total_seconds()
    assert abs(actual_delta - SCAN_INTERVAL_SECONDS) < 10, (
        f"Expected ~{SCAN_INTERVAL_SECONDS}s fixed interval, got {actual_delta:.0f}s"
    )


async def test_listing_purge(scanner):
    """Test 22: run_cleanup deletes orphaned unresolved ListingObservation rows
    and old DailyListingSummary rows beyond retention.

    Resolved observations are now deleted inline during resolve_outcomes(),
    so cleanup only handles orphaned unresolved observations and old summaries.
    """
    from src.server.models_db import DailyListingSummary
    svc, session_factory, _ = scanner
    now = datetime.utcnow()
    old_time = now - timedelta(days=LISTING_RETENTION_DAYS + 3)
    old_date_str = old_time.strftime("%Y-%m-%d")
    recent_date_str = (now - timedelta(days=1)).strftime("%Y-%m-%d")

    async with session_factory() as session:
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
        # Recent unresolved — should be preserved
        session.add(ListingObservation(
            fingerprint="recent_unresolved:1",
            ea_id=5001,
            buy_now_price=24000,
            market_price_at_obs=20000,
            first_seen_at=now - timedelta(days=2),
            last_seen_at=now - timedelta(days=1),
            scan_count=2,
            outcome=None,
            resolved_at=None,
        ))
        # Old daily summary — should be purged
        session.add(DailyListingSummary(
            ea_id=5001, date=old_date_str, margin_pct=10,
            op_listed_count=5, op_sold_count=3, op_expired_count=2,
            total_listed_count=20, total_sold_count=15, total_expired_count=5,
        ))
        # Recent daily summary — should be preserved
        session.add(DailyListingSummary(
            ea_id=5001, date=recent_date_str, margin_pct=10,
            op_listed_count=5, op_sold_count=3, op_expired_count=2,
            total_listed_count=20, total_sold_count=15, total_expired_count=5,
        ))
        await session.commit()

    await svc.run_cleanup()

    async with session_factory() as session:
        obs = (await session.execute(select(ListingObservation))).scalars().all()
        summaries = (await session.execute(select(DailyListingSummary))).scalars().all()

    assert len(obs) == 1, f"Expected 1 listing observation preserved, got {len(obs)}"
    assert obs[0].fingerprint == "recent_unresolved:1"
    assert len(summaries) == 1, f"Expected 1 summary preserved, got {len(summaries)}"
    assert summaries[0].date == recent_date_str


# ── Deduplication and name population tests ──────────────────────────────────

@patch("src.server.scanner.score_player_v2", new_callable=AsyncMock, return_value={
    "ea_id": 401, "buy_price": 20000, "sell_price": 24000,
    "net_profit": 2800, "margin_pct": 20, "op_sold": 5,
    "op_total": 50, "op_sell_rate": 0.1,
    "expected_profit_per_hour": 280.0, "efficiency": 0.014,
})
async def test_scan_player_populates_name(mock_v2, scanner):
    """scan_player updates PlayerRecord.name from market_data.player.name."""
    svc, session_factory, mock_client = scanner

    market_data = make_player(ea_id=401, name="Klostermann", price=20000, num_sales=10, num_listings=30)
    mock_client.get_player_market_data_sync = MagicMock(return_value=market_data)

    # Seed with ea_id as name (simulating the old behavior)
    async with session_factory() as session:
        session.add(PlayerRecord(
            ea_id=401, name="401", rating=80, position="CB",
            nation="Germany", league="Bundesliga", club="Leipzig", card_type="gold",
        ))
        await session.commit()

    await svc.scan_player(401)

    async with session_factory() as session:
        record = await session.get(PlayerRecord, 401)

    assert record is not None
    assert record.name == "Klostermann", f"Expected 'Klostermann', got '{record.name}'"


@patch("src.server.scanner.score_player_v2", new_callable=AsyncMock, return_value={
    "ea_id": 402, "buy_price": 20000, "sell_price": 24000,
    "net_profit": 2800, "margin_pct": 20, "op_sold": 5,
    "op_total": 50, "op_sell_rate": 0.1,
    "expected_profit_per_hour": 280.0, "efficiency": 0.014,
})
async def test_scan_player_sets_scorer_version(mock_v2, scanner):
    """scan_player sets scorer_version='v2' on PlayerScore rows from v2 scorer."""
    svc, session_factory, mock_client = scanner

    market_data = make_player(ea_id=402, name="Version Test", price=20000, num_sales=10, num_listings=30)
    mock_client.get_player_market_data_sync = MagicMock(return_value=market_data)

    async with session_factory() as session:
        _seed_player_record(session, 402)
        await session.commit()

    await svc.scan_player(402)

    async with session_factory() as session:
        result = await session.execute(
            select(PlayerScore).where(PlayerScore.ea_id == 402)
        )
        score = result.scalar_one_or_none()

    assert score is not None, "Expected a PlayerScore row"
    assert score.scorer_version == "v2", f"Expected scorer_version='v2', got '{score.scorer_version}'"
