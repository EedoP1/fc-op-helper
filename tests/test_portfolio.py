"""Integration tests for the portfolio optimization endpoint."""
from __future__ import annotations

import pytest
from datetime import datetime, timedelta

from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from src.server.db import create_engine_and_tables
from src.server.models_db import PlayerRecord, PlayerScore
from src.server.api.portfolio import router as portfolio_router
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
                expected_profit_per_hour=float(buy_price) * efficiency,
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
