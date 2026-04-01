"""CORS integration tests: chrome-extension origin accepted, others blocked."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from httpx import AsyncClient, ASGITransport

from src.server.api.health import router as health_router
from src.server.circuit_breaker import CBState
from src.server.db import create_engine_and_tables


# ── Minimal test app with the same CORS config as main.py ────────────────────


class MockScannerService:
    """Minimal mock scanner for CORS tests."""

    def __init__(self):
        self.is_running = True
        self.last_scan_at = None

    def success_rate_1h(self) -> float:
        return 1.0

    async def count_players(self) -> int:
        return 0

    def queue_depth(self) -> int:
        return 0


class MockCircuitBreaker:
    """Minimal mock circuit breaker for CORS tests."""

    def __init__(self):
        self.state = CBState.CLOSED


@pytest.fixture
async def cors_db():
    """In-memory SQLite DB for CORS tests."""
    engine, session_factory = await create_engine_and_tables("sqlite+aiosqlite:///:memory:")
    yield session_factory
    await engine.dispose()


def make_cors_test_app(session_factory=None) -> FastAPI:
    """Create a minimal FastAPI app mirroring the CORSMiddleware config from main.py.

    Uses the health router as the target endpoint for CORS verification.
    State is wired directly (no real lifespan).
    """
    app = FastAPI(title="CORS Test App")
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"chrome-extension://.*",
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type"],
    )
    app.include_router(health_router)

    app.state.session_factory = session_factory
    app.state.read_session_factory = session_factory
    app.state.scanner = MockScannerService()
    app.state.circuit_breaker = MockCircuitBreaker()

    return app


# ── Test 1: Preflight OPTIONS from chrome-extension origin returns CORS headers ──

async def test_cors_preflight_chrome_extension():
    """OPTIONS preflight from chrome-extension origin returns 200 and correct CORS header."""
    app = make_cors_test_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.options(
            "/api/v1/health",
            headers={
                "Origin": "chrome-extension://fakeid123",
                "Access-Control-Request-Method": "GET",
            },
        )
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == "chrome-extension://fakeid123"


# ── Test 2: Preflight OPTIONS from blocked origin returns no CORS header ─────────

async def test_cors_preflight_blocked_origin():
    """OPTIONS preflight from a non-chrome-extension origin does not return CORS allow-origin."""
    app = make_cors_test_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.options(
            "/api/v1/health",
            headers={
                "Origin": "https://evil.com",
                "Access-Control-Request-Method": "GET",
            },
        )
    assert "access-control-allow-origin" not in resp.headers


# ── Test 3: Simple GET request from chrome-extension origin includes CORS header ──

async def test_cors_simple_request(cors_db):
    """GET /api/v1/health with chrome-extension Origin returns access-control-allow-origin header."""
    app = make_cors_test_app(session_factory=cors_db)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/health",
            headers={"Origin": "chrome-extension://fakeid123"},
        )
    assert resp.headers["access-control-allow-origin"] == "chrome-extension://fakeid123"
