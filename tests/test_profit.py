"""Integration tests for GET /api/v1/profit/summary endpoint.

Tests the buy-anchored FIFO profit calculation:
- Each buy record is matched to the next chronological sell for that ea_id.
- Matched pairs → realized P&L.  Unmatched buys → unrealized P&L.
- Sells without buys are ignored (ghost / pre-bot events).
"""
from __future__ import annotations

import pytest
from datetime import datetime, timedelta

from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from src.server.db import create_engine_and_tables
from src.server.models_db import PlayerRecord, TradeRecord, MarketSnapshot


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
    """GET /api/v1/profit/summary with no trade records returns all-zero totals."""
    _, session_factory = db
    app = make_test_app(session_factory)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/profit/summary")

    assert resp.status_code == 200
    body = resp.json()
    totals = body["totals"]
    assert totals["total_spent"] == 0
    assert totals["total_earned"] == 0
    assert totals["realized_profit"] == 0
    assert totals["unrealized_pnl"] == 0
    assert totals["buy_count"] == 0
    assert totals["sell_count"] == 0
    assert totals["held_count"] == 0
    assert body["per_player"] == []


# ── Test 2: Buy-only record → unrealized ─────────────────────────────────────

async def test_profit_summary_buy_only_no_snapshot(db):
    """One buy, no sell, no market snapshot → spent=50000, unrealized=0 (no price data)."""
    _, session_factory = db
    app = make_test_app(session_factory)

    now = datetime.utcnow()
    async with session_factory() as session:
        session.add(TradeRecord(
            ea_id=1001, action_type="buy", price=50000,
            outcome="bought", recorded_at=now,
        ))
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/profit/summary")

    assert resp.status_code == 200
    body = resp.json()
    totals = body["totals"]
    assert totals["total_spent"] == 50000
    assert totals["total_earned"] == 0
    assert totals["realized_profit"] == 0
    assert totals["unrealized_pnl"] == 0  # no snapshot → can't compute
    assert totals["buy_count"] == 1
    assert totals["sell_count"] == 0
    assert totals["held_count"] == 1

    assert len(body["per_player"]) == 1
    pp = body["per_player"][0]
    assert pp["total_spent"] == 50000
    assert pp["held_count"] == 1


# ── Test 3: Buy-only with market snapshot → unrealized P&L computed ──────────

async def test_profit_summary_buy_with_snapshot(db):
    """Buy@50000, current BIN=60000 → unrealized = 60000*0.95 - 50000 = 7000."""
    _, session_factory = db
    app = make_test_app(session_factory)

    now = datetime.utcnow()
    async with session_factory() as session:
        session.add(TradeRecord(
            ea_id=1001, action_type="buy", price=50000,
            outcome="bought", recorded_at=now,
        ))
        session.add(MarketSnapshot(
            ea_id=1001, captured_at=now, current_lowest_bin=60000,
            listing_count=20,
        ))
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/profit/summary")

    assert resp.status_code == 200
    body = resp.json()
    totals = body["totals"]
    assert totals["unrealized_pnl"] == 7000  # 60000*0.95 - 50000
    assert totals["held_count"] == 1
    assert totals["realized_profit"] == 0


# ── Test 4: Full cycle — bought and sold → realized ──────────────────────────

async def test_profit_summary_full_cycle(db):
    """Buy@50000, sell@70000 → realized = 70000*0.95 - 50000 = 16500."""
    _, session_factory = db
    app = make_test_app(session_factory)

    now = datetime.utcnow()
    async with session_factory() as session:
        session.add(TradeRecord(
            ea_id=1001, action_type="buy", price=50000,
            outcome="bought", recorded_at=now,
        ))
        session.add(TradeRecord(
            ea_id=1001, action_type="list", price=70000,
            outcome="sold", recorded_at=now + timedelta(hours=1),
        ))
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/profit/summary")

    assert resp.status_code == 200
    body = resp.json()
    totals = body["totals"]
    assert totals["total_spent"] == 50000
    assert totals["total_earned"] == 66500  # 70000 * 0.95
    assert totals["realized_profit"] == 16500
    assert totals["unrealized_pnl"] == 0
    assert totals["buy_count"] == 1
    assert totals["sell_count"] == 1
    assert totals["held_count"] == 0


# ── Test 5: Per-player breakdown — two players ──────────────────────────────

async def test_profit_summary_per_player(db):
    """Two players: A sold, B still held. Both appear in per_player."""
    _, session_factory = db
    app = make_test_app(session_factory)

    now = datetime.utcnow()
    async with session_factory() as session:
        # Player A: bought 30000, sold 40000
        session.add(TradeRecord(ea_id=2001, action_type="buy", price=30000, outcome="bought", recorded_at=now))
        session.add(TradeRecord(ea_id=2001, action_type="list", price=40000, outcome="sold", recorded_at=now + timedelta(hours=1)))
        # Player B: bought 50000, still held, BIN=55000
        session.add(TradeRecord(ea_id=2002, action_type="buy", price=50000, outcome="bought", recorded_at=now))
        session.add(MarketSnapshot(ea_id=2002, captured_at=now, current_lowest_bin=55000, listing_count=10))
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/profit/summary")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["per_player"]) == 2

    by_id = {p["ea_id"]: p for p in body["per_player"]}

    a = by_id[2001]
    assert a["total_spent"] == 30000
    assert a["total_earned"] == 38000  # 40000 * 0.95
    assert a["realized_profit"] == 8000
    assert a["held_count"] == 0

    b = by_id[2002]
    assert b["total_spent"] == 50000
    assert b["total_earned"] == 0
    assert b["realized_profit"] == 0
    assert b["unrealized_pnl"] == 2250  # 55000*0.95 - 50000
    assert b["held_count"] == 1


# ── Test 6: Multiple buy/sell cycles FIFO matched ───────────────────────────

async def test_profit_summary_multiple_cycles(db):
    """Two buy/sell cycles for same player — FIFO matched correctly."""
    _, session_factory = db
    app = make_test_app(session_factory)

    t0 = datetime.utcnow()
    async with session_factory() as session:
        # Cycle 1: buy@20000, sell@30000
        session.add(TradeRecord(ea_id=3001, action_type="buy", price=20000, outcome="bought", recorded_at=t0))
        session.add(TradeRecord(ea_id=3001, action_type="list", price=30000, outcome="sold", recorded_at=t0 + timedelta(hours=1)))
        # Cycle 2: buy@20000, sell@28000
        session.add(TradeRecord(ea_id=3001, action_type="buy", price=20000, outcome="bought", recorded_at=t0 + timedelta(hours=2)))
        session.add(TradeRecord(ea_id=3001, action_type="list", price=28000, outcome="sold", recorded_at=t0 + timedelta(hours=3)))
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/profit/summary")

    assert resp.status_code == 200
    body = resp.json()
    totals = body["totals"]
    # spent: 20000 + 20000 = 40000
    assert totals["total_spent"] == 40000
    # earned: 30000*0.95 + 28000*0.95 = 28500 + 26600 = 55100
    assert totals["total_earned"] == 55100
    assert totals["realized_profit"] == 15100  # 55100 - 40000
    assert totals["buy_count"] == 2
    assert totals["sell_count"] == 2
    assert totals["held_count"] == 0


# ── Test 7: Ghost sells (no corresponding buy) are ignored ──────────────────

async def test_profit_summary_ghost_sells_ignored(db):
    """Sell records without a buy are not counted in profit."""
    _, session_factory = db
    app = make_test_app(session_factory)

    now = datetime.utcnow()
    async with session_factory() as session:
        # One real cycle: buy@10000, sell@15000
        session.add(TradeRecord(ea_id=4001, action_type="buy", price=10000, outcome="bought", recorded_at=now))
        session.add(TradeRecord(ea_id=4001, action_type="list", price=15000, outcome="sold", recorded_at=now + timedelta(hours=1)))
        # Ghost sells (no buy for ea_id=4001 to match these)
        session.add(TradeRecord(ea_id=4001, action_type="list", price=15000, outcome="sold", recorded_at=now + timedelta(hours=2)))
        session.add(TradeRecord(ea_id=4001, action_type="list", price=15000, outcome="sold", recorded_at=now + timedelta(hours=3)))
        # Ghost sells for player never bought
        session.add(TradeRecord(ea_id=9999, action_type="list", price=50000, outcome="sold", recorded_at=now))
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/profit/summary")

    assert resp.status_code == 200
    body = resp.json()
    totals = body["totals"]
    # Only 1 buy matched to 1 sell
    assert totals["total_spent"] == 10000
    assert totals["total_earned"] == 14250  # 15000 * 0.95
    assert totals["realized_profit"] == 4250
    assert totals["buy_count"] == 1
    assert totals["sell_count"] == 1
    # Ghost player 9999 should NOT appear (no buys)
    assert len(body["per_player"]) == 1
    assert body["per_player"][0]["ea_id"] == 4001


# ── Test 8: Mixed — some sold, some held ────────────────────────────────────

async def test_profit_summary_mixed_sold_and_held(db):
    """Two buys for same player, one sold, one still held."""
    _, session_factory = db
    app = make_test_app(session_factory)

    t0 = datetime.utcnow()
    async with session_factory() as session:
        # Buy 1: sold
        session.add(TradeRecord(ea_id=5001, action_type="buy", price=25000, outcome="bought", recorded_at=t0))
        session.add(TradeRecord(ea_id=5001, action_type="list", price=35000, outcome="sold", recorded_at=t0 + timedelta(hours=1)))
        # Buy 2: still held
        session.add(TradeRecord(ea_id=5001, action_type="buy", price=26000, outcome="bought", recorded_at=t0 + timedelta(hours=2)))
        # Market snapshot for unrealized
        session.add(MarketSnapshot(ea_id=5001, captured_at=t0 + timedelta(hours=3), current_lowest_bin=30000, listing_count=15))
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/profit/summary")

    assert resp.status_code == 200
    body = resp.json()
    totals = body["totals"]
    assert totals["buy_count"] == 2
    assert totals["sell_count"] == 1
    assert totals["held_count"] == 1
    # Realized: 35000*0.95 - 25000 = 33250 - 25000 = 8250
    assert totals["realized_profit"] == 8250
    # Unrealized: 30000*0.95 - 26000 = 28500 - 26000 = 2500
    assert totals["unrealized_pnl"] == 2500
    assert totals["total_profit"] == 10750  # 8250 + 2500
