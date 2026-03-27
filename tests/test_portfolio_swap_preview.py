"""Integration tests for POST /api/v1/portfolio/swap-preview endpoint."""
from __future__ import annotations

import pytest
from datetime import datetime, timedelta

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
    """App with 5 seeded PlayerRecord + PlayerScore rows for swap-preview tests."""
    engine, session_factory = db

    now = datetime.utcnow()

    players_data = [
        # ea_id, name, rating, buy_price, efficiency
        (2001, "Porto A", 88, 15000, 0.05),
        (2002, "Porto B", 89, 35000, 0.04),
        (2003, "Porto C", 90, 60000, 0.03),
        (2004, "Porto D", 87, 45000, 0.06),
        (2005, "Porto E", 86, 20000, 0.02),
    ]

    async with session_factory() as session:
        for ea_id, name, rating, buy_price, efficiency in players_data:
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
                last_scanned_at=now,
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


# ── Test 1: swap-preview returns replacements excluding specified ea_ids ────────

async def test_swap_preview_returns_replacements(seeded_portfolio_app):
    """POST /swap-preview with freed_budget returns items with ea_id != excluded."""
    async with AsyncClient(
        transport=ASGITransport(app=seeded_portfolio_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/v1/portfolio/swap-preview",
            json={"freed_budget": 200000, "excluded_ea_ids": [2001]},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert "replacements" in body
    assert "count" in body
    assert isinstance(body["replacements"], list)

    # No excluded player appears in replacements
    returned_ea_ids = {r["ea_id"] for r in body["replacements"]}
    assert 2001 not in returned_ea_ids


# ── Test 2: replacements contain required fields ────────────────────────────────

async def test_swap_preview_replacement_fields(seeded_portfolio_app):
    """Each replacement item contains the required field set."""
    async with AsyncClient(
        transport=ASGITransport(app=seeded_portfolio_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/v1/portfolio/swap-preview",
            json={"freed_budget": 200000, "excluded_ea_ids": []},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["replacements"]) > 0

    required_fields = {
        "ea_id", "name", "rating", "position",
        "price", "sell_price", "margin_pct", "op_ratio",
        "expected_profit", "efficiency",
    }
    for item in body["replacements"]:
        missing = required_fields - set(item.keys())
        assert not missing, f"Replacement missing fields: {missing}"


# ── Test 3: all players excluded returns empty replacements ────────────────────

async def test_swap_preview_excludes_all_specified_ids(seeded_portfolio_app):
    """POST /swap-preview with all 5 ea_ids excluded returns empty replacements."""
    all_ids = [2001, 2002, 2003, 2004, 2005]
    async with AsyncClient(
        transport=ASGITransport(app=seeded_portfolio_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/v1/portfolio/swap-preview",
            json={"freed_budget": 200000, "excluded_ea_ids": all_ids},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["replacements"] == []
    assert body["count"] == 0


# ── Test 4: empty DB returns 200 with empty replacements ──────────────────────

async def test_swap_preview_empty_db(db):
    """POST /swap-preview with no viable players returns 200 with empty list."""
    _, session_factory = db
    app = make_test_app(session_factory)
    app.include_router(portfolio_router)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/v1/portfolio/swap-preview",
            json={"freed_budget": 50000, "excluded_ea_ids": []},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["replacements"] == []
    assert body["count"] == 0


# ── Test 5: freed_budget=0 returns 422 ────────────────────────────────────────

async def test_swap_preview_invalid_freed_budget(seeded_portfolio_app):
    """POST /swap-preview with freed_budget=0 returns 422."""
    async with AsyncClient(
        transport=ASGITransport(app=seeded_portfolio_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/v1/portfolio/swap-preview",
            json={"freed_budget": 0, "excluded_ea_ids": []},
        )
    assert resp.status_code == 422
