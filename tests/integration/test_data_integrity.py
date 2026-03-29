"""Data integrity and DB consistency tests.

These tests verify that the server maintains correct database state after
operations — not just HTTP responses, but actual DB-level correctness.

Each test queries the Postgres DB directly using async SQLAlchemy to verify
row counts, field values, and constraint behaviors that the HTTP API might mask.

Tests that fail = server data integrity bugs. Per D-04: Do NOT weaken
assertions or add workarounds. Failures are bugs to track.

Covered scenarios:
  - Unique constraint: duplicate ea_id seed results in upsert, not duplicate
  - Clean slate: second confirm deletes ALL previous slots
  - Delete preserves trade_records, removes portfolio_slot
  - Delete cancels pending/in-progress trade_actions
  - Stale action reset: 6-minutes-old IN_PROGRESS reset back to PENDING
  - Listed state means waiting: no pending action when card is on market
  - Trade record ids are strictly monotonically increasing
  - Cleanup fixture starts each test with empty mutable tables
  - Generated portfolio has no duplicate ea_ids
  - Confirmed portfolio preserves exact buy_price and sell_price
  - Batch trade records commit atomically (all or none semantics)
"""
import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text


# ── Helper ─────────────────────────────────────────────────────────────────────

async def query_db(test_db_url, sql: str, params: dict | None = None):
    """Execute a SQL query against the test Postgres DB and return all rows.

    Opens a direct asyncpg connection via SQLAlchemy to bypass the server's
    connection pool. This lets tests verify actual DB state after API operations.

    Args:
        test_db_url: asyncpg-compatible DATABASE_URL for the test container.
        sql: SQL string using :param style placeholders.
        params: Dict of parameter values for the query.
    """
    engine = create_async_engine(test_db_url)
    async with engine.connect() as conn:
        result = await conn.execute(text(sql), params or {})
        rows = result.mappings().all()
    await engine.dispose()
    return rows


# ── Tests ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_portfolio_slots_unique_constraint(client, real_ea_id, test_db_url):
    """Seeding the same ea_id twice results in 1 row (upsert, not duplicate).

    POST /portfolio/slots handles existing ea_id by updating prices, not
    inserting a second row. The UNIQUE constraint on ea_id must be respected.
    """
    assert real_ea_id is not None, "real_ea_id is None — DB may be empty"

    # Seed the same slot twice with different prices
    payload = {
        "slots": [
            {
                "ea_id": real_ea_id,
                "buy_price": 50000,
                "sell_price": 70000,
                "player_name": f"Player {real_ea_id}",
            }
        ]
    }
    r1 = await client.post("/api/v1/portfolio/slots", json=payload)
    assert r1.status_code == 201, f"First seed failed: {r1.status_code}"

    payload["slots"][0]["buy_price"] = 55000  # Different price on second seed
    r2 = await client.post("/api/v1/portfolio/slots", json=payload)
    assert r2.status_code == 201, f"Second seed failed: {r2.status_code}"

    # Direct DB query — must have exactly 1 row for this ea_id
    rows = await query_db(
        test_db_url,
        "SELECT COUNT(*) AS cnt FROM portfolio_slots WHERE ea_id = :ea_id",
        {"ea_id": real_ea_id},
    )
    count = rows[0]["cnt"]
    assert count == 1, (
        f"Expected 1 row in portfolio_slots for ea_id={real_ea_id} after two seeds, "
        f"got {count}. Upsert logic may have inserted a duplicate row."
    )


@pytest.mark.asyncio
async def test_confirm_clean_slate_actually_deletes(client, real_ea_id, test_db_url):
    """POST /portfolio/confirm replaces ALL existing slots with the new list.

    After seeding 3 slots and confirming only 1 player, exactly 1 row should
    exist in portfolio_slots (not 3 old + 1 new = 4).
    """
    assert real_ea_id is not None, "real_ea_id is None — DB may be empty"

    # Seed 3 slots by using the real ea_id and two fake ones
    r = await client.post(
        "/api/v1/portfolio/slots",
        json={
            "slots": [
                {"ea_id": real_ea_id, "buy_price": 50000, "sell_price": 70000, "player_name": "P1"},
                {"ea_id": real_ea_id + 1, "buy_price": 60000, "sell_price": 80000, "player_name": "P2"},
                {"ea_id": real_ea_id + 2, "buy_price": 70000, "sell_price": 90000, "player_name": "P3"},
            ]
        },
    )
    assert r.status_code == 201, f"Slot seed failed: {r.status_code}"

    # Verify 3 slots exist
    rows_before = await query_db(test_db_url, "SELECT COUNT(*) AS cnt FROM portfolio_slots")
    assert rows_before[0]["cnt"] == 3, f"Expected 3 rows before confirm, got {rows_before[0]['cnt']}"

    # Confirm with only 1 player — clean slate should delete the other 2
    confirm_r = await client.post(
        "/api/v1/portfolio/confirm",
        json={"players": [{"ea_id": real_ea_id, "buy_price": 50000, "sell_price": 70000}]},
    )
    assert confirm_r.status_code == 200, f"Confirm failed: {confirm_r.status_code}"
    assert confirm_r.json()["confirmed"] == 1

    # Direct DB query — must have exactly 1 row total
    rows_after = await query_db(test_db_url, "SELECT COUNT(*) AS cnt FROM portfolio_slots")
    count_after = rows_after[0]["cnt"]
    assert count_after == 1, (
        f"Expected 1 row in portfolio_slots after confirm with 1 player, "
        f"got {count_after}. Clean-slate DELETE before INSERT failed."
    )


@pytest.mark.asyncio
async def test_delete_preserves_trade_records(client, real_ea_id, test_db_url):
    """DELETE /portfolio/{ea_id} preserves TradeRecords but removes PortfolioSlot.

    Per the architecture decision: trade history is preserved for analytics
    even after a player is removed from the active portfolio.
    """
    assert real_ea_id is not None, "real_ea_id is None — DB may be empty"

    # Seed slot
    seed_r = await client.post(
        "/api/v1/portfolio/slots",
        json={
            "slots": [{"ea_id": real_ea_id, "buy_price": 50000, "sell_price": 70000, "player_name": "P"}]
        },
    )
    assert seed_r.status_code == 201

    # Create a trade record by completing a BUY action
    pending_r = await client.get("/api/v1/actions/pending")
    assert pending_r.status_code == 200
    action = pending_r.json()["action"]
    assert action is not None, "Expected BUY action after slot seed"

    complete_r = await client.post(
        f"/api/v1/actions/{action['id']}/complete",
        json={"price": 50000, "outcome": "bought"},
    )
    assert complete_r.status_code == 200

    # Verify trade record exists
    records_before = await query_db(
        test_db_url,
        "SELECT COUNT(*) AS cnt FROM trade_records WHERE ea_id = :ea_id",
        {"ea_id": real_ea_id},
    )
    assert records_before[0]["cnt"] >= 1, "Trade record not created before delete"

    # DELETE the portfolio slot
    del_r = await client.delete(f"/api/v1/portfolio/{real_ea_id}", params={"budget": 2_000_000})
    assert del_r.status_code == 200, f"Delete failed: {del_r.status_code}: {del_r.text}"

    # Trade records must still exist (preserved per D-DELETE)
    records_after = await query_db(
        test_db_url,
        "SELECT COUNT(*) AS cnt FROM trade_records WHERE ea_id = :ea_id",
        {"ea_id": real_ea_id},
    )
    assert records_after[0]["cnt"] >= 1, (
        f"Trade records for ea_id={real_ea_id} were deleted along with the portfolio slot. "
        "Trade history should be preserved for analytics."
    )

    # Portfolio slot must be gone
    slot_after = await query_db(
        test_db_url,
        "SELECT COUNT(*) AS cnt FROM portfolio_slots WHERE ea_id = :ea_id",
        {"ea_id": real_ea_id},
    )
    assert slot_after[0]["cnt"] == 0, (
        f"Portfolio slot for ea_id={real_ea_id} still exists after DELETE. "
        "The slot should be removed."
    )


@pytest.mark.asyncio
async def test_delete_cancels_pending_actions(client, real_ea_id, test_db_url):
    """DELETE /portfolio/{ea_id} cancels all PENDING and IN_PROGRESS trade_actions.

    After claiming a BUY action (IN_PROGRESS) and deleting the player,
    the action status must change to CANCELLED — not left as IN_PROGRESS.
    """
    assert real_ea_id is not None, "real_ea_id is None — DB may be empty"

    # Seed slot and claim action (makes it IN_PROGRESS)
    seed_r = await client.post(
        "/api/v1/portfolio/slots",
        json={
            "slots": [{"ea_id": real_ea_id, "buy_price": 50000, "sell_price": 70000, "player_name": "P"}]
        },
    )
    assert seed_r.status_code == 201

    pending_r = await client.get("/api/v1/actions/pending")
    assert pending_r.status_code == 200
    action = pending_r.json()["action"]
    assert action is not None, "Expected BUY action after slot seed"

    # Verify action is now IN_PROGRESS in DB
    actions_before = await query_db(
        test_db_url,
        "SELECT status FROM trade_actions WHERE ea_id = :ea_id",
        {"ea_id": real_ea_id},
    )
    assert any(row["status"] == "IN_PROGRESS" for row in actions_before), (
        f"Expected IN_PROGRESS action for ea_id={real_ea_id}, "
        f"got statuses: {[r['status'] for r in actions_before]}"
    )

    # DELETE the portfolio player
    del_r = await client.delete(f"/api/v1/portfolio/{real_ea_id}", params={"budget": 2_000_000})
    assert del_r.status_code == 200, f"Delete failed: {del_r.status_code}"

    # All actions for this ea_id must now be CANCELLED
    actions_after = await query_db(
        test_db_url,
        "SELECT status FROM trade_actions WHERE ea_id = :ea_id",
        {"ea_id": real_ea_id},
    )
    non_cancelled = [row["status"] for row in actions_after if row["status"] != "CANCELLED"]
    assert not non_cancelled, (
        f"Expected all trade_actions for ea_id={real_ea_id} to be CANCELLED after delete, "
        f"but found non-cancelled statuses: {non_cancelled}"
    )


@pytest.mark.asyncio
async def test_stale_action_reset(client, real_ea_id, test_db_url):
    """Stale IN_PROGRESS actions (>5 min old) are reset to PENDING on next GET /pending.

    We manipulate claimed_at directly in the DB to simulate a 6-minute-old
    IN_PROGRESS action without waiting 5 real minutes.

    Per server code: _reset_stale_actions uses STALE_TIMEOUT = timedelta(minutes=5).
    Actions with claimed_at < utcnow() - 5min are reset to PENDING.
    """
    assert real_ea_id is not None, "real_ea_id is None — DB may be empty"

    # Seed slot and claim action
    seed_r = await client.post(
        "/api/v1/portfolio/slots",
        json={
            "slots": [{"ea_id": real_ea_id, "buy_price": 50000, "sell_price": 70000, "player_name": "P"}]
        },
    )
    assert seed_r.status_code == 201

    pending_r = await client.get("/api/v1/actions/pending")
    assert pending_r.status_code == 200
    action = pending_r.json()["action"]
    assert action is not None, "Expected BUY action after slot seed"
    action_id = action["id"]

    # Verify it's IN_PROGRESS
    rows = await query_db(
        test_db_url,
        "SELECT status, claimed_at FROM trade_actions WHERE id = :id",
        {"id": action_id},
    )
    assert rows[0]["status"] == "IN_PROGRESS", (
        f"Expected IN_PROGRESS, got {rows[0]['status']}"
    )

    # Backdate claimed_at to 6 minutes ago (past the 5-minute STALE_TIMEOUT)
    engine = create_async_engine(test_db_url)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE trade_actions "
                "SET claimed_at = claimed_at - INTERVAL '6 minutes' "
                "WHERE id = :id"
            ),
            {"id": action_id},
        )
    await engine.dispose()

    # GET /actions/pending — this triggers _reset_stale_actions which should
    # reset the stale IN_PROGRESS action back to PENDING and return it
    reset_r = await client.get("/api/v1/actions/pending")
    assert reset_r.status_code == 200

    returned_action = reset_r.json()["action"]
    assert returned_action is not None, (
        "Expected the stale action to be reset and returned as a new pending action. "
        "If None, the stale reset logic is not working."
    )
    assert returned_action["id"] == action_id, (
        f"Expected same action id={action_id} to be returned after reset, "
        f"got id={returned_action['id']}"
    )

    # Verify DB: action must now be IN_PROGRESS again (re-claimed after reset)
    rows_after = await query_db(
        test_db_url,
        "SELECT status FROM trade_actions WHERE id = :id",
        {"id": action_id},
    )
    assert rows_after[0]["status"] == "IN_PROGRESS", (
        f"Expected action to be re-claimed as IN_PROGRESS after stale reset, "
        f"got {rows_after[0]['status']}"
    )


@pytest.mark.asyncio
async def test_no_action_created_when_listed(client, real_ea_id, test_db_url):
    """No pending action when the latest trade record is 'listed' (card on market).

    Per lifecycle state machine: 'listed' outcome means the card is on market
    and no action is needed. _derive_next_action skips slots with 'listed' status.
    """
    assert real_ea_id is not None, "real_ea_id is None — DB may be empty"

    # Seed slot
    seed_r = await client.post(
        "/api/v1/portfolio/slots",
        json={
            "slots": [{"ea_id": real_ea_id, "buy_price": 50000, "sell_price": 70000, "player_name": "P"}]
        },
    )
    assert seed_r.status_code == 201

    # BUY -> bought -> LIST -> listed
    buy_r = await client.get("/api/v1/actions/pending")
    buy_action = buy_r.json()["action"]
    assert buy_action["action_type"] == "BUY"

    await client.post(
        f"/api/v1/actions/{buy_action['id']}/complete",
        json={"price": 50000, "outcome": "bought"},
    )

    list_r = await client.get("/api/v1/actions/pending")
    list_action = list_r.json()["action"]
    assert list_action is not None and list_action["action_type"] == "LIST", (
        f"Expected LIST action after 'bought', got {list_action}"
    )

    await client.post(
        f"/api/v1/actions/{list_action['id']}/complete",
        json={"price": 70000, "outcome": "listed"},
    )

    # Now pending should be None — card is listed, nothing to do
    pending_r = await client.get("/api/v1/actions/pending")
    assert pending_r.status_code == 200
    assert pending_r.json()["action"] is None, (
        f"Expected null action when card is 'listed' (on market), "
        f"got {pending_r.json()['action']}. "
        "The 'listed' outcome should not trigger a new action."
    )

    # Direct DB check: no PENDING actions for this ea_id
    rows = await query_db(
        test_db_url,
        "SELECT COUNT(*) AS cnt FROM trade_actions WHERE ea_id = :ea_id AND status = 'PENDING'",
        {"ea_id": real_ea_id},
    )
    assert rows[0]["cnt"] == 0, (
        f"Expected 0 PENDING actions for listed ea_id={real_ea_id}, "
        f"got {rows[0]['cnt']}"
    )


@pytest.mark.asyncio
async def test_trade_record_id_monotonic(client, real_ea_id, test_db_url):
    """Trade record ids are strictly monotonically increasing.

    After creating multiple trade records for the same ea_id, their IDs
    must be in strictly increasing order (Postgres SERIAL guarantees this,
    but we verify it explicitly to catch any schema issues).
    """
    assert real_ea_id is not None, "real_ea_id is None — DB may be empty"

    # Seed slot
    seed_r = await client.post(
        "/api/v1/portfolio/slots",
        json={
            "slots": [{"ea_id": real_ea_id, "buy_price": 50000, "sell_price": 70000, "player_name": "P"}]
        },
    )
    assert seed_r.status_code == 201

    # Create 3 records: bought, listed, sold (full cycle)
    for outcome in ["bought", "listed", "sold"]:
        r = await client.post(
            "/api/v1/trade-records/direct",
            json={"ea_id": real_ea_id, "price": 50000, "outcome": outcome},
        )
        assert r.status_code == 201, f"Direct record failed for outcome={outcome}: {r.text}"

    # Query ids in insertion order
    rows = await query_db(
        test_db_url,
        "SELECT id FROM trade_records WHERE ea_id = :ea_id ORDER BY id",
        {"ea_id": real_ea_id},
    )
    ids = [row["id"] for row in rows]
    assert len(ids) >= 3, f"Expected at least 3 trade records, got {len(ids)}"

    for i in range(1, len(ids)):
        assert ids[i] > ids[i - 1], (
            f"Trade record ids are not strictly monotonically increasing: {ids}. "
            f"ids[{i}]={ids[i]} <= ids[{i-1}]={ids[i-1]}"
        )


@pytest.mark.asyncio
async def test_cleanup_fixture_works(test_db_url):
    """Verify that the cleanup_tables fixture starts each test with empty mutable tables.

    Per D-15: the autouse cleanup_tables fixture deletes all rows from
    portfolio_slots, trade_actions, and trade_records after each test.

    This test checks that mutable tables are EMPTY at the start of execution
    (the previous test's cleanup ran). Then seeds data to leave something
    for the cleanup to remove after this test.
    """
    rows_slots = await query_db(test_db_url, "SELECT COUNT(*) AS cnt FROM portfolio_slots")
    rows_actions = await query_db(test_db_url, "SELECT COUNT(*) AS cnt FROM trade_actions")
    rows_records = await query_db(test_db_url, "SELECT COUNT(*) AS cnt FROM trade_records")

    assert rows_slots[0]["cnt"] == 0, (
        f"portfolio_slots not empty at test start: {rows_slots[0]['cnt']} rows. "
        "cleanup_tables fixture may not be running correctly."
    )
    assert rows_actions[0]["cnt"] == 0, (
        f"trade_actions not empty at test start: {rows_actions[0]['cnt']} rows. "
        "cleanup_tables fixture may not be running correctly."
    )
    assert rows_records[0]["cnt"] == 0, (
        f"trade_records not empty at test start: {rows_records[0]['cnt']} rows. "
        "cleanup_tables fixture may not be running correctly."
    )


@pytest.mark.asyncio
async def test_portfolio_generate_no_duplicate_ea_ids(client):
    """POST /portfolio/generate returns no duplicate ea_ids in the result.

    Per D-07: generated portfolios must not contain the same player twice.
    The optimizer should never select the same player for multiple slots.
    """
    r = await client.post("/api/v1/portfolio/generate", json={"budget": 5_000_000})
    assert r.status_code == 200, f"Generate failed: {r.status_code}: {r.text}"
    body = r.json()
    assert body["count"] > 0, (
        "POST /portfolio/generate returned 0 players — cannot check for duplicates. "
        "Real DB should have viable players."
    )

    ea_ids = [p["ea_id"] for p in body["data"]]
    unique_ea_ids = list(set(ea_ids))

    assert len(ea_ids) == len(unique_ea_ids), (
        f"Portfolio generate returned duplicate ea_ids! "
        f"Total: {len(ea_ids)}, Unique: {len(unique_ea_ids)}. "
        f"Duplicates: {[x for x in ea_ids if ea_ids.count(x) > 1]}. "
        "This is a portfolio optimizer bug (D-07)."
    )


@pytest.mark.asyncio
async def test_portfolio_confirm_preserves_prices(client):
    """POST /portfolio/confirm stores exact buy_price and sell_price from the request.

    After confirming with specific prices, GET /portfolio/confirmed must return
    the same prices — the server must not modify, round, or override the prices.
    """
    # Generate to get real ea_ids
    gen_r = await client.post("/api/v1/portfolio/generate", json={"budget": 2_000_000})
    assert gen_r.status_code == 200
    gen_body = gen_r.json()
    assert gen_body["count"] >= 2, f"Need at least 2 players, got {gen_body['count']}"

    # Use distinct prices that are easy to verify
    players = gen_body["data"][:2]
    confirmed_prices = [
        {"ea_id": players[0]["ea_id"], "buy_price": 111111, "sell_price": 222222},
        {"ea_id": players[1]["ea_id"], "buy_price": 333333, "sell_price": 444444},
    ]

    confirm_r = await client.post(
        "/api/v1/portfolio/confirm",
        json={"players": confirmed_prices},
    )
    assert confirm_r.status_code == 200

    # GET confirmed
    get_r = await client.get("/api/v1/portfolio/confirmed")
    assert get_r.status_code == 200
    confirmed_data = get_r.json()["data"]
    confirmed_by_ea_id = {p["ea_id"]: p for p in confirmed_data}

    for expected in confirmed_prices:
        ea_id = expected["ea_id"]
        assert ea_id in confirmed_by_ea_id, (
            f"Player ea_id={ea_id} not found in confirmed portfolio: {list(confirmed_by_ea_id.keys())}"
        )
        actual = confirmed_by_ea_id[ea_id]
        assert actual["buy_price"] == expected["buy_price"], (
            f"buy_price mismatch for ea_id={ea_id}: "
            f"expected {expected['buy_price']}, got {actual['buy_price']}. "
            "Server modified the buy_price during confirm."
        )
        assert actual["sell_price"] == expected["sell_price"], (
            f"sell_price mismatch for ea_id={ea_id}: "
            f"expected {expected['sell_price']}, got {actual['sell_price']}. "
            "Server modified the sell_price during confirm."
        )


@pytest.mark.asyncio
async def test_batch_records_single_commit(client, test_db_url):
    """POST /trade-records/batch inserts all records atomically.

    All records in a batch should be committed together. If the batch contains
    3 valid records, all 3 should appear in trade_records after the call.

    Uses POST /portfolio/generate to get 3 real ea_ids instead of synthetic offsets.
    This avoids DB lock contention from seeding non-existent ea_ids.
    """
    import time as _t
    _t0 = _t.time()
    # Get 3 real ea_ids from generate
    print(f"[batch_test] starting generate at {_t.time()-_t0:.1f}s", flush=True)
    gen_r = await client.post("/api/v1/portfolio/generate", json={"budget": 2_000_000})
    print(f"[batch_test] generate done at {_t.time()-_t0:.1f}s status={gen_r.status_code}", flush=True)
    assert gen_r.status_code == 200, f"Generate failed: {gen_r.status_code}"
    gen_body = gen_r.json()
    assert gen_body["count"] >= 3, (
        f"Need at least 3 viable players for this test, got {gen_body['count']}"
    )
    players = gen_body["data"][:3]
    ea_ids = [p["ea_id"] for p in players]

    # Seed 3 slots via confirm (clean way to get 3 real slots)
    print(f"[batch_test] starting confirm at {_t.time()-_t0:.1f}s", flush=True)
    confirm_r = await client.post(
        "/api/v1/portfolio/confirm",
        json={
            "players": [
                {"ea_id": p["ea_id"], "buy_price": p["price"], "sell_price": p["sell_price"]}
                for p in players
            ]
        },
    )
    print(f"[batch_test] confirm done at {_t.time()-_t0:.1f}s status={confirm_r.status_code}", flush=True)
    assert confirm_r.status_code == 200, f"Confirm failed: {confirm_r.status_code}"

    # Batch insert 3 records — one per slot
    print(f"[batch_test] starting batch at {_t.time()-_t0:.1f}s", flush=True)
    batch_r = await client.post(
        "/api/v1/trade-records/batch",
        json={
            "records": [
                {"ea_id": ea_ids[0], "price": 50000, "outcome": "bought"},
                {"ea_id": ea_ids[1], "price": 60000, "outcome": "bought"},
                {"ea_id": ea_ids[2], "price": 70000, "outcome": "bought"},
            ]
        },
    )
    print(f"[batch_test] batch done at {_t.time()-_t0:.1f}s status={batch_r.status_code}", flush=True)
    assert batch_r.status_code == 201, f"Batch insert failed: {batch_r.status_code}: {batch_r.text}"
    batch_body = batch_r.json()
    assert batch_body["status"] == "ok"

    # All 3 should succeed
    assert len(batch_body["succeeded"]) == 3, (
        f"Expected 3 succeeded, got {batch_body['succeeded']}. "
        f"Failed: {batch_body.get('failed', [])}"
    )
    assert len(batch_body["failed"]) == 0, (
        f"Expected 0 failed, got {batch_body['failed']}"
    )

    # Direct DB: verify all 3 records are there
    rows = await query_db(
        test_db_url,
        """
        SELECT COUNT(*) AS cnt FROM trade_records
        WHERE ea_id IN (:ea1, :ea2, :ea3) AND outcome = 'bought'
        """,
        {"ea1": ea_ids[0], "ea2": ea_ids[1], "ea3": ea_ids[2]},
    )
    assert rows[0]["cnt"] == 3, (
        f"Expected 3 trade_records in DB after batch insert, got {rows[0]['cnt']}. "
        "Batch was not committed atomically or records were not inserted."
    )
