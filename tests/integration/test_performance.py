"""Performance latency and concurrent request integration tests.

Measures real HTTP round-trip latency (over loopback to a real uvicorn process)
and verifies concurrent requests do not corrupt data. Thresholds are generous
(100-300ms) to account for network overhead, SQLite file I/O, and Python async
overhead on Windows.
"""
import asyncio
import time

import httpx
import pytest


# ── Helpers ───────────────────────────────────────────────────────────────────


async def _seed_realistic_data(client, n_players=10):
    """Seed N portfolio slots and some trade records to simulate realistic DB state."""
    slots = [
        {"ea_id": 1000 + i, "buy_price": 30000 + i * 1000,
         "sell_price": 45000 + i * 1000, "player_name": f"Perf Player {i}"}
        for i in range(n_players)
    ]
    await client.post("/api/v1/portfolio/slots", json={"slots": slots})

    # Add some trade records for half the players
    for i in range(n_players // 2):
        await client.post("/api/v1/trade-records/direct", json={
            "ea_id": 1000 + i, "price": 30000 + i * 1000, "outcome": "bought"
        })


# ── Latency tests ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_health_latency(client):
    """GET /health responds with p95 < 100ms over real HTTP loopback."""
    # Warmup
    await client.get("/api/v1/health")
    # Measure
    times = []
    for _ in range(10):
        start = time.perf_counter()
        r = await client.get("/api/v1/health")
        elapsed = (time.perf_counter() - start) * 1000
        assert r.status_code == 200
        times.append(elapsed)
    p95 = sorted(times)[int(len(times) * 0.95)]
    assert p95 < 100, f"Health p95={p95:.1f}ms exceeds 100ms"


@pytest.mark.asyncio
async def test_pending_action_latency(client):
    """GET /actions/pending responds with p95 < 200ms with 10 portfolio slots."""
    await _seed_realistic_data(client, n_players=10)
    await client.get("/api/v1/actions/pending")  # warmup
    times = []
    for _ in range(10):
        start = time.perf_counter()
        r = await client.get("/api/v1/actions/pending")
        elapsed = (time.perf_counter() - start) * 1000
        assert r.status_code == 200
        times.append(elapsed)
    p95 = sorted(times)[int(len(times) * 0.95)]
    assert p95 < 200, f"Pending action p95={p95:.1f}ms exceeds 200ms"


@pytest.mark.asyncio
async def test_portfolio_status_latency(client):
    """GET /portfolio/status responds with p95 < 300ms with 10 slots and trade records."""
    await _seed_realistic_data(client, n_players=10)
    await client.get("/api/v1/portfolio/status")  # warmup
    times = []
    for _ in range(10):
        start = time.perf_counter()
        r = await client.get("/api/v1/portfolio/status")
        elapsed = (time.perf_counter() - start) * 1000
        assert r.status_code == 200
        times.append(elapsed)
    p95 = sorted(times)[int(len(times) * 0.95)]
    assert p95 < 300, f"Portfolio status p95={p95:.1f}ms exceeds 300ms"


@pytest.mark.asyncio
async def test_profit_summary_latency(client):
    """GET /profit/summary responds with p95 < 200ms with trade data present."""
    await _seed_realistic_data(client, n_players=10)
    await client.get("/api/v1/profit/summary")  # warmup
    times = []
    for _ in range(10):
        start = time.perf_counter()
        r = await client.get("/api/v1/profit/summary")
        elapsed = (time.perf_counter() - start) * 1000
        assert r.status_code == 200
        times.append(elapsed)
    p95 = sorted(times)[int(len(times) * 0.95)]
    assert p95 < 200, f"Profit summary p95={p95:.1f}ms exceeds 200ms"


# ── Concurrent request tests ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_concurrent_pending_actions(client, base_url):
    """5 simultaneous GET /actions/pending all succeed without errors."""
    await _seed_realistic_data(client, n_players=5)
    async with httpx.AsyncClient(base_url=base_url, timeout=10.0) as c:
        tasks = [c.get("/api/v1/actions/pending") for _ in range(5)]
        results = await asyncio.gather(*tasks)
    for r in results:
        assert r.status_code == 200


@pytest.mark.asyncio
async def test_concurrent_batch_trade_records(client, base_url):
    """3 simultaneous batch POSTs to different ea_ids all return 201."""
    # Seed 3 slots
    await client.post("/api/v1/portfolio/slots", json={"slots": [
        {"ea_id": 2001, "buy_price": 30000, "sell_price": 45000, "player_name": "Concurrent A"},
        {"ea_id": 2002, "buy_price": 40000, "sell_price": 55000, "player_name": "Concurrent B"},
        {"ea_id": 2003, "buy_price": 50000, "sell_price": 65000, "player_name": "Concurrent C"},
    ]})
    async with httpx.AsyncClient(base_url=base_url, timeout=10.0) as c:
        tasks = [
            c.post("/api/v1/trade-records/batch", json={"records": [
                {"ea_id": 2001 + i, "price": 30000 + i * 10000, "outcome": "bought"}
            ]})
            for i in range(3)
        ]
        results = await asyncio.gather(*tasks)
    for r in results:
        assert r.status_code == 201


@pytest.mark.asyncio
async def test_concurrent_reads_during_write(client, base_url):
    """Simultaneous GET /portfolio/status reads while a write occurs do not 500."""
    await _seed_realistic_data(client, n_players=5)
    async with httpx.AsyncClient(base_url=base_url, timeout=10.0) as c:
        write_task = c.post("/api/v1/trade-records/direct", json={
            "ea_id": 1000, "price": 30000, "outcome": "listed"
        })
        read_tasks = [c.get("/api/v1/portfolio/status") for _ in range(3)]
        results = await asyncio.gather(write_task, *read_tasks)
    # Write may succeed (201), be deduplicated (200), or succeed as listed (201)
    # It should not return a 5xx error
    assert results[0].status_code in (200, 201), (
        f"Write returned unexpected status {results[0].status_code}"
    )
    for r in results[1:]:
        assert r.status_code == 200
