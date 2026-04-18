"""Integration tests for GET /api/v1/portfolio/card-types endpoint."""
from __future__ import annotations

import pytest
from datetime import datetime

from httpx import AsyncClient, ASGITransport

from src.server.db import create_engine_and_tables
from src.server.models_db import PlayerRecord
from src.server.api.portfolio import router as portfolio_router
from tests.test_api import make_test_app


@pytest.fixture
async def db():
    """In-memory SQLite DB for tests."""
    engine, session_factory = await create_engine_and_tables("sqlite+aiosqlite:///:memory:")
    yield engine, session_factory
    await engine.dispose()


async def _seed(session_factory, rows):
    """rows = [(ea_id, card_type, is_active)]"""
    now = datetime.utcnow()
    async with session_factory() as session:
        for ea_id, card_type, is_active in rows:
            session.add(PlayerRecord(
                ea_id=ea_id,
                name=f"P{ea_id}",
                rating=85,
                position="ST",
                nation="Brazil",
                league="LaLiga",
                club="Real Madrid",
                card_type=card_type,
                scan_tier="normal",
                last_scanned_at=now,
                is_active=is_active,
                listing_count=30,
                sales_per_hour=10.0,
            ))
        await session.commit()


async def test_card_types_returns_sorted_counts(db):
    """Endpoint returns list of {card_type, count} sorted by count DESC from active rows only."""
    _, session_factory = db
    await _seed(session_factory, [
        (3001, "Team of the Season", True),
        (3002, "Team of the Season", True),
        (3003, "Team of the Season", True),
        (3004, "Rare", True),
        (3005, "Rare", True),
        (3006, "TOTY ICON", True),
        (3007, "Inactive Type", False),  # must be excluded
    ])
    app = make_test_app(session_factory)
    app.include_router(portfolio_router)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/portfolio/card-types")

    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    # Shape check
    for entry in body:
        assert set(entry.keys()) == {"card_type", "count"}
        assert isinstance(entry["card_type"], str)
        assert isinstance(entry["count"], int)
    # Inactive-row card_type must not appear
    assert "Inactive Type" not in [e["card_type"] for e in body]
    # DESC-by-count ordering
    counts = [e["count"] for e in body]
    assert counts == sorted(counts, reverse=True)
    # First entry is the most frequent
    assert body[0] == {"card_type": "Team of the Season", "count": 3}
    # Total: 3 distinct active card_types
    assert len(body) == 3


async def test_card_types_empty_db_returns_empty_list(db):
    """Empty DB → 200 with empty list (not an error object)."""
    _, session_factory = db
    app = make_test_app(session_factory)
    app.include_router(portfolio_router)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/portfolio/card-types")

    assert resp.status_code == 200
    assert resp.json() == []
