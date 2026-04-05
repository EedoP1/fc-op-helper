"""Smoke tests for all API endpoints using real DB data.

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
    GET  /api/v1/portfolio/actions-needed
    GET  /api/v1/portfolio/player-price/{ea_id}
    GET  /api/v1/automation/daily-cap
    POST /api/v1/automation/daily-cap/increment
    GET  /dashboard
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
    """GET /players/top?limit=10 returns 200 with proper response shape."""
    r = await client.get("/api/v1/players/top", params={"limit": 10})
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    body = r.json()
    assert "data" in body, f"Missing data key in {body}"
    assert "count" in body, f"Missing count key in {body}"
    assert isinstance(body["data"], list), f"data should be a list, got {type(body['data'])}"
    # Verify response fields when data is present
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
    # current_score may be None if the player has no viable scores
    assert "current_score" in body, f"Missing current_score in response: {body}"


@pytest.mark.asyncio
async def test_player_detail_nonexistent(client):
    """GET /players/999999999 returns 404 for a player not in DB."""
    r = await client.get("/api/v1/players/999999999")
    assert r.status_code == 404, f"Expected 404, got {r.status_code}: {r.text}"


# ── Portfolio generate ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_portfolio_generate_real_budget(client):
    """POST /portfolio/generate with real budget returns 200 with proper shape."""
    r = await client.post("/api/v1/portfolio/generate", json={"budget": 2_000_000})
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    body = r.json()
    assert "data" in body, f"Missing data key in {body}"
    assert "count" in body, f"Missing count key in {body}"
    assert isinstance(body["data"], list), f"data should be a list, got {type(body['data'])}"
    # Verify response fields when data is present
    for p in body["data"]:
        assert "ea_id" in p, f"Missing ea_id in generate response: {p}"
        assert "name" in p, f"Missing name in generate response: {p}"
        assert "price" in p, f"Missing price in generate response: {p}"
        assert "sell_price" in p, f"Missing sell_price in generate response: {p}"


@pytest.mark.asyncio
async def test_portfolio_get_real_budget(client):
    """GET /portfolio?budget=2000000 returns 200 with proper shape."""
    r = await client.get("/api/v1/portfolio", params={"budget": 2_000_000})
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    body = r.json()
    assert "data" in body, f"Missing data key in {body}"
    assert "count" in body, f"Missing count key in {body}"
    assert isinstance(body["data"], list)


# ── Portfolio confirm / confirmed ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_portfolio_confirm(client, real_ea_id):
    """POST /portfolio/confirm with a real ea_id returns confirmed > 0."""
    assert real_ea_id is not None, "real_ea_id is None"
    confirm_payload = {
        "players": [
            {
                "ea_id": real_ea_id,
                "buy_price": 50000,
                "sell_price": 70000,
            }
        ]
    }
    r = await client.post("/api/v1/portfolio/confirm", json=confirm_payload)
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    body = r.json()
    assert body["confirmed"] > 0, f"Expected confirmed > 0, got {body['confirmed']}"
    assert body["status"] == "ok", f"Expected status=ok, got {body['status']}"


@pytest.mark.asyncio
async def test_portfolio_confirmed_after_confirm(client, real_ea_id):
    """GET /portfolio/confirmed returns players after confirming."""
    assert real_ea_id is not None, "real_ea_id is None"
    # Confirm a slot first
    conf_r = await client.post(
        "/api/v1/portfolio/confirm",
        json={"players": [{"ea_id": real_ea_id, "buy_price": 50000, "sell_price": 70000}]},
    )
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
        json={"freed_budget": 100_000, "excluded_ea_ids": [real_ea_id], "current_count": 10},
    )
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    body = r.json()
    assert "replacements" in body, f"Missing replacements key in {body}"
    assert "count" in body, f"Missing count key in {body}"
    # replacements may be 0 if all viable players are excluded, but the endpoint must succeed


# ── Portfolio delete ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_portfolio_delete(client, real_ea_id):
    """DELETE /portfolio/{ea_id}?budget=2000000 returns 200 after confirming a slot."""
    assert real_ea_id is not None, "real_ea_id is None"
    # Confirm one player first
    conf_r = await client.post(
        "/api/v1/portfolio/confirm",
        json={"players": [{"ea_id": real_ea_id, "buy_price": 50000, "sell_price": 70000}]},
    )
    assert conf_r.status_code == 200

    # Now delete
    r = await client.delete(f"/api/v1/portfolio/{real_ea_id}", params={"budget": 2_000_000})
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    body = r.json()
    assert body["removed_ea_id"] == real_ea_id, f"Wrong removed_ea_id: {body['removed_ea_id']}"
    assert "freed_budget" in body
    # After deleting the only slot, remaining_total_cost=0, so freed_budget must equal the full budget.
    assert body["freed_budget"] == 2_000_000, (
        f"freed_budget should equal full budget when no slots remain, "
        f"got {body['freed_budget']} (expected 2_000_000)"
    )
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
    assert "realized_profit" in totals, f"Missing realized_profit in totals: {totals}"
    assert "total_profit" in totals, f"Missing total_profit in totals: {totals}"


# ── Actions needed ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_actions_needed_empty(client):
    """GET /portfolio/actions-needed with no portfolio returns 200 with zero summary."""
    r = await client.get("/api/v1/portfolio/actions-needed")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    body = r.json()
    assert "actions" in body, f"Missing actions key in {body}"
    assert "summary" in body, f"Missing summary key in {body}"
    summary = body["summary"]
    assert "to_buy" in summary, f"Missing to_buy in summary: {summary}"
    assert "to_list" in summary, f"Missing to_list in summary: {summary}"
    assert "to_relist" in summary, f"Missing to_relist in summary: {summary}"


@pytest.mark.asyncio
async def test_actions_needed_with_slot(client, seed_real_portfolio_slot, real_ea_id):
    """GET /portfolio/actions-needed after seeding slot shows to_buy > 0."""
    assert real_ea_id is not None
    assert seed_real_portfolio_slot is not None
    assert seed_real_portfolio_slot.status_code == 201

    r = await client.get("/api/v1/portfolio/actions-needed")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    body = r.json()
    assert body["summary"]["to_buy"] >= 1, (
        f"Expected to_buy >= 1 after seeding a slot, got {body['summary']}"
    )


# ── Player price (price guard) ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_player_price_with_slot(client, seed_real_portfolio_slot, real_ea_id):
    """GET /portfolio/player-price/{ea_id} returns buy/sell price for a portfolio player."""
    assert real_ea_id is not None
    assert seed_real_portfolio_slot is not None
    assert seed_real_portfolio_slot.status_code == 201

    r = await client.get(f"/api/v1/portfolio/player-price/{real_ea_id}")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    body = r.json()
    assert "buy_price" in body, f"Missing buy_price in {body}"
    assert "sell_price" in body, f"Missing sell_price in {body}"
    assert body["buy_price"] == 50000, f"Expected buy_price=50000, got {body['buy_price']}"
    assert body["sell_price"] == 70000, f"Expected sell_price=70000, got {body['sell_price']}"


@pytest.mark.asyncio
async def test_player_price_not_in_portfolio(client):
    """GET /portfolio/player-price/{ea_id} returns 404 for player not in portfolio."""
    r = await client.get("/api/v1/portfolio/player-price/999999999")
    assert r.status_code == 404, f"Expected 404, got {r.status_code}: {r.text}"


# ── Automation daily cap ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_daily_cap_response_shape(client):
    """GET /automation/daily-cap returns 200 with required fields."""
    r = await client.get("/api/v1/automation/daily-cap")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    body = r.json()
    assert "count" in body, f"Missing count in {body}"
    assert "cap" in body, f"Missing cap in {body}"
    assert "capped" in body, f"Missing capped in {body}"
    assert "date" in body, f"Missing date in {body}"
    assert isinstance(body["count"], int), f"count should be int, got {type(body['count'])}"
    assert isinstance(body["cap"], int), f"cap should be int, got {type(body['cap'])}"
    assert isinstance(body["capped"], bool), f"capped should be bool, got {type(body['capped'])}"


@pytest.mark.asyncio
async def test_daily_cap_increment(client):
    """POST /automation/daily-cap/increment increases count by 1."""
    # Get initial count
    r1 = await client.get("/api/v1/automation/daily-cap")
    assert r1.status_code == 200
    initial_count = r1.json()["count"]

    # Increment
    r2 = await client.post("/api/v1/automation/daily-cap/increment")
    assert r2.status_code == 200, f"Expected 200, got {r2.status_code}: {r2.text}"
    body = r2.json()
    assert body["count"] == initial_count + 1, (
        f"Expected count={initial_count + 1} after increment, got {body['count']}"
    )
    assert "capped" in body, f"Missing capped in {body}"


@pytest.mark.asyncio
async def test_daily_cap_increment_twice(client):
    """POST /automation/daily-cap/increment twice increases count by 2."""
    r0 = await client.get("/api/v1/automation/daily-cap")
    initial = r0.json()["count"]

    await client.post("/api/v1/automation/daily-cap/increment")
    r = await client.post("/api/v1/automation/daily-cap/increment")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == initial + 2, (
        f"Expected count={initial + 2} after 2 increments, got {body['count']}"
    )


# ── Dashboard ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dashboard_returns_html(client):
    """GET /dashboard returns 200 with HTML content."""
    r = await client.get("/dashboard")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    assert "text/html" in r.headers.get("content-type", ""), (
        f"Expected HTML content-type, got {r.headers.get('content-type')}"
    )
    assert "<html" in r.text.lower(), "Response doesn't contain <html> tag"
