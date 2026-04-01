"""Integration tests for GET /api/v1/profit/summary endpoint."""
from __future__ import annotations

import pytest
from datetime import datetime

from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from src.server.db import create_engine_and_tables
from src.server.models_db import PlayerRecord, TradeRecord


# ── Test app factory ───────────────────────────────────────────────────────────

def make_test_app(session_factory):
    """Create a minimal FastAPI app with profit router wired."""
    from src.server.api.profit import router as profit_router

    app = FastAPI(title="OP Seller Test — Profit")
    app.include_router(profit_router)
    app.state.session_factory = session_factory
    app.state.read_session_factory = session_factory
    return app


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture
async def db():
    """In-memory SQLite DB for tests."""
    engine, session_factory = await create_engine_and_tables("sqlite+aiosqlite:///:memory:")
    yield engine, session_factory
    await engine.dispose()


# ── Test 1: Empty DB returns zeroed totals and empty per_player list ──────────

async def test_profit_summary_empty(db):
    """GET /api/v1/profit/summary with no trade records returns all-zero totals and empty per_player."""
    _, session_factory = db
    app = make_test_app(session_factory)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/profit/summary")

    assert resp.status_code == 200
    body = resp.json()
    assert "totals" in body
    assert "per_player" in body

    totals = body["totals"]
    assert totals["total_spent"] == 0
    assert totals["total_earned"] == 0
    assert totals["net_profit"] == 0
    assert totals["trade_count"] == 0

    assert body["per_player"] == []


# ── Test 2: Buy-only record ───────────────────────────────────────────────────

async def test_profit_summary_buy_only(db):
    """With one 'bought' record (price=50000), returns total_spent=50000, earned=0, net=-50000, count=1."""
    _, session_factory = db
    app = make_test_app(session_factory)

    now = datetime.utcnow()
    async with session_factory() as session:
        session.add(TradeRecord(
            ea_id=1001,
            action_type="buy",
            price=50000,
            outcome="bought",
            recorded_at=now,
        ))
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/profit/summary")

    assert resp.status_code == 200
    body = resp.json()
    totals = body["totals"]
    assert totals["total_spent"] == 50000
    assert totals["total_earned"] == 0
    assert totals["net_profit"] == -50000
    assert totals["trade_count"] == 1

    assert len(body["per_player"]) == 1
    pp = body["per_player"][0]
    assert pp["ea_id"] == 1001
    assert pp["total_spent"] == 50000
    assert pp["total_earned"] == 0
    assert pp["net_profit"] == -50000
    assert pp["trade_count"] == 1


# ── Test 3: Full cycle — bought and sold ──────────────────────────────────────

async def test_profit_summary_full_cycle(db):
    """With bought@50000 and sold@70000, returns spent=50000, earned=66500 (5% tax), net=16500, count=2."""
    _, session_factory = db
    app = make_test_app(session_factory)

    now = datetime.utcnow()
    async with session_factory() as session:
        session.add(TradeRecord(
            ea_id=1001,
            action_type="buy",
            price=50000,
            outcome="bought",
            recorded_at=now,
        ))
        session.add(TradeRecord(
            ea_id=1001,
            action_type="list",
            price=70000,
            outcome="sold",
            recorded_at=now,
        ))
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/profit/summary")

    assert resp.status_code == 200
    body = resp.json()
    totals = body["totals"]
    assert totals["total_spent"] == 50000
    assert totals["total_earned"] == 66500   # 70000 * 0.95 = 66500
    assert totals["net_profit"] == 16500
    assert totals["trade_count"] == 2


# ── Test 4: Per-player breakdown with two different ea_ids ───────────────────

async def test_profit_summary_per_player(db):
    """With records for two ea_ids, per_player list contains both with correct individual breakdowns."""
    _, session_factory = db
    app = make_test_app(session_factory)

    now = datetime.utcnow()
    async with session_factory() as session:
        # Player A: bought 30000, sold 40000 (earned = 38000)
        session.add(TradeRecord(ea_id=2001, action_type="buy", price=30000, outcome="bought", recorded_at=now))
        session.add(TradeRecord(ea_id=2001, action_type="list", price=40000, outcome="sold", recorded_at=now))
        # Player B: bought 50000 only
        session.add(TradeRecord(ea_id=2002, action_type="buy", price=50000, outcome="bought", recorded_at=now))
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/profit/summary")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["per_player"]) == 2

    by_id = {p["ea_id"]: p for p in body["per_player"]}

    a = by_id[2001]
    assert a["total_spent"] == 30000
    assert a["total_earned"] == 38000   # 40000 * 0.95
    assert a["net_profit"] == 8000
    assert a["trade_count"] == 2

    b = by_id[2002]
    assert b["total_spent"] == 50000
    assert b["total_earned"] == 0
    assert b["net_profit"] == -50000
    assert b["trade_count"] == 1


# ── Test 5: Multiple buy/sell cycles sum correctly ────────────────────────────

async def test_profit_summary_multiple_cycles(db):
    """Player bought twice and sold twice — totals sum correctly across all trades."""
    _, session_factory = db
    app = make_test_app(session_factory)

    now = datetime.utcnow()
    async with session_factory() as session:
        # Cycle 1: bought 20000, sold 30000
        session.add(TradeRecord(ea_id=3001, action_type="buy", price=20000, outcome="bought", recorded_at=now))
        session.add(TradeRecord(ea_id=3001, action_type="list", price=30000, outcome="sold", recorded_at=now))
        # Cycle 2: bought 20000, sold 28000
        session.add(TradeRecord(ea_id=3001, action_type="buy", price=20000, outcome="bought", recorded_at=now))
        session.add(TradeRecord(ea_id=3001, action_type="list", price=28000, outcome="sold", recorded_at=now))
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/profit/summary")

    assert resp.status_code == 200
    body = resp.json()
    totals = body["totals"]
    # spent: 20000 + 20000 = 40000
    assert totals["total_spent"] == 40000
    # earned: (30000 + 28000) * 0.95 = 55100
    assert totals["total_earned"] == 55100
    # net: 55100 - 40000 = 15100
    assert totals["net_profit"] == 15100
    assert totals["trade_count"] == 4

    per_player = body["per_player"]
    assert len(per_player) == 1
    pp = per_player[0]
    assert pp["ea_id"] == 3001
    assert pp["total_spent"] == 40000
    assert pp["total_earned"] == 55100
    assert pp["net_profit"] == 15100
    assert pp["trade_count"] == 4
