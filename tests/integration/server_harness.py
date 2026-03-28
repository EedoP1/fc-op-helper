"""Test server harness for integration tests.

This module runs inside a FRESH subprocess spawned by conftest.py via:

    Popen(["uvicorn", "tests.integration.server_harness:app", ...], env=env)

where `env` contains TEST_DB_PATH set by conftest before Popen is called.

Isolation model:
    Setting os.environ["DATABASE_URL"] at module load time here affects ONLY
    this server subprocess — the test process (conftest, test files) is a
    separate process and is not affected. This is why it is safe to mutate
    os.environ at the top level: there is no shared state between the two
    processes.

The harness sets DATABASE_URL BEFORE importing anything from src.server,
because src.config reads the env var at import time. Once DATABASE_URL is
set, the REAL server components are imported and wired together in a lifespan
that mirrors src.server.main.lifespan exactly, with one difference:

    The bootstrap one-shot job (scanner.run_bootstrap_and_score) is NOT added.

Why: the bootstrap downloads 1819+ players from fut.gg and holds the SQLite
write lock for minutes, causing all concurrent API requests to time out. Tests
use a pre-seeded DB with real player_records and player_scores — bootstrapping
is unnecessary and disruptive to test isolation.

Everything else is identical to the production server:
    - Real ScannerService starts and connects to fut.gg
    - Real CircuitBreaker is used
    - Real APScheduler runs (dispatch, discovery, cleanup, aggregation)
    - Real lifespan (stale-score purge, scanner.start(), scheduler.start())
    - Real CORS middleware
    - All real routers

Per D-01: No MockScanner, no MockCircuitBreaker. If the scanner needs fut.gg
API and it is unavailable, the test fails — that is a design issue to resolve.
Per D-03: scanner errors cause test failures, not silent suppression.
"""
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Must be set before ANY import from src.server (src.config reads env at import time).
_db_path = os.environ["TEST_DB_PATH"]
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_db_path}"

from src.server.db import create_engine_and_tables, create_read_engine, create_api_write_engine, create_session_factory  # noqa: E402
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
    is omitted to prevent the scanner from holding the write lock during tests.

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

    read_engine = create_read_engine()
    read_session_factory = create_session_factory(read_engine)

    api_write_engine = create_api_write_engine()
    api_write_session_factory = create_session_factory(api_write_engine)

    app.state.engine = engine
    app.state.session_factory = api_write_session_factory
    app.state.scanner_session_factory = session_factory
    app.state.read_session_factory = read_session_factory
    app.state.scanner = scanner
    app.state.circuit_breaker = cb

    scheduler = create_scheduler(scanner)
    app.state.scheduler = scheduler
    scheduler.start()

    # NOTE: bootstrap job intentionally omitted — see module docstring.
    # Production server adds: scheduler.add_job(scanner.run_bootstrap_and_score, ...)
    # Tests use a pre-seeded DB, so bootstrap is unnecessary and causes write-lock
    # contention that times out all concurrent API requests.

    logger.info("Test server started (scanner active, bootstrap skipped).")

    yield

    logger.info("Test server shutting down...")
    scheduler.shutdown(wait=False)
    await scanner.stop()
    await api_write_engine.dispose()
    await read_engine.dispose()
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
