"""Integration tests for GET /api/v1/portfolio/confirmed endpoint."""
from __future__ import annotations

import pytest
from datetime import datetime

from httpx import AsyncClient, ASGITransport

from src.server.db import create_engine_and_tables
from src.server.models_db import PlayerRecord, PortfolioSlot
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
async def portfolio_app(db):
    """App with session_factory wired, no seeded data."""
    _, session_factory = db
    app = make_test_app(session_factory)
    app.include_router(portfolio_router)
    yield app, session_factory


# ── Test 1: GET /confirmed returns seeded slots with player metadata ───────────

async def test_confirmed_returns_seeded_slots(portfolio_app):
    """GET /confirmed returns count=2 with ea_id, name, rating, position, buy_price, sell_price."""
    app, session_factory = portfolio_app

    # Seed PlayerRecord + PortfolioSlot rows
    async with session_factory() as session:
        for ea_id, name, rating in [(3001, "Slot Alpha", 90), (3002, "Slot Beta", 87)]:
            session.add(PlayerRecord(
                ea_id=ea_id,
                name=name,
                rating=rating,
                position="CAM",
                nation="Brazil",
                league="LaLiga",
                club="Real Madrid",
                card_type="gold",
                scan_tier="normal",
                last_scanned_at=None,
                is_active=True,
                listing_count=20,
                sales_per_hour=8.0,
            ))
            session.add(PortfolioSlot(
                ea_id=ea_id,
                buy_price=20000,
                sell_price=24000,
                added_at=datetime.utcnow(),
            ))
        await session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/v1/portfolio/confirmed")

    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 2
    assert len(body["data"]) == 2

    # Check required fields in each item
    required_fields = {"ea_id", "name", "rating", "position", "buy_price", "sell_price"}
    for item in body["data"]:
        missing = required_fields - set(item.keys())
        assert not missing, f"Item missing fields: {missing}"

    # Verify player names come from PlayerRecord join
    returned_names = {item["name"] for item in body["data"]}
    assert "Slot Alpha" in returned_names
    assert "Slot Beta" in returned_names


# ── Test 2: GET /confirmed with no slots returns empty ─────────────────────────

async def test_confirmed_empty(portfolio_app):
    """GET /confirmed with no PortfolioSlot rows returns count=0 and empty data."""
    app, _ = portfolio_app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/v1/portfolio/confirmed")

    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 0
    assert body["data"] == []


# ── Test 3: GET /confirmed returns only slots with matching PlayerRecord ────────

async def test_confirmed_player_data_from_record(portfolio_app):
    """GET /confirmed each item's name, rating, position come from PlayerRecord."""
    app, session_factory = portfolio_app

    async with session_factory() as session:
        session.add(PlayerRecord(
            ea_id=4001,
            name="Test Player",
            rating=92,
            position="ST",
            nation="France",
            league="Ligue1",
            club="PSG",
            card_type="gold",
            scan_tier="hot",
            last_scanned_at=None,
            is_active=True,
            listing_count=50,
            sales_per_hour=15.0,
        ))
        session.add(PortfolioSlot(
            ea_id=4001,
            buy_price=50000,
            sell_price=60000,
            added_at=datetime.utcnow(),
        ))
        await session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/v1/portfolio/confirmed")

    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    item = body["data"][0]
    assert item["ea_id"] == 4001
    assert item["name"] == "Test Player"
    assert item["rating"] == 92
    assert item["position"] == "ST"
    assert item["buy_price"] == 50000
    assert item["sell_price"] == 60000
