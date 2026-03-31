"""Integration tests for POST /api/v1/portfolio/confirm endpoint."""
from __future__ import annotations

import pytest
from datetime import datetime, timedelta

from httpx import AsyncClient, ASGITransport
from sqlalchemy import select as sa_select

from src.server.db import create_engine_and_tables
from src.server.models_db import PlayerRecord, PlayerScore, PortfolioSlot
from src.server.api.portfolio import router as portfolio_router
from src.config import TARGET_PLAYER_COUNT
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


# ── Test 1: POST /confirm seeds PortfolioSlot rows ────────────────────────────

async def test_confirm_seeds_portfolio_slots(portfolio_app):
    """POST /confirm inserts PortfolioSlot rows for each player in the request."""
    app, session_factory = portfolio_app
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/v1/portfolio/confirm",
            json={
                "players": [
                    {"ea_id": 2001, "buy_price": 15000, "sell_price": 18000},
                    {"ea_id": 2002, "buy_price": 35000, "sell_price": 42000},
                ]
            },
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["confirmed"] == 2
    assert body["status"] == "ok"

    # Verify DB rows created
    async with session_factory() as session:
        result = await session.execute(sa_select(PortfolioSlot))
        slots = result.scalars().all()
    assert len(slots) == 2
    ea_ids = {s.ea_id for s in slots}
    assert ea_ids == {2001, 2002}


# ── Test 2: second confirm clears first (clean slate) ─────────────────────────

async def test_confirm_clears_existing_slots(portfolio_app):
    """POST /confirm twice: second call clears first set and seeds new players."""
    app, session_factory = portfolio_app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # First confirm
        resp1 = await client.post(
            "/api/v1/portfolio/confirm",
            json={
                "players": [
                    {"ea_id": 2001, "buy_price": 15000, "sell_price": 18000},
                    {"ea_id": 2002, "buy_price": 35000, "sell_price": 42000},
                ]
            },
        )
        assert resp1.status_code == 200

        # Second confirm with different players
        resp2 = await client.post(
            "/api/v1/portfolio/confirm",
            json={
                "players": [
                    {"ea_id": 3001, "buy_price": 20000, "sell_price": 24000},
                ]
            },
        )
        assert resp2.status_code == 200
        body2 = resp2.json()
        assert body2["confirmed"] == 1

    # Only the second confirm's player should be in DB
    async with session_factory() as session:
        result = await session.execute(sa_select(PortfolioSlot))
        slots = result.scalars().all()
    assert len(slots) == 1
    assert slots[0].ea_id == 3001


# ── Test 3: empty players list confirms with 0 ────────────────────────────────

async def test_confirm_empty_players(portfolio_app):
    """POST /confirm with empty players list confirms 0 and clears existing slots."""
    app, session_factory = portfolio_app

    # Pre-seed a slot to verify it gets cleared
    async with session_factory() as session:
        session.add(PortfolioSlot(
            ea_id=9999,
            buy_price=10000,
            sell_price=12000,
            added_at=datetime.utcnow(),
        ))
        await session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/v1/portfolio/confirm",
            json={"players": []},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["confirmed"] == 0
    assert body["status"] == "ok"

    # Previous slot should be cleared
    async with session_factory() as session:
        result = await session.execute(sa_select(PortfolioSlot))
        slots = result.scalars().all()
    assert len(slots) == 0


# ── Test 4: server caps active slots at TARGET_PLAYER_COUNT ───────────────────

async def test_confirm_caps_slots_at_target_player_count(portfolio_app):
    """POST /confirm with > TARGET_PLAYER_COUNT players inserts at most TARGET_PLAYER_COUNT slots.

    Bug: confirm_portfolio had no server-side cap on active slot count.
    A buggy client (or replayed request) sending 135 players would create 135 slots.
    Fix: server truncates to TARGET_PLAYER_COUNT before inserting.
    """
    app, session_factory = portfolio_app

    # Send TARGET_PLAYER_COUNT + 35 players (simulates the 135-player bug)
    overshoot_count = TARGET_PLAYER_COUNT + 35
    players = [
        {"ea_id": 5000 + i, "buy_price": 10000, "sell_price": 12000}
        for i in range(overshoot_count)
    ]

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/v1/portfolio/confirm", json={"players": players})

    assert resp.status_code == 200

    # DB must have at most TARGET_PLAYER_COUNT active (non-leftover) slots
    async with session_factory() as session:
        result = await session.execute(
            sa_select(PortfolioSlot).where(PortfolioSlot.is_leftover == False)  # noqa: E712
        )
        active_slots = result.scalars().all()

    assert len(active_slots) <= TARGET_PLAYER_COUNT, (
        f"OVERSHOOT BUG: confirm inserted {len(active_slots)} active slots, "
        f"expected at most {TARGET_PLAYER_COUNT}. "
        "Server must enforce the cap regardless of what the client sends."
    )
