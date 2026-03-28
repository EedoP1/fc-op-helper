"""Session-scoped fixtures for integration tests.

Starts a real uvicorn process on a free port backed by an ephemeral Postgres
container (testcontainers). Each test session gets a fresh Postgres DB — no
shared SQLite file, no state leaks across runs.

Uses synchronous live_server fixture to avoid pytest-asyncio 1.3.0 event loop
scoping issues with session-scoped async fixtures.
"""
import os
import socket
import subprocess
import sys
import time

import docker
import httpx
import pytest
from testcontainers.postgres import PostgresContainer


# ── Docker pre-flight ─────────────────────────────────────────────────────────

def _check_docker():
    """Skip integration tests if Docker is not available.

    On Windows, Docker Desktop must be running. This guard provides a clear
    skip message instead of a confusing DockerException at test collection time
    (per Pitfall 6 — Windows Docker Desktop must be running).
    """
    try:
        docker.from_env().ping()
    except Exception:
        pytest.skip("Docker not available -- skipping integration tests")


# ── Port discovery ────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def server_port():
    """Find a free TCP port for the test server."""
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


# ── Postgres container ────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def postgres_container():
    """Session-scoped Postgres 17 container. Starts once for all tests.

    testcontainers manages the Docker container lifecycle — starts before the
    first test, stops after the last. Each test session gets a completely fresh
    DB (no leftover data from previous runs).
    """
    _check_docker()
    with PostgresContainer("postgres:17", driver=None) as pg:
        yield pg


@pytest.fixture(scope="session")
def test_db_url(postgres_container) -> str:
    """Return asyncpg-compatible URL for the test container.

    testcontainers returns a psycopg2-style URL; we convert it to asyncpg
    for use with SQLAlchemy create_async_engine.
    """
    url = postgres_container.get_connection_url()
    # testcontainers returns psycopg2 URL; convert to asyncpg
    url = url.replace("postgresql+psycopg2://", "postgresql+asyncpg://")
    url = url.replace("postgresql://", "postgresql+asyncpg://")
    return url


# ── Live server ───────────────────────────────────────────────────────────────

@pytest.fixture(scope="session", autouse=True)
def live_server(test_db_url, server_port):
    """Start a real uvicorn process serving the real server harness.

    This fixture is SYNCHRONOUS (not async def) to avoid the pytest-asyncio 1.3.0
    event loop scoping issue where session-scoped async fixtures fail because
    pytest-asyncio defaults to function-scoped event loops.

    Passes DATABASE_URL via env to the subprocess — no TEST_DB_PATH needed.
    The harness reads DATABASE_URL at import time (src.config).

    The real server starts with real ScannerService, CircuitBreaker, and
    APScheduler — startup takes longer than the mock harness did, so the
    readiness poll allows up to 30 seconds (300 x 0.1s).
    """
    env = {**os.environ, "DATABASE_URL": test_db_url}
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "tests.integration.server_harness:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(server_port),
            "--no-access-log",
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # Synchronous readiness poll — no async, no event loop issues.
    # 300 x 0.1s = 30 seconds max; the real server (scanner + scheduler) takes
    # longer to start than a minimal mock harness.
    base_url = f"http://127.0.0.1:{server_port}"
    for _ in range(300):
        try:
            r = httpx.get(f"{base_url}/api/v1/health", timeout=1.0)
            if r.status_code == 200:
                break
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.TimeoutException):
            pass
        time.sleep(0.1)
    else:
        proc.kill()
        stdout, stderr = proc.communicate(timeout=5)
        raise RuntimeError(
            f"Test server failed to start on port {server_port}\n"
            f"stdout: {stdout.decode()}\nstderr: {stderr.decode()}"
        )

    yield proc

    proc.terminate()
    proc.wait(timeout=5)


# ── Base URL ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def base_url(server_port):
    """Return the base URL for the running test server."""
    return f"http://127.0.0.1:{server_port}"


# ── HTTP client ───────────────────────────────────────────────────────────────

@pytest.fixture
async def client(base_url):
    """Function-scoped async httpx client targeting the live test server."""
    async with httpx.AsyncClient(base_url=base_url, timeout=30.0) as c:
        yield c


# ── Real ea_id helper ─────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def real_ea_id(test_db_url):
    """Return a real ea_id by querying the Postgres test container.

    Uses an asyncpg engine to query player_scores for a viable player. Returns
    None on a fresh container with no seed data — seed_real_portfolio_slot and
    test helpers using POST /portfolio/generate handle the None case.
    """
    import asyncio
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy import text

    async def _query():
        engine = create_async_engine(test_db_url)
        async with engine.connect() as conn:
            result = await conn.execute(text(
                "SELECT ps.ea_id FROM player_scores ps "
                "JOIN players pr ON pr.ea_id = ps.ea_id "
                "WHERE ps.is_viable = true AND pr.is_active = true "
                "ORDER BY ps.efficiency DESC LIMIT 1"
            ))
            row = result.first()
            await engine.dispose()
            return row[0] if row else None

    return asyncio.run(_query())


# ── Seed helper (real ea_id) ──────────────────────────────────────────────────

@pytest.fixture
async def seed_real_portfolio_slot(client, real_ea_id):
    """Seed one portfolio slot using a real ea_id from the test container.

    This replaces the old fake ea_id=100 fixture which would fail with
    the real server because trade records validate ea_id in portfolio_slots
    and portfolio_slots.ea_id has a foreign-key-like constraint in practice.

    If real_ea_id is None (empty DB), skips the seed and returns None.
    """
    if real_ea_id is None:
        return None

    resp = await client.post(
        "/api/v1/portfolio/slots",
        json={
            "slots": [
                {
                    "ea_id": real_ea_id,
                    "buy_price": 50000,
                    "sell_price": 70000,
                    "player_name": f"Player {real_ea_id}",
                }
            ]
        },
    )
    return resp


# ── Per-test cleanup ──────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
async def cleanup_tables(test_db_url):
    """Delete all rows from mutable tables after each test.

    Preserves read-only data (players, player_scores, market_snapshots,
    snapshot_price_points, listing_observations, daily_listing_summaries,
    snapshot_sales) — this is the scored player data that makes tests meaningful.

    Uses asyncpg engine instead of aiosqlite for Postgres compatibility.
    """
    yield
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy import text

    engine = create_async_engine(test_db_url)
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM trade_records"))
        await conn.execute(text("DELETE FROM trade_actions"))
        await conn.execute(text("DELETE FROM portfolio_slots"))
    await engine.dispose()
