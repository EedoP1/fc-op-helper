"""Integration tests for GET /api/v1/portfolio/status endpoint."""
from __future__ import annotations

import pytest
from datetime import datetime

from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from src.server.db import create_engine_and_tables
from src.server.models_db import PortfolioSlot, TradeRecord, MarketSnapshot, PlayerRecord


# ── Test app factory ───────────────────────────────────────────────────────────

def make_test_app(session_factory):
    """Create a minimal FastAPI app with portfolio_status router wired."""
    from src.server.api.portfolio_status import router as status_router

    app = FastAPI(title="OP Seller Test — Portfolio Status")
    app.include_router(status_router)
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


# ── Test 1: Empty portfolio returns zeroed summary and empty players list ──────

async def test_status_empty_portfolio(db):
    """GET /api/v1/portfolio/status with no portfolio slots returns all-zero summary and empty players."""
    _, session_factory = db
    app = make_test_app(session_factory)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/portfolio/status")

    assert resp.status_code == 200
    body = resp.json()
    assert "summary" in body
    assert "players" in body

    summary = body["summary"]
    assert summary["realized_profit"] == 0
    assert summary["unrealized_pnl"] == 0
    assert summary["trade_counts"] == {"bought": 0, "sold": 0, "expired": 0}
    assert body["players"] == []


# ── Test 2: PortfolioSlot with no trade records → PENDING ─────────────────────

async def test_status_pending(db):
    """PortfolioSlot exists but no TradeRecords → status=PENDING, times_sold=0, unrealized_pnl=null."""
    _, session_factory = db
    app = make_test_app(session_factory)

    now = datetime.utcnow()
    async with session_factory() as session:
        session.add(PortfolioSlot(
            ea_id=1001,
            buy_price=50000,
            sell_price=70000,
            added_at=now,
        ))
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/portfolio/status")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["players"]) == 1

    player = body["players"][0]
    assert player["ea_id"] == 1001
    assert player["status"] == "PENDING"
    assert player["times_sold"] == 0
    assert player["realized_profit"] == 0
    assert player["unrealized_pnl"] is None
    assert player["buy_price"] == 50000
    assert player["sell_price"] == 70000


# ── Test 3: BOUGHT status with market snapshot ────────────────────────────────

async def test_status_bought(db):
    """PortfolioSlot + TradeRecord(outcome=bought) + MarketSnapshot → status=BOUGHT, unrealized_pnl computed."""
    _, session_factory = db
    app = make_test_app(session_factory)

    now = datetime.utcnow()
    async with session_factory() as session:
        session.add(PortfolioSlot(ea_id=2001, buy_price=50000, sell_price=70000, added_at=now))
        session.add(TradeRecord(
            ea_id=2001,
            action_type="buy",
            price=50000,
            outcome="bought",
            recorded_at=now,
        ))
        session.add(MarketSnapshot(
            ea_id=2001,
            captured_at=now,
            current_lowest_bin=55000,
            listing_count=50,
            live_auction_prices="[]",
        ))
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/portfolio/status")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["players"]) == 1

    player = body["players"][0]
    assert player["status"] == "BOUGHT"
    assert player["times_sold"] == 0
    assert player["realized_profit"] == 0
    # unrealized_pnl = current_lowest_bin - buy_price = 55000 - 50000 = 5000
    assert player["unrealized_pnl"] == 5000
    assert player["current_bin"] == 55000


# ── Test 4: LISTED status ─────────────────────────────────────────────────────

async def test_status_listed(db):
    """Latest TradeRecord outcome=listed → status=LISTED, unrealized_pnl computed from market snapshot."""
    _, session_factory = db
    app = make_test_app(session_factory)

    now = datetime.utcnow()
    async with session_factory() as session:
        session.add(PortfolioSlot(ea_id=3001, buy_price=40000, sell_price=60000, added_at=now))
        session.add(TradeRecord(ea_id=3001, action_type="buy", price=40000, outcome="bought", recorded_at=now))
        session.add(TradeRecord(ea_id=3001, action_type="list", price=60000, outcome="listed", recorded_at=now))
        session.add(MarketSnapshot(
            ea_id=3001,
            captured_at=now,
            current_lowest_bin=45000,
            listing_count=30,
            live_auction_prices="[]",
        ))
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/portfolio/status")

    assert resp.status_code == 200
    body = resp.json()
    player = body["players"][0]
    assert player["status"] == "LISTED"
    # unrealized_pnl = current_lowest_bin - buy_price = 45000 - 40000 = 5000
    assert player["unrealized_pnl"] == 5000
    assert player["current_bin"] == 45000


# ── Test 5: SOLD status ───────────────────────────────────────────────────────

async def test_status_sold(db):
    """Latest TradeRecord outcome=sold → status=SOLD, times_sold=1, realized_profit computed, unrealized_pnl=null."""
    _, session_factory = db
    app = make_test_app(session_factory)

    now = datetime.utcnow()
    async with session_factory() as session:
        session.add(PortfolioSlot(ea_id=4001, buy_price=50000, sell_price=70000, added_at=now))
        session.add(TradeRecord(ea_id=4001, action_type="buy", price=50000, outcome="bought", recorded_at=now))
        session.add(TradeRecord(ea_id=4001, action_type="list", price=70000, outcome="sold", recorded_at=now))
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/portfolio/status")

    assert resp.status_code == 200
    body = resp.json()
    player = body["players"][0]
    assert player["status"] == "SOLD"
    assert player["times_sold"] == 1
    # realized_profit = int(70000 * 0.95) - 50000 = 66500 - 50000 = 16500
    assert player["realized_profit"] == 16500
    assert player["unrealized_pnl"] is None
    assert player["current_bin"] is None


# ── Test 6: EXPIRED status ────────────────────────────────────────────────────

async def test_status_expired(db):
    """Latest TradeRecord outcome=expired → status=EXPIRED, unrealized_pnl=null."""
    _, session_factory = db
    app = make_test_app(session_factory)

    now = datetime.utcnow()
    async with session_factory() as session:
        session.add(PortfolioSlot(ea_id=5001, buy_price=30000, sell_price=45000, added_at=now))
        session.add(TradeRecord(ea_id=5001, action_type="buy", price=30000, outcome="bought", recorded_at=now))
        session.add(TradeRecord(ea_id=5001, action_type="list", price=45000, outcome="expired", recorded_at=now))
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/portfolio/status")

    assert resp.status_code == 200
    body = resp.json()
    player = body["players"][0]
    assert player["status"] == "EXPIRED"
    assert player["times_sold"] == 0
    assert player["unrealized_pnl"] is None


# ── Test 7: Multiple trade cycles ─────────────────────────────────────────────

async def test_status_multiple_cycles(db):
    """Player bought twice and sold twice → times_sold=2, realized_profit sums both cycles, status from latest record."""
    _, session_factory = db
    app = make_test_app(session_factory)

    now = datetime.utcnow()
    async with session_factory() as session:
        session.add(PortfolioSlot(ea_id=6001, buy_price=20000, sell_price=30000, added_at=now))
        # Cycle 1
        session.add(TradeRecord(ea_id=6001, action_type="buy", price=20000, outcome="bought", recorded_at=now))
        session.add(TradeRecord(ea_id=6001, action_type="list", price=30000, outcome="sold", recorded_at=now))
        # Cycle 2
        session.add(TradeRecord(ea_id=6001, action_type="buy", price=20000, outcome="bought", recorded_at=now))
        session.add(TradeRecord(ea_id=6001, action_type="list", price=28000, outcome="sold", recorded_at=now))
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/portfolio/status")

    assert resp.status_code == 200
    body = resp.json()
    player = body["players"][0]
    assert player["status"] == "SOLD"
    assert player["times_sold"] == 2
    # realized_profit = int((30000 + 28000) * 0.95) - (20000 + 20000) = 55100 - 40000 = 15100
    assert player["realized_profit"] == 15100
    assert player["unrealized_pnl"] is None


# ── Test 8: Summary totals aggregate across players ───────────────────────────

async def test_summary_totals(db):
    """Two players with different statuses → summary aggregates realized_profit, unrealized_pnl, trade_counts correctly."""
    _, session_factory = db
    app = make_test_app(session_factory)

    now = datetime.utcnow()
    async with session_factory() as session:
        # Player A: SOLD → realized_profit = int(70000 * 0.95) - 50000 = 16500
        session.add(PortfolioSlot(ea_id=7001, buy_price=50000, sell_price=70000, added_at=now))
        session.add(TradeRecord(ea_id=7001, action_type="buy", price=50000, outcome="bought", recorded_at=now))
        session.add(TradeRecord(ea_id=7001, action_type="list", price=70000, outcome="sold", recorded_at=now))

        # Player B: BOUGHT → unrealized_pnl = 55000 - 40000 = 15000
        session.add(PortfolioSlot(ea_id=7002, buy_price=40000, sell_price=60000, added_at=now))
        session.add(TradeRecord(ea_id=7002, action_type="buy", price=40000, outcome="bought", recorded_at=now))
        session.add(MarketSnapshot(
            ea_id=7002,
            captured_at=now,
            current_lowest_bin=55000,
            listing_count=20,
            live_auction_prices="[]",
        ))

        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/portfolio/status")

    assert resp.status_code == 200
    body = resp.json()
    summary = body["summary"]
    # realized_profit sums across all players
    assert summary["realized_profit"] == 16500
    # unrealized_pnl only sums BOUGHT/LISTED players: 15000
    assert summary["unrealized_pnl"] == 15000
    # trade_counts
    assert summary["trade_counts"]["bought"] == 2
    assert summary["trade_counts"]["sold"] == 1
    assert summary["trade_counts"]["expired"] == 0

    assert len(body["players"]) == 2


# ── Test 9: No market snapshot → unrealized_pnl=null, current_bin=null ────────

async def test_no_market_snapshot(db):
    """Player is BOUGHT but no MarketSnapshot row → unrealized_pnl=null, current_bin=null."""
    _, session_factory = db
    app = make_test_app(session_factory)

    now = datetime.utcnow()
    async with session_factory() as session:
        session.add(PortfolioSlot(ea_id=8001, buy_price=50000, sell_price=70000, added_at=now))
        session.add(TradeRecord(ea_id=8001, action_type="buy", price=50000, outcome="bought", recorded_at=now))
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/portfolio/status")

    assert resp.status_code == 200
    body = resp.json()
    player = body["players"][0]
    assert player["status"] == "BOUGHT"
    assert player["unrealized_pnl"] is None
    assert player["current_bin"] is None


# ── Test 10: Player name from PlayerRecord, fallback to "Player {ea_id}" ──────

async def test_player_name_from_player_record(db):
    """PlayerRecord exists → name field populated; no PlayerRecord → falls back to 'Player {ea_id}'."""
    _, session_factory = db
    app = make_test_app(session_factory)

    now = datetime.utcnow()
    async with session_factory() as session:
        # Player A with PlayerRecord
        session.add(PortfolioSlot(ea_id=9001, buy_price=50000, sell_price=70000, added_at=now))
        session.add(PlayerRecord(
            ea_id=9001,
            name="Kylian Mbappe",
            rating=99,
            position="ST",
            nation="France",
            league="Ligue 1",
            club="PSG",
            card_type="TOTY",
        ))

        # Player B without PlayerRecord
        session.add(PortfolioSlot(ea_id=9002, buy_price=30000, sell_price=45000, added_at=now))
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/portfolio/status")

    assert resp.status_code == 200
    body = resp.json()
    by_id = {p["ea_id"]: p for p in body["players"]}

    assert by_id[9001]["name"] == "Kylian Mbappe"
    assert by_id[9002]["name"] == "Player 9002"
