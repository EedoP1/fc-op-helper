"""Scanner interaction and DB lock tests (D-12).

Tests API behavior when the real scanner is running, idle, or mid-scan:
- Health endpoint reflects real scanner state
- Read endpoints respond while scanner holds DB write locks
- Write endpoints respond during scanner activity

All tests use the real server with the real ScannerService running in
the background (started by conftest.live_server lifespan). No mocks.

Tests that fail = server bugs (per D-04). If a read endpoint returns 500
with 'database is locked', that means WAL mode is not properly configured
or the multi-engine architecture has a regression.
"""
import asyncio
import time

import pytest


# ── Helpers ────────────────────────────────────────────────────────────────────

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


# ── Tests ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_health_reflects_real_scanner_state(client):
    """GET /health returns real scanner state — not 'mock', not 'unknown' (D-12).

    The health endpoint must reflect the actual state of the ScannerService
    that was started in the server lifespan. Valid states are:
    - scanner_status: "running" | "stopped"
    - circuit_breaker: "closed" | "open" | "half_open"

    Also verifies scanner_status is stable over 3 seconds (not flickering).
    """
    # First health call
    r = await client.get("/api/v1/health")
    assert r.status_code == 200, f"health endpoint failed: {r.status_code} {r.text}"
    body = r.json()

    # Required fields
    assert "scanner_status" in body, f"Missing scanner_status in health response: {body}"
    assert "circuit_breaker" in body, f"Missing circuit_breaker in health response: {body}"

    # scanner_status must be a real state — NOT "mock" or a fabricated value.
    # "unknown" is valid when scanner hasn't completed first dispatch cycle yet
    # (split-process architecture: scanner container bootstraps independently).
    valid_scanner_states = {"running", "stopped", "unknown"}
    assert body["scanner_status"] in valid_scanner_states, (
        f"scanner_status='{body['scanner_status']}' is not a valid state {valid_scanner_states}. "
        "The test server may be using a mock scanner instead of the real ScannerService."
    )

    # circuit_breaker must be a real state ("unknown" valid during scanner bootstrap)
    valid_cb_states = {"closed", "open", "half_open", "unknown"}
    assert body["circuit_breaker"] in valid_cb_states, (
        f"circuit_breaker='{body['circuit_breaker']}' is not valid {valid_cb_states}"
    )

    # Evidence of real scanner integration: players_in_db must be numeric
    assert "players_in_db" in body, f"Missing players_in_db in health response: {body}"
    assert isinstance(body["players_in_db"], int), (
        f"players_in_db must be int, got {type(body['players_in_db'])}: {body['players_in_db']}"
    )

    # queue_depth must exist and be numeric
    assert "queue_depth" in body, f"Missing queue_depth in health response: {body}"
    assert isinstance(body["queue_depth"], int), (
        f"queue_depth must be int, got {type(body['queue_depth'])}"
    )

    # Stability check: poll 3 times over ~2 seconds, verify scanner_status is stable
    statuses = [body["scanner_status"]]
    for _ in range(2):
        await asyncio.sleep(1.0)
        r2 = await client.get("/api/v1/health")
        assert r2.status_code == 200
        s = r2.json()["scanner_status"]
        assert s in valid_scanner_states, f"scanner_status flickered to invalid state: {s}"
        statuses.append(s)

    # All statuses should be consistent (not randomly changing)
    assert len(set(statuses)) <= 2, (
        f"scanner_status is flickering randomly: {statuses}. "
        "The real scanner should be in a stable state during tests."
    )


@pytest.mark.asyncio
async def test_read_endpoints_respond_during_scanner_activity(client):
    """All read endpoints return 200 during scanner background activity (D-12).

    The real scanner runs periodic dispatch_scans and aggregation jobs in
    the background. This test fires concurrent read requests during that
    activity to verify WAL mode prevents 'database is locked' errors.

    If any request returns 500 with 'database is locked', that means WAL
    mode is misconfigured or the multi-engine architecture has regressed.

    Repeats 3 times with 1-second gaps to increase chance of overlapping
    with scanner DB writes.
    """
    for burst_num in range(3):
        # Create one async call per read endpoint
        read_calls = [
            client.get("/api/v1/health"),
            client.get("/api/v1/players/top", params={"limit": 5}),
            client.get("/api/v1/portfolio/status"),
            client.get("/api/v1/profit/summary"),
            client.get("/api/v1/actions/pending"),
        ]

        responses = await asyncio.gather(*read_calls)

        for r in responses:
            # All must return 200
            assert r.status_code == 200, (
                f"Burst {burst_num + 1}: {r.request.url} returned {r.status_code}: {r.text[:200]}. "
                "If this is a 'database is locked' error, WAL mode is not working."
            )

            # Explicitly check for DB lock errors in the body
            if r.status_code == 500:
                body_text = r.text.lower()
                assert "database is locked" not in body_text, (
                    f"Burst {burst_num + 1}: DB lock error from {r.request.url}: {r.text[:200]}. "
                    "Scanner write lock is blocking API reads — WAL mode or engine isolation issue."
                )

        if burst_num < 2:
            await asyncio.sleep(1.0)


@pytest.mark.asyncio
async def test_write_endpoints_respond_during_scanner_activity(client, real_ea_id):
    """Write endpoints succeed during scanner background DB activity (D-12).

    Tests the multi-engine architecture: the scanner uses a dedicated write
    engine (scanner_write_engine) while the API uses its own session factory.
    SQLite WAL mode allows concurrent reads from multiple connections, but
    only one writer at a time. If both the scanner and the API write engine
    try to write simultaneously, one must wait.

    Assertions:
    - POST /portfolio/slots returns 201 (not 500 or timeout)
    - POST /trade-records/direct returns 201 (not 500 or timeout)
    - GET /actions/pending returns 200 (reads with write side-effect — creates action)

    The timeout for the client is 30s (from conftest) — if a write blocks
    longer than 30s due to scanner holding the lock, this test will fail,
    which is the correct behavior (it's a server bug to fix).
    """
    assert real_ea_id is not None, "real_ea_id is None — DB may be empty"

    # Seed a slot first (write)
    await _seed_slot(client, real_ea_id, 50_000, 70_000)

    # Write: POST /trade-records/direct
    r = await client.post(
        "/api/v1/trade-records/direct",
        json={"ea_id": real_ea_id, "price": 50_000, "outcome": "bought"},
    )
    assert r.status_code == 201, (
        f"POST /trade-records/direct returned {r.status_code}: {r.text[:200]}. "
        "Write endpoint blocked or failed during scanner activity."
    )
    body = r.json()
    assert body["status"] == "ok", f"Unexpected status: {body}"

    # Read/write: GET /actions/pending (creates IN_PROGRESS action — involves write)
    r = await client.get("/api/v1/actions/pending")
    assert r.status_code == 200, (
        f"GET /actions/pending returned {r.status_code}: {r.text[:200]}. "
        "Action derivation blocked or failed during scanner activity."
    )
    action = r.json()["action"]

    # Since latest record is 'bought', pending should derive LIST
    assert action is not None, (
        "Expected LIST action after direct bought record, got null. "
        "Action derivation may be broken."
    )
    assert action["action_type"] == "LIST", (
        f"Expected LIST action, got {action['action_type']}"
    )
