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
    - Runs Alembic migrations to head.

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

    # Run Alembic migrations using the existing engine (no new event loop)
    from alembic.config import Config as AlembicConfig
    from alembic import command
    from alembic.runtime.environment import EnvironmentContext
    from alembic.script import ScriptDirectory
    import os

    alembic_ini = os.path.join(os.path.dirname(__file__), "..", "..", "alembic.ini")
    alembic_cfg = AlembicConfig(alembic_ini)

    async with engine.begin() as conn:
        def _run_upgrade(connection):
            alembic_cfg.attributes["connection"] = connection
            command.upgrade(alembic_cfg, "head")
        await conn.run_sync(_run_upgrade)
    logger.info("Alembic migrations applied.")

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
    allow_origin_regex=r"(chrome-extension://.*|http://localhost(:\d+)?)",
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


# ── Dashboard ────────────────────────────────────────────────────────────────
import pathlib
from fastapi.responses import HTMLResponse

_DASHBOARD_PATH = pathlib.Path(__file__).resolve().parent.parent.parent / "dashboard.html"


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    """Serve the dashboard UI."""
    return _DASHBOARD_PATH.read_text(encoding="utf-8")
