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
with one difference:

    The bootstrap one-shot job (scanner.run_bootstrap_and_score) is NOT added.

Why: the bootstrap downloads 1819+ players from fut.gg and holds the write
pool for minutes, causing all concurrent API requests to time out. Tests use
a fresh Postgres container — bootstrapping is unnecessary and disruptive to
test isolation.

Everything else is identical to the production server:
    - Real ScannerService starts and connects to fut.gg
    - Real CircuitBreaker is used
    - Real APScheduler runs (dispatch, discovery, cleanup, aggregation)
    - Real lifespan (stale-score purge, scanner.start(), scheduler.start())
    - Real CORS middleware
    - All real routers

Per D-01: No MockScanner, no MockCircuitBreaker. If the scanner needs fut.gg
API and it is unavailable, the test fails — that is a design issue to resolve.
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
from src.server.scheduler import create_scheduler  # noqa: E402
from src.server.circuit_breaker import CircuitBreaker  # noqa: E402
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

    cb = CircuitBreaker()
    scanner = ScannerService(session_factory=session_factory, circuit_breaker=cb)
    await scanner.start()

    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.read_session_factory = session_factory  # Same pool — Postgres MVCC
    app.state.scanner = scanner
    app.state.circuit_breaker = cb

    scheduler = create_scheduler(scanner)
    app.state.scheduler = scheduler
    scheduler.start()

    # NOTE: bootstrap job intentionally omitted — see module docstring.
    # Production server adds: scheduler.add_job(scanner.run_bootstrap_and_score, ...)
    # Tests use a fresh Postgres container, so bootstrap is unnecessary and causes
    # write-pool contention that times out all concurrent API requests.

    logger.info("Test server started (scanner active, bootstrap skipped).")

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
