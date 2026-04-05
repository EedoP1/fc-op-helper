"""Integration tests for the ?since= time filter on /profit/summary.

Validates that the since query parameter is accepted, filters correctly,
and rejects invalid values with 422.
"""
import pytest


# -- Profit summary since param ------------------------------------------------

@pytest.mark.asyncio
async def test_profit_summary_since_param_accepted(client):
    """GET /profit/summary?since=24h returns 200 with correct response shape."""
    r = await client.get("/api/v1/profit/summary", params={"since": "24h"})
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    body = r.json()
    assert "totals" in body, f"Missing 'totals' key in response: {body}"
    assert "per_player" in body, f"Missing 'per_player' key in response: {body}"

    totals = body["totals"]
    for key in ("total_spent", "total_earned", "realized_profit",
                "unrealized_pnl", "total_profit", "buy_count",
                "sell_count", "held_count"):
        assert key in totals, f"Missing '{key}' in totals: {totals}"

    # Verify per_player entries have the new profit rate fields
    for entry in body["per_player"]:
        for key in ("profit_per_hour", "active_hours", "first_buy_at", "last_sell_at"):
            assert key in entry, f"Missing '{key}' in per_player entry: {entry}"


@pytest.mark.asyncio
async def test_profit_summary_since_all(client):
    """GET /profit/summary?since=all returns same shape as no param."""
    r_all = await client.get("/api/v1/profit/summary", params={"since": "all"})
    r_none = await client.get("/api/v1/profit/summary")
    assert r_all.status_code == 200, f"Expected 200, got {r_all.status_code}: {r_all.text}"
    assert r_none.status_code == 200, f"Expected 200, got {r_none.status_code}: {r_none.text}"

    body_all = r_all.json()
    body_none = r_none.json()

    # Both should return identical totals (since=all means no filter)
    assert body_all["totals"] == body_none["totals"], (
        f"since=all totals differ from no-param totals:\n"
        f"  all:  {body_all['totals']}\n"
        f"  none: {body_none['totals']}"
    )


@pytest.mark.asyncio
async def test_profit_summary_invalid_since(client):
    """GET /profit/summary?since=bogus returns 422."""
    r = await client.get("/api/v1/profit/summary", params={"since": "bogus"})
    assert r.status_code == 422, f"Expected 422, got {r.status_code}: {r.text}"


# -- Portfolio status since param ----------------------------------------------

@pytest.mark.asyncio
async def test_portfolio_status_since_param_accepted(client):
    """GET /portfolio/status?since=7d returns 200 with correct response shape."""
    r = await client.get("/api/v1/portfolio/status", params={"since": "7d"})
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    body = r.json()
    assert "summary" in body, f"Missing 'summary' key in response: {body}"
    assert "players" in body, f"Missing 'players' key in response: {body}"

    summary = body["summary"]
    for key in ("realized_profit", "unrealized_pnl", "trade_counts"):
        assert key in summary, f"Missing '{key}' in summary: {summary}"

    counts = summary["trade_counts"]
    for key in ("bought", "sold", "expired"):
        assert key in counts, f"Missing '{key}' in trade_counts: {counts}"


@pytest.mark.asyncio
async def test_portfolio_status_invalid_since(client):
    """GET /portfolio/status?since=bogus returns 422."""
    r = await client.get("/api/v1/portfolio/status", params={"since": "bogus"})
    assert r.status_code == 422, f"Expected 422, got {r.status_code}: {r.text}"
