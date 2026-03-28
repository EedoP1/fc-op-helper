"""Smoke tests — verify all 16 API endpoints respond correctly via real HTTP.

Each test makes a real HTTP call to a real uvicorn server backed by a real
SQLite file. The live_server fixture in conftest.py starts the server once
per session; the cleanup_tables fixture deletes all mutable rows after each test.
"""
import pytest


# -- Health ----------------------------------------------------------------


async def test_health_returns_200(client):
    """GET /api/v1/health returns 200 with scanner_status and circuit_breaker fields."""
    r = await client.get("/api/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert "scanner_status" in body
    assert "circuit_breaker" in body


# -- Players ---------------------------------------------------------------


async def test_top_players_empty_db(client):
    """GET /api/v1/players/top returns 200 with empty data on a fresh DB."""
    r = await client.get("/api/v1/players/top")
    assert r.status_code == 200
    body = r.json()
    assert body["data"] == []
    assert body["count"] == 0


async def test_player_detail_not_found(client):
    """GET /api/v1/players/{ea_id} returns 404 when player does not exist."""
    r = await client.get("/api/v1/players/999999")
    assert r.status_code == 404


# -- Portfolio -------------------------------------------------------------


async def test_portfolio_requires_budget(client):
    """GET /api/v1/portfolio without budget param returns 422 validation error."""
    r = await client.get("/api/v1/portfolio")
    assert r.status_code == 422  # missing required budget param


async def test_portfolio_empty_db(client):
    """GET /api/v1/portfolio?budget=1000000 returns 200 on empty DB (no viable players)."""
    r = await client.get("/api/v1/portfolio?budget=1000000")
    assert r.status_code == 200


async def test_generate_portfolio_empty_db(client):
    """POST /api/v1/portfolio/generate returns 200 with data key on empty DB."""
    r = await client.post("/api/v1/portfolio/generate", json={"budget": 1000000})
    assert r.status_code == 200
    body = r.json()
    assert "data" in body


async def test_confirm_portfolio(client):
    """POST /api/v1/portfolio/confirm seeds portfolio_slots and returns confirmed count."""
    r = await client.post(
        "/api/v1/portfolio/confirm",
        json={"players": [{"ea_id": 100, "buy_price": 50000, "sell_price": 70000}]},
    )
    assert r.status_code == 200
    assert r.json()["confirmed"] == 1


async def test_swap_preview(client):
    """POST /api/v1/portfolio/swap-preview returns 200 with replacements key."""
    r = await client.post(
        "/api/v1/portfolio/swap-preview",
        json={"freed_budget": 50000, "excluded_ea_ids": []},
    )
    assert r.status_code == 200
    assert "replacements" in r.json()


async def test_confirmed_portfolio(client):
    """GET /api/v1/portfolio/confirmed returns 200 with data list after seeding."""
    # Seed first via confirm
    await client.post(
        "/api/v1/portfolio/confirm",
        json={"players": [{"ea_id": 200, "buy_price": 30000, "sell_price": 45000}]},
    )
    r = await client.get("/api/v1/portfolio/confirmed")
    assert r.status_code == 200
    # Note: confirmed endpoint joins with PlayerRecord, which may be empty
    # so data could be empty even after confirm (no matching PlayerRecord)
    body = r.json()
    assert "data" in body
    assert "count" in body


async def test_delete_portfolio_player_not_found(client):
    """DELETE /api/v1/portfolio/{ea_id} returns 404 when player not in portfolio."""
    r = await client.delete("/api/v1/portfolio/888888?budget=1000000")
    assert r.status_code == 404


# -- Actions ---------------------------------------------------------------


async def test_pending_action_empty(client):
    """GET /api/v1/actions/pending returns 200 with action=null on empty portfolio."""
    r = await client.get("/api/v1/actions/pending")
    assert r.status_code == 200
    assert r.json()["action"] is None


async def test_complete_action_not_found(client):
    """POST /api/v1/actions/999/complete returns 404 for unknown action."""
    r = await client.post(
        "/api/v1/actions/999/complete",
        json={"price": 50000, "outcome": "bought"},
    )
    assert r.status_code == 404


async def test_seed_portfolio_slots(client):
    """POST /api/v1/portfolio/slots creates slots and returns count."""
    r = await client.post(
        "/api/v1/portfolio/slots",
        json={
            "slots": [
                {
                    "ea_id": 300,
                    "buy_price": 40000,
                    "sell_price": 55000,
                    "player_name": "Smoke Test",
                }
            ]
        },
    )
    assert r.status_code == 201
    assert r.json()["count"] == 1


async def test_direct_trade_record_no_slot(client):
    """POST /api/v1/trade-records/direct returns 404 when ea_id not in portfolio."""
    r = await client.post(
        "/api/v1/trade-records/direct",
        json={"ea_id": 999, "price": 50000, "outcome": "bought"},
    )
    assert r.status_code == 404  # ea_id not in portfolio


async def test_batch_trade_records_empty(client):
    """POST /api/v1/trade-records/batch with empty records returns 201 with empty lists."""
    r = await client.post("/api/v1/trade-records/batch", json={"records": []})
    assert r.status_code == 201
    body = r.json()
    assert body["succeeded"] == []
    assert body["failed"] == []


# -- Profit ----------------------------------------------------------------


async def test_profit_summary_empty(client):
    """GET /api/v1/profit/summary returns 200 with zero trade_count on empty DB."""
    r = await client.get("/api/v1/profit/summary")
    assert r.status_code == 200
    body = r.json()
    assert body["totals"]["trade_count"] == 0


# -- Portfolio Status ------------------------------------------------------


async def test_portfolio_status_empty(client):
    """GET /api/v1/portfolio/status returns 200 with empty players list on empty portfolio."""
    r = await client.get("/api/v1/portfolio/status")
    assert r.status_code == 200
    body = r.json()
    assert body["players"] == []
