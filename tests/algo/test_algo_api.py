"""Tests for algo API endpoints — start/stop/status/signals."""
import pytest
from datetime import datetime

from httpx import AsyncClient, ASGITransport
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from src.server.db import Base
from src.server.main import app
from src.server.models_db import (
    AlgoConfig, AlgoSignal, AlgoPosition, AlgoTrade, PlayerRecord,
)


@pytest.fixture
async def db():
    """In-memory SQLite session factory for tests."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    yield sf
    await engine.dispose()


@pytest.fixture
async def client(db):
    """AsyncClient wired to FastAPI app with in-memory DB."""
    app.state.session_factory = db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ── Tests ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_start(client, db):
    """POST /algo/start creates AlgoConfig with is_active=True."""
    resp = await client.post("/api/v1/algo/start", json={"budget": 500_000})
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_active"] is True
    assert data["budget"] == 500_000

    async with db() as session:
        config = (await session.execute(select(AlgoConfig))).scalar_one()
    assert config.is_active is True
    assert config.budget == 500_000


@pytest.mark.asyncio
async def test_stop(client, db):
    """POST /algo/stop sets is_active=False and cancels pending signals."""
    # Start first
    await client.post("/api/v1/algo/start", json={"budget": 500_000})

    # Seed a PENDING and a CLAIMED signal
    async with db() as session:
        now = datetime.utcnow()
        session.add(AlgoSignal(
            ea_id=1001, action="BUY", quantity=1, reference_price=10000,
            status="PENDING", created_at=now,
        ))
        session.add(AlgoSignal(
            ea_id=1002, action="BUY", quantity=1, reference_price=10000,
            status="CLAIMED", created_at=now, claimed_at=now,
        ))
        await session.commit()

    resp = await client.post("/api/v1/algo/stop")
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_active"] is False
    assert data["cancelled"] == 2

    async with db() as session:
        config = (await session.execute(select(AlgoConfig))).scalar_one()
        signals = (await session.execute(select(AlgoSignal))).scalars().all()
    assert config.is_active is False
    assert all(s.status == "CANCELLED" for s in signals)


@pytest.mark.asyncio
async def test_status_empty(client, db):
    """GET /algo/status returns sensible defaults when no config exists."""
    resp = await client.get("/api/v1/algo/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_active"] is False
    assert data["budget"] == 0
    assert data["cash"] == 0
    assert data["positions"] == []
    assert data["pending_signals"] == 0


@pytest.mark.asyncio
async def test_status_active(client, db):
    """GET /algo/status returns correct cash after buying a position."""
    await client.post("/api/v1/algo/start", json={"budget": 1_000_000})

    # Add a position directly in DB
    async with db() as session:
        now = datetime.utcnow()
        session.add(PlayerRecord(
            ea_id=5001, name="Mbappe", rating=96, position="ST",
            nation="France", league="LIGUE1", club="PSG",
            card_type="TOTY", created_at=now,
        ))
        session.add(AlgoPosition(
            ea_id=5001, quantity=2, buy_price=300_000,
            buy_time=now, peak_price=300_000,
        ))
        await session.commit()

    resp = await client.get("/api/v1/algo/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_active"] is True
    assert data["budget"] == 1_000_000
    # cash = 1_000_000 - (300_000 * 2) = 400_000
    assert data["cash"] == 400_000
    assert len(data["positions"]) == 1
    pos = data["positions"][0]
    assert pos["ea_id"] == 5001
    assert pos["player_name"] == "Mbappe"
    assert pos["quantity"] == 2


@pytest.mark.asyncio
async def test_signal_pending_empty(client, db):
    """GET /algo/signals/pending returns null when no signals exist."""
    resp = await client.get("/api/v1/algo/signals/pending")
    assert resp.status_code == 200
    assert resp.json()["signal"] is None


@pytest.mark.asyncio
async def test_signal_claim(client, db):
    """GET /algo/signals/pending claims the oldest PENDING signal."""
    async with db() as session:
        now = datetime.utcnow()
        session.add(PlayerRecord(
            ea_id=6001, name="Bellingham", rating=94, position="CM",
            nation="England", league="LALIGA", club="RealMadrid",
            card_type="TOTS", created_at=now,
        ))
        session.add(AlgoSignal(
            ea_id=6001, action="BUY", quantity=1, reference_price=50000,
            status="PENDING", created_at=now,
        ))
        await session.commit()

    resp = await client.get("/api/v1/algo/signals/pending")
    assert resp.status_code == 200
    data = resp.json()
    sig = data["signal"]
    assert sig is not None
    assert sig["ea_id"] == 6001
    assert sig["action"] == "BUY"
    assert sig["player_name"] == "Bellingham"
    assert sig["rating"] == 94
    assert sig["position"] == "CM"
    assert sig["card_type"] == "TOTS"

    # Signal should now be CLAIMED in DB
    async with db() as session:
        s = (await session.execute(select(AlgoSignal))).scalar_one()
    assert s.status == "CLAIMED"
    assert s.claimed_at is not None


@pytest.mark.asyncio
async def test_signal_complete_bought(client, db):
    """POST /algo/signals/{id}/complete with outcome=bought creates position."""
    async with db() as session:
        now = datetime.utcnow()
        session.add(AlgoSignal(
            ea_id=7001, action="BUY", quantity=1, reference_price=40000,
            status="CLAIMED", created_at=now, claimed_at=now,
        ))
        await session.commit()
        signal_id = (await session.execute(select(AlgoSignal))).scalar_one().id

    resp = await client.post(
        f"/api/v1/algo/signals/{signal_id}/complete",
        json={"outcome": "bought", "price": 39000, "quantity": 1},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

    async with db() as session:
        sig = (await session.execute(select(AlgoSignal))).scalar_one()
        positions = (await session.execute(select(AlgoPosition))).scalars().all()

    assert sig.status == "DONE"
    assert len(positions) == 1
    pos = positions[0]
    assert pos.ea_id == 7001
    assert pos.buy_price == 39000
    assert pos.quantity == 1


@pytest.mark.asyncio
async def test_signal_complete_sold(client, db):
    """POST /algo/signals/{id}/complete with outcome=sold removes position."""
    async with db() as session:
        now = datetime.utcnow()
        # Existing position to be removed
        session.add(AlgoPosition(
            ea_id=8001, quantity=1, buy_price=50000,
            buy_time=now, peak_price=50000,
        ))
        session.add(AlgoSignal(
            ea_id=8001, action="SELL", quantity=1, reference_price=60000,
            status="CLAIMED", created_at=now, claimed_at=now,
        ))
        await session.commit()
        signal_id = (await session.execute(select(AlgoSignal))).scalar_one().id

    resp = await client.post(
        f"/api/v1/algo/signals/{signal_id}/complete",
        json={"outcome": "sold", "price": 62000, "quantity": 1},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

    async with db() as session:
        sig = (await session.execute(select(AlgoSignal))).scalar_one()
        positions = (await session.execute(select(AlgoPosition))).scalars().all()

    assert sig.status == "DONE"
    assert len(positions) == 0


@pytest.mark.asyncio
async def test_signal_complete_not_found(client, db):
    """POST /algo/signals/{id}/complete returns 404 for missing signal."""
    resp = await client.post(
        "/api/v1/algo/signals/9999/complete",
        json={"outcome": "bought", "price": 10000, "quantity": 1},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_signal_complete_listed(client, db):
    """POST /algo/signals/{id}/complete with outcome=listed updates position, keeps it."""
    async with db() as session:
        now = datetime.utcnow()
        session.add(AlgoPosition(
            ea_id=9001, quantity=5, buy_price=25000,
            buy_time=now, peak_price=30000,
        ))
        session.add(AlgoSignal(
            ea_id=9001, action="SELL", quantity=5, reference_price=45000,
            status="CLAIMED", created_at=now, claimed_at=now,
        ))
        await session.commit()
        signal_id = (await session.execute(select(AlgoSignal))).scalar_one().id

    resp = await client.post(
        f"/api/v1/algo/signals/{signal_id}/complete",
        json={"outcome": "listed", "price": 45000, "quantity": 5},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

    async with db() as session:
        sig = (await session.execute(select(AlgoSignal))).scalar_one()
        positions = (await session.execute(select(AlgoPosition))).scalars().all()

    assert sig.status == "DONE"
    assert len(positions) == 1
    pos = positions[0]
    assert pos.ea_id == 9001
    assert pos.quantity == 5
    assert pos.listed_price == 45000
    assert pos.listed_at is not None


@pytest.mark.asyncio
async def test_position_sold_full(client, db):
    """POST /algo/positions/{ea_id}/sold with full quantity deletes position and writes trade."""
    async with db() as session:
        now = datetime.utcnow()
        session.add(AlgoPosition(
            ea_id=10001, quantity=5, buy_price=25000,
            buy_time=now, peak_price=30000,
            listed_at=now, listed_price=45000,
        ))
        await session.commit()

    resp = await client.post(
        "/api/v1/algo/positions/10001/sold",
        json={"sell_price": 45000, "quantity": 5},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["pnl"] == 5 * (int(45000 * 0.95) - 25000)

    async with db() as session:
        positions = (await session.execute(select(AlgoPosition))).scalars().all()
        trades = (await session.execute(select(AlgoTrade))).scalars().all()

    assert len(positions) == 0
    assert len(trades) == 1
    trade = trades[0]
    assert trade.ea_id == 10001
    assert trade.quantity == 5
    assert trade.buy_price == 25000
    assert trade.sell_price == 45000
    assert trade.pnl == 5 * (int(45000 * 0.95) - 25000)


@pytest.mark.asyncio
async def test_position_sold_partial(client, db):
    """POST /algo/positions/{ea_id}/sold with partial quantity decrements position."""
    async with db() as session:
        now = datetime.utcnow()
        session.add(AlgoPosition(
            ea_id=10002, quantity=8, buy_price=25000,
            buy_time=now, peak_price=30000,
            listed_at=now, listed_price=45000,
        ))
        await session.commit()

    resp = await client.post(
        "/api/v1/algo/positions/10002/sold",
        json={"sell_price": 45000, "quantity": 3},
    )
    assert resp.status_code == 200

    async with db() as session:
        pos = (await session.execute(select(AlgoPosition))).scalar_one()
        trades = (await session.execute(select(AlgoTrade))).scalars().all()

    assert pos.quantity == 5
    assert len(trades) == 1
    assert trades[0].quantity == 3


@pytest.mark.asyncio
async def test_position_sold_not_found(client, db):
    """POST /algo/positions/{ea_id}/sold returns 404 if no position."""
    resp = await client.post(
        "/api/v1/algo/positions/99999/sold",
        json={"sell_price": 45000, "quantity": 1},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_position_relist(client, db):
    """POST /algo/positions/{ea_id}/relist updates listed_price and listed_at."""
    async with db() as session:
        now = datetime.utcnow()
        session.add(AlgoPosition(
            ea_id=11001, quantity=5, buy_price=25000,
            buy_time=now, peak_price=30000,
            listed_at=now, listed_price=45000,
        ))
        await session.commit()

    resp = await client.post(
        "/api/v1/algo/positions/11001/relist",
        json={"price": 42000, "quantity": 5},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

    async with db() as session:
        pos = (await session.execute(select(AlgoPosition))).scalar_one()

    assert pos.listed_price == 42000
    assert pos.listed_at is not None
    assert pos.quantity == 5


@pytest.mark.asyncio
async def test_position_relist_not_found(client, db):
    """POST /algo/positions/{ea_id}/relist returns 404 if no position."""
    resp = await client.post(
        "/api/v1/algo/positions/99999/relist",
        json={"price": 42000, "quantity": 5},
    )
    assert resp.status_code == 404
