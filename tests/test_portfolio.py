"""Integration tests for the portfolio optimization endpoint."""
from __future__ import annotations

import pytest
from datetime import datetime, timedelta

from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from src.server.db import create_engine_and_tables
from src.server.models_db import PlayerRecord, PlayerScore, MarketSnapshot, SnapshotPricePoint
from src.server.api.portfolio import router as portfolio_router, _get_volatile_ea_ids
from tests.test_api import make_test_app


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture
async def db():
    """In-memory SQLite DB for tests."""
    engine, session_factory = await create_engine_and_tables("sqlite+aiosqlite:///:memory:")
    yield engine, session_factory
    await engine.dispose()


@pytest.fixture
async def seeded_portfolio_app(db):
    """App with 5 seeded PlayerRecord + PlayerScore rows for portfolio tests."""
    engine, session_factory = db

    now = datetime.utcnow()
    stale_time = now - timedelta(hours=5)  # older than STALE_THRESHOLD_HOURS=4

    players_data = [
        # ea_id, name, rating, buy_price, efficiency, last_scanned_at
        (2001, "Porto A", 88, 15000, 0.05, now),
        (2002, "Porto B", 89, 35000, 0.04, now),
        (2003, "Porto C", 90, 60000, 0.03, stale_time),
        (2004, "Porto D", 87, 45000, 0.06, now),
        (2005, "Porto E", 86, 20000, 0.02, now),
    ]

    async with session_factory() as session:
        for ea_id, name, rating, buy_price, efficiency, last_scanned_at in players_data:
            rec = PlayerRecord(
                ea_id=ea_id,
                name=name,
                rating=rating,
                position="ST",
                nation="Brazil",
                league="LaLiga",
                club="Real Madrid",
                card_type="gold",
                scan_tier="normal",
                last_scanned_at=last_scanned_at,
                is_active=True,
                listing_count=30,
                sales_per_hour=10.0,
            )
            session.add(rec)

            score = PlayerScore(
                ea_id=ea_id,
                scored_at=now,
                buy_price=buy_price,
                sell_price=int(buy_price * 1.2),
                net_profit=int(buy_price * 0.14),
                margin_pct=20,
                op_sales=5,
                total_sales=50,
                op_ratio=0.1,
                expected_profit=float(buy_price) * efficiency,
                efficiency=efficiency,
                sales_per_hour=10.0,
                is_viable=True,
            )
            session.add(score)

        await session.commit()

    app = make_test_app(session_factory)
    app.include_router(portfolio_router)
    yield app


# ── Test 1: GET /api/v1/portfolio?budget=1000000 returns 200 with correct keys ─

async def test_portfolio_returns_200(seeded_portfolio_app):
    """GET /api/v1/portfolio?budget=1000000 returns 200 with expected keys."""
    async with AsyncClient(
        transport=ASGITransport(app=seeded_portfolio_app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/v1/portfolio?budget=1000000")
    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body
    assert "count" in body
    assert "budget" in body
    assert "budget_used" in body
    assert "budget_remaining" in body


# ── Test 2: budget_used <= budget ─────────────────────────────────────────────

async def test_portfolio_budget_constraint(seeded_portfolio_app):
    """budget_used in response does not exceed the budget query param."""
    async with AsyncClient(
        transport=ASGITransport(app=seeded_portfolio_app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/v1/portfolio?budget=50000")
    body = resp.json()
    assert body["budget_used"] <= 50000
    assert body["budget_remaining"] >= 0
    assert body["budget"] == 50000


# ── Test 3: budget=0 returns 422 ─────────────────────────────────────────────

async def test_portfolio_invalid_budget_zero(seeded_portfolio_app):
    """GET /api/v1/portfolio?budget=0 returns 422."""
    async with AsyncClient(
        transport=ASGITransport(app=seeded_portfolio_app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/v1/portfolio?budget=0")
    assert resp.status_code == 422


# ── Test 4: negative budget returns 422 ───────────────────────────────────────

async def test_portfolio_invalid_budget_negative(seeded_portfolio_app):
    """GET /api/v1/portfolio?budget=-100 returns 422."""
    async with AsyncClient(
        transport=ASGITransport(app=seeded_portfolio_app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/v1/portfolio?budget=-100")
    assert resp.status_code == 422


# ── Test 5: missing budget returns 422 ────────────────────────────────────────

async def test_portfolio_missing_budget(seeded_portfolio_app):
    """GET /api/v1/portfolio (no budget param) returns 422."""
    async with AsyncClient(
        transport=ASGITransport(app=seeded_portfolio_app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/v1/portfolio")
    assert resp.status_code == 422


# ── Test 6: each player has expected fields ───────────────────────────────────

async def test_portfolio_player_fields(seeded_portfolio_app):
    """Each player in 'data' has the required field set."""
    async with AsyncClient(
        transport=ASGITransport(app=seeded_portfolio_app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/v1/portfolio?budget=1000000")
    data = resp.json()["data"]
    assert len(data) > 0

    required_fields = {
        "ea_id", "name", "rating", "position", "price", "margin_pct",
        "op_ratio", "expected_profit", "efficiency",
        "is_stale", "last_scanned",
    }
    for player in data:
        missing = required_fields - set(player.keys())
        assert not missing, f"Player missing fields: {missing}"


# ── Test 7: empty DB returns 200 with error message ───────────────────────────

async def test_portfolio_empty_db(db):
    """Portfolio with no scored players returns 200 with error message and empty data."""
    _, session_factory = db
    app = make_test_app(session_factory)
    app.include_router(portfolio_router)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/v1/portfolio?budget=1000000")

    assert resp.status_code == 200
    body = resp.json()
    assert "error" in body
    assert body["data"] == []
    assert body["count"] == 0


# ── Volatility filter unit tests ───────────────────────────────────────────────

@pytest.fixture
async def volatile_db():
    """In-memory SQLite DB seeded with MarketSnapshot data for volatility tests."""
    engine, session_factory = await create_engine_and_tables("sqlite+aiosqlite:///:memory:")
    yield engine, session_factory
    await engine.dispose()


async def _seed_snapshots(session_factory, snapshots: list[tuple[int, datetime, int]]):
    """Seed MarketSnapshot + SnapshotPricePoint rows: (ea_id, captured_at, current_lowest_bin).

    Each MarketSnapshot is flushed to obtain its id, then a matching SnapshotPricePoint
    is added so _get_volatile_ea_ids (which reads SnapshotPricePoint) sees the same data.
    """
    async with session_factory() as session:
        for ea_id, captured_at, bin_price in snapshots:
            snapshot = MarketSnapshot(
                ea_id=ea_id,
                captured_at=captured_at,
                current_lowest_bin=bin_price,
                listing_count=10,
                live_auction_prices="[]",
            )
            session.add(snapshot)
            await session.flush()  # get snapshot.id
            session.add(SnapshotPricePoint(
                snapshot_id=snapshot.id,
                recorded_at=captured_at,
                lowest_bin=bin_price,
            ))
        await session.commit()


async def test_volatile_player_50pct_increase_is_flagged(volatile_db):
    """Player with 50% price increase over 3 days is returned in the volatile set."""
    engine, session_factory = volatile_db
    now = datetime.utcnow()
    # ea_id=3001: price went from 10000 to 15000 (+50%) over 3 days — volatile
    await _seed_snapshots(session_factory, [
        (3001, now - timedelta(days=2, hours=12), 10000),
        (3001, now - timedelta(days=1), 12000),
        (3001, now, 15000),
    ])
    async with session_factory() as session:
        volatile = await _get_volatile_ea_ids(session, [3001])
    assert 3001 in volatile


async def test_stable_player_10pct_increase_not_flagged(volatile_db):
    """Player with only 10% price increase is NOT returned in the volatile set."""
    engine, session_factory = volatile_db
    now = datetime.utcnow()
    # ea_id=3002: price went from 10000 to 11000 (+10%) — stable
    await _seed_snapshots(session_factory, [
        (3002, now - timedelta(days=2), 10000),
        (3002, now - timedelta(days=1), 10500),
        (3002, now, 11000),
    ])
    async with session_factory() as session:
        volatile = await _get_volatile_ea_ids(session, [3002])
    assert 3002 not in volatile


async def test_insufficient_data_not_flagged(volatile_db):
    """Player with fewer than 2 distinct timestamps in lookback window is NOT flagged."""
    engine, session_factory = volatile_db
    now = datetime.utcnow()
    # ea_id=3003: only one snapshot — cannot determine trend
    await _seed_snapshots(session_factory, [
        (3003, now - timedelta(hours=6), 10000),
    ])
    async with session_factory() as session:
        volatile = await _get_volatile_ea_ids(session, [3003])
    assert 3003 not in volatile


async def test_price_decrease_small_not_flagged(volatile_db):
    """Player whose price decreased modestly (within 30% threshold) is NOT flagged.

    MIN/MAX approach: (max - min) / min = (11000 - 9000) / 9000 ≈ 22% < 30% threshold.
    Player is stable despite a slight downward trend.
    """
    engine, session_factory = volatile_db
    now = datetime.utcnow()
    # ea_id=3004: price dropped from 11000 to 9000 (~22%) — within stable threshold
    await _seed_snapshots(session_factory, [
        (3004, now - timedelta(days=2), 11000),
        (3004, now - timedelta(days=1), 10000),
        (3004, now, 9000),
    ])
    async with session_factory() as session:
        volatile = await _get_volatile_ea_ids(session, [3004])
    assert 3004 not in volatile


async def test_mixed_players_only_volatile_flagged(volatile_db):
    """Multiple players — only the volatile ones appear in the volatile set."""
    engine, session_factory = volatile_db
    now = datetime.utcnow()
    # 3005: +50% — volatile
    # 3006: +10% — stable
    # 3007: -20% — stable
    await _seed_snapshots(session_factory, [
        (3005, now - timedelta(days=2), 10000),
        (3005, now, 15000),
        (3006, now - timedelta(days=2), 10000),
        (3006, now, 11000),
        (3007, now - timedelta(days=2), 20000),
        (3007, now, 16000),
    ])
    async with session_factory() as session:
        volatile = await _get_volatile_ea_ids(session, [3005, 3006, 3007])
    assert volatile == {3005}


# ── Volatility filter integration tests ───────────────────────────────────────

@pytest.fixture
async def volatility_integration_app(db):
    """App with one volatile player (50% spike) and one stable player.

    ea_id=4001: volatile — 50% price spike over 3 days
    ea_id=4002: stable — 10% price increase over 3 days
    Both are viable and active.
    """
    engine, session_factory = db
    now = datetime.utcnow()

    async with session_factory() as session:
        for ea_id, name in [(4001, "Volatile Player"), (4002, "Stable Player")]:
            rec = PlayerRecord(
                ea_id=ea_id,
                name=name,
                rating=88,
                position="ST",
                nation="Brazil",
                league="LaLiga",
                club="Real Madrid",
                card_type="gold",
                scan_tier="normal",
                last_scanned_at=now,
                is_active=True,
                listing_count=30,
                sales_per_hour=10.0,
            )
            session.add(rec)

            buy_price = 20000
            score = PlayerScore(
                ea_id=ea_id,
                scored_at=now,
                buy_price=buy_price,
                sell_price=int(buy_price * 1.2),
                net_profit=int(buy_price * 0.14),
                margin_pct=20,
                op_sales=5,
                total_sales=50,
                op_ratio=0.1,
                expected_profit=float(buy_price) * 0.05,
                efficiency=0.05,
                sales_per_hour=10.0,
                is_viable=True,
            )
            session.add(score)

        # Volatile player: 50% spike (10000 -> 15000) over 3 days
        for captured_at, bin_price in [
            (now - timedelta(days=2, hours=12), 10000),
            (now - timedelta(days=1), 12000),
            (now, 15000),
        ]:
            snapshot = MarketSnapshot(
                ea_id=4001,
                captured_at=captured_at,
                current_lowest_bin=bin_price,
                listing_count=30,
                live_auction_prices="[]",
            )
            session.add(snapshot)
            await session.flush()
            session.add(SnapshotPricePoint(
                snapshot_id=snapshot.id,
                recorded_at=captured_at,
                lowest_bin=bin_price,
            ))

        # Stable player: 10% increase (10000 -> 11000) over 3 days
        for captured_at, bin_price in [
            (now - timedelta(days=2), 10000),
            (now - timedelta(days=1), 10500),
            (now, 11000),
        ]:
            snapshot = MarketSnapshot(
                ea_id=4002,
                captured_at=captured_at,
                current_lowest_bin=bin_price,
                listing_count=30,
                live_auction_prices="[]",
            )
            session.add(snapshot)
            await session.flush()
            session.add(SnapshotPricePoint(
                snapshot_id=snapshot.id,
                recorded_at=captured_at,
                lowest_bin=bin_price,
            ))

        await session.commit()

    app = make_test_app(session_factory)
    app.include_router(portfolio_router)
    yield app


async def test_get_portfolio_excludes_volatile_player(volatility_integration_app):
    """GET /portfolio excludes volatile player (50% price spike) and includes stable player."""
    async with AsyncClient(
        transport=ASGITransport(app=volatility_integration_app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/v1/portfolio?budget=1000000")

    assert resp.status_code == 200
    body = resp.json()
    ea_ids_in_response = {p["ea_id"] for p in body["data"]}
    assert 4001 not in ea_ids_in_response, "Volatile player should be excluded"
    assert 4002 in ea_ids_in_response, "Stable player should be included"


async def test_generate_portfolio_excludes_volatile_player(volatility_integration_app):
    """POST /portfolio/generate excludes volatile player (50% price spike) and includes stable player."""
    async with AsyncClient(
        transport=ASGITransport(app=volatility_integration_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/v1/portfolio/generate",
            json={"budget": 1000000},
        )

    assert resp.status_code == 200
    body = resp.json()
    ea_ids_in_response = {p["ea_id"] for p in body["data"]}
    assert 4001 not in ea_ids_in_response, "Volatile player should be excluded"
    assert 4002 in ea_ids_in_response, "Stable player should be included"


async def test_mid_window_spike_returns_to_baseline_is_flagged(volatile_db):
    """Mid-window spike that returns to baseline IS flagged.

    _get_volatile_ea_ids uses MIN/MAX over the lookback window, not
    earliest/latest. A spike to 16000 from 10000 is a 60% absolute swing
    even if the price later returned to 10500, so the player is correctly
    flagged as volatile.
    """
    engine, session_factory = volatile_db
    now = datetime.utcnow()
    await _seed_snapshots(session_factory, [
        (3010, now - timedelta(days=2), 10000),
        (3010, now - timedelta(days=1), 16000),  # 60% swing from min — volatile
        (3010, now, 10500),
    ])
    async with session_factory() as session:
        volatile = await _get_volatile_ea_ids(session, [3010])
    assert 3010 in volatile, "60% MIN/MAX swing should be flagged as volatile"


# ── Absolute volatility threshold tests ───────────────────────────────────────

async def test_volatile_absolute_increase_above_threshold(volatile_db):
    """Player with 15% pct increase but 15k absolute increase IS flagged.

    15% < 30% percentage threshold, but 15000 > 10000 absolute threshold.
    Both conditions independently flag as volatile — absolute threshold catches this.
    """
    engine, session_factory = volatile_db
    now = datetime.utcnow()
    # ea_id=3020: 100k -> 115k (+15%, +15k) — pct below threshold, abs above threshold
    await _seed_snapshots(session_factory, [
        (3020, now - timedelta(days=2), 100_000),
        (3020, now - timedelta(days=1), 107_000),
        (3020, now, 115_000),
    ])
    async with session_factory() as session:
        volatile = await _get_volatile_ea_ids(session, [3020])
    assert 3020 in volatile, (
        "15% pct is below 30% threshold, but 15k abs > 10k abs threshold — should be flagged"
    )


async def test_stable_below_both_thresholds(volatile_db):
    """Player with 3% pct and 3k absolute increase is NOT flagged.

    Both below their respective thresholds (30% pct and 10k abs).
    """
    engine, session_factory = volatile_db
    now = datetime.utcnow()
    # ea_id=3021: 100k -> 103k (+3%, +3k) — below both thresholds
    await _seed_snapshots(session_factory, [
        (3021, now - timedelta(days=2), 100_000),
        (3021, now - timedelta(days=1), 101_500),
        (3021, now, 103_000),
    ])
    async with session_factory() as session:
        volatile = await _get_volatile_ea_ids(session, [3021])
    assert 3021 not in volatile, (
        "3% / 3k swing is below both thresholds — should not be flagged"
    )


async def test_volatile_pct_above_abs_below(volatile_db):
    """Player with 40% pct increase but only 8k absolute increase IS flagged.

    40% > 30% percentage threshold, but 8k < 10k absolute threshold.
    Percentage threshold alone is sufficient to flag.
    """
    engine, session_factory = volatile_db
    now = datetime.utcnow()
    # ea_id=3022: 20k -> 28k (+40%, +8k) — pct above threshold, abs below threshold
    await _seed_snapshots(session_factory, [
        (3022, now - timedelta(days=2), 20_000),
        (3022, now - timedelta(days=1), 24_000),
        (3022, now, 28_000),
    ])
    async with session_factory() as session:
        volatile = await _get_volatile_ea_ids(session, [3022])
    assert 3022 in volatile, (
        "40% pct > 30% threshold — should be flagged even though 8k abs < 10k abs threshold"
    )
