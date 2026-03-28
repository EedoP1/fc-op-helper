"""Session-scoped fixtures for integration tests.

Starts a real uvicorn process on a free port with a real SQLite file.
Uses a synchronous live_server fixture to avoid pytest-asyncio 1.3.0
event loop scoping issues with session-scoped async fixtures.
"""
import os
import socket
import subprocess
import sys
import time

import httpx
import pytest


# ── Port discovery ────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def server_port():
    """Find a free TCP port for the test server."""
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


# ── DB path ───────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def test_db_path(tmp_path_factory):
    """Return a path to a temporary SQLite file for the integration test session."""
    return tmp_path_factory.mktemp("integration") / "test_op_seller.db"


# ── Live server ───────────────────────────────────────────────────────────────

@pytest.fixture(scope="session", autouse=True)
def live_server(test_db_path, server_port):
    """Start a real uvicorn process serving the test server harness.

    This fixture is SYNCHRONOUS (not async def) to avoid the pytest-asyncio 1.3.0
    event loop scoping issue where session-scoped async fixtures fail because
    pytest-asyncio defaults to function-scoped event loops.

    Uses subprocess.Popen + a synchronous readiness poll with time.sleep()
    and httpx.get() (sync client).
    """
    env = {**os.environ, "TEST_DB_PATH": str(test_db_path)}
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

    # Synchronous readiness poll — no async, no event loop issues
    base_url = f"http://127.0.0.1:{server_port}"
    for _ in range(100):  # 10 seconds max (server startup can take a few seconds)
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
    async with httpx.AsyncClient(base_url=base_url, timeout=10.0) as c:
        yield c


# ── Seed helper ───────────────────────────────────────────────────────────────

@pytest.fixture
async def seed_portfolio_slot(client):
    """Seed one portfolio slot and return the response."""
    resp = await client.post(
        "/api/v1/portfolio/slots",
        json={
            "slots": [
                {
                    "ea_id": 100,
                    "buy_price": 50000,
                    "sell_price": 70000,
                    "player_name": "Test Player",
                }
            ]
        },
    )
    return resp


# ── Per-test cleanup ──────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
async def cleanup_tables(test_db_path):
    """Delete all rows from mutable tables after each test.

    Uses connect_args={"timeout": 10} to avoid 'database is locked' errors on
    Windows where SQLite file locking is stricter.
    """
    yield
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy import text

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{test_db_path}",
        connect_args={"timeout": 10},
    )
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM trade_records"))
        await conn.execute(text("DELETE FROM trade_actions"))
        await conn.execute(text("DELETE FROM portfolio_slots"))
    await engine.dispose()
