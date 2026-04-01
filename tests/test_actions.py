"""Integration tests for action queue endpoints and portfolio slot seeding."""
from __future__ import annotations

import pytest
from datetime import datetime, timedelta

from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from src.server.db import create_engine_and_tables
from src.server.models_db import PortfolioSlot, TradeAction, TradeRecord
from src.server.api.actions import router as actions_router


# ── Test app factory ───────────────────────────────────────────────────────────

def make_test_app(session_factory):
    """Create a FastAPI app with the actions router and session_factory wired."""
    app = FastAPI(title="OP Seller Test")
    app.include_router(actions_router)
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


@pytest.fixture
async def app(db):
    """App with fresh in-memory DB."""
    _, session_factory = db
    yield make_test_app(session_factory)


@pytest.fixture
async def app_with_slot(db):
    """App with one PortfolioSlot seeded (ea_id=100, Player A)."""
    engine, session_factory = db
    now = datetime.utcnow()
    async with session_factory() as session:
        session.add(PortfolioSlot(
            ea_id=100,
            buy_price=50000,
            sell_price=70000,
            added_at=now,
        ))
        await session.commit()
    yield make_test_app(session_factory), session_factory


# ── Test 1: Tables created ─────────────────────────────────────────────────────

async def test_tables_created(db):
    """TradeAction and TradeRecord tables exist after engine+tables creation."""
    engine, session_factory = db
    # If tables are missing, inserting would raise; just verify we can query
    async with session_factory() as session:
        from sqlalchemy import text
        result = await session.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
        tables = {row[0] for row in result.fetchall()}
    assert "trade_actions" in tables
    assert "trade_records" in tables
    assert "portfolio_slots" in tables


# ── Test 2: GET /pending with no portfolio returns null ───────────────────────

async def test_pending_no_portfolio(app):
    """GET /api/v1/actions/pending with empty portfolio_slots returns 200 with action=null."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/actions/pending")
    assert resp.status_code == 200
    assert resp.json()["action"] is None


# ── Test 3: GET /pending returns BUY action ───────────────────────────────────

async def test_pending_returns_buy_action(app_with_slot):
    """With a PortfolioSlot and no trade records, GET /pending returns a BUY action."""
    app, session_factory = app_with_slot

    # Seed a player name via a direct DB insert to portfolio_slots (player_name is derived from slot)
    # The slot has ea_id=100. Since PortfolioSlot has no player_name, we add it for the action.
    # The action derivation will use player_name from a helper — here we expect a default or ea_id-based name.
    # Per the plan: action has ea_id, player_name, target_price, action_type="BUY"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/actions/pending")

    assert resp.status_code == 200
    body = resp.json()
    assert body["action"] is not None
    action = body["action"]
    assert action["action_type"] == "BUY"
    assert action["ea_id"] == 100
    assert action["target_price"] == 50000


# ── Test 4: GET /pending returns LIST action ──────────────────────────────────

async def test_pending_returns_list_action(app_with_slot):
    """With a PortfolioSlot and a 'bought' trade record, GET /pending returns LIST action."""
    app, session_factory = app_with_slot
    now = datetime.utcnow()

    async with session_factory() as session:
        session.add(TradeRecord(
            ea_id=100,
            action_type="BUY",
            price=50000,
            outcome="bought",
            recorded_at=now,
        ))
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/actions/pending")

    assert resp.status_code == 200
    action = resp.json()["action"]
    assert action is not None
    assert action["action_type"] == "LIST"
    assert action["ea_id"] == 100
    assert action["target_price"] == 70000


# ── Test 5: GET /pending returns RELIST action ────────────────────────────────

async def test_pending_returns_relist_action(app_with_slot):
    """With 'bought' and 'listed' + 'expired' records, GET /pending returns RELIST action."""
    app, session_factory = app_with_slot
    now = datetime.utcnow()

    async with session_factory() as session:
        session.add(TradeRecord(
            ea_id=100, action_type="BUY", price=50000, outcome="bought",
            recorded_at=now - timedelta(minutes=30),
        ))
        session.add(TradeRecord(
            ea_id=100, action_type="LIST", price=70000, outcome="listed",
            recorded_at=now - timedelta(minutes=20),
        ))
        session.add(TradeRecord(
            ea_id=100, action_type="LIST", price=70000, outcome="expired",
            recorded_at=now - timedelta(minutes=5),
        ))
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/actions/pending")

    assert resp.status_code == 200
    action = resp.json()["action"]
    assert action is not None
    assert action["action_type"] == "RELIST"
    assert action["ea_id"] == 100
    assert action["target_price"] == 70000


# ── Test 6: GET /pending claims action (idempotent) ───────────────────────────

async def test_pending_claims_action(app_with_slot):
    """Calling GET /pending twice returns the same IN_PROGRESS action (idempotent claim)."""
    app, session_factory = app_with_slot

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp1 = await client.get("/api/v1/actions/pending")
        resp2 = await client.get("/api/v1/actions/pending")

    assert resp1.status_code == 200
    assert resp2.status_code == 200

    action1 = resp1.json()["action"]
    action2 = resp2.json()["action"]

    assert action1 is not None
    assert action2 is not None
    assert action1["id"] == action2["id"]


# ── Test 7: Stale action is reset ─────────────────────────────────────────────

async def test_stale_action_reset(app_with_slot):
    """An IN_PROGRESS action older than 5 minutes is reset to PENDING then re-claimed."""
    app, session_factory = app_with_slot
    stale_claimed_at = datetime.utcnow() - timedelta(minutes=6)
    now = datetime.utcnow()

    async with session_factory() as session:
        stale = TradeAction(
            ea_id=100,
            action_type="BUY",
            status="IN_PROGRESS",
            target_price=50000,
            player_name="Player 100",
            created_at=now - timedelta(minutes=10),
            claimed_at=stale_claimed_at,
        )
        session.add(stale)
        await session.commit()
        stale_id = stale.id

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/actions/pending")

    assert resp.status_code == 200
    action = resp.json()["action"]
    assert action is not None
    assert action["id"] == stale_id
    assert action["action_type"] == "BUY"

    # Verify claimed_at was updated (is recent)
    async with session_factory() as session:
        from sqlalchemy import select as sa_select
        result = await session.execute(
            sa_select(TradeAction).where(TradeAction.id == stale_id)
        )
        updated = result.scalar_one()
        assert updated.status == "IN_PROGRESS"
        assert updated.claimed_at is not None
        # claimed_at should be within the last 10 seconds
        delta = datetime.utcnow() - updated.claimed_at
        assert delta.total_seconds() < 10


# ── Test 8: POST /complete marks action DONE ─────────────────────────────────

async def test_complete_action(app_with_slot):
    """POST /api/v1/actions/{id}/complete marks action DONE and inserts a TradeRecord."""
    app, session_factory = app_with_slot
    now = datetime.utcnow()

    # Create an IN_PROGRESS action
    async with session_factory() as session:
        action = TradeAction(
            ea_id=100,
            action_type="BUY",
            status="IN_PROGRESS",
            target_price=50000,
            player_name="Player 100",
            created_at=now,
            claimed_at=now,
        )
        session.add(action)
        await session.commit()
        action_id = action.id

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            f"/api/v1/actions/{action_id}/complete",
            json={"price": 50000, "outcome": "bought"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "trade_record_id" in body

    # Verify action is DONE
    async with session_factory() as session:
        from sqlalchemy import select as sa_select
        result = await session.execute(
            sa_select(TradeAction).where(TradeAction.id == action_id)
        )
        done_action = result.scalar_one()
        assert done_action.status == "DONE"
        assert done_action.completed_at is not None

        # Verify TradeRecord was inserted
        tr_result = await session.execute(
            sa_select(TradeRecord).where(TradeRecord.id == body["trade_record_id"])
        )
        record = tr_result.scalar_one()
        assert record.ea_id == 100
        assert record.price == 50000
        assert record.outcome == "bought"


# ── Test 9: POST /complete 404 for unknown action ────────────────────────────

async def test_complete_action_not_found(app):
    """POST /api/v1/actions/999/complete returns 404."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/actions/999/complete",
            json={"price": 50000, "outcome": "bought"},
        )
    assert resp.status_code == 404


# ── Test 10: POST /portfolio/slots creates rows ───────────────────────────────

async def test_seed_portfolio_slots(app):
    """POST /api/v1/portfolio/slots with one entry creates a PortfolioSlot row."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/portfolio/slots",
            json={"slots": [{"ea_id": 123, "buy_price": 50000, "sell_price": 70000, "player_name": "Test Player"}]},
        )

    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "ok"
    assert body["count"] == 1


# ── Test 11: POST /portfolio/slots upserts existing rows ─────────────────────

async def test_seed_portfolio_slots_upsert(app):
    """POST /portfolio/slots with same ea_id updates prices instead of failing."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # First insert
        await client.post(
            "/api/v1/portfolio/slots",
            json={"slots": [{"ea_id": 200, "buy_price": 30000, "sell_price": 40000, "player_name": "Player X"}]},
        )
        # Update with same ea_id
        resp = await client.post(
            "/api/v1/portfolio/slots",
            json={"slots": [{"ea_id": 200, "buy_price": 35000, "sell_price": 50000, "player_name": "Player X"}]},
        )

    assert resp.status_code == 201
    assert resp.json()["status"] == "ok"


# ── Test 12: POST /portfolio/slots with empty list ────────────────────────────

async def test_seed_portfolio_slots_empty(app):
    """POST /api/v1/portfolio/slots with empty list returns 200 with count=0."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/portfolio/slots",
            json={"slots": []},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 0


# ── Tests for POST /trade-records/direct ─────────────────────────────────────

async def test_direct_trade_record_returns_201(app_with_slot):
    """POST /api/v1/trade-records/direct with valid ea_id returns 201 with trade_record_id."""
    app, session_factory = app_with_slot

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/trade-records/direct",
            json={"ea_id": 100, "price": 50000, "outcome": "bought"},
        )

    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "ok"
    assert "trade_record_id" in body
    assert isinstance(body["trade_record_id"], int)


async def test_direct_trade_record_unknown_ea_id_returns_404(app):
    """POST /api/v1/trade-records/direct with unknown ea_id returns 404."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/trade-records/direct",
            json={"ea_id": 9999, "price": 50000, "outcome": "bought"},
        )

    assert resp.status_code == 404


async def test_direct_trade_record_inserts_row(app_with_slot):
    """POST /api/v1/trade-records/direct inserts a TradeRecord row with correct fields."""
    app, session_factory = app_with_slot

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/trade-records/direct",
            json={"ea_id": 100, "price": 55000, "outcome": "bought"},
        )

    assert resp.status_code == 201
    record_id = resp.json()["trade_record_id"]

    async with session_factory() as session:
        from sqlalchemy import select as sa_select
        result = await session.execute(
            sa_select(TradeRecord).where(TradeRecord.id == record_id)
        )
        record = result.scalar_one()
        assert record.ea_id == 100
        assert record.price == 55000
        assert record.outcome == "bought"
        assert record.action_type == "buy"
        assert record.recorded_at is not None


async def test_direct_trade_record_outcome_listed_maps_to_list(app_with_slot):
    """POST /trade-records/direct with outcome 'listed' sets action_type to 'list'."""
    app, session_factory = app_with_slot

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/trade-records/direct",
            json={"ea_id": 100, "price": 70000, "outcome": "listed"},
        )

    assert resp.status_code == 201
    record_id = resp.json()["trade_record_id"]

    async with session_factory() as session:
        from sqlalchemy import select as sa_select
        result = await session.execute(
            sa_select(TradeRecord).where(TradeRecord.id == record_id)
        )
        record = result.scalar_one()
        assert record.action_type == "list"
        assert record.outcome == "listed"


async def test_direct_trade_record_outcome_sold_maps_to_list(app_with_slot):
    """POST /trade-records/direct with outcome 'sold' sets action_type to 'list'."""
    app, session_factory = app_with_slot

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/trade-records/direct",
            json={"ea_id": 100, "price": 70000, "outcome": "sold"},
        )

    assert resp.status_code == 201
    record_id = resp.json()["trade_record_id"]

    async with session_factory() as session:
        from sqlalchemy import select as sa_select
        result = await session.execute(
            sa_select(TradeRecord).where(TradeRecord.id == record_id)
        )
        record = result.scalar_one()
        assert record.action_type == "list"
        assert record.outcome == "sold"


async def test_direct_trade_record_outcome_expired_maps_to_list(app_with_slot):
    """POST /trade-records/direct with outcome 'expired' sets action_type to 'list'."""
    app, session_factory = app_with_slot

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/trade-records/direct",
            json={"ea_id": 100, "price": 70000, "outcome": "expired"},
        )

    assert resp.status_code == 201
    record_id = resp.json()["trade_record_id"]

    async with session_factory() as session:
        from sqlalchemy import select as sa_select
        result = await session.execute(
            sa_select(TradeRecord).where(TradeRecord.id == record_id)
        )
        record = result.scalar_one()
        assert record.action_type == "list"
        assert record.outcome == "expired"


async def test_direct_trade_record_outcome_bought_maps_to_buy(app_with_slot):
    """POST /trade-records/direct with outcome 'bought' sets action_type to 'buy'."""
    app, session_factory = app_with_slot

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/trade-records/direct",
            json={"ea_id": 100, "price": 50000, "outcome": "bought"},
        )

    assert resp.status_code == 201
    record_id = resp.json()["trade_record_id"]

    async with session_factory() as session:
        from sqlalchemy import select as sa_select
        result = await session.execute(
            sa_select(TradeRecord).where(TradeRecord.id == record_id)
        )
        record = result.scalar_one()
        assert record.action_type == "buy"
        assert record.outcome == "bought"


async def test_direct_trade_record_invalid_outcome_returns_400(app_with_slot):
    """POST /trade-records/direct with invalid outcome returns 400."""
    app, session_factory = app_with_slot

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/trade-records/direct",
            json={"ea_id": 100, "price": 50000, "outcome": "invalid_outcome"},
        )

    assert resp.status_code == 400
