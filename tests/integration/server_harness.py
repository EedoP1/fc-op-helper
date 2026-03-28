"""Test server harness for integration tests.

Standalone FastAPI app that uvicorn can import and serve.
Uses a simplified lifespan — DB only, no scanner/scheduler.
The TEST_DB_PATH environment variable must be set before uvicorn starts.
"""
import os
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


# ── Mock objects ─────────────────────────────────────────────────────────────

class MockScanner:
    """Minimal scanner stub satisfying health endpoint attribute access."""

    is_running: bool = True
    last_scan_at = None

    def success_rate_1h(self) -> float:
        """Return a mock 100% success rate."""
        return 1.0

    async def count_players(self) -> int:
        """Return mock player count."""
        return 0

    def queue_depth(self) -> int:
        """Return mock queue depth."""
        return 0


class MockCircuitBreaker:
    """Minimal circuit breaker stub satisfying health endpoint attribute access."""

    def __init__(self):
        self.state = type("State", (), {"value": "CLOSED"})()


# ── Lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage test server startup and shutdown.

    Startup:
    - Reads TEST_DB_PATH from environment (required — conftest sets it before uvicorn starts).
    - Creates DB engine and all tables against the test SQLite file.
    - Wires session_factory, read_session_factory, scanner, circuit_breaker.

    Shutdown:
    - Disposes the engine.
    """
    db_path = os.environ["TEST_DB_PATH"]
    db_url = f"sqlite+aiosqlite:///{db_path}"
    engine, session_factory = await create_engine_and_tables(db_url)

    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.read_session_factory = session_factory  # same factory for tests
    app.state.scanner = MockScanner()
    app.state.circuit_breaker = MockCircuitBreaker()

    yield

    await engine.dispose()


# ── App ───────────────────────────────────────────────────────────────────────

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
