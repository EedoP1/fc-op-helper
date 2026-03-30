"""FastAPI application with API-only lifespan (D-06).

Scanner runs as a separate process via scanner_main.py (D-05).
API process has no scanner, no scheduler, no FutGGClient.
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.server.db import create_engine_and_tables
from src.server.api.players import router as players_router
from src.server.api.health import router as health_router
from src.server.api.portfolio import router as portfolio_router
from src.server.api.actions import router as actions_router
from src.server.api.profit import router as profit_router
from src.server.api.portfolio_status import router as status_router
from src.server.api import automation

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage startup and shutdown of DB pool only.

    Scanner and scheduler run in a separate process (scanner_main.py).
    API process only needs the DB connection pool.

    Startup:
    - Creates DB engine and tables (idempotent).
    - Runs inline migrations if needed.
    - Purges stale v1 scores.

    Shutdown:
    - Disposes the DB engine.
    """
    # -- Startup ---------------------------------------------------------------
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    logger.info("Starting OP Seller API server...")

    engine, session_factory = await create_engine_and_tables()

    # Migrate: add is_leftover column to portfolio_slots if missing
    async with engine.begin() as conn:
        from sqlalchemy import text, inspect

        def _check_column(connection):
            insp = inspect(connection)
            cols = [c["name"] for c in insp.get_columns("portfolio_slots")]
            return "is_leftover" in cols

        has_col = await conn.run_sync(_check_column)
        if not has_col:
            await conn.execute(text(
                "ALTER TABLE portfolio_slots ADD COLUMN is_leftover BOOLEAN DEFAULT FALSE NOT NULL"
            ))
            logger.info("Migrated portfolio_slots: added is_leftover column")

    # Purge stale v1 scores that lack expected_profit_per_hour
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

    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.read_session_factory = session_factory  # Same pool -- Postgres MVCC

    logger.info("API server started (scanner runs as separate process).")

    yield

    # -- Shutdown --------------------------------------------------------------
    logger.info("Shutting down API server...")
    await engine.dispose()
    logger.info("API server stopped.")


app = FastAPI(title="OP Seller", lifespan=lifespan)
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
app.include_router(automation.router)
