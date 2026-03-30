"""Smoke tests for all 16 API endpoints using real DB data.

These tests hit the REAL running server (started by conftest.live_server)
against a COPY of the production DB. No mocks. No fake ea_ids.

Tests that fail = server bugs. Do NOT weaken assertions to make tests pass
(per D-04 from 09-CONTEXT.md).

Covered endpoints:
    GET  /api/v1/health
    GET  /api/v1/players/top
    GET  /api/v1/players/{ea_id}           (real ea_id)
    GET  /api/v1/players/999999999         (nonexistent)
    POST /api/v1/portfolio/generate
    GET  /api/v1/portfolio
    POST /api/v1/portfolio/confirm
    GET  /api/v1/portfolio/confirmed
    POST /api/v1/portfolio/swap-preview
    DELETE /api/v1/portfolio/{ea_id}       (with slot seeded)
    DELETE /api/v1/portfolio/999999999     (nonexistent)
    GET  /api/v1/actions/pending           (empty portfolio)
    GET  /api/v1/actions/pending           (with slot seeded)
    POST /api/v1/actions/{id}/complete
    POST /api/v1/portfolio/slots
    POST /api/v1/trade-records/direct
    POST /api/v1/trade-records/batch
    GET  /api/v1/portfolio/status
    GET  /api/v1/profit/summary
"""
import pytest
import pytest_asyncio


# ── Health ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_health_returns_scanner_status(client):
    """GET /health returns 200 with scanner_status and circuit_breaker fields."""
    r = await client.get("/api/v1/health")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    body = r.json()
    assert "scanner_status" in body, f"Missing scanner_status in {body}"
    assert "circuit_breaker" in body, f"Missing circuit_breaker in {body}"
    assert body["scanner_status"] in ("running", "stopped", "unknown"), f"Unexpected scanner_status: {body['scanner_status']}"
    assert body["circuit_breaker"] in ("closed", "open", "half_open", "unknown"), f"Unexpected circuit_breaker: {body['circuit_breaker']}"


# ── Players ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_top_players_returns_data(client):
    """GET /players/top?limit=10 returns 200 with real scored players from DB."""
    r = await client.get("/api/v1/players/top", params={"limit": 10})
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    body = r.json()
    assert "data" in body, f"Missing data key in {body}"
    assert "count" in body, f"Missing count key in {body}"
    # Real DB has hundreds of viable scored players — count must be > 0
    assert body["count"] > 0, (
        "Expected scored players in DB but count=0. "
        "Either the DB copy is empty or viable scores were purged."
    )
    # Each player entry must have required fields
    for player in body["data"]:
        assert "ea_id" in player, f"Missing ea_id in player entry: {player}"
        assert "name" in player, f"Missing name in player entry: {player}"
        assert "price" in player, f"Missing price in player entry: {player}"


@pytest.mark.asyncio
async def test_player_detail_real_player(client, real_ea_id):
    """GET /players/{ea_id} returns 200 with current_score for a real player."""
    assert real_ea_id is not None, "real_ea_id fixture returned None — DB may be empty"
    r = await client.get(f"/api/v1/players/{real_ea_id}")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    body = r.json()
    assert body["ea_id"] == real_ea_id, f"ea_id mismatch: {body['ea_id']} != {real_ea_id}"
    assert "current_score" in body, f"Missing current_score in response: {body}"
    # current_score must be a dict (not None) for a player with viable scores
    assert body["current_score"] is not None, (
        f"current_score is None for ea_id={real_ea_id} — player has no viable scores"
    )


@pytest.mark.asyncio
async def test_player_detail_nonexistent(client):
    """GET /players/999999999 returns 404 for a player not in DB."""
    r = await client.get("/api/v1/players/999999999")
    assert r.status_code == 404, f"Expected 404, got {r.status_code}: {r.text}"


# ── Portfolio generate ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_portfolio_generate_real_budget(client):
    """POST /portfolio/generate with real budget returns players from real DB."""
    r = await client.post("/api/v1/portfolio/generate", json={"budget": 2_000_000})
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    body = r.json()
    assert "data" in body, f"Missing data key in {body}"
    assert "count" in body, f"Missing count key in {body}"
    # Real DB has viable players — must get results with sufficient budget
    assert body["count"] > 0, (
        "POST /portfolio/generate returned 0 players with budget=2_000_000. "
        "If viable scored players exist in the DB, this is a server bug."
    )
    assert body["budget_used"] > 0, f"budget_used=0 with count={body['count']} — server bug"
    # Each player must have ea_id, name, price, sell_price
    for p in body["data"]:
        assert "ea_id" in p, f"Missing ea_id in generate response: {p}"
        assert "name" in p, f"Missing name in generate response: {p}"
        assert "price" in p, f"Missing price in generate response: {p}"
        assert "sell_price" in p, f"Missing sell_price in generate response: {p}"


@pytest.mark.asyncio
async def test_portfolio_get_real_budget(client):
    """GET /portfolio?budget=2000000 returns players from real DB."""
    r = await client.get("/api/v1/portfolio", params={"budget": 2_000_000})
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    body = r.json()
    assert body["count"] > 0, (
        "GET /portfolio returned 0 players with budget=2_000_000. "
        "Real DB has viable players — this is a server bug."
    )


# ── Portfolio confirm / confirmed ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_portfolio_confirm(client):
    """POST /portfolio/confirm with real players from generate returns confirmed > 0."""
    # First generate to get real players
    gen_r = await client.post("/api/v1/portfolio/generate", json={"budget": 2_000_000})
    assert gen_r.status_code == 200
    gen_body = gen_r.json()
    assert gen_body["count"] > 0, "No players generated — cannot confirm empty list"

    # Take first 2 players from generate response
    players = gen_body["data"][:2]
    confirm_payload = {
        "players": [
            {
                "ea_id": p["ea_id"],
                "buy_price": p["price"],
                "sell_price": p["sell_price"],
            }
            for p in players
        ]
    }
    r = await client.post("/api/v1/portfolio/confirm", json=confirm_payload)
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    body = r.json()
    assert body["confirmed"] > 0, f"Expected confirmed > 0, got {body['confirmed']}"
    assert body["status"] == "ok", f"Expected status=ok, got {body['status']}"


@pytest.mark.asyncio
async def test_portfolio_confirmed_after_confirm(client):
    """GET /portfolio/confirmed returns players after confirming."""
    # Confirm a slot first
    gen_r = await client.post("/api/v1/portfolio/generate", json={"budget": 2_000_000})
    assert gen_r.status_code == 200
    gen_body = gen_r.json()
    assert gen_body["count"] > 0, "No players generated"

    players = gen_body["data"][:1]
    confirm_payload = {
        "players": [{"ea_id": p["ea_id"], "buy_price": p["price"], "sell_price": p["sell_price"]} for p in players]
    }
    conf_r = await client.post("/api/v1/portfolio/confirm", json=confirm_payload)
    assert conf_r.status_code == 200

    # Now get confirmed
    r = await client.get("/api/v1/portfolio/confirmed")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    body = r.json()
    assert body["count"] > 0, "Expected confirmed players after confirm, got 0"
    for p in body["data"]:
        assert "ea_id" in p
        assert "buy_price" in p
        assert "sell_price" in p


# ── Portfolio swap-preview ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_portfolio_swap_preview(client, real_ea_id):
    """POST /portfolio/swap-preview returns replacements for freed budget."""
    assert real_ea_id is not None, "real_ea_id is None — DB may be empty"
    r = await client.post(
        "/api/v1/portfolio/swap-preview",
        json={"freed_budget": 100_000, "excluded_ea_ids": [real_ea_id]},
    )
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    body = r.json()
    assert "replacements" in body, f"Missing replacements key in {body}"
    assert "count" in body, f"Missing count key in {body}"
    # replacements may be 0 if all viable players are excluded, but the endpoint must succeed


# ── Portfolio delete ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_portfolio_delete(client):
    """DELETE /portfolio/{ea_id}?budget=2000000 returns 200 after seeding a slot."""
    # Generate and confirm one player first
    gen_r = await client.post("/api/v1/portfolio/generate", json={"budget": 2_000_000})
    assert gen_r.status_code == 200
    gen_body = gen_r.json()
    assert gen_body["count"] > 0

    player = gen_body["data"][0]
    ea_id = player["ea_id"]
    conf_r = await client.post(
        "/api/v1/portfolio/confirm",
        json={"players": [{"ea_id": ea_id, "buy_price": player["price"], "sell_price": player["sell_price"]}]},
    )
    assert conf_r.status_code == 200

    # Now delete
    r = await client.delete(f"/api/v1/portfolio/{ea_id}", params={"budget": 2_000_000})
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    body = r.json()
    assert body["removed_ea_id"] == ea_id, f"Wrong removed_ea_id: {body['removed_ea_id']}"
    assert "freed_budget" in body
    assert "replacements" in body


@pytest.mark.asyncio
async def test_portfolio_delete_nonexistent(client):
    """DELETE /portfolio/999999999?budget=2000000 returns 404 for unknown player."""
    r = await client.delete("/api/v1/portfolio/999999999", params={"budget": 2_000_000})
    assert r.status_code == 404, f"Expected 404, got {r.status_code}: {r.text}"


# ── Actions ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_actions_pending_empty(client):
    """GET /actions/pending with no portfolio returns {"action": null}."""
    r = await client.get("/api/v1/actions/pending")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    body = r.json()
    assert "action" in body, f"Missing action key in {body}"
    assert body["action"] is None, f"Expected null action with empty portfolio, got {body['action']}"


@pytest.mark.asyncio
async def test_actions_pending_with_slot(client, seed_real_portfolio_slot, real_ea_id):
    """GET /actions/pending returns a BUY action after seeding a portfolio slot."""
    assert real_ea_id is not None, "real_ea_id is None — DB may be empty"
    assert seed_real_portfolio_slot is not None, "seed_real_portfolio_slot is None"
    assert seed_real_portfolio_slot.status_code == 201, (
        f"Slot seed failed: {seed_real_portfolio_slot.status_code} {seed_real_portfolio_slot.text}"
    )

    r = await client.get("/api/v1/actions/pending")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    body = r.json()
    assert body["action"] is not None, (
        "Expected a pending action after seeding slot, got null. "
        "Either the action derivation loop is broken or the slot was not committed."
    )
    action = body["action"]
    assert action["action_type"] == "BUY", f"Expected BUY action for new slot, got {action['action_type']}"
    assert action["ea_id"] == real_ea_id, f"Wrong ea_id in action: {action['ea_id']}"


@pytest.mark.asyncio
async def test_actions_complete(client, seed_real_portfolio_slot, real_ea_id):
    """POST /actions/{id}/complete records outcome and returns status ok."""
    assert real_ea_id is not None
    assert seed_real_portfolio_slot is not None
    assert seed_real_portfolio_slot.status_code == 201

    # Get the pending action
    pending_r = await client.get("/api/v1/actions/pending")
    assert pending_r.status_code == 200
    action = pending_r.json()["action"]
    assert action is not None, "No pending action to complete"
    action_id = action["id"]

    # Complete it
    r = await client.post(
        f"/api/v1/actions/{action_id}/complete",
        json={"price": 50000, "outcome": "bought"},
    )
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    body = r.json()
    assert body["status"] == "ok", f"Expected status=ok, got {body['status']}"
    assert "trade_record_id" in body, f"Missing trade_record_id in {body}"
    assert body["trade_record_id"] > 0, f"Expected positive trade_record_id, got {body['trade_record_id']}"


# ── Portfolio slots ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_seed_portfolio_slots(client, real_ea_id):
    """POST /portfolio/slots with real ea_id returns 201."""
    assert real_ea_id is not None
    r = await client.post(
        "/api/v1/portfolio/slots",
        json={
            "slots": [
                {
                    "ea_id": real_ea_id,
                    "buy_price": 50000,
                    "sell_price": 70000,
                    "player_name": f"Player {real_ea_id}",
                }
            ]
        },
    )
    assert r.status_code == 201, f"Expected 201, got {r.status_code}: {r.text}"
    body = r.json()
    assert body["status"] == "ok", f"Expected status=ok, got {body['status']}"
    assert body["count"] == 1, f"Expected count=1, got {body['count']}"


# ── Trade records ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_trade_records_direct(client, seed_real_portfolio_slot, real_ea_id):
    """POST /trade-records/direct after seeding slot returns 201.

    Smoke test (happy path only). Partial failure and edge cases for trade
    records are tested in Plan 02 (test_lifecycle_flows.py) and Plan 03.
    """
    assert real_ea_id is not None
    assert seed_real_portfolio_slot is not None
    assert seed_real_portfolio_slot.status_code == 201

    r = await client.post(
        "/api/v1/trade-records/direct",
        json={"ea_id": real_ea_id, "price": 50000, "outcome": "bought"},
    )
    assert r.status_code == 201, f"Expected 201, got {r.status_code}: {r.text}"
    body = r.json()
    assert body["status"] == "ok", f"Expected status=ok, got {body['status']}"
    assert "trade_record_id" in body, f"Missing trade_record_id in {body}"


@pytest.mark.asyncio
async def test_trade_records_batch(client, seed_real_portfolio_slot, real_ea_id):
    """POST /trade-records/batch after seeding slot returns 201.

    Smoke test (happy path only). Partial failure with mixed valid/invalid
    ea_ids is tested in Plan 02 (test_lifecycle_flows.py).
    """
    assert real_ea_id is not None
    assert seed_real_portfolio_slot is not None
    assert seed_real_portfolio_slot.status_code == 201

    r = await client.post(
        "/api/v1/trade-records/batch",
        json={
            "records": [
                {"ea_id": real_ea_id, "price": 50000, "outcome": "bought"},
            ]
        },
    )
    assert r.status_code == 201, f"Expected 201, got {r.status_code}: {r.text}"
    body = r.json()
    assert body["status"] == "ok", f"Expected status=ok, got {body['status']}"
    assert real_ea_id in body["succeeded"], (
        f"Expected ea_id={real_ea_id} in succeeded, got {body['succeeded']}"
    )


# ── Portfolio status ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_portfolio_status_empty(client):
    """GET /portfolio/status with no slots returns 200 with empty players list."""
    r = await client.get("/api/v1/portfolio/status")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    body = r.json()
    assert "summary" in body, f"Missing summary key in {body}"
    assert "players" in body, f"Missing players key in {body}"
    assert isinstance(body["players"], list), f"Expected players to be a list, got {type(body['players'])}"


# ── Profit summary ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_profit_summary_empty(client):
    """GET /profit/summary with no trade records returns 200 with zero totals."""
    r = await client.get("/api/v1/profit/summary")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    body = r.json()
    assert "totals" in body, f"Missing totals key in {body}"
    assert "per_player" in body, f"Missing per_player key in {body}"
    totals = body["totals"]
    assert "total_spent" in totals, f"Missing total_spent in totals: {totals}"
    assert "total_earned" in totals, f"Missing total_earned in totals: {totals}"
    assert "net_profit" in totals, f"Missing net_profit in totals: {totals}"
    assert "trade_count" in totals, f"Missing trade_count in totals: {totals}"
