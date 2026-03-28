"""Error handling and CORS tests for all API endpoints.

Tests invalid JSON bodies, missing required fields, wrong types, CORS headers,
and 404 responses for nonexistent resources via real HTTP to real uvicorn server.

All tests are self-contained and rely on the autouse cleanup_tables fixture.
"""
import pytest


# ── Invalid JSON / malformed requests ────────────────────────────────────────

@pytest.mark.asyncio
async def test_confirm_invalid_json(client):
    """POST /portfolio/confirm with non-JSON body returns 422."""
    r = await client.post(
        "/api/v1/portfolio/confirm",
        content=b"not json",
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_confirm_missing_required_field(client):
    """POST /portfolio/confirm with player missing buy_price and sell_price returns 422."""
    r = await client.post(
        "/api/v1/portfolio/confirm",
        json={"players": [{"ea_id": 100}]},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_generate_missing_budget(client):
    """POST /portfolio/generate with empty body returns 422."""
    r = await client.post("/api/v1/portfolio/generate", json={})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_complete_action_missing_fields(client):
    """POST /actions/1/complete with empty body returns 422."""
    r = await client.post("/api/v1/actions/1/complete", json={})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_direct_trade_record_missing_ea_id(client):
    """POST /trade-records/direct without ea_id returns 422."""
    r = await client.post(
        "/api/v1/trade-records/direct",
        json={"price": 100, "outcome": "bought"},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_batch_invalid_record_shape(client):
    """POST /trade-records/batch with invalid record shape returns 422."""
    r = await client.post(
        "/api/v1/trade-records/batch",
        json={"records": [{"invalid": True}]},
    )
    assert r.status_code == 422


# ── Wrong types ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_confirm_ea_id_string(client):
    """POST /portfolio/confirm with ea_id as string returns 422."""
    r = await client.post(
        "/api/v1/portfolio/confirm",
        json={"players": [{"ea_id": "not_an_int", "buy_price": 1000, "sell_price": 2000}]},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_portfolio_budget_string(client):
    """GET /portfolio?budget=abc returns 422."""
    r = await client.get("/api/v1/portfolio?budget=abc")
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_slots_buy_price_string(client):
    """POST /portfolio/slots with buy_price as non-numeric string returns 422."""
    r = await client.post(
        "/api/v1/portfolio/slots",
        json={
            "slots": [
                {
                    "ea_id": 100,
                    "buy_price": "not_a_number",
                    "sell_price": 70000,
                    "player_name": "Test Player",
                }
            ]
        },
    )
    assert r.status_code == 422


# ── CORS validation ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cors_chrome_extension_origin(client):
    """OPTIONS preflight with chrome-extension:// origin returns Access-Control-Allow-Origin."""
    r = await client.options(
        "/api/v1/health",
        headers={
            "Origin": "chrome-extension://abcdef123456",
            "Access-Control-Request-Method": "GET",
        },
    )
    # 200 or 204 are both valid preflight response codes
    assert r.status_code in (200, 204)
    assert "access-control-allow-origin" in r.headers
    assert "chrome-extension://" in r.headers["access-control-allow-origin"]


@pytest.mark.asyncio
async def test_cors_regular_origin_rejected(client):
    """Request with http://evil.com origin should NOT have Access-Control-Allow-Origin header."""
    r = await client.get(
        "/api/v1/health",
        headers={"Origin": "http://evil.com"},
    )
    # Server responds (200) but should not include CORS header for non-extension origin
    assert r.status_code == 200
    assert "access-control-allow-origin" not in r.headers


@pytest.mark.asyncio
async def test_cors_actual_request(client):
    """GET /health with chrome-extension:// origin includes Access-Control-Allow-Origin."""
    r = await client.get(
        "/api/v1/health",
        headers={"Origin": "chrome-extension://test123"},
    )
    assert r.status_code == 200
    assert "access-control-allow-origin" in r.headers
    assert "chrome-extension://" in r.headers["access-control-allow-origin"]


# ── Non-existent resources ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_complete_nonexistent_action(client):
    """POST /actions/999999/complete with valid payload returns 404."""
    r = await client.post(
        "/api/v1/actions/999999/complete",
        json={"price": 50000, "outcome": "bought"},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_nonexistent_player(client):
    """DELETE /portfolio/999999?budget=1000000 returns 404."""
    r = await client.delete("/api/v1/portfolio/999999?budget=1000000")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_direct_record_nonexistent_slot(client):
    """POST /trade-records/direct with ea_id not in portfolio returns 404."""
    r = await client.post(
        "/api/v1/trade-records/direct",
        json={"ea_id": 999999, "price": 50000, "outcome": "bought"},
    )
    assert r.status_code == 404


# ── Content type handling ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_post_without_content_type(client):
    """POST /portfolio/generate without Content-Type sends raw JSON; FastAPI returns 422."""
    r = await client.post(
        "/api/v1/portfolio/generate",
        content=b'{"budget": 1000000}',
    )
    # Without Content-Type: application/json, FastAPI cannot parse the body
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_generate_negative_budget(client):
    """POST /portfolio/generate with budget=-1 returns 422 (gt=0 constraint)."""
    r = await client.post("/api/v1/portfolio/generate", json={"budget": -1})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_direct_trade_record_missing_price(client):
    """POST /trade-records/direct without price field returns 422."""
    r = await client.post(
        "/api/v1/trade-records/direct",
        json={"ea_id": 100, "outcome": "bought"},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_complete_action_invalid_price_type(client):
    """POST /actions/1/complete with price as string returns 422."""
    r = await client.post(
        "/api/v1/actions/1/complete",
        json={"price": "fifty_thousand", "outcome": "bought"},
    )
    assert r.status_code == 422
