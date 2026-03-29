"""Test server harness for integration tests.

This module runs inside a FRESH subprocess spawned by conftest.py via:

    Popen(["uvicorn", "tests.integration.server_harness:app", ...], env=env)

where `env` contains DATABASE_URL set by conftest before Popen is called.

Isolation model:
    conftest.py passes DATABASE_URL (pointing to the testcontainers Postgres
    instance) in the subprocess environment. src.config reads DATABASE_URL at
    import time. So the harness does NOT need to set DATABASE_URL — it is
    already in os.environ when uvicorn loads this module.

    This is safe to rely on because this module runs inside a dedicated
    subprocess. There is no shared state between the test process (conftest,
    test files) and the server process.

The harness uses the REAL server components, mirroring src.server.main exactly,
with two differences:

    1. The bootstrap one-shot job (scanner.run_bootstrap_and_score) is NOT added.

    2. The discovery, cleanup, and aggregation APScheduler jobs are NOT added
       to the test scheduler.

Why no bootstrap: the bootstrap downloads 1819+ players from fut.gg and holds
the write pool for minutes, causing all concurrent API requests to time out.

Why no discovery/cleanup/aggregation: these make bulk API/DB operations that
are only needed for long-running production. They are not required for
realistic integration test conditions.

Why scan_dispatch IS enabled: after Fix 1 (fire-and-forget dispatch),
dispatch_scans() returns immediately after creating tasks. The APScheduler
callback no longer blocks the event loop, so API handlers are not starved.
Enabling scan_dispatch gives tests realistic concurrency conditions (background
scans running while API handlers process requests), which surfaces real bugs
that would otherwise be hidden.

The ScannerService starts (scanner.start() is called). This means:
    - Real FutGGClient (started and used by background scan tasks)
    - Real CircuitBreaker (functional)
    - Health endpoint reports scanner as running
    - scanner.is_running = True

This is NOT mocking. All server components are real. Only the one-shot bootstrap
and the periodic bulk-operation jobs (discovery, cleanup, aggregation) are
omitted to keep tests fast and deterministic.

Per D-03: Single engine; Postgres MVCC handles concurrent scanner + API access.
"""
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# DATABASE_URL is already in os.environ — set by conftest via subprocess env.
# src.config reads it at import time. No override needed here.
from src.server.db import create_engine_and_tables, create_session_factory  # noqa: E402
from src.server.scanner import ScannerService  # noqa: E402
from src.server.circuit_breaker import CircuitBreaker  # noqa: E402
from apscheduler.schedulers.asyncio import AsyncIOScheduler  # noqa: E402
from src.server.api.players import router as players_router  # noqa: E402
from src.server.api.health import router as health_router  # noqa: E402
from src.server.api.portfolio import router as portfolio_router  # noqa: E402
from src.server.api.actions import router as actions_router  # noqa: E402
from src.server.api.profit import router as profit_router  # noqa: E402
from src.server.api.portfolio_status import router as status_router  # noqa: E402

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Real server lifespan for integration tests.

    Identical to src.server.main.lifespan except the bootstrap one-shot job
    is omitted to prevent the scanner from holding the write pool during tests.

    Uses a single Postgres engine (not 3 SQLite engines) — Postgres MVCC
    allows concurrent scanner and API reads/writes on one connection pool.

    See module docstring for full rationale.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    logger.info("Starting OP Seller test server (no bootstrap job)...")

    engine, session_factory = await create_engine_and_tables()

    # Purge stale v1 scores that lack expected_profit_per_hour (same as production)
    async with session_factory() as session:
        from sqlalchemy import delete
        from src.server.models_db import PlayerScore
        result = await session.execute(
            delete(PlayerScore).where(PlayerScore.expected_profit_per_hour == None)  # noqa: E711
        )
        purged = result.rowcount
        await session.commit()
        if purged:
            logger.info("Purged %d stale v1 scores (missing expected_profit_per_hour)", purged)


    # Warm Postgres shared_buffers by running the exact queries the portfolio
    # endpoints will use. Without this, the first API call triggers cold disk
    # reads through Docker's WSL2 filesystem, exceeding the httpx 120s timeout.
    async with session_factory() as session:
        from sqlalchemy import text
        import time as _time
        t0 = _time.time()
        # Warm player_scores (254K rows, used by viable-candidates subquery)
        await session.execute(text(
            "SELECT count(*) FROM player_scores WHERE is_viable = true"
        ))
        # Warm market_snapshots composite index (ix_market_snapshots_ea_id_captured_at)
        # used by _get_volatile_ea_ids GROUP BY query. A simple count(*) primes the
        # table pages but NOT the composite index pages — this exercises the actual
        # GROUP BY pattern so the index is hot before the first API call.
        await session.execute(text(
            "SELECT ea_id, min(current_lowest_bin), max(current_lowest_bin) "
            "FROM market_snapshots "
            "WHERE captured_at >= NOW() - INTERVAL '7 days' "
            "GROUP BY ea_id "
            "HAVING count(id) >= 2"
        ))
        # Warm the joined viable-candidates + optimizer query (used by
        # /portfolio/generate, GET /portfolio, DELETE /portfolio).
        await session.execute(text(
            "SELECT ps.ea_id, ps.efficiency FROM player_scores ps "
            "JOIN players pr ON pr.ea_id = ps.ea_id "
            "WHERE ps.is_viable = true AND pr.is_active = true "
            "ORDER BY ps.efficiency DESC LIMIT 100"
        ))
        # Warm players table (1.8K rows, joined in all portfolio queries)
        await session.execute(text("SELECT count(*) FROM players WHERE is_active = true"))
        logger.info("Cache warmup complete in %.1fs", _time.time() - t0)

    # Throttle scanner for tests BEFORE start() — production scale (200 tasks,
    # 40 concurrency) exhausts the DB pool and starves API handlers.
    # Patch module-level constants since they were copied at import time.
    import src.server.scanner as _scanner_mod
    _scanner_mod.SCAN_DISPATCH_BATCH_SIZE = 10
    _scanner_mod.SCAN_CONCURRENCY = 5

    cb = CircuitBreaker()
    scanner = ScannerService(session_factory=session_factory, circuit_breaker=cb)
    await scanner.start()

    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.read_session_factory = session_factory  # Same pool — Postgres MVCC
    app.state.scanner = scanner
    app.state.circuit_breaker = cb

    scheduler = AsyncIOScheduler(timezone="UTC")
    app.state.scheduler = scheduler
    scheduler.start()

    # NOTE: bootstrap job intentionally omitted — see module docstring.
    # NOTE: discovery, cleanup, aggregation jobs intentionally omitted.
    # Dispatch interval raised for tests: real fut.gg HTTP calls + DB write
    # callbacks can saturate the event loop and starve API handlers.  A 5-minute
    # interval means the first dispatch fires well after most tests complete
    # while still exercising the scanner interaction path.
    scheduler.add_job(
        scanner.dispatch_scans,
        "interval",
        seconds=300,
        id="scan_dispatch",
    )

    logger.info("Test server started (scanner running, scan_dispatch job active every 300s).")

    yield

    logger.info("Test server shutting down...")
    scheduler.shutdown(wait=False)
    await scanner.stop()
    await engine.dispose()
    logger.info("Test server stopped.")


app = FastAPI(title="OP Seller Integration Test", lifespan=lifespan)



app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"chrome-extension://.*",
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type"],
)
app.include_router(players_router)
app.include_router(health_router)
app.include_router(portfolio_router)
app.include_router(actions_router)
app.include_router(profit_router)
app.include_router(status_router)
