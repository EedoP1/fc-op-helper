"""Integration tests for POST /api/v1/portfolio/generate endpoint."""
from __future__ import annotations

import pytest
from datetime import datetime, timedelta

from httpx import AsyncClient, ASGITransport

from src.server.db import create_engine_and_tables
from src.server.models_db import PlayerRecord, PlayerScore, PortfolioSlot
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
    stale_time = now - timedelta(hours=5)

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
    yield app, session_factory


# ── Test 1: POST /api/v1/portfolio/generate returns 200 with correct keys ──────

async def test_generate_returns_200(seeded_portfolio_app):
    """POST /api/v1/portfolio/generate returns 200 with expected keys."""
    app, _ = seeded_portfolio_app
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/v1/portfolio/generate",
            json={"budget": 1000000},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body
    assert "count" in body
    assert "budget" in body
    assert "budget_used" in body
    assert "budget_remaining" in body


# ── Test 2: POST /api/v1/portfolio/generate does NOT create PortfolioSlot rows ──

async def test_generate_does_not_seed_portfolio_slots(seeded_portfolio_app):
    """POST /api/v1/portfolio/generate must NOT create any PortfolioSlot rows."""
    from sqlalchemy import select as sa_select

    app, session_factory = seeded_portfolio_app
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/v1/portfolio/generate",
            json={"budget": 1000000},
        )
    assert resp.status_code == 200

    # Verify no portfolio slots created
    async with session_factory() as session:
        result = await session.execute(sa_select(PortfolioSlot))
        slots = result.scalars().all()
    assert len(slots) == 0, f"Expected no PortfolioSlot rows, got {len(slots)}"


# ── Test 3: budget=0 returns 422 ──────────────────────────────────────────────

async def test_generate_invalid_budget_zero(seeded_portfolio_app):
    """POST /api/v1/portfolio/generate with budget=0 returns 422."""
    app, _ = seeded_portfolio_app
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/v1/portfolio/generate",
            json={"budget": 0},
        )
    assert resp.status_code == 422


# ── Test 4: missing budget returns 422 ────────────────────────────────────────

async def test_generate_missing_budget(seeded_portfolio_app):
    """POST /api/v1/portfolio/generate with no budget field returns 422."""
    app, _ = seeded_portfolio_app
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/v1/portfolio/generate",
            json={},
        )
    assert resp.status_code == 422


# ── Test 5: empty DB returns 200 with error message ───────────────────────────

async def test_generate_empty_db_returns_error_message(db):
    """POST /api/v1/portfolio/generate with no scored players returns error message."""
    _, session_factory = db
    app = make_test_app(session_factory)
    app.include_router(portfolio_router)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/v1/portfolio/generate",
            json={"budget": 1000000},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert "error" in body
    assert body["data"] == []
    assert body["count"] == 0
    assert body["budget"] == 1000000
    assert body["budget_used"] == 0
    assert body["budget_remaining"] == 1000000


# ── Test 6: response includes all required player fields ──────────────────────

async def test_generate_player_fields(seeded_portfolio_app):
    """Each player in generate response has the required field set."""
    app, _ = seeded_portfolio_app
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/v1/portfolio/generate",
            json={"budget": 1000000},
        )
    body = resp.json()
    data = body["data"]
    assert len(data) > 0

    required_fields = {
        "ea_id", "name", "rating", "position", "price",
        "margin_pct", "op_sales", "total_sales", "op_ratio",
        "expected_profit", "efficiency",
    }
    for player in data:
        missing = required_fields - set(player.keys())
        assert not missing, f"Player missing fields: {missing}"
