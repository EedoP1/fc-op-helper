"""Integration tests for POST /api/v1/portfolio/rebalance.

Tests that the rebalance endpoint correctly:
 - Keeps existing confirmed portfolio players as "leftovers"
 - Subtracts kept players' buy_price from the total budget
 - Fills remaining budget with new picks (excluding kept ea_ids)
 - Drops least-efficient kept players when budget is too small
 - Returns kept, new, and dropped arrays with correct budget math
 - Handles empty portfolio (all new, like generate)

All tests use the real server started by conftest.live_server and a copy
of the production DB. No mocks. No fake ea_ids.

Tests that fail = server bugs. Do NOT weaken assertions.
"""
import pytest


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _confirm_ea_ids(client, ea_ids: list[int], buy_price: int = 50000, sell_price: int = 70000) -> None:
    """Seed portfolio via POST /portfolio/confirm with fixed prices."""
    confirm_payload = {
        "players": [
            {"ea_id": eid, "buy_price": buy_price, "sell_price": sell_price}
            for eid in ea_ids
        ]
    }
    r = await client.post("/api/v1/portfolio/confirm", json=confirm_payload)
    assert r.status_code == 200, f"confirm failed: {r.status_code} {r.text}"


# ── Tests ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rebalance_normal_flow(client, real_ea_ids):
    """POST /portfolio/rebalance with budget > sum(existing) keeps players and fills new.

    Verifies:
    - kept array contains ea_ids of existing portfolio slots
    - new array does not overlap with kept ea_ids
    - budget_used = sum(kept prices) + sum(new prices)
    - budget_remaining = budget - budget_used
    - Response has kept, new, dropped, budget, budget_used, budget_remaining keys
    """
    assert len(real_ea_ids) >= 2, "Need at least 2 active players in DB"
    buy_price = 50000

    # Confirm 2 players into portfolio
    await _confirm_ea_ids(client, real_ea_ids[:2])
    confirmed_ea_ids = set(real_ea_ids[:2])
    confirmed_cost = buy_price * 2

    # Rebalance with a large budget (much more than confirmed cost)
    large_budget = max(confirmed_cost * 5, 2_000_000)
    r = await client.post("/api/v1/portfolio/rebalance", json={"budget": large_budget})
    assert r.status_code == 200, f"rebalance failed: {r.status_code} {r.text}"
    body = r.json()

    # Response must have all required keys
    assert "kept" in body, f"Response missing 'kept' key: {body}"
    assert "new" in body, f"Response missing 'new' key: {body}"
    assert "dropped" in body, f"Response missing 'dropped' key: {body}"
    assert "budget" in body, f"Response missing 'budget' key: {body}"
    assert "budget_used" in body, f"Response missing 'budget_used' key: {body}"
    assert "budget_remaining" in body, f"Response missing 'budget_remaining' key: {body}"

    # Budget echoed back correctly
    assert body["budget"] == large_budget, (
        f"Expected budget={large_budget}, got {body['budget']}"
    )

    # Kept players must include the confirmed ea_ids
    kept_ea_ids = {p["ea_id"] for p in body["kept"]}
    assert kept_ea_ids == confirmed_ea_ids, (
        f"Expected kept ea_ids={confirmed_ea_ids}, got {kept_ea_ids}"
    )

    # No dropped players (budget was large enough)
    assert body["dropped"] == [], (
        f"Expected empty dropped array with large budget, got {body['dropped']}"
    )

    # Budget math: budget_used = kept cost + new cost
    kept_cost = sum(p["price"] for p in body["kept"])
    new_cost = sum(p["price"] for p in body["new"])
    assert body["budget_used"] == kept_cost + new_cost, (
        f"budget_used={body['budget_used']} != kept_cost={kept_cost} + new_cost={new_cost}"
    )

    # budget_remaining correct
    assert body["budget_remaining"] == large_budget - body["budget_used"], (
        f"budget_remaining mismatch: {body['budget_remaining']} != "
        f"{large_budget} - {body['budget_used']}"
    )


@pytest.mark.asyncio
async def test_rebalance_no_duplicates(client, real_ea_ids):
    """Kept players' ea_ids do not appear in the new candidates (no duplicates).

    After seeding 2 players and rebalancing, ea_ids in 'new' must not
    overlap with ea_ids in 'kept'.
    """
    assert len(real_ea_ids) >= 2, "Need at least 2 active players in DB"
    await _confirm_ea_ids(client, real_ea_ids[:2])

    r = await client.post("/api/v1/portfolio/rebalance", json={"budget": 2_000_000})
    assert r.status_code == 200, f"rebalance failed: {r.status_code} {r.text}"
    body = r.json()

    kept_ea_ids = {p["ea_id"] for p in body["kept"]}
    new_ea_ids = {p["ea_id"] for p in body["new"]}

    overlap = kept_ea_ids & new_ea_ids
    assert not overlap, (
        f"ea_ids appeared in both 'kept' and 'new': {overlap}. "
        "Rebalance must exclude existing portfolio players from new candidates."
    )


@pytest.mark.asyncio
async def test_rebalance_budget_accounting(client, real_ea_ids):
    """budget_used = sum(kept prices) + sum(new prices), budget_remaining = budget - budget_used."""
    assert len(real_ea_ids) >= 2, "Need at least 2 active players in DB"
    await _confirm_ea_ids(client, real_ea_ids[:2])

    budget = 2_000_000
    r = await client.post("/api/v1/portfolio/rebalance", json={"budget": budget})
    assert r.status_code == 200, f"rebalance failed: {r.status_code} {r.text}"
    body = r.json()

    kept_cost = sum(p["price"] for p in body["kept"])
    new_cost = sum(p["price"] for p in body["new"])
    dropped_cost = sum(p["price"] for p in body["dropped"])

    # budget_used accounts for kept + new (not dropped — those were removed)
    assert body["budget_used"] == kept_cost + new_cost, (
        f"budget_used={body['budget_used']} should equal kept_cost={kept_cost} + "
        f"new_cost={new_cost} = {kept_cost + new_cost}"
    )

    assert body["budget_remaining"] == budget - body["budget_used"], (
        f"budget_remaining={body['budget_remaining']} should equal "
        f"budget={budget} - budget_used={body['budget_used']} = {budget - body['budget_used']}"
    )

    # Sanity: budget_used <= budget
    assert body["budget_used"] <= budget, (
        f"budget_used={body['budget_used']} exceeds budget={budget}"
    )


@pytest.mark.asyncio
async def test_rebalance_budget_too_small_drops_expensive(client, real_ea_ids):
    """POST /portfolio/rebalance with budget < existing portfolio drops least efficient players.

    Seeds 2 players, then rebalances with a tiny budget (less than sum of both).
    Expects some to be in 'dropped', the rest in 'kept', budget math still correct.
    """
    assert len(real_ea_ids) >= 2, "Need at least 2 active players in DB"
    buy_price = 50000
    await _confirm_ea_ids(client, real_ea_ids[:2], buy_price=buy_price)

    # Budget smaller than total cost of both players but larger than one
    total_cost = buy_price * 2
    tiny_budget = buy_price + 1

    r = await client.post("/api/v1/portfolio/rebalance", json={"budget": tiny_budget})
    assert r.status_code == 200, f"rebalance failed: {r.status_code} {r.text}"
    body = r.json()

    # At least one player must be dropped
    assert len(body["dropped"]) >= 1, (
        f"Expected at least 1 dropped player when budget={tiny_budget} < "
        f"total_cost={total_cost}, but got dropped={body['dropped']}"
    )

    # Kept players must fit within budget
    kept_cost = sum(p["price"] for p in body["kept"])
    assert kept_cost <= tiny_budget, (
        f"Kept players cost={kept_cost} exceeds budget={tiny_budget}. "
        "Drop logic failed to trim portfolio to fit budget."
    )

    # No ea_id in both kept and dropped
    kept_ea_ids = {p["ea_id"] for p in body["kept"]}
    dropped_ea_ids = {p["ea_id"] for p in body["dropped"]}
    assert not (kept_ea_ids & dropped_ea_ids), (
        f"ea_ids in both kept and dropped: {kept_ea_ids & dropped_ea_ids}"
    )

    # Budget math still correct
    new_cost = sum(p["price"] for p in body["new"])
    assert body["budget_used"] == kept_cost + new_cost, (
        f"budget_used={body['budget_used']} != kept_cost={kept_cost} + new_cost={new_cost}"
    )


@pytest.mark.asyncio
async def test_rebalance_empty_portfolio_all_new(client):
    """POST /portfolio/rebalance with no existing portfolio behaves like generate (all new).

    When there are no confirmed portfolio slots, rebalance should return:
    - kept = []
    - dropped = []
    - new = same results as generate (non-empty if viable players exist)
    """
    # No confirm — portfolio is empty (cleanup_tables fixture handles this)
    budget = 2_000_000
    r = await client.post("/api/v1/portfolio/rebalance", json={"budget": budget})
    assert r.status_code == 200, f"rebalance failed: {r.status_code} {r.text}"
    body = r.json()

    assert body["kept"] == [], (
        f"Expected kept=[] for empty portfolio, got {body['kept']}"
    )
    assert body["dropped"] == [], (
        f"Expected dropped=[] for empty portfolio, got {body['dropped']}"
    )

    # new should be non-empty if there are viable players in the DB
    # (if generate returns results, rebalance should too)
    gen_r = await client.post("/api/v1/portfolio/generate", json={"budget": budget})
    gen_body = gen_r.json()
    if gen_body.get("count", 0) > 0:
        assert len(body["new"]) > 0, (
            f"Expected non-empty 'new' when generate returns {gen_body['count']} players, "
            f"but rebalance returned new={body['new']}"
        )


@pytest.mark.asyncio
async def test_rebalance_response_player_fields(client, real_ea_ids):
    """Response kept and new arrays have required player fields.

    Each player dict in kept/new/dropped must have: ea_id, name, rating,
    position, price, sell_price, margin_pct.
    """
    assert len(real_ea_ids) >= 1, "Need at least 1 active player in DB"
    await _confirm_ea_ids(client, real_ea_ids[:1])

    r = await client.post("/api/v1/portfolio/rebalance", json={"budget": 2_000_000})
    assert r.status_code == 200, f"rebalance failed: {r.status_code} {r.text}"
    body = r.json()

    required_fields = {"ea_id", "name", "rating", "position", "price", "sell_price", "margin_pct"}

    for array_name in ("kept", "new"):
        for i, player in enumerate(body[array_name]):
            missing = required_fields - player.keys()
            assert not missing, (
                f"Player {i} in '{array_name}' is missing fields: {missing}. "
                f"Player dict: {player}"
            )
