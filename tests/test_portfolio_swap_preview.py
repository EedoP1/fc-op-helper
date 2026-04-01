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
                expected_profit_per_hour=float(buy_price) * efficiency,
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
            json={"freed_budget": 200000, "excluded_ea_ids": [2001], "current_count": 99},
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
            json={"freed_budget": 200000, "excluded_ea_ids": [], "current_count": 99},
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
            json={"freed_budget": 200000, "excluded_ea_ids": all_ids, "current_count": 99},
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
            json={"freed_budget": 50000, "excluded_ea_ids": [], "current_count": 99},
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
            json={"freed_budget": 0, "excluded_ea_ids": [], "current_count": 99},
        )
    assert resp.status_code == 422


# ── Test 6: swap-preview caps replacements to open slots (current_count controls cap) ──

async def _seed_cheap_players(session_factory, count: int, id_base: int = 3000):
    """Helper: seed `count` cheap players starting from ea_id=id_base."""
    now = datetime.utcnow()
    async with session_factory() as session:
        for i in range(count):
            ea_id = id_base + i
            rec = PlayerRecord(
                ea_id=ea_id,
                name=f"Cheap {i}",
                rating=85,
                position="CM",
                nation="Spain",
                league="LaLiga",
                club="Barcelona",
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
                buy_price=5000,
                sell_price=6000,
                net_profit=700,
                margin_pct=20,
                op_sales=5,
                total_sales=50,
                op_ratio=0.1,
                expected_profit=250.0,
                efficiency=0.05,
                sales_per_hour=10.0,
                is_viable=True,
                expected_profit_per_hour=800.0,
            )
            session.add(score)
        await session.commit()


async def test_swap_preview_caps_replacements_to_one_open_slot(db):
    """current_count=99 → 1 open slot → at most 1 replacement returned.

    The optimizer can fit multiple cheap players in the freed_budget, but the
    server must cap to the number of open draft slots.  One slot open means
    at most one replacement regardless of freed budget size.
    """
    engine, session_factory = db

    # Seed 5 cheap candidates — freed_budget=30000 can fit all 5 (5 × 5000)
    await _seed_cheap_players(session_factory, count=5)

    app = make_test_app(session_factory)
    app.include_router(portfolio_router)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # current_count=99: only 1 slot open
        resp = await client.post(
            "/api/v1/portfolio/swap-preview",
            json={"freed_budget": 30000, "excluded_ea_ids": [], "current_count": 99},
        )
    assert resp.status_code == 200
    body = resp.json()

    replacements = body["replacements"]
    assert len(replacements) <= 1, (
        f"OVERSHOOT BUG: swap-preview returned {len(replacements)} replacements "
        "when current_count=99 (1 slot open). Must return at most 1."
    )


async def test_swap_preview_returns_multiple_when_slots_available(db):
    """current_count=95 → 5 open slots → up to 5 replacements maximise freed budget.

    When the user removes several players rapidly (or the draft has open slots),
    the server should fill as many as possible from the freed budget, up to the
    number of open slots.  This maximises budget utilisation.
    """
    engine, session_factory = db

    # Seed 5 cheap candidates (5 × 5000 = 25000 < freed_budget=30000)
    await _seed_cheap_players(session_factory, count=5)

    app = make_test_app(session_factory)
    app.include_router(portfolio_router)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # current_count=95: 5 slots open — optimizer may return up to 5
        resp = await client.post(
            "/api/v1/portfolio/swap-preview",
            json={"freed_budget": 30000, "excluded_ea_ids": [], "current_count": 95},
        )
    assert resp.status_code == 200
    body = resp.json()

    replacements = body["replacements"]
    # At least 2 replacements (5 × 5000 fits in 30000, but optimizer may cap internally)
    # The key assertion: more than 1 is allowed when multiple slots are open
    assert len(replacements) >= 2, (
        f"Budget maximisation failure: swap-preview returned only {len(replacements)} "
        "replacement(s) when current_count=95 (5 slots open) and freed_budget=30000 "
        "can fit at least 5 × 5000 candidates. Multiple replacements should be returned."
    )
    # And never exceeds the number of open slots
    assert len(replacements) <= 5, (
        f"OVERSHOOT BUG: {len(replacements)} replacements returned with 5 slots open."
    )


async def test_swap_preview_no_replacements_when_portfolio_full(db):
    """current_count=100 → 0 open slots → empty replacements regardless of freed_budget.

    A fully-replaced draft (current_count already at TARGET_PLAYER_COUNT) must
    never receive additional replacements, even if budget was freed.
    """
    engine, session_factory = db

    await _seed_cheap_players(session_factory, count=5)

    app = make_test_app(session_factory)
    app.include_router(portfolio_router)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/v1/portfolio/swap-preview",
            json={"freed_budget": 30000, "excluded_ea_ids": [], "current_count": 100},
        )
    assert resp.status_code == 200
    body = resp.json()

    assert body["replacements"] == [], (
        "OVERSHOOT BUG: replacements returned even though current_count=100 "
        "(no slots open). Portfolio is already at TARGET_PLAYER_COUNT."
    )
    assert body["count"] == 0
