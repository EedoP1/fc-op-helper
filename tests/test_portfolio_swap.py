"""Integration tests for DELETE /api/v1/portfolio/{ea_id} player swap endpoint."""
from __future__ import annotations

import pytest
from datetime import datetime

from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from src.server.db import create_engine_and_tables
from src.server.models_db import PlayerRecord, PlayerScore, PortfolioSlot, TradeAction, TradeRecord
from src.server.api.portfolio import router as portfolio_router


# ── Test app factory ───────────────────────────────────────────────────────────

def make_test_app(session_factory):
    """Create a minimal FastAPI app with portfolio router wired."""
    app = FastAPI(title="OP Seller Test — Portfolio Swap")
    app.include_router(portfolio_router)
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


def _make_player_record(ea_id: int, name: str = None) -> PlayerRecord:
    return PlayerRecord(
        ea_id=ea_id,
        name=name or f"Player {ea_id}",
        rating=88,
        position="ST",
        nation="Brazil",
        league="LaLiga",
        club="Real Madrid",
        card_type="gold",
        scan_tier="normal",
        last_scanned_at=datetime.utcnow(),
        is_active=True,
        listing_count=30,
        sales_per_hour=10.0,
    )


def _make_player_score(ea_id: int, buy_price: int = 20000, epph: float = 500.0) -> PlayerScore:
    return PlayerScore(
        ea_id=ea_id,
        scored_at=datetime.utcnow(),
        buy_price=buy_price,
        sell_price=int(buy_price * 1.2),
        net_profit=int(buy_price * 0.14),
        margin_pct=20,
        op_sales=5,
        total_sales=50,
        op_ratio=0.1,
        expected_profit=float(buy_price) * 0.05,
        efficiency=0.05,
        sales_per_hour=10.0,
        is_viable=True,
        expected_profit_per_hour=epph,
    )


# ── Test 1: DELETE removes the PortfolioSlot ─────────────────────────────────

async def test_swap_removes_slot(db):
    """DELETE /api/v1/portfolio/123 removes PortfolioSlot with ea_id=123 from DB."""
    _, session_factory = db
    app = make_test_app(session_factory)
    now = datetime.utcnow()

    async with session_factory() as session:
        session.add(PortfolioSlot(ea_id=123, buy_price=20000, sell_price=24000, added_at=now))
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.delete("/api/v1/portfolio/123?budget=100000")

    assert resp.status_code == 200

    # Verify slot is gone from DB
    async with session_factory() as session:
        result = await session.execute(
            __import__("sqlalchemy", fromlist=["select"]).select(PortfolioSlot).where(PortfolioSlot.ea_id == 123)
        )
        slot = result.scalar_one_or_none()
    assert slot is None


# ── Test 2: DELETE cancels PENDING and IN_PROGRESS actions ───────────────────

async def test_swap_cancels_pending_actions(db):
    """DELETE /api/v1/portfolio/123 sets PENDING and IN_PROGRESS TradeActions for ea_id=123 to CANCELLED."""
    _, session_factory = db
    app = make_test_app(session_factory)
    now = datetime.utcnow()

    async with session_factory() as session:
        session.add(PortfolioSlot(ea_id=123, buy_price=20000, sell_price=24000, added_at=now))
        session.add(TradeAction(
            ea_id=123, action_type="BUY", status="PENDING",
            target_price=20000, player_name="Player 123", created_at=now,
        ))
        session.add(TradeAction(
            ea_id=123, action_type="LIST", status="IN_PROGRESS",
            target_price=24000, player_name="Player 123", created_at=now,
        ))
        # DONE action should not be changed
        session.add(TradeAction(
            ea_id=123, action_type="RELIST", status="DONE",
            target_price=24000, player_name="Player 123", created_at=now,
        ))
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.delete("/api/v1/portfolio/123?budget=100000")

    assert resp.status_code == 200

    from sqlalchemy import select
    async with session_factory() as session:
        result = await session.execute(
            select(TradeAction).where(TradeAction.ea_id == 123)
        )
        actions = result.scalars().all()

    statuses = {a.status for a in actions}
    # PENDING and IN_PROGRESS must be CANCELLED; DONE must remain DONE
    pending_or_in_progress = [a for a in actions if a.action_type in ("BUY", "LIST")]
    for a in pending_or_in_progress:
        assert a.status == "CANCELLED"

    done_action = next(a for a in actions if a.action_type == "RELIST")
    assert done_action.status == "DONE"


# ── Test 3: DELETE does NOT delete TradeRecords ───────────────────────────────

async def test_swap_preserves_completed_trades(db):
    """DELETE /api/v1/portfolio/123 does NOT delete TradeRecords for ea_id=123."""
    _, session_factory = db
    app = make_test_app(session_factory)
    now = datetime.utcnow()

    async with session_factory() as session:
        session.add(PortfolioSlot(ea_id=123, buy_price=20000, sell_price=24000, added_at=now))
        session.add(TradeRecord(ea_id=123, action_type="buy", price=20000, outcome="bought", recorded_at=now))
        session.add(TradeRecord(ea_id=123, action_type="list", price=24000, outcome="sold", recorded_at=now))
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.delete("/api/v1/portfolio/123?budget=100000")

    assert resp.status_code == 200

    from sqlalchemy import select
    async with session_factory() as session:
        result = await session.execute(
            select(TradeRecord).where(TradeRecord.ea_id == 123)
        )
        records = result.scalars().all()

    # Trade records should NOT be deleted
    assert len(records) == 2


# ── Test 4: DELETE returns replacement players from optimizer ─────────────────

async def test_swap_returns_replacements(db):
    """DELETE /api/v1/portfolio/123 returns replacement player(s) from optimizer within freed budget."""
    _, session_factory = db
    app = make_test_app(session_factory)
    now = datetime.utcnow()

    async with session_factory() as session:
        # The player being removed
        session.add(PortfolioSlot(ea_id=123, buy_price=30000, sell_price=36000, added_at=now))
        session.add(_make_player_record(123))
        session.add(_make_player_score(123, buy_price=30000, epph=300.0))

        # Candidate replacements (not in portfolio)
        for ea_id, buy_price, epph in [(201, 15000, 500.0), (202, 12000, 400.0), (203, 25000, 350.0)]:
            session.add(_make_player_record(ea_id, name=f"Candidate {ea_id}"))
            session.add(_make_player_score(ea_id, buy_price=buy_price, epph=epph))

        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # freed_budget = 30000
        resp = await client.delete("/api/v1/portfolio/123?budget=30000")

    assert resp.status_code == 200
    body = resp.json()

    assert "removed_ea_id" in body
    assert body["removed_ea_id"] == 123
    assert "freed_budget" in body
    assert body["freed_budget"] == 30000
    assert "replacements" in body

    # Some replacements should have been returned within the freed budget
    replacements = body["replacements"]
    assert isinstance(replacements, list)
    assert len(replacements) > 0

    # Each replacement must have required fields
    for r in replacements:
        assert "ea_id" in r
        assert "name" in r
        assert "buy_price" in r
        assert "sell_price" in r
        assert "margin_pct" in r
        assert "expected_profit_per_hour" in r


# ── Test 5: DELETE returns 404 for ea_id not in portfolio ────────────────────

async def test_swap_not_found(db):
    """DELETE /api/v1/portfolio/999 (ea_id not in portfolio_slots) returns 404."""
    _, session_factory = db
    app = make_test_app(session_factory)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.delete("/api/v1/portfolio/999?budget=100000")

    assert resp.status_code == 404


# ── Test 6: Bug fix — no overshoot when freed_budget fits multiple cheap players ──

async def test_swap_no_overshoot_when_multiple_fit_in_freed_budget(db):
    """DELETE must not return more replacements than the portfolio needs to reach TARGET_PLAYER_COUNT.

    Scenario: Portfolio has 2 players (near-full for test purposes). Remove 1 player
    with buy_price=30000. Three candidates each cost 10000, all fit in 30000.
    Without the fix, the optimizer returns 3 replacements → portfolio would become
    2 - 1 + 3 = 4 players, overshooting the target.
    With the fix, at most 1 replacement is returned (needed = 2 - 1 + 1 = 2,
    but since target is 100 and we only have 2 slots total, needed = 100 - 1 = 99;
    the optimizer only has 3 candidates so it returns all 3... unless we specifically
    cap to `needed`).

    This test uses a portfolio of exactly TARGET_PLAYER_COUNT - 1 = 99 slots so that
    exactly 1 replacement is needed after 1 removal (100 - 98 = 2... wait, 99 - 1 = 98,
    needed = 100 - 98 = 2). To isolate the single-replacement case, we use a portfolio
    of exactly TARGET_PLAYER_COUNT slots (100) before removal, so needed = 100 - 99 = 1.

    Budget: each of the 3 candidates (ea_ids 301, 302, 303) costs 5000.
    removed player cost = 20000. All 3 candidates fit in freed_budget=20000.
    Without fix: optimizer returns 3 candidates. With fix: at most 1.
    """
    from src.config import TARGET_PLAYER_COUNT
    _, session_factory = db
    app = make_test_app(session_factory)
    now = datetime.utcnow()

    async with session_factory() as session:
        # Fill portfolio to exactly TARGET_PLAYER_COUNT slots
        for i in range(TARGET_PLAYER_COUNT):
            ea_id = 1000 + i
            session.add(PortfolioSlot(ea_id=ea_id, buy_price=20000, sell_price=24000, added_at=now))
            session.add(_make_player_record(ea_id))
            session.add(_make_player_score(ea_id, buy_price=20000, epph=300.0))

        # Three cheap candidate replacements (not in portfolio)
        for ea_id in [301, 302, 303]:
            session.add(_make_player_record(ea_id, name=f"CheapCandidate {ea_id}"))
            session.add(_make_player_score(ea_id, buy_price=5000, epph=800.0))

        await session.commit()

    # Remove one slot (ea_id=1000, buy_price=20000 → freed_budget=20000)
    # All 3 candidates cost 5000 each → total 15000 < 20000 → without fix, all 3 returned
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.delete(f"/api/v1/portfolio/1000?budget={TARGET_PLAYER_COUNT * 20000}")

    assert resp.status_code == 200
    body = resp.json()

    replacements = body["replacements"]
    remaining_count = body["remaining_count"]

    # After removing 1 of 100 slots, remaining = 99, needed = 1
    assert remaining_count == TARGET_PLAYER_COUNT - 1, (
        f"Expected remaining_count={TARGET_PLAYER_COUNT - 1}, got {remaining_count}"
    )
    # Only 1 replacement should be returned (needed=1), even though 3 fit in freed_budget
    assert len(replacements) <= 1, (
        f"OVERSHOOT BUG: Expected at most 1 replacement (needed=1), "
        f"got {len(replacements)}. All {len(replacements)} would push portfolio above {TARGET_PLAYER_COUNT}."
    )


# ── Test 7: Bug fix — remaining_count returned in response ───────────────────

async def test_swap_returns_remaining_count(db):
    """DELETE response must include remaining_count field (post-deletion slot count)."""
    _, session_factory = db
    app = make_test_app(session_factory)
    now = datetime.utcnow()

    async with session_factory() as session:
        for ea_id in [10, 20, 30]:
            session.add(PortfolioSlot(ea_id=ea_id, buy_price=20000, sell_price=24000, added_at=now))
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.delete("/api/v1/portfolio/10?budget=100000")

    assert resp.status_code == 200
    body = resp.json()

    assert "remaining_count" in body, "Response must include remaining_count"
    assert body["remaining_count"] == 2, (
        f"Expected remaining_count=2 after removing 1 of 3 slots, got {body['remaining_count']}"
    )
