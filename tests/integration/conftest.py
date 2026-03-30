"""Session-scoped fixtures for integration tests.

Starts api + scanner services via Docker Compose with a test override
file that points both containers at the postgres-test database.

This mirrors the EXACT production deployment (D-07): same Dockerfile,
same docker-compose.yml, same service definitions. The only difference
is docker-compose.test.yml overriding DATABASE_URL to use postgres-test.

Setup (run once, before first test session):
    docker compose up -d postgres-test
    python scripts/setup_test_db.py

Mutable tables (portfolio_slots, trade_actions, trade_records, scanner_status)
are cleaned after each test. Production data is never touched.

Uses synchronous live_server fixture to avoid pytest-asyncio 1.3.0 event loop
scoping issues with session-scoped async fixtures.
"""
import os
import subprocess
import time

import httpx
import pytest


# -- Constants -----------------------------------------------------------------

COMPOSE_PROJECT = "op_seller_test"
# Resolve absolute paths for docker compose -f flags
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
COMPOSE_FILE = os.path.join(_REPO_ROOT, "docker-compose.yml")
COMPOSE_TEST_OVERRIDE = os.path.join(_REPO_ROOT, "docker-compose.test.yml")

# Test API is on port 8001 (mapped in docker-compose.test.yml to avoid prod conflict)
TEST_API_PORT = 8001
TEST_API_BASE = f"http://127.0.0.1:{TEST_API_PORT}"

# For direct DB access in fixtures (from host, not from inside Docker)
TEST_DB_URL = "postgresql+asyncpg://op_seller:op_seller@localhost:5433/op_seller"


def _compose_cmd(*args):
    """Build a docker compose command with project name and both compose files."""
    return [
        "docker", "compose",
        "-f", COMPOSE_FILE,
        "-f", COMPOSE_TEST_OVERRIDE,
        "-p", COMPOSE_PROJECT,
        *args,
    ]


def _check_test_db():
    """Skip integration tests if the pre-prepared test DB is not reachable.

    The test DB (op_seller_test) must be set up before running tests:
        docker compose up -d postgres-test
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
            "Test DB not available -- run 'docker compose up -d postgres-test' "
            "and 'python scripts/setup_test_db.py' first"
        )


# -- Test database -------------------------------------------------------------

@pytest.fixture(scope="session")
def test_db_url() -> str:
    """Return asyncpg URL for the pre-prepared test database.

    The test DB (op_seller_test) must already exist -- it's a full clone
    of production, prepared once via scripts/setup_test_db.py.
    """
    _check_test_db()
    return TEST_DB_URL


# -- Live server (API + Scanner via Docker Compose) ----------------------------

@pytest.fixture(scope="session", autouse=True)
def live_server(test_db_url):
    """Start API and scanner via Docker Compose with test override (D-07, D-08).

    This fixture is SYNCHRONOUS (not async def) to avoid the pytest-asyncio 1.3.0
    event loop scoping issue where session-scoped async fixtures fail because
    pytest-asyncio defaults to function-scoped event loops.

    Uses docker-compose.test.yml override which:
    - Points DATABASE_URL at postgres-test service (Docker DNS, not localhost)
    - Maps API to host port 8001 (avoids prod conflict)
    - Sets restart: "no" (tests should not auto-restart on failure)
    """
    # Build and start api + scanner containers (postgres-test should already be running)
    subprocess.run(
        _compose_cmd("up", "-d", "--build", "api", "scanner"),
        check=True,
    )

    # Phase 1: Wait for API HTTP health endpoint to respond with 200
    # Allow up to 180s — Docker build can take 60-90s before the container starts
    for i in range(1800):
        try:
            r = httpx.get(f"{TEST_API_BASE}/api/v1/health", timeout=2.0)
            if r.status_code == 200:
                break
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.TimeoutException, httpx.RemoteProtocolError):
            pass
        time.sleep(0.1)
    else:
        # Dump logs for debugging before tearing down
        subprocess.run(_compose_cmd("logs", "api", "scanner"))
        subprocess.run(_compose_cmd("down"))
        raise RuntimeError(
            f"Test API server failed to start (port {TEST_API_PORT}). "
            "Check Docker Compose logs above."
        )

    # Phase 2: Wait for scanner to write first scanner_status row (Warning 2).
    # The scanner needs ~30s to complete its first dispatch cycle and upsert
    # scanner_status. Without this wait, tests checking health response fields
    # would see scanner_status="unknown" intermittently.
    for i in range(900):  # 90 seconds max
        try:
            r = httpx.get(f"{TEST_API_BASE}/api/v1/health", timeout=2.0)
            if r.status_code == 200:
                data = r.json()
                if data.get("scanner_status") != "unknown":
                    break
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.TimeoutException, httpx.RemoteProtocolError):
            pass
        time.sleep(0.1)
    else:
        # Scanner didn't write status in 90s -- warn but don't fail.
        # Tests that check scanner health fields may see "unknown".
        import warnings
        warnings.warn(
            "Scanner did not write scanner_status within 90s. "
            "Health endpoint may return 'unknown' for scanner fields.",
            stacklevel=2,
        )

    yield

    # Tear down api + scanner containers (leave postgres-test running for next test run)
    subprocess.run(
        _compose_cmd("down", "--remove-orphans"),
        check=True,
    )


# -- Base URL ------------------------------------------------------------------

@pytest.fixture(scope="session")
def base_url():
    """Return the base URL for the running test API server."""
    return TEST_API_BASE


# -- HTTP client ---------------------------------------------------------------

@pytest.fixture
async def client(base_url):
    """Function-scoped async httpx client targeting the live test server."""
    async with httpx.AsyncClient(base_url=base_url, timeout=120.0) as c:
        yield c


# -- Real ea_id helper ---------------------------------------------------------

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


# -- Seed helper (real ea_id) --------------------------------------------------

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


# -- Per-test cleanup ----------------------------------------------------------

@pytest.fixture(autouse=True)
async def cleanup_tables(test_db_url):
    """Delete all rows from mutable tables in the TEST database after each test.

    Only touches op_seller_test -- production op_seller is never modified.
    Preserves read-only data (players, player_scores, market_snapshots, etc.)

    Creates a fresh engine per cleanup to avoid cross-event-loop issues with
    pytest-asyncio's function-scoped event loops. NullPool avoids accumulating
    connections across tests.
    """
    yield
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool
    from sqlalchemy import text

    engine = create_async_engine(test_db_url, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM trade_records"))
        await conn.execute(text("DELETE FROM trade_actions"))
        await conn.execute(text("DELETE FROM portfolio_slots"))
        await conn.execute(text("DELETE FROM scanner_status"))
    await engine.dispose()
