"""FastAPI application with lifespan managing scanner and DB."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.server.db import create_engine_and_tables
from src.server.scanner import ScannerService
from src.server.scheduler import create_scheduler
from src.server.circuit_breaker import CircuitBreaker
from src.server.api.players import router as players_router
from src.server.api.health import router as health_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage startup and shutdown of DB, scanner, and scheduler.

    Startup:
    - Creates DB engine and tables.
    - Creates CircuitBreaker and ScannerService.
    - Starts the FutGG HTTP client via scanner.start().
    - Creates and starts APScheduler.
    - Queues bootstrap discovery as a one-shot job (non-blocking).

    Shutdown:
    - Shuts down scheduler without waiting for running jobs.
    - Stops the scanner (closes HTTP client).
    - Disposes the DB engine.
    """
    # ── Startup ────────────────────────────────────────────────────────────────
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    logger.info("Starting OP Seller server...")

    engine, session_factory = await create_engine_and_tables()
    cb = CircuitBreaker()
    scanner = ScannerService(session_factory=session_factory, circuit_breaker=cb)
    await scanner.start()

    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.scanner = scanner
    app.state.circuit_breaker = cb

    scheduler = create_scheduler(scanner)
    app.state.scheduler = scheduler
    scheduler.start()

    # Launch bootstrap + initial scoring as a one-shot job (per Research pitfall 5 — non-blocking)
    scheduler.add_job(scanner.run_bootstrap_and_score, id="bootstrap", replace_existing=True)
    logger.info("Server started. Bootstrap + initial scoring queued.")

    yield

    # ── Shutdown ───────────────────────────────────────────────────────────────
    logger.info("Shutting down...")
    scheduler.shutdown(wait=False)
    await scanner.stop()
    await engine.dispose()
    logger.info("Server stopped.")


app = FastAPI(title="OP Seller", lifespan=lifespan)
app.include_router(players_router)
app.include_router(health_router)
