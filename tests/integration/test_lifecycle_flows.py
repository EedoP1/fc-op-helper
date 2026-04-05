"""Cross-endpoint lifecycle flow tests.

Tests the real-world workflows the Chrome extension actually executes:
BUY -> LIST -> SOLD, EXPIRED -> RELIST, direct/batch records, status
reflection, and profit calculation.

All tests use the real server started by conftest.live_server and a copy
of the production DB. No mocks. No fake ea_ids.

Tests that fail = server bugs. Do NOT weaken assertions to make tests
pass (per D-04 from 09-CONTEXT.md).
"""
import pytest


# ── Helpers ────────────────────────────────────────────────────────────────────


async def _seed_slot(client, ea_id: int, buy_price: int, sell_price: int) -> None:
    """Seed a single portfolio slot via POST /portfolio/slots."""
    r = await client.post(
        "/api/v1/portfolio/slots",
        json={
            "slots": [
                {
                    "ea_id": ea_id,
                    "buy_price": buy_price,
                    "sell_price": sell_price,
                    "player_name": f"Player {ea_id}",
                }
            ]
        },
    )
    assert r.status_code == 201, f"seed_slot failed: {r.status_code} {r.text}"


async def _get_pending(client) -> dict | None:
    """GET /actions/pending, assert 200, return action dict or None."""
    r = await client.get("/api/v1/actions/pending")
    assert r.status_code == 200, f"pending failed: {r.status_code} {r.text}"
    return r.json()["action"]


async def _complete(client, action_id: int, outcome: str, price: int) -> dict:
    """POST /actions/{id}/complete, assert 200, return response body."""
    r = await client.post(
        f"/api/v1/actions/{action_id}/complete",
        json={"price": price, "outcome": outcome},
    )
    assert r.status_code == 200, f"complete failed: {r.status_code} {r.text}"
    return r.json()


# ── Tests ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_full_buy_list_sold_cycle(client, real_ea_ids):
    """BUY -> bought -> LIST -> sold -> next BUY confirmed by /actions/pending (D-07, D-10).

    This exercises the happy-path full trade cycle that the extension
    performs for every player:
      1. Seed 1 slot
      2. GET pending -> BUY
      3. Complete BUY with outcome=bought
      4. GET pending -> LIST
      5. Complete LIST with outcome=sold
      6. GET pending -> BUY (new cycle)
      7. GET /portfolio/status -> player status SOLD
      8. GET /profit/summary -> net_profit != 0
    """
    assert len(real_ea_ids) >= 1, "Need at least 1 active player in DB"
    ea_id = real_ea_ids[0]
    buy_price = 50000
    sell_price = 70000

    await _seed_slot(client, ea_id, buy_price, sell_price)

    # Step 1: GET pending -> BUY
    action = await _get_pending(client)
    assert action is not None, "Expected BUY action after seeding slot, got null"
    assert action["action_type"] == "BUY", (
        f"Expected action_type=BUY for new slot, got {action['action_type']}"
    )
    assert action["ea_id"] == ea_id

    buy_action_id = action["id"]

    # Step 2: Complete BUY with outcome=bought
    result = await _complete(client, buy_action_id, "bought", buy_price)
    assert result["status"] == "ok"
    assert result["trade_record_id"] > 0

    # Step 3: GET pending -> LIST
    action = await _get_pending(client)
    assert action is not None, (
        "Expected LIST action after bought, got null. "
        "Lifecycle derivation broken: bought -> LIST not triggering."
    )
    assert action["action_type"] == "LIST", (
        f"Expected action_type=LIST after bought, got {action['action_type']}"
    )
    assert action["ea_id"] == ea_id

    list_action_id = action["id"]

    # Step 4: Complete LIST with outcome=sold
    result = await _complete(client, list_action_id, "sold", sell_price)
    assert result["status"] == "ok"
    assert result["trade_record_id"] > 0

    # Step 5: GET pending -> BUY (cycle restarts after sold)
    action = await _get_pending(client)
    assert action is not None, (
        "Expected BUY action after sold (new cycle), got null. "
        "Lifecycle derivation broken: sold -> BUY not triggering."
    )
    assert action["action_type"] == "BUY", (
        f"Expected BUY for new cycle after sold, got {action['action_type']}"
    )

    # Step 6: GET /portfolio/status -> status should be SOLD (most recent record=sold)
    r = await client.get("/api/v1/portfolio/status")
    assert r.status_code == 200
    status_body = r.json()
    assert len(status_body["players"]) > 0, "Expected at least 1 player in status"
    player_status = next(
        (p for p in status_body["players"] if p["ea_id"] == ea_id), None
    )
    assert player_status is not None, f"ea_id={ea_id} not found in status players"
    assert player_status["status"] == "SOLD", (
        f"Expected status=SOLD after sold record, got {player_status['status']}"
    )

    # Step 7: GET /profit/summary -> net_profit != 0 (there was a complete buy+sell)
    r = await client.get("/api/v1/profit/summary")
    assert r.status_code == 200
    profit_body = r.json()
    assert profit_body["totals"]["net_profit"] != 0, (
        "Expected non-zero net_profit after completed buy+sell cycle, got 0. "
        "Either the profit calculation is broken or the records were not persisted."
    )


@pytest.mark.asyncio
async def test_buy_list_expired_relist_cycle(client, real_ea_ids):
    """BUY -> bought -> LIST -> expired -> RELIST -> listed -> waiting (D-10).

    Tests the expired/relist branch of the lifecycle state machine:
      - expired leads to RELIST action
      - after relist completes with listed, card is on market (no more actions)
    """
    assert len(real_ea_ids) >= 1, "Need at least 1 active player in DB"
    ea_id = real_ea_ids[0]
    buy_price = 50000
    sell_price = 70000

    await _seed_slot(client, ea_id, buy_price, sell_price)

    # BUY
    action = await _get_pending(client)
    assert action is not None and action["action_type"] == "BUY"
    await _complete(client, action["id"], "bought", buy_price)

    # LIST
    action = await _get_pending(client)
    assert action is not None, "Expected LIST action after bought"
    assert action["action_type"] == "LIST"
    await _complete(client, action["id"], "expired", sell_price)

    # RELIST
    action = await _get_pending(client)
    assert action is not None, (
        "Expected RELIST action after expired, got null. "
        "Lifecycle broken: expired -> RELIST not triggering."
    )
    assert action["action_type"] == "RELIST", (
        f"Expected RELIST after expired, got {action['action_type']}"
    )

    # Complete RELIST with outcome=listed -> card on market, nothing to do
    await _complete(client, action["id"], "listed", sell_price)

    action = await _get_pending(client)
    assert action is None, (
        f"Expected null action after listed (card on market), got action_type={action.get('action_type') if action else None}. "
        "Lifecycle broken: listed should mean 'waiting, nothing to do'."
    )


@pytest.mark.asyncio
async def test_listed_means_waiting(client, real_ea_ids):
    """After BUY+bought+LIST+listed, no more actions should be pending (D-10).

    When the most recent trade record is 'listed', the card is on the
    transfer market. The server must return null from /actions/pending.
    """
    assert len(real_ea_ids) >= 1, "Need at least 1 active player in DB"
    ea_id = real_ea_ids[0]
    buy_price = 50000
    sell_price = 70000

    await _seed_slot(client, ea_id, buy_price, sell_price)

    # BUY -> bought
    action = await _get_pending(client)
    assert action is not None and action["action_type"] == "BUY"
    await _complete(client, action["id"], "bought", buy_price)

    # LIST -> listed
    action = await _get_pending(client)
    assert action is not None and action["action_type"] == "LIST"
    await _complete(client, action["id"], "listed", sell_price)

    # Nothing to do — card is listed
    action = await _get_pending(client)
    assert action is None, (
        f"Expected null action after listed (card on market), got {action}. "
        "Lifecycle bug: 'listed' state should produce no pending action."
    )


@pytest.mark.asyncio
async def test_direct_trade_record_advances_lifecycle(client, real_ea_ids):
    """POST /trade-records/direct with outcome=bought should derive LIST next.

    Direct records bypass the action queue — used for bootstrap when the
    extension scans the Transfer List before any actions exist.
    """
    assert len(real_ea_ids) >= 1, "Need at least 1 active player in DB"
    ea_id = real_ea_ids[0]
    buy_price = 50000
    sell_price = 70000

    await _seed_slot(client, ea_id, buy_price, sell_price)

    # Direct record: bought (no action_id needed)
    r = await client.post(
        "/api/v1/trade-records/direct",
        json={"ea_id": ea_id, "price": buy_price, "outcome": "bought"},
    )
    assert r.status_code == 201, f"direct record failed: {r.status_code} {r.text}"
    body = r.json()
    assert body["status"] == "ok"
    assert body["trade_record_id"] > 0

    # GET pending -> should derive LIST because latest record is bought
    action = await _get_pending(client)
    assert action is not None, (
        "Expected LIST action after direct bought record, got null. "
        "Direct record did not advance lifecycle."
    )
    assert action["action_type"] == "LIST", (
        f"Expected LIST after direct bought record, got {action['action_type']}"
    )
    assert action["ea_id"] == ea_id


@pytest.mark.asyncio
async def test_direct_trade_record_deduplication(client, real_ea_ids):
    """POST /trade-records/direct twice with same outcome returns deduplicated=True.

    Server-side dedup: if the latest trade record has the same outcome as
    the new request, return deduplicated=True and trade_record_id=-1.
    """
    assert len(real_ea_ids) >= 1, "Need at least 1 active player in DB"
    ea_id = real_ea_ids[0]
    buy_price = 50000

    await _seed_slot(client, ea_id, buy_price, 70000)

    # First direct record: bought
    r = await client.post(
        "/api/v1/trade-records/direct",
        json={"ea_id": ea_id, "price": buy_price, "outcome": "bought"},
    )
    assert r.status_code == 201
    first_body = r.json()
    assert first_body["status"] == "ok"
    assert first_body.get("deduplicated") is not True, (
        "First direct record unexpectedly deduplicated — no prior record existed"
    )

    # Second direct record: same outcome=bought -> should be deduplicated
    r = await client.post(
        "/api/v1/trade-records/direct",
        json={"ea_id": ea_id, "price": buy_price, "outcome": "bought"},
    )
    assert r.status_code == 201
    second_body = r.json()
    assert second_body.get("deduplicated") is True, (
        f"Expected deduplicated=True on second identical direct record, got {second_body}"
    )
    assert second_body["trade_record_id"] == -1, (
        f"Expected trade_record_id=-1 for deduped record, got {second_body['trade_record_id']}"
    )


@pytest.mark.asyncio
async def test_batch_trade_records_mixed(client, real_ea_ids):
    """POST /trade-records/batch with 2 valid + 1 invalid ea_id handles partial failures.

    The batch endpoint must:
    - succeed for valid ea_ids (in portfolio)
    - fail for invalid ea_ids (not in portfolio or invalid outcome)
    - return succeeded and failed arrays
    """
    assert len(real_ea_ids) >= 2, "Need at least 2 active players in DB"
    ea_id_1 = real_ea_ids[0]
    ea_id_2 = real_ea_ids[1]
    invalid_ea_id = 999_999_999

    # Seed both valid slots
    r = await client.post(
        "/api/v1/portfolio/slots",
        json={
            "slots": [
                {
                    "ea_id": ea_id_1,
                    "buy_price": 50000,
                    "sell_price": 70000,
                    "player_name": f"Player {ea_id_1}",
                },
                {
                    "ea_id": ea_id_2,
                    "buy_price": 50000,
                    "sell_price": 70000,
                    "player_name": f"Player {ea_id_2}",
                },
            ]
        },
    )
    assert r.status_code == 201

    # Batch: 2 valid + 1 invalid
    r = await client.post(
        "/api/v1/trade-records/batch",
        json={
            "records": [
                {"ea_id": ea_id_1, "price": 50000, "outcome": "bought"},
                {"ea_id": ea_id_2, "price": 50000, "outcome": "bought"},
                {"ea_id": invalid_ea_id, "price": 50000, "outcome": "bought"},
            ]
        },
    )
    assert r.status_code == 201, f"batch failed: {r.status_code} {r.text}"
    body = r.json()
    assert body["status"] == "ok"

    assert ea_id_1 in body["succeeded"], (
        f"Expected ea_id={ea_id_1} in succeeded, got {body['succeeded']}"
    )
    assert ea_id_2 in body["succeeded"], (
        f"Expected ea_id={ea_id_2} in succeeded, got {body['succeeded']}"
    )
    assert invalid_ea_id in body["failed"], (
        f"Expected invalid ea_id={invalid_ea_id} in failed, got {body['failed']}"
    )


@pytest.mark.asyncio
async def test_batch_trade_records_dedup(client, real_ea_ids):
    """Batch record for same ea_id+outcome that was already direct-recorded is deduped.

    Dedup should be success: the batch endpoint treats deduplicated records
    as succeeded (not failed). Lifecycle must be unaffected.
    """
    assert len(real_ea_ids) >= 1, "Need at least 1 active player in DB"
    ea_id = real_ea_ids[0]
    buy_price = 50000
    sell_price = 70000

    await _seed_slot(client, ea_id, buy_price, sell_price)

    # Direct record: bought
    r = await client.post(
        "/api/v1/trade-records/direct",
        json={"ea_id": ea_id, "price": buy_price, "outcome": "bought"},
    )
    assert r.status_code == 201
    assert r.json()["status"] == "ok"

    # Batch with same outcome (should dedup)
    r = await client.post(
        "/api/v1/trade-records/batch",
        json={
            "records": [
                {"ea_id": ea_id, "price": buy_price, "outcome": "bought"},
            ]
        },
    )
    assert r.status_code == 201
    batch_body = r.json()
    assert ea_id in batch_body["succeeded"], (
        f"Expected ea_id in succeeded (deduped = success), got {batch_body}"
    )

    # Lifecycle should still be correct: latest record is still bought -> LIST
    action = await _get_pending(client)
    assert action is not None, "Expected LIST action after bought (deduped batch did not corrupt lifecycle)"
    assert action["action_type"] == "LIST", (
        f"Expected LIST after bought + deduped batch, got {action['action_type']}"
    )


@pytest.mark.asyncio
async def test_portfolio_status_reflects_lifecycle(client, real_ea_ids):
    """GET /portfolio/status status field reflects the most recent trade record (D-07).

    Seeds 2 slots, drives slot1 to BOUGHT and slot2 to SOLD.
    Verifies the status endpoint reports the correct per-player status.
    """
    assert len(real_ea_ids) >= 2, "Need at least 2 active players in DB"
    slot1_ea_id = real_ea_ids[0]
    slot2_ea_id = real_ea_ids[1]
    buy_price = 50000
    sell_price = 70000

    # Seed both slots at once
    r = await client.post(
        "/api/v1/portfolio/slots",
        json={
            "slots": [
                {
                    "ea_id": slot1_ea_id,
                    "buy_price": buy_price,
                    "sell_price": sell_price,
                    "player_name": f"Player {slot1_ea_id}",
                },
                {
                    "ea_id": slot2_ea_id,
                    "buy_price": buy_price,
                    "sell_price": sell_price,
                    "player_name": f"Player {slot2_ea_id}",
                },
            ]
        },
    )
    assert r.status_code == 201

    # Drive actions until slot1 is BOUGHT and slot2 is SOLD.
    # Action ordering is non-deterministic (_derive_next_action iterates
    # slots without ORDER BY), so we consume actions by ea_id rather than
    # assuming a fixed order.
    prices = {
        slot1_ea_id: {"buy": buy_price, "sell": sell_price},
        slot2_ea_id: {"buy": buy_price, "sell": sell_price},
    }

    # Track per-slot state so we know when we're done
    bought = set()   # ea_ids that have been bought
    sold = set()     # ea_ids that have been sold

    # We need: slot1 bought, slot2 bought+listed+sold = up to 4 actions
    for _ in range(6):  # safety bound
        action = await _get_pending(client)
        if action is None:
            break
        ea = action["ea_id"]
        assert ea in prices, f"Unexpected ea_id {ea} in action"

        if action["action_type"] == "BUY":
            await _complete(client, action["id"], "bought", prices[ea]["buy"])
            bought.add(ea)
        elif action["action_type"] == "LIST":
            if ea == slot2_ea_id:
                # Drive slot2 to sold
                await _complete(client, action["id"], "sold", prices[ea]["sell"])
                sold.add(ea)
            else:
                # slot1 LIST surfaced before slot2 BUY — complete as listed
                # so it stops generating actions (listed = waiting on market)
                await _complete(client, action["id"], "listed", prices[ea]["sell"])

        # Check if we've reached the target state
        if slot1_ea_id in bought and slot2_ea_id in sold:
            break

    assert slot1_ea_id in bought, f"slot1 (ea_id={slot1_ea_id}) never reached BOUGHT"
    assert slot2_ea_id in sold, f"slot2 (ea_id={slot2_ea_id}) never reached SOLD"

    # GET /portfolio/status
    r = await client.get("/api/v1/portfolio/status")
    assert r.status_code == 200
    status_body = r.json()

    players_map = {p["ea_id"]: p for p in status_body["players"]}

    # slot1: latest record was 'bought' or 'listed' depending on action order
    assert slot1_ea_id in players_map, f"slot1 ea_id={slot1_ea_id} not in status response"
    assert players_map[slot1_ea_id]["status"] in ("BOUGHT", "LISTED"), (
        f"Expected BOUGHT or LISTED for slot1, got {players_map[slot1_ea_id]['status']}"
    )

    # slot2: latest record was 'sold' -> status SOLD
    assert slot2_ea_id in players_map, f"slot2 ea_id={slot2_ea_id} not in status response"
    assert players_map[slot2_ea_id]["status"] == "SOLD", (
        f"Expected SOLD for slot2, got {players_map[slot2_ea_id]['status']}"
    )

    # Summary trade counts
    summary = status_body["summary"]
    assert summary["trade_counts"]["bought"] >= 1, (
        f"Expected bought_count >= 1, got {summary['trade_counts']['bought']}"
    )
    assert summary["trade_counts"]["sold"] >= 1, (
        f"Expected sold_count >= 1, got {summary['trade_counts']['sold']}"
    )


@pytest.mark.asyncio
async def test_profit_summary_after_full_cycle(client, real_ea_ids):
    """GET /profit/summary after buy+sold cycle returns correct totals.

    Uses buy_price=50000, sell_price=70000.
    Expected:
      total_spent  = 50000
      total_earned = int(70000 * 0.95)  = 66500
      net_profit   = 66500 - 50000      = 16500
    """
    assert len(real_ea_ids) >= 1, "Need at least 1 active player in DB"
    ea_id = real_ea_ids[0]
    buy_price = 50_000
    sell_price = 70_000

    await _seed_slot(client, ea_id, buy_price, sell_price)

    # BUY -> bought
    action = await _get_pending(client)
    assert action is not None and action["action_type"] == "BUY"
    await _complete(client, action["id"], "bought", buy_price)

    # LIST -> sold
    action = await _get_pending(client)
    assert action is not None and action["action_type"] == "LIST"
    await _complete(client, action["id"], "sold", sell_price)

    r = await client.get("/api/v1/profit/summary")
    assert r.status_code == 200
    profit_body = r.json()
    totals = profit_body["totals"]

    expected_spent = buy_price
    expected_earned = int(sell_price * 0.95)
    expected_net = expected_earned - expected_spent

    assert totals["total_spent"] == expected_spent, (
        f"Expected total_spent={expected_spent}, got {totals['total_spent']}"
    )
    assert totals["total_earned"] == expected_earned, (
        f"Expected total_earned={expected_earned} (after EA 5% tax), got {totals['total_earned']}"
    )
    assert totals["net_profit"] == expected_net, (
        f"Expected net_profit={expected_net}, got {totals['net_profit']}"
    )


@pytest.mark.asyncio
async def test_confirm_then_lifecycle(client, real_ea_ids):
    """POST /portfolio/confirm seeds portfolio; GET /actions/pending derives BUY.

    After confirm, the portfolio is seeded and the action queue should
    derive BUY for the first slot.
    """
    assert len(real_ea_ids) >= 2, "Need at least 2 active players in DB"
    ea_ids = real_ea_ids[:2]

    confirm_payload = {
        "players": [
            {"ea_id": eid, "buy_price": 50000, "sell_price": 70000}
            for eid in ea_ids
        ]
    }

    # Confirm
    r = await client.post("/api/v1/portfolio/confirm", json=confirm_payload)
    assert r.status_code == 200, f"confirm failed: {r.status_code} {r.text}"
    conf_body = r.json()
    assert conf_body["confirmed"] == len(ea_ids), (
        f"Expected confirmed={len(ea_ids)}, got {conf_body['confirmed']}"
    )
    assert conf_body["status"] == "ok"

    # GET /portfolio/confirmed
    r = await client.get("/api/v1/portfolio/confirmed")
    assert r.status_code == 200
    confirmed_body = r.json()
    assert confirmed_body["count"] == len(ea_ids), (
        f"Expected {len(ea_ids)} confirmed slots, got {confirmed_body['count']}"
    )

    # GET /actions/pending -> should derive BUY for first slot
    action = await _get_pending(client)
    assert action is not None, (
        "Expected BUY action after confirm, got null. "
        "Lifecycle derivation broken after portfolio confirmation."
    )
    assert action["action_type"] == "BUY", (
        f"Expected BUY action for confirmed slot, got {action['action_type']}"
    )
    # ea_id should be one of the confirmed players
    confirmed_ea_ids = set(ea_ids)
    assert action["ea_id"] in confirmed_ea_ids, (
        f"Action ea_id={action['ea_id']} not in confirmed portfolio {confirmed_ea_ids}"
    )
