"""Integration tests for /api/v1/portfolio/stale endpoint."""
import pytest
import httpx


@pytest.mark.asyncio
async def test_stale_endpoint_returns_200(client):
    """GET /api/v1/portfolio/stale returns 200 with both views."""
    resp = await client.get("/api/v1/portfolio/stale")
    assert resp.status_code == 200
    data = resp.json()
    assert "longest_unsold" in data
    assert "avg_sale_time" in data
    assert isinstance(data["longest_unsold"], list)
    assert isinstance(data["avg_sale_time"], list)


@pytest.mark.asyncio
async def test_stale_endpoint_with_since(client):
    """GET /api/v1/portfolio/stale?since=7d returns 200."""
    resp = await client.get("/api/v1/portfolio/stale?since=7d")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_stale_endpoint_invalid_since(client):
    """GET /api/v1/portfolio/stale?since=bogus returns 422."""
    resp = await client.get("/api/v1/portfolio/stale?since=bogus")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_stale_longest_unsold_shape(client, seed_real_portfolio_slot):
    """Longest unsold entries have the expected fields."""
    if seed_real_portfolio_slot is None:
        pytest.skip("No real ea_id available")
    resp = await client.get("/api/v1/portfolio/stale")
    assert resp.status_code == 200
    data = resp.json()
    for entry in data["longest_unsold"]:
        assert "ea_id" in entry
        assert "name" in entry
        assert "buy_price" in entry
        assert "bought_at" in entry
        assert "time_since_buy_hours" in entry
        assert "status" in entry


@pytest.mark.asyncio
async def test_stale_avg_sale_time_shape(client):
    """Avg sale time entries have the expected fields."""
    resp = await client.get("/api/v1/portfolio/stale")
    assert resp.status_code == 200
    data = resp.json()
    for entry in data["avg_sale_time"]:
        assert "ea_id" in entry
        assert "name" in entry
        assert "total_sales" in entry
        assert "first_activity" in entry
        assert "last_activity" in entry
        assert "time_period_hours" in entry
        assert "avg_hours_between_sales" in entry
