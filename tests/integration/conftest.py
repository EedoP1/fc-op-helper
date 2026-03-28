"""Session-scoped fixtures for integration tests.

Starts a real uvicorn process on a free port backed by a pre-prepared test
database (op_seller_test) in the same Postgres container. The test DB is a
full clone of production — same data, same size, same performance profile.

Setup (run once, before first test session):
    python scripts/setup_test_db.py

Mutable tables (portfolio_slots, trade_actions, trade_records) are cleaned
after each test. Production data is never touched.

Uses synchronous live_server fixture to avoid pytest-asyncio 1.3.0 event loop
scoping issues with session-scoped async fixtures.
"""
import os
import socket
import subprocess
import sys
import time

import httpx
import pytest


# ── Database URLs ────────────────────────────────────────────────────────────

PROD_DB_URL = "postgresql+asyncpg://op_seller:op_seller@localhost:5432/op_seller"
TEST_DB_URL = "postgresql+asyncpg://op_seller:op_seller@localhost:5433/op_seller"

def _check_test_db():
    """Skip integration tests if the pre-prepared test DB is not reachable.

    The test DB (op_seller_test) must be set up before running tests:
        python scripts/setup_test_db.py
    """
    import asyncio
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy import text

    async def _ping():
        engine = create_async_engine(TEST_DB_URL)
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        finally:
            await engine.dispose()

    try:
        asyncio.run(_ping())
    except Exception:
        pytest.skip(
            "Test DB not available -- run 'python scripts/setup_test_db.py' first"
        )


# ── Port discovery ────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def server_port():
    """Find a free TCP port for the test server."""
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


# ── Test database ────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def test_db_url() -> str:
    """Return asyncpg URL for the pre-prepared test database.

    The test DB (op_seller_test) must already exist — it's a full clone
    of production, prepared once via scripts/setup_test_db.py.
    """
    _check_test_db()
    return TEST_DB_URL


# ── Live server ───────────────────────────────────────────────────────────────

@pytest.fixture(scope="session", autouse=True)
def live_server(test_db_url, server_port):
    """Start a real uvicorn process serving the real server harness.

    This fixture is SYNCHRONOUS (not async def) to avoid the pytest-asyncio 1.3.0
    event loop scoping issue where session-scoped async fixtures fail because
    pytest-asyncio defaults to function-scoped event loops.

    Passes DATABASE_URL pointing to op_seller_test (not production) via env
    to the subprocess, so the harness has full read-only data but mutable
    tables are isolated.
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

    base_url = f"http://127.0.0.1:{server_port}"
    for _ in range(600):
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
    async with httpx.AsyncClient(base_url=base_url, timeout=120.0) as c:
        yield c


# ── Real ea_id helper ─────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def real_ea_id(test_db_url):
    """Return a real ea_id from the cloned test data.

    Queries player_scores for a viable active player. With the full cloned
    dataset this should always return a valid ea_id.
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
    """Seed one portfolio slot using a real ea_id from the cloned data.

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
    """Delete all rows from mutable tables in the TEST database after each test.

    Only touches op_seller_test — production op_seller is never modified.
    Preserves read-only data (players, player_scores, market_snapshots, etc.)
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
