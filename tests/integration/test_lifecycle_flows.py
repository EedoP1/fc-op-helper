"""Cross-endpoint lifecycle flow integration tests.

Verifies state transitions across multiple endpoints work correctly end-to-end
(BUY->LIST->SOLD, BUY->LIST->EXPIRED->RELIST, multi-player, direct records,
delete mid-cycle, confirm reset). All via real HTTP to a real uvicorn server.
"""
import pytest


# ── Helpers ───────────────────────────────────────────────────────────────────


async def _seed_slot(client, ea_id=100, buy_price=50000, sell_price=70000, name="Test Player"):
    """Seed a portfolio slot and return the response."""
    r = await client.post("/api/v1/portfolio/slots", json={"slots": [
        {"ea_id": ea_id, "buy_price": buy_price, "sell_price": sell_price, "player_name": name}
    ]})
    assert r.status_code == 201
    return r


async def _get_and_complete(client, expected_type, outcome, price):
    """Get pending action, assert type, complete it, return action data."""
    r = await client.get("/api/v1/actions/pending")
    assert r.status_code == 200
    action = r.json()["action"]
    assert action is not None, f"Expected {expected_type} action but got null"
    assert action["action_type"] == expected_type
    r2 = await client.post(f"/api/v1/actions/{action['id']}/complete",
                            json={"price": price, "outcome": outcome})
    assert r2.status_code == 200
    return action


# ── Tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_full_buy_list_sold_cycle(client):
    """BUY -> LIST -> SOLD produces correct profit and restarts cycle."""
    # 1. Seed slot
    await _seed_slot(client, ea_id=100, buy_price=50000, sell_price=70000)

    # 2. GET /actions/pending -> BUY action (target_price=50000)
    # 3. POST /actions/{id}/complete with outcome="bought", price=50000
    await _get_and_complete(client, "BUY", "bought", 50000)

    # 4. GET /actions/pending -> LIST action (target_price=70000)
    # 5. POST /actions/{id}/complete with outcome="sold", price=70000
    await _get_and_complete(client, "LIST", "sold", 70000)

    # 6. GET /portfolio/status -> player status="SOLD", times_sold=1, realized_profit > 0
    r = await client.get("/api/v1/portfolio/status")
    assert r.status_code == 200
    body = r.json()
    players = body["players"]
    assert len(players) == 1
    p = players[0]
    assert p["ea_id"] == 100
    assert p["status"] == "SOLD"
    assert p["times_sold"] == 1
    assert p["realized_profit"] > 0

    # 7. GET /profit/summary -> totals
    # Expected: total_spent=50000, total_earned=int(70000*0.95)=66500, net_profit=16500
    r = await client.get("/api/v1/profit/summary")
    assert r.status_code == 200
    totals = r.json()["totals"]
    assert totals["total_spent"] == 50000
    assert totals["total_earned"] == 66500
    assert totals["net_profit"] == 16500
    assert totals["trade_count"] == 2

    # 8. GET /actions/pending -> BUY again (cycle restarts after sold)
    r = await client.get("/api/v1/actions/pending")
    assert r.status_code == 200
    action = r.json()["action"]
    assert action is not None
    assert action["action_type"] == "BUY"


@pytest.mark.asyncio
async def test_buy_list_expired_relist_cycle(client):
    """BUY -> LIST -> EXPIRED -> RELIST cycle derives correct action types."""
    # 1. Seed slot
    await _seed_slot(client, ea_id=200, buy_price=40000, sell_price=55000)

    # 2. BUY -> complete with "bought"
    await _get_and_complete(client, "BUY", "bought", 40000)

    # 3. LIST -> complete with "listed" (card is on market)
    await _get_and_complete(client, "LIST", "listed", 55000)

    # 4. GET /actions/pending -> null (card is "listed", waiting)
    r = await client.get("/api/v1/actions/pending")
    assert r.status_code == 200
    assert r.json()["action"] is None, "Expected null action when card is listed"

    # 5. Record expired outcome directly
    r = await client.post("/api/v1/trade-records/direct", json={
        "ea_id": 200, "outcome": "expired", "price": 55000
    })
    assert r.status_code == 201

    # 6. GET /actions/pending -> RELIST action (target_price=55000)
    r = await client.get("/api/v1/actions/pending")
    assert r.status_code == 200
    action = r.json()["action"]
    assert action is not None, "Expected RELIST action after expired record"
    assert action["action_type"] == "RELIST"
    assert action["target_price"] == 55000

    # 7. Complete RELIST with "listed"
    r2 = await client.post(f"/api/v1/actions/{action['id']}/complete",
                            json={"price": 55000, "outcome": "listed"})
    assert r2.status_code == 200

    # 8. GET /portfolio/status -> status="LISTED"
    r = await client.get("/api/v1/portfolio/status")
    assert r.status_code == 200
    players = r.json()["players"]
    assert len(players) == 1
    assert players[0]["status"] == "LISTED"


@pytest.mark.asyncio
async def test_multi_player_interleaved(client):
    """Multi-player portfolio with interleaved actions produces correct per-player status.

    Uses direct records to set up specific states for each player, then verifies
    that portfolio/status and profit/summary reflect both players correctly.
    """
    # 1. Seed two slots
    r = await client.post("/api/v1/portfolio/slots", json={"slots": [
        {"ea_id": 300, "buy_price": 30000, "sell_price": 45000, "player_name": "Player 300"},
        {"ea_id": 400, "buy_price": 60000, "sell_price": 80000, "player_name": "Player 400"},
    ]})
    assert r.status_code == 201

    # 2. Use direct records to set player 300 as "sold" and player 400 as "bought"
    #    This simulates interleaved cycle progression without relying on action ordering
    r = await client.post("/api/v1/trade-records/direct", json={
        "ea_id": 300, "price": 30000, "outcome": "bought"
    })
    assert r.status_code == 201

    r = await client.post("/api/v1/trade-records/direct", json={
        "ea_id": 300, "price": 45000, "outcome": "sold"
    })
    assert r.status_code == 201

    r = await client.post("/api/v1/trade-records/direct", json={
        "ea_id": 400, "price": 60000, "outcome": "bought"
    })
    assert r.status_code == 201

    # 3. GET /portfolio/status -> player 300 is SOLD, player 400 is BOUGHT
    r = await client.get("/api/v1/portfolio/status")
    assert r.status_code == 200
    players_by_ea = {p["ea_id"]: p for p in r.json()["players"]}
    assert len(players_by_ea) == 2
    assert players_by_ea[300]["status"] == "SOLD"
    assert players_by_ea[300]["times_sold"] == 1
    assert players_by_ea[400]["status"] == "BOUGHT"

    # 4. GET /profit/summary -> per_player has entries for both ea_ids
    r = await client.get("/api/v1/profit/summary")
    assert r.status_code == 200
    per_player_ea_ids = {p["ea_id"] for p in r.json()["per_player"]}
    assert 300 in per_player_ea_ids
    assert 400 in per_player_ea_ids

    # 5. Verify actions are derived correctly for the remaining needed work
    #    Player 400 is "bought" -> next action should be LIST for 400
    #    Player 300 is "sold" -> next action should be BUY for 300
    r = await client.get("/api/v1/actions/pending")
    assert r.status_code == 200
    action = r.json()["action"]
    assert action is not None
    # Either BUY for 300 (restart after sold) or LIST for 400 (after bought)
    assert action["ea_id"] in {300, 400}
    if action["ea_id"] == 300:
        assert action["action_type"] == "BUY"
    else:
        assert action["action_type"] == "LIST"


@pytest.mark.asyncio
async def test_direct_record_affects_lifecycle(client):
    """Direct trade record sets state so action derivation picks up where it left off."""
    # 1. Seed slot
    await _seed_slot(client, ea_id=500, buy_price=50000, sell_price=65000)

    # 2. POST /trade-records/direct with ea_id=500, outcome="bought"
    r = await client.post("/api/v1/trade-records/direct", json={
        "ea_id": 500, "price": 50000, "outcome": "bought"
    })
    assert r.status_code == 201

    # 3. GET /actions/pending -> LIST (not BUY, because direct record set state to "bought")
    r = await client.get("/api/v1/actions/pending")
    assert r.status_code == 200
    action = r.json()["action"]
    assert action is not None, "Expected LIST action after direct bought record"
    assert action["action_type"] == "LIST"
    assert action["ea_id"] == 500

    # 4. Complete LIST with "sold"
    r = await client.post(f"/api/v1/actions/{action['id']}/complete",
                           json={"price": 65000, "outcome": "sold"})
    assert r.status_code == 200

    # 5. GET /portfolio/status -> status="SOLD"
    r = await client.get("/api/v1/portfolio/status")
    assert r.status_code == 200
    players = r.json()["players"]
    assert len(players) == 1
    assert players[0]["status"] == "SOLD"


@pytest.mark.asyncio
async def test_delete_player_mid_cycle(client):
    """Deleting a player mid-cycle removes it from actions and status."""
    # 1. Seed slot via /portfolio/slots (this creates the PortfolioSlot that DELETE can find)
    # NOTE: Do NOT call /portfolio/confirm — that resets the whole portfolio
    await _seed_slot(client, ea_id=600, buy_price=35000, sell_price=50000)

    # 2. GET pending -> BUY, complete with "bought"
    await _get_and_complete(client, "BUY", "bought", 35000)

    # 3. DELETE /portfolio/600?budget=1000000 -> 200 with removed_ea_id=600
    r = await client.delete("/api/v1/portfolio/600?budget=1000000")
    assert r.status_code == 200
    body = r.json()
    assert body["removed_ea_id"] == 600

    # 4. GET /actions/pending -> null (slot removed, no more actions for ea_id=600)
    r = await client.get("/api/v1/actions/pending")
    assert r.status_code == 200
    assert r.json()["action"] is None, "Expected null action after player deleted"

    # 5. GET /portfolio/status -> player 600 no longer in players list
    r = await client.get("/api/v1/portfolio/status")
    assert r.status_code == 200
    ea_ids = [p["ea_id"] for p in r.json()["players"]]
    assert 600 not in ea_ids


@pytest.mark.asyncio
async def test_confirm_resets_portfolio(client):
    """POST /portfolio/confirm replaces all existing slots with a clean slate."""
    # 1. Seed two slots via /portfolio/confirm (ea_id=700, ea_id=800)
    r = await client.post("/api/v1/portfolio/confirm", json={"players": [
        {"ea_id": 700, "buy_price": 20000, "sell_price": 30000},
        {"ea_id": 800, "buy_price": 25000, "sell_price": 35000},
    ]})
    assert r.status_code == 200
    assert r.json()["confirmed"] == 2

    # 2. GET /portfolio/status -> 2 players
    r = await client.get("/api/v1/portfolio/status")
    assert r.status_code == 200
    assert len(r.json()["players"]) == 2

    # 3. POST /portfolio/confirm with only ea_id=900
    r = await client.post("/api/v1/portfolio/confirm", json={"players": [
        {"ea_id": 900, "buy_price": 15000, "sell_price": 22000},
    ]})
    assert r.status_code == 200
    assert r.json()["confirmed"] == 1

    # 4. GET /portfolio/status -> only 1 player (ea_id=900), previous slots gone
    r = await client.get("/api/v1/portfolio/status")
    assert r.status_code == 200
    players = r.json()["players"]
    assert len(players) == 1
    assert players[0]["ea_id"] == 900
