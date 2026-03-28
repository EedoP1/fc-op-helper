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

import aiosqlite
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

REAL_DB_PATH = "D:/op-seller/op_seller.db"


@pytest.fixture(scope="session")
def test_db_path(tmp_path_factory):
    """Build a lean test DB from the real production DB.

    The production DB is ~7GB (years of scan history). Copying it entirely
    takes 3+ minutes and causes server startup delays because the v1-score
    purge query scans 249k rows without an index.

    Instead, build a lean test DB that contains:
    - ALL rows from ``players`` (player records, ~2k rows)
    - Latest 5 player_scores per player (sufficient for scoring tests, ~10k rows)
    - Latest 500 market_snapshots per player for current_bin queries (~1M rows)
    - Schema-only for mutable tables (portfolio_slots, trade_actions, trade_records)

    The mutable tables start empty; per-test cleanup keeps them that way.
    Read-only tables (listing_observations, daily_listing_summaries,
    snapshot_price_points, snapshot_sales) are omitted — tests do not require
    them for smoke or performance testing.

    If REAL_DB_PATH does not exist, the empty DB is still valid — the ORM
    creates all tables on startup; tests that assert real data will fail with
    clear messages.

    [Deviation Rule 3] Using sqlite3.backup() + selective copy to avoid the
    two blockers that prevented server startup:
    1. shutil.copy2() on a live WAL DB creates a malformed copy.
    2. Full 7GB copy + full-scan purge took 10+ min, exceeding 30s poll window.
    """
    import sqlite3 as _sqlite3

    dest = tmp_path_factory.mktemp("integration") / "test_op_seller.db"

    if not os.path.exists(REAL_DB_PATH):
        # Empty DB — server will create schema on startup
        return dest

    # Open source with WAL-safe read (immutable=0, no write lock needed)
    src = _sqlite3.connect(f"file:{REAL_DB_PATH}?mode=ro", uri=True, check_same_thread=False)
    dst = _sqlite3.connect(str(dest))

    # Enable WAL on the destination so the server can start quickly
    dst.execute("PRAGMA journal_mode=WAL")
    dst.execute("PRAGMA synchronous=NORMAL")

    # --- players (all rows, typically ~2k) ---
    _copy_table(src, dst, "players")

    # --- player_scores (latest 5 viable per player) ---
    # This gives enough data for portfolio generation without 249k row scan
    _copy_query(
        src, dst,
        "player_scores",
        """
        SELECT ps.*
        FROM player_scores ps
        INNER JOIN (
            SELECT ea_id, MAX(id) AS max_id
            FROM player_scores
            WHERE is_viable = 1
            GROUP BY ea_id
        ) latest ON ps.id = latest.max_id
        """,
    )

    # --- market_snapshots (latest 1 per player for current_bin queries) ---
    _copy_query(
        src, dst,
        "market_snapshots",
        """
        SELECT ms.*
        FROM market_snapshots ms
        INNER JOIN (
            SELECT ea_id, MAX(id) AS max_id
            FROM market_snapshots
            GROUP BY ea_id
        ) latest ON ms.id = latest.max_id
        """,
    )

    # --- schema-only tables (mutable, start empty) ---
    for tbl in ("portfolio_slots", "trade_actions", "trade_records"):
        _copy_table_schema_only(src, dst, tbl)

    # --- optional tables (schema only, not needed for smoke tests) ---
    for tbl in ("listing_observations", "daily_listing_summaries",
                "snapshot_price_points", "snapshot_sales"):
        _copy_table_schema_only(src, dst, tbl)

    dst.commit()
    src.close()
    dst.close()

    return dest


def _get_table_create_sql(src_con, table_name: str) -> str | None:
    """Return the CREATE TABLE SQL for the given table from the source DB."""
    row = src_con.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row[0] if row else None


def _get_table_index_sqls(src_con, table_name: str) -> list[str]:
    """Return CREATE INDEX statements for the given table."""
    rows = src_con.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL",
        (table_name,),
    ).fetchall()
    return [r[0] for r in rows]


def _copy_table(src_con, dst_con, table_name: str) -> None:
    """Copy full table DDL + all rows from src to dst."""
    create_sql = _get_table_create_sql(src_con, table_name)
    if not create_sql:
        return
    dst_con.execute(create_sql)
    for idx_sql in _get_table_index_sqls(src_con, table_name):
        dst_con.execute(idx_sql)
    rows = src_con.execute(f"SELECT * FROM [{table_name}]").fetchall()
    if rows:
        placeholders = ",".join("?" * len(rows[0]))
        dst_con.executemany(f"INSERT INTO [{table_name}] VALUES ({placeholders})", rows)
    dst_con.commit()


def _copy_query(src_con, dst_con, table_name: str, query: str) -> None:
    """Copy table DDL + rows matching the given SELECT query."""
    create_sql = _get_table_create_sql(src_con, table_name)
    if not create_sql:
        return
    dst_con.execute(create_sql)
    for idx_sql in _get_table_index_sqls(src_con, table_name):
        dst_con.execute(idx_sql)
    rows = src_con.execute(query).fetchall()
    if rows:
        placeholders = ",".join("?" * len(rows[0]))
        dst_con.executemany(f"INSERT INTO [{table_name}] VALUES ({placeholders})", rows)
    dst_con.commit()


def _copy_table_schema_only(src_con, dst_con, table_name: str) -> None:
    """Copy only the DDL (no rows) for the given table."""
    create_sql = _get_table_create_sql(src_con, table_name)
    if not create_sql:
        return
    dst_con.execute(create_sql)
    for idx_sql in _get_table_index_sqls(src_con, table_name):
        dst_con.execute(idx_sql)
    dst_con.commit()


# ── Live server ───────────────────────────────────────────────────────────────

@pytest.fixture(scope="session", autouse=True)
def live_server(test_db_path, server_port):
    """Start a real uvicorn process serving the real server harness.

    This fixture is SYNCHRONOUS (not async def) to avoid the pytest-asyncio 1.3.0
    event loop scoping issue where session-scoped async fixtures fail because
    pytest-asyncio defaults to function-scoped event loops.

    Uses subprocess.Popen + a synchronous readiness poll with time.sleep()
    and httpx.get() (sync client).

    The real server starts with real ScannerService, CircuitBreaker, and
    APScheduler — startup takes longer than the mock harness did, so the
    readiness poll allows up to 30 seconds (300 x 0.1s).
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
def real_ea_id(test_db_path):
    """Return a real ea_id from the production DB copy.

    Queries the test DB directly (synchronously via aiosqlite event loop) to
    find an active player with a viable score. Falls back to None if the DB
    has no viable players (e.g., fresh empty DB).
    """
    import asyncio

    async def _query():
        async with aiosqlite.connect(str(test_db_path)) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT ps.ea_id
                FROM player_scores ps
                JOIN players pr ON pr.ea_id = ps.ea_id
                WHERE ps.is_viable = 1
                  AND pr.is_active = 1
                ORDER BY ps.efficiency DESC
                LIMIT 1
                """
            )
            row = await cursor.fetchone()
            return row["ea_id"] if row else None

    return asyncio.run(_query())


# ── Seed helper (real ea_id) ──────────────────────────────────────────────────

@pytest.fixture
async def seed_real_portfolio_slot(client, real_ea_id):
    """Seed one portfolio slot using a real ea_id from the production DB.

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
async def cleanup_tables(test_db_path):
    """Delete all rows from mutable tables after each test.

    Preserves read-only data (player_records, player_scores, market_snapshots,
    snapshot_price_points, listing_observations, daily_listing_summaries,
    snapshot_sales) per D-16 — this is the real data that makes tests
    meaningful.

    Uses connect_args={"timeout": 30} to avoid 'database is locked' errors on
    Windows where SQLite file locking is stricter (especially with the real
    scanner holding write locks).
    """
    yield
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy import text

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{test_db_path}",
        connect_args={"timeout": 30},
    )
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM trade_records"))
        await conn.execute(text("DELETE FROM trade_actions"))
        await conn.execute(text("DELETE FROM portfolio_slots"))
    await engine.dispose()
