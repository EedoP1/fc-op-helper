"""Concurrent access, duplicate detection, and race condition tests.

Tests the known concurrent-remove duplicate bug (D-08) and other race
conditions caused by rapid parallel API calls — exactly the pattern the
Chrome extension uses.

All tests use asyncio.gather to fire real concurrent HTTP requests to the
real server. No mocks. No fake ea_ids.

Tests that fail = server bugs (per D-04). The concurrent-remove duplicate
bug is a KNOWN issue — if test_concurrent_remove_two_players_no_duplicates
fails, that confirms the bug exists and needs a server-side fix.
"""
import asyncio

import pytest


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _confirm_ea_ids(client, ea_ids: list[int]) -> int:
    """Confirm a list of ea_ids into portfolio_slots with fixed prices.

    Returns confirmed count.
    """
    r = await client.post(
        "/api/v1/portfolio/confirm",
        json={
            "players": [
                {"ea_id": eid, "buy_price": 50000, "sell_price": 70000}
                for eid in ea_ids
            ]
        },
    )
    assert r.status_code == 200, f"confirm failed: {r.status_code} {r.text}"
    return r.json()["confirmed"]


async def _seed_slot(client, ea_id: int, buy_price: int, sell_price: int) -> None:
    """Seed a single portfolio slot."""
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


async def _get_confirmed_ea_ids(client) -> list[int]:
    """Return all ea_ids currently in portfolio/confirmed."""
    r = await client.get("/api/v1/portfolio/confirmed")
    assert r.status_code == 200, f"confirmed failed: {r.status_code} {r.text}"
    return [p["ea_id"] for p in r.json()["data"]]


# ── Concurrent remove tests ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_concurrent_remove_two_players_no_duplicates(client, real_ea_ids):
    """Fire 2 concurrent DELETE /portfolio/{ea_id} and assert NO duplicates in portfolio (D-08).

    The server-side invariant: concurrent removes must NOT produce duplicate
    entries in portfolio_slots.
    """
    assert len(real_ea_ids) >= 4, "Need at least 4 active players in DB"

    confirmed = await _confirm_ea_ids(client, real_ea_ids[:4])
    assert confirmed >= 3, f"Expected at least 3 confirmed, got {confirmed}"

    # Pick 2 players to remove concurrently
    remove_1 = real_ea_ids[0]
    remove_2 = real_ea_ids[1]
    budget = 5_000_000

    # Fire both DELETEs simultaneously
    async def _delete(ea_id: int) -> dict:
        r = await client.delete(f"/api/v1/portfolio/{ea_id}", params={"budget": budget})
        return {"status_code": r.status_code, "body": r.json() if r.status_code == 200 else {}}

    results = await asyncio.gather(_delete(remove_1), _delete(remove_2))

    # Both should succeed (200) — if either 404s due to a race, that itself is a bug
    for i, result in enumerate(results):
        assert result["status_code"] == 200, (
            f"DELETE {i+1} failed: status={result['status_code']}, body={result['body']}"
        )

    # Verify the removed players are gone from portfolio
    current_ea_ids = await _get_confirmed_ea_ids(client)
    assert remove_1 not in current_ea_ids, (
        f"Removed ea_id={remove_1} still in portfolio after DELETE"
    )
    assert remove_2 not in current_ea_ids, (
        f"Removed ea_id={remove_2} still in portfolio after DELETE"
    )

    # CRITICAL: No duplicates in the portfolio
    assert len(current_ea_ids) == len(set(current_ea_ids)), (
        f"DUPLICATE PLAYERS DETECTED in portfolio after concurrent removes. "
        f"ea_ids={current_ea_ids}. "
        "This is the known D-08 concurrent-remove duplicate bug."
    )


@pytest.mark.asyncio
async def test_concurrent_remove_three_players(client, real_ea_ids):
    """Fire 3 concurrent DELETE /portfolio/{ea_id} — higher chance of triggering races (D-08 extended).
    """
    assert len(real_ea_ids) >= 5, "Need at least 5 active players in DB"

    confirmed = await _confirm_ea_ids(client, real_ea_ids[:5])
    assert confirmed >= 4, f"Expected at least 4 confirmed, got {confirmed}"

    # Pick 3 players to remove concurrently
    remove_ids = real_ea_ids[:3]
    budget = 5_000_000

    async def _delete(ea_id: int) -> dict:
        r = await client.delete(f"/api/v1/portfolio/{ea_id}", params={"budget": budget})
        return {"ea_id": ea_id, "status_code": r.status_code}

    results = await asyncio.gather(*[_delete(ea_id) for ea_id in remove_ids])

    # All 3 should return 200
    for result in results:
        assert result["status_code"] == 200, (
            f"DELETE ea_id={result['ea_id']} failed with status {result['status_code']}"
        )

    # Portfolio must have no duplicates
    current_ea_ids = await _get_confirmed_ea_ids(client)
    assert len(current_ea_ids) == len(set(current_ea_ids)), (
        f"DUPLICATE PLAYERS DETECTED after 3 concurrent removes. "
        f"ea_ids={current_ea_ids}"
    )

    # All 3 removed players must be gone
    for ea_id in remove_ids:
        assert ea_id not in current_ea_ids, (
            f"Removed ea_id={ea_id} still in portfolio"
        )


@pytest.mark.asyncio
async def test_rapid_pending_action_polling(client, real_ea_ids):
    """Fire 10 concurrent GET /actions/pending — all must return 200, no duplicate actions (D-11).

    Assertions:
    1. All 10 responses return 200.
    2. All non-null actions have the same id (no duplicate actions created).
    3. No response is a 500 or other error.
    """
    assert len(real_ea_ids) >= 1, "Need at least 1 active player in DB"
    await _seed_slot(client, real_ea_ids[0], 50000, 70000)

    async def _get_pending() -> dict:
        r = await client.get("/api/v1/actions/pending")
        return {"status_code": r.status_code, "body": r.json()}

    # Fire 10 concurrent polling calls
    responses = await asyncio.gather(*[_get_pending() for _ in range(10)])

    # All must return 200
    for i, resp in enumerate(responses):
        assert resp["status_code"] == 200, (
            f"Concurrent pending poll {i} returned {resp['status_code']}: {resp['body']}"
        )

    # Collect all action ids from non-null responses
    action_ids = [
        resp["body"]["action"]["id"]
        for resp in responses
        if resp["body"]["action"] is not None
    ]

    if action_ids:
        # All non-null responses must have the same action id
        unique_ids = set(action_ids)
        assert len(unique_ids) == 1, (
            f"Expected all concurrent polls to return the same action id, "
            f"got {len(unique_ids)} distinct ids: {unique_ids}. "
            "This means multiple actions were created for the same slot — a race condition bug."
        )


@pytest.mark.asyncio
async def test_rapid_complete_same_action(client, real_ea_ids):
    """Fire 3 concurrent POST /actions/{id}/complete — lifecycle state must be consistent (D-11).

    The key invariant: after all 3 complete, GET /actions/pending should
    still derive LIST (correct state), and GET /portfolio/status should
    show BOUGHT (not corrupted).
    """
    assert len(real_ea_ids) >= 1, "Need at least 1 active player in DB"
    ea_id = real_ea_ids[0]
    buy_price = 50000
    sell_price = 70000

    await _seed_slot(client, ea_id, buy_price, sell_price)

    # Get the single pending action
    r = await client.get("/api/v1/actions/pending")
    assert r.status_code == 200
    action = r.json()["action"]
    assert action is not None, "Expected BUY action after seeding slot"
    action_id = action["id"]

    # Fire 3 concurrent completes for the same action
    async def _complete() -> int:
        r = await client.post(
            f"/api/v1/actions/{action_id}/complete",
            json={"price": buy_price, "outcome": "bought"},
        )
        return r.status_code

    statuses = await asyncio.gather(*[_complete() for _ in range(3)])

    # At least one must succeed (200 or similar non-5xx)
    non_5xx = [s for s in statuses if s < 500]
    assert len(non_5xx) >= 1, (
        f"All concurrent complete calls returned 5xx: {statuses}. Server crashed."
    )

    # After concurrent completes, GET pending should derive LIST (not corrupted)
    r = await client.get("/api/v1/actions/pending")
    assert r.status_code == 200, f"GET pending failed after concurrent completes: {r.status_code}"
    next_action = r.json()["action"]
    if next_action is not None:
        assert next_action["action_type"] == "LIST", (
            f"Expected LIST after bought, got {next_action['action_type']}. "
            "Concurrent completes may have corrupted lifecycle state."
        )

    # GET /portfolio/status should show BOUGHT (not None or PENDING)
    r = await client.get("/api/v1/portfolio/status")
    assert r.status_code == 200
    status_body = r.json()
    player_entry = next(
        (pl for pl in status_body["players"] if pl["ea_id"] == ea_id), None
    )
    assert player_entry is not None, f"ea_id={ea_id} not found in status after concurrent completes"
    assert player_entry["status"] == "BOUGHT", (
        f"Expected BOUGHT status after bought outcome, got {player_entry['status']}. "
        "Concurrent completes may have corrupted player status."
    )


@pytest.mark.asyncio
async def test_concurrent_slot_seeding(client, real_ea_ids):
    """Fire 5 concurrent POST /portfolio/slots with the same ea_id — no duplicates (D-11).

    ea_id has a UNIQUE constraint in portfolio_slots. Concurrent inserts must
    not create duplicate rows — the server must handle the conflict gracefully
    (upsert or ignore).
    """
    assert len(real_ea_ids) >= 1, "Need at least 1 active player in DB"
    ea_id = real_ea_ids[0]
    buy_price = 50000
    sell_price = 70000

    payload = {
        "slots": [
            {
                "ea_id": ea_id,
                "buy_price": buy_price,
                "sell_price": sell_price,
                "player_name": f"Player {ea_id}",
            }
        ]
    }

    async def _seed() -> int:
        r = await client.post("/api/v1/portfolio/slots", json=payload)
        return r.status_code

    # Fire 5 concurrent seeds
    statuses = await asyncio.gather(*[_seed() for _ in range(5)])

    # None should be 500 (UNIQUE constraint violation unhandled)
    for i, status in enumerate(statuses):
        assert status in (200, 201), (
            f"Concurrent slot seed {i} returned {status}. "
            "Server may not handle UNIQUE constraint gracefully under concurrency."
        )

    # Only 1 row should exist for this ea_id
    confirmed = await _get_confirmed_ea_ids(client)
    count_for_ea_id = confirmed.count(ea_id)
    assert count_for_ea_id == 1, (
        f"Expected 1 row for ea_id={ea_id} after 5 concurrent seeds, "
        f"got {count_for_ea_id}. Duplicate rows inserted."
    )


@pytest.mark.asyncio
async def test_concurrent_direct_trade_records(client, real_ea_ids):
    """Fire 5 concurrent POST /trade-records/direct with same ea_id+outcome (D-11).

    Server-side dedup should ensure only 1 trade record is created. After
    dedup, lifecycle should correctly derive LIST (not confused by duplicates).
    """
    assert len(real_ea_ids) >= 1, "Need at least 1 active player in DB"
    ea_id = real_ea_ids[0]
    buy_price = 50000
    sell_price = 70000

    await _seed_slot(client, ea_id, buy_price, sell_price)

    payload = {"ea_id": ea_id, "price": buy_price, "outcome": "bought"}

    async def _direct() -> dict:
        r = await client.post("/api/v1/trade-records/direct", json=payload)
        return {"status_code": r.status_code, "body": r.json() if r.status_code in (200, 201) else {}}

    # Fire 5 concurrent direct records
    responses = await asyncio.gather(*[_direct() for _ in range(5)])

    # All must return 201 (deduped ones also return 201 with deduplicated=True)
    for i, resp in enumerate(responses):
        assert resp["status_code"] == 201, (
            f"Concurrent direct record {i} returned {resp['status_code']}: {resp['body']}"
        )

    # GET /actions/pending should derive LIST (not be confused by possible duplicates)
    r = await client.get("/api/v1/actions/pending")
    assert r.status_code == 200
    action = r.json()["action"]
    assert action is not None, (
        "Expected LIST action after direct bought records, got null. "
        "Concurrent records may have interfered with lifecycle."
    )
    assert action["action_type"] == "LIST", (
        f"Expected LIST action, got {action['action_type']}. "
        "Lifecycle state may be corrupted by concurrent direct records."
    )


@pytest.mark.asyncio
async def test_remove_during_action_lifecycle(client, real_ea_ids):
    """DELETE a slot while its BUY action is IN_PROGRESS — action should be cancelled (D-10).

    Workflow:
      1. Seed 2 slots (slot1, slot2)
      2. GET /actions/pending -> BUY for slot1 (slot1's action is now IN_PROGRESS)
      3. DELETE /portfolio/{slot1_ea_id} (while action is IN_PROGRESS)
      4. The delete should cancel slot1's pending/in-progress actions
      5. GET /actions/pending -> should return action for slot2 (not the cancelled slot1)
    """
    assert len(real_ea_ids) >= 2, "Need at least 2 active players in DB"

    # Seed 2 slots
    r = await client.post(
        "/api/v1/portfolio/slots",
        json={
            "slots": [
                {
                    "ea_id": real_ea_ids[0],
                    "buy_price": 50000,
                    "sell_price": 70000,
                    "player_name": f"Player {real_ea_ids[0]}",
                },
                {
                    "ea_id": real_ea_ids[1],
                    "buy_price": 50000,
                    "sell_price": 70000,
                    "player_name": f"Player {real_ea_ids[1]}",
                },
            ]
        },
    )
    assert r.status_code == 201

    # GET pending -> BUY for slot1 (claims it as IN_PROGRESS)
    r = await client.get("/api/v1/actions/pending")
    assert r.status_code == 200
    action = r.json()["action"]
    assert action is not None, "Expected BUY action for slot1"
    slot1_ea_id = action["ea_id"]

    # DELETE slot1 while its action is IN_PROGRESS
    r = await client.delete(
        f"/api/v1/portfolio/{slot1_ea_id}",
        params={"budget": 5_000_000},
    )
    assert r.status_code == 200, f"DELETE slot1 failed: {r.status_code} {r.text}"

    # GET pending -> should now return action for slot2, NOT the cancelled slot1
    r = await client.get("/api/v1/actions/pending")
    assert r.status_code == 200
    next_action = r.json()["action"]

    # The next action should be for slot2 (or null if slot2 has no records yet
    # but another BUY is correctly derived)
    assert next_action is None or next_action["ea_id"] != slot1_ea_id, (
        f"Expected action for slot2 (not the deleted slot1 ea_id={slot1_ea_id}), "
        f"got action ea_id={next_action['ea_id'] if next_action else 'null'}. "
        "DELETE did not cancel the IN_PROGRESS action for slot1."
    )

    # Slot1 must not be in the portfolio
    current_ea_ids = await _get_confirmed_ea_ids(client)
    assert slot1_ea_id not in current_ea_ids, (
        f"Slot1 ea_id={slot1_ea_id} still in portfolio after DELETE"
    )
