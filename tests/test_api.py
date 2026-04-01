"""Integration tests for FastAPI endpoints: /api/v1/players/top and /api/v1/health."""
from __future__ import annotations

import pytest
from datetime import datetime, timedelta

from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from src.server.db import create_engine_and_tables
from src.server.models_db import PlayerRecord, PlayerScore, ScannerStatus
from src.server.api.players import router as players_router
from src.server.api.health import router as health_router
from src.server.circuit_breaker import CBState


# ── Mock scanner ───────────────────────────────────────────────────────────────

class MockScannerService:
    """Minimal mock scanner for API tests (no real DB or HTTP)."""

    def __init__(self):
        self.is_running = True
        self.last_scan_at = None

    def success_rate_1h(self) -> float:
        return 1.0

    async def count_players(self) -> int:
        return 0

    def queue_depth(self) -> int:
        return 0


class MockCircuitBreaker:
    """Minimal mock circuit breaker for API tests."""

    def __init__(self):
        self.state = CBState.CLOSED


# ── Test app factory ───────────────────────────────────────────────────────────

def make_test_app(session_factory, scanner=None, cb=None):
    """Create a FastAPI app with app.state pre-wired (no real lifespan).

    State is set directly on the app object before requests are made.
    """
    if scanner is None:
        scanner = MockScannerService()
    if cb is None:
        cb = MockCircuitBreaker()

    app = FastAPI(title="OP Seller Test")
    app.include_router(players_router)
    app.include_router(health_router)

    # Set state directly — ASGITransport does not trigger lifespan events,
    # so we wire state before the app handles any requests.
    app.state.session_factory = session_factory
    app.state.read_session_factory = session_factory
    app.state.scanner = scanner
    app.state.circuit_breaker = cb

    return app


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture
async def db():
    """In-memory SQLite DB for tests."""
    engine, session_factory = await create_engine_and_tables("sqlite+aiosqlite:///:memory:")
    yield engine, session_factory
    await engine.dispose()


@pytest.fixture
async def seeded_app(db):
    """App with 5 seeded PlayerRecord + PlayerScore rows."""
    engine, session_factory = db

    now = datetime.utcnow()
    stale_time = now - timedelta(hours=5)  # older than STALE_THRESHOLD_HOURS=4

    players_data = [
        # ea_id, name, rating, buy_price, efficiency, last_scanned_at
        (1001, "Player A", 88, 15000, 0.05, now),          # cheap, not in 20k-50k range
        (1002, "Player B", 89, 35000, 0.04, now),          # in 20k-50k range
        (1003, "Player C", 90, 60000, 0.03, stale_time),   # stale (>4 hrs), outside 20k-50k range
        (1004, "Player D", 87, 45000, 0.06, now),          # highest efficiency, in 20k-50k range
        (1005, "Player E", 86, 20000, 0.02, now),          # low efficiency, in 20k-50k range
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
    yield app


# ── Test 1: GET /api/v1/players/top returns 200 with correct structure ─────────

async def test_top_players_returns_200_and_structure(seeded_app):
    """Test 1: GET /api/v1/players/top returns 200 with {data, count, offset, limit}."""
    async with AsyncClient(transport=ASGITransport(app=seeded_app), base_url="http://test") as client:
        resp = await client.get("/api/v1/players/top")
    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body
    assert "count" in body
    assert "offset" in body
    assert "limit" in body
    assert body["offset"] == 0
    assert body["limit"] == 100


# ── Test 2: Results are ordered by efficiency desc ─────────────────────────────

async def test_top_players_ordered_by_efficiency(seeded_app):
    """Test 2: GET /api/v1/players/top returns players ordered by efficiency descending."""
    async with AsyncClient(transport=ASGITransport(app=seeded_app), base_url="http://test") as client:
        resp = await client.get("/api/v1/players/top")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data) == 5
    efficiencies = [p["efficiency"] for p in data]
    assert efficiencies == sorted(efficiencies, reverse=True)


# ── Test 3: Price filter (price_min / price_max) ───────────────────────────────

async def test_top_players_price_filter(seeded_app):
    """Test 3: GET /api/v1/players/top?price_min=20000&price_max=50000 filters by buy_price."""
    async with AsyncClient(transport=ASGITransport(app=seeded_app), base_url="http://test") as client:
        resp = await client.get("/api/v1/players/top?price_min=20000&price_max=50000")
    assert resp.status_code == 200
    data = resp.json()["data"]
    # Players in 20k-50k range: Player B (35k), Player D (45k), Player E (20k)
    assert len(data) == 3
    for p in data:
        assert 20000 <= p["price"] <= 50000


# ── Test 4: Pagination (limit / offset) ───────────────────────────────────────

async def test_top_players_pagination(seeded_app):
    """Test 4: Pagination via limit/offset returns correct subsets."""
    async with AsyncClient(transport=ASGITransport(app=seeded_app), base_url="http://test") as client:
        resp_all = await client.get("/api/v1/players/top")
        resp_paged = await client.get("/api/v1/players/top?limit=2&offset=0")
        resp_offset = await client.get("/api/v1/players/top?limit=2&offset=2")

    all_data = resp_all.json()["data"]
    paged_data = resp_paged.json()["data"]
    offset_data = resp_offset.json()["data"]

    # First page: top 2 by efficiency
    assert len(paged_data) == 2
    assert paged_data[0]["ea_id"] == all_data[0]["ea_id"]
    assert paged_data[1]["ea_id"] == all_data[1]["ea_id"]

    # Second page: next 2 by efficiency
    assert len(offset_data) == 2
    assert offset_data[0]["ea_id"] == all_data[2]["ea_id"]

    # Response carries back the requested limit/offset
    assert resp_paged.json()["limit"] == 2
    assert resp_paged.json()["offset"] == 0
    assert resp_offset.json()["offset"] == 2


# ── Test 5: Staleness flag ─────────────────────────────────────────────────────

async def test_top_players_staleness(seeded_app):
    """Test 5: Players with last_scanned_at older than 4 hours have is_stale=true."""
    async with AsyncClient(transport=ASGITransport(app=seeded_app), base_url="http://test") as client:
        resp = await client.get("/api/v1/players/top")
    data = resp.json()["data"]
    stale_players = [p for p in data if p["ea_id"] == 1003]
    fresh_players = [p for p in data if p["ea_id"] != 1003]

    assert len(stale_players) == 1
    assert stale_players[0]["is_stale"] is True

    for p in fresh_players:
        assert p["is_stale"] is False


# ── Test 6: All D-04 fields present ───────────────────────────────────────────

async def test_top_players_all_fields_present(seeded_app):
    """Test 6: Each player object contains all D-04 fields."""
    async with AsyncClient(transport=ASGITransport(app=seeded_app), base_url="http://test") as client:
        resp = await client.get("/api/v1/players/top")
    data = resp.json()["data"]
    assert len(data) > 0

    required_fields = {
        "ea_id", "name", "price", "margin_pct", "op_ratio",
        "expected_profit", "efficiency", "last_scanned", "is_stale",
    }
    for player in data:
        missing = required_fields - set(player.keys())
        assert not missing, f"Player missing fields: {missing}"


# ── Test 7: GET /api/v1/health returns all D-10 fields ────────────────────────

async def test_health_returns_all_fields(db):
    """Test 7: GET /api/v1/health returns 200 with all D-10 fields."""
    _, session_factory = db

    # Seed ScannerStatus row so health endpoint returns real values
    async with session_factory() as session:
        session.add(ScannerStatus(
            id=1,
            is_running=True,
            last_scan_at=None,
            success_rate_1h=1.0,
            queue_depth=0,
            circuit_breaker_state="closed",
            updated_at=datetime.utcnow(),
        ))
        await session.commit()

    scanner = MockScannerService()
    cb = MockCircuitBreaker()
    app = make_test_app(session_factory, scanner=scanner, cb=cb)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/health")

    assert resp.status_code == 200
    body = resp.json()
    assert "scanner_status" in body
    assert "circuit_breaker" in body
    assert "scan_success_rate_1h" in body
    assert "last_scan_at" in body
    assert "players_in_db" in body
    assert "queue_depth" in body

    # Verify values match the mock scanner/cb state
    assert body["scanner_status"] == "running"
    assert body["circuit_breaker"] == "closed"
    assert body["scan_success_rate_1h"] == 1.0
    assert body["last_scan_at"] is None
    assert body["players_in_db"] == 0
    assert body["queue_depth"] == 0


# ── Fixture: seeded app with score history ────────────────────────────────────

@pytest.fixture
async def seeded_app_with_history(db):
    """App with players seeded for player detail + trend tests.

    Seeds:
    - ea_id=2001: 5 PlayerScore rows with increasing efficiency (trend "up")
    - ea_id=2002: 1 PlayerScore row (trend "stable")
    - ea_id=2003: PlayerRecord only, no PlayerScore rows (no-viable-scores case)
    """
    engine, session_factory = db
    now = datetime.utcnow()

    async with session_factory() as session:
        # Player 2001 — 5 scores with increasing efficiency
        session.add(PlayerRecord(
            ea_id=2001, name="Trend Up Player", rating=90, position="CAM",
            nation="France", league="Ligue 1", club="PSG", card_type="gold",
            scan_tier="hot", last_scanned_at=now, is_active=True,
            listing_count=40, sales_per_hour=12.0,
        ))
        for i in range(5):
            eff = 0.01 + i * 0.01  # 0.01, 0.02, 0.03, 0.04, 0.05
            session.add(PlayerScore(
                ea_id=2001,
                scored_at=now - timedelta(hours=4 - i),  # oldest first: -4h, -3h, -2h, -1h, now
                buy_price=30000,
                sell_price=36000,
                net_profit=4200,
                margin_pct=20,
                op_sales=5,
                total_sales=50,
                op_ratio=0.1,
                expected_profit=30000.0 * eff,
                efficiency=eff,
                sales_per_hour=10.0,
                is_viable=True,
            ))

        # Player 2002 — 1 score (stable trend)
        session.add(PlayerRecord(
            ea_id=2002, name="Stable Player", rating=85, position="CM",
            nation="Spain", league="LaLiga", club="Barcelona", card_type="gold",
            scan_tier="normal", last_scanned_at=now, is_active=True,
            listing_count=25, sales_per_hour=8.0,
        ))
        session.add(PlayerScore(
            ea_id=2002,
            scored_at=now,
            buy_price=25000,
            sell_price=30000,
            net_profit=3500,
            margin_pct=20,
            op_sales=3,
            total_sales=40,
            op_ratio=0.075,
            expected_profit=1875.0,
            efficiency=0.075,
            sales_per_hour=8.0,
            is_viable=True,
        ))

        # Player 2003 — record only, no scores
        session.add(PlayerRecord(
            ea_id=2003, name="No Scores Player", rating=80, position="CB",
            nation="Germany", league="Bundesliga", club="Bayern", card_type="gold",
            scan_tier="cold", last_scanned_at=now, is_active=True,
            listing_count=10, sales_per_hour=3.0,
        ))

        await session.commit()

    app = make_test_app(session_factory)
    yield app


# ── Test 8: Player detail returns 200 ────────────────────────────────────────

async def test_player_detail_returns_200(seeded_app_with_history):
    """Test 8: GET /api/v1/players/2001 returns 200."""
    async with AsyncClient(
        transport=ASGITransport(app=seeded_app_with_history), base_url="http://test"
    ) as client:
        resp = await client.get("/api/v1/players/2001")
    assert resp.status_code == 200


# ── Test 9: Player detail has all required fields ────────────────────────────

async def test_player_detail_fields(seeded_app_with_history):
    """Test 9: Player detail response contains all required keys."""
    async with AsyncClient(
        transport=ASGITransport(app=seeded_app_with_history), base_url="http://test"
    ) as client:
        resp = await client.get("/api/v1/players/2001")
    assert resp.status_code == 200
    body = resp.json()

    # Top-level keys
    top_keys = {
        "ea_id", "name", "rating", "position", "nation", "league", "club",
        "card_type", "last_scanned", "is_stale",
        "current_score", "score_history", "trend",
    }
    missing = top_keys - set(body.keys())
    assert not missing, f"Missing top-level keys: {missing}"

    # current_score keys
    cs = body["current_score"]
    assert cs is not None
    cs_keys = {
        "buy_price", "sell_price", "net_profit", "margin_pct", "op_sales",
        "total_sales", "op_ratio", "expected_profit", "efficiency",
        "sales_per_hour", "scored_at",
    }
    cs_missing = cs_keys - set(cs.keys())
    assert not cs_missing, f"Missing current_score keys: {cs_missing}"

    # score_history is a list
    assert isinstance(body["score_history"], list)
    assert len(body["score_history"]) > 0

    # trend keys
    trend_keys = {"direction", "price_change", "efficiency_change"}
    t_missing = trend_keys - set(body["trend"].keys())
    assert not t_missing, f"Missing trend keys: {t_missing}"


# ── Test 10: Player detail 404 for unknown player ────────────────────────────

async def test_player_detail_not_found(seeded_app_with_history):
    """Test 10: GET /api/v1/players/999999 returns 404 with 'Player not found'."""
    async with AsyncClient(
        transport=ASGITransport(app=seeded_app_with_history), base_url="http://test"
    ) as client:
        resp = await client.get("/api/v1/players/999999")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Player not found"


# ── Test 11: Trend direction "up" for increasing efficiency ──────────────────

async def test_player_detail_trend_up(seeded_app_with_history):
    """Test 11: Player 2001 with increasing efficiency shows trend direction 'up'."""
    async with AsyncClient(
        transport=ASGITransport(app=seeded_app_with_history), base_url="http://test"
    ) as client:
        resp = await client.get("/api/v1/players/2001")
    body = resp.json()
    assert body["trend"]["direction"] == "up"
    assert body["trend"]["efficiency_change"] > 0


# ── Test 12: Trend direction "stable" for single score ───────────────────────

async def test_player_detail_trend_stable(seeded_app_with_history):
    """Test 12: Player 2002 with single score entry shows trend direction 'stable'."""
    async with AsyncClient(
        transport=ASGITransport(app=seeded_app_with_history), base_url="http://test"
    ) as client:
        resp = await client.get("/api/v1/players/2002")
    body = resp.json()
    assert body["trend"]["direction"] == "stable"


# ── Test 13: No viable scores returns null current_score and stable trend ────

async def test_player_detail_no_viable_scores(seeded_app_with_history):
    """Test 13: Player 2003 with no PlayerScore rows returns current_score=null, empty history, stable trend."""
    async with AsyncClient(
        transport=ASGITransport(app=seeded_app_with_history), base_url="http://test"
    ) as client:
        resp = await client.get("/api/v1/players/2003")
    assert resp.status_code == 200
    body = resp.json()
    assert body["current_score"] is None
    assert body["score_history"] == []
    assert body["trend"]["direction"] == "stable"
