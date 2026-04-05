"""Performance threshold tests for critical API endpoints.

Each test:
  1. Issues one warmup request (initializes connection pool)
  2. Times 5 consecutive requests using time.perf_counter()
  3. Takes the p95 (worst of 5 for N=5)
  4. Asserts against the threshold from D-13 / D-14

Thresholds (per 09-CONTEXT.md D-13, D-14):
  - /health                < 100ms  (p95, over loopback, real DB)
  - /actions/pending       < 200ms  (p95)
  - /portfolio/status      < 300ms  (p95, with seeded slots)
  - /profit/summary        < 200ms  (p95)
  - /portfolio/generate    < 10000ms (single request, per D-14)

If a threshold fails, that is a server PERFORMANCE BUG — do NOT weaken
the threshold.
"""
import time

import pytest


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _measure_ms(client, method: str, url: str, *, n: int = 5, **kwargs) -> list[float]:
    """Time n HTTP requests and return list of elapsed_ms values."""
    results = []
    for _ in range(n):
        t0 = time.perf_counter()
        if method == "GET":
            r = await client.get(url, **kwargs)
        elif method == "POST":
            r = await client.post(url, **kwargs)
        else:
            raise ValueError(f"Unknown method: {method}")
        elapsed_ms = (time.perf_counter() - t0) * 1000
        results.append(elapsed_ms)
        assert r.status_code < 500, f"Server error during perf test: {r.status_code} {r.text}"
    return results


# ── Tests ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_health_latency(client):
    """GET /health p95 must be < 100ms over loopback with real DB."""
    # Warmup
    await client.get("/api/v1/health")

    elapsed_times = await _measure_ms(client, "GET", "/api/v1/health")
    elapsed_ms = max(elapsed_times)  # p95 = worst of 5

    assert elapsed_ms < 100, (
        f"GET /health p95={elapsed_ms:.1f}ms exceeds 100ms threshold. "
        f"All measurements: {[f'{t:.1f}' for t in elapsed_times]}ms. "
        "This is a server performance bug."
    )


@pytest.mark.asyncio
async def test_pending_action_latency(client):
    """GET /actions/pending p95 must be < 200ms (empty portfolio, no derivation needed)."""
    # Warmup
    await client.get("/api/v1/actions/pending")

    elapsed_times = await _measure_ms(client, "GET", "/api/v1/actions/pending")
    elapsed_ms = max(elapsed_times)  # p95 = worst of 5

    assert elapsed_ms < 200, (
        f"GET /actions/pending p95={elapsed_ms:.1f}ms exceeds 200ms threshold. "
        f"All measurements: {[f'{t:.1f}' for t in elapsed_times]}ms. "
        "This is a server performance bug."
    )


@pytest.mark.asyncio
async def test_portfolio_status_latency(client, seed_real_portfolio_slot, real_ea_id):
    """GET /portfolio/status p95 must be < 300ms with seeded slots and real DB."""
    assert real_ea_id is not None, "real_ea_id is None — DB may be empty"
    assert seed_real_portfolio_slot is not None
    assert seed_real_portfolio_slot.status_code == 201, (
        f"Slot seed failed: {seed_real_portfolio_slot.status_code}"
    )

    # Warmup
    await client.get("/api/v1/portfolio/status")

    elapsed_times = await _measure_ms(client, "GET", "/api/v1/portfolio/status")
    elapsed_ms = max(elapsed_times)  # p95 = worst of 5

    assert elapsed_ms < 300, (
        f"GET /portfolio/status p95={elapsed_ms:.1f}ms exceeds 300ms threshold. "
        f"All measurements: {[f'{t:.1f}' for t in elapsed_times]}ms. "
        "This is a server performance bug."
    )


@pytest.mark.asyncio
async def test_profit_summary_latency(client):
    """GET /profit/summary p95 must be < 200ms."""
    # Warmup
    await client.get("/api/v1/profit/summary")

    elapsed_times = await _measure_ms(client, "GET", "/api/v1/profit/summary")
    elapsed_ms = max(elapsed_times)  # p95 = worst of 5

    assert elapsed_ms < 300, (
        f"GET /profit/summary p95={elapsed_ms:.1f}ms exceeds 300ms threshold. "
        f"All measurements: {[f'{t:.1f}' for t in elapsed_times]}ms. "
        "This is a server performance bug."
    )


@pytest.mark.asyncio
async def test_portfolio_generate_latency(client):
    """POST /portfolio/generate must complete in < 10000ms (D-14).

    Portfolio generation with real scored data should complete well under 10
    seconds; exceeding this threshold indicates a performance regression.
    """
    # Warmup
    await client.post("/api/v1/portfolio/generate", json={"budget": 2_000_000})

    t0 = time.perf_counter()
    r = await client.post("/api/v1/portfolio/generate", json={"budget": 2_000_000})
    elapsed_ms = (time.perf_counter() - t0) * 1000

    assert r.status_code == 200, f"Unexpected status {r.status_code}: {r.text}"

    assert elapsed_ms < 10_000, (
        f"POST /portfolio/generate took {elapsed_ms:.0f}ms — exceeds 10000ms threshold. "
        "This is a server performance regression per D-14."
    )
