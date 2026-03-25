---
phase: 01-persistent-scanner
plan: 03
subsystem: api
tags: [fastapi, sqlalchemy, httpx, aiosqlite, apscheduler]

# Dependency graph
requires:
  - phase: 01-persistent-scanner plan 01
    provides: DB layer (create_engine_and_tables), PlayerRecord, PlayerScore ORM models, CircuitBreaker
  - phase: 01-persistent-scanner plan 02
    provides: ScannerService (start/stop/count_players/success_rate_1h/queue_depth), create_scheduler

provides:
  - FastAPI app (src/server/main.py) with lifespan managing DB, scanner, scheduler, and bootstrap
  - GET /api/v1/players/top endpoint with efficiency-ranked OP sell data (D-01..D-04, D-11..D-13)
  - GET /api/v1/health endpoint with full operational status (D-09, D-10)
  - 7 integration tests covering all endpoint behaviors

affects: [chrome-extension, phase-02, future-api-consumers]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "app.state pattern for dependency injection in FastAPI endpoints"
    - "ASGITransport with direct app.state wiring for async endpoint tests (lifespan not triggered by ASGITransport)"
    - "Subquery for latest-score-per-player using func.max(scored_at)"

key-files:
  created:
    - src/server/main.py
    - src/server/api/__init__.py
    - src/server/api/players.py
    - src/server/api/health.py
    - tests/test_api.py

key-decisions:
  - "ASGITransport does not trigger FastAPI lifespan — tests wire app.state directly on the app object before requests"
  - "Latest score per player via subquery on func.max(scored_at) filtered to is_viable=True only"

patterns-established:
  - "Test apps for async FastAPI tests: make_test_app() sets app.state directly, skips lifespan entirely"
  - "All endpoints access session_factory/scanner/circuit_breaker via request.app.state"

requirements-completed: [API-03, SCAN-01, SCAN-02]

# Metrics
duration: 4min
completed: 2026-03-25
---

# Phase 1 Plan 3: FastAPI App with Lifespan and API Endpoints Summary

**FastAPI server with lifespan-managed DB/scanner/scheduler, GET /api/v1/players/top returning efficiency-ranked OP sell data with price filtering and staleness flags, and GET /api/v1/health with full operational status**

## Performance

- **Duration:** 4 min
- **Started:** 2026-03-25T16:03:29Z
- **Completed:** 2026-03-25T16:07:27Z
- **Tasks:** 1 (TDD: RED + GREEN)
- **Files modified:** 5

## Accomplishments
- FastAPI app (`src/server/main.py`) with async lifespan that creates DB, starts scanner/scheduler, and queues bootstrap as a one-shot job
- `GET /api/v1/players/top` returns players ranked by efficiency with price filtering (D-02), pagination (D-03), all D-04 fields, and staleness flags (D-11/D-12/D-13)
- `GET /api/v1/health` returns scanner_status, circuit_breaker, scan_success_rate_1h, last_scan_at, players_in_db, queue_depth (D-09/D-10)
- 7 integration tests all passing, full test suite (31 tests) all passing

## Task Commits

Each task was committed atomically:

1. **Task 1: Create API tests, then implement FastAPI app with lifespan and endpoints** - `f4748ad` (feat)

**Plan metadata:** (to be added)

_Note: TDD task — RED (failing tests) verified before GREEN (implementation)_

## Files Created/Modified
- `src/server/main.py` - FastAPI app with lifespan managing DB engine, ScannerService, APScheduler
- `src/server/api/__init__.py` - Empty package init for api sub-package
- `src/server/api/players.py` - GET /api/v1/players/top with SQLAlchemy subquery for latest viable score per player
- `src/server/api/health.py` - GET /api/v1/health returning all D-10 operational fields
- `tests/test_api.py` - 7 integration tests using httpx ASGITransport with direct app.state wiring

## Decisions Made
- `ASGITransport` in httpx 0.28 does not trigger the FastAPI lifespan protocol, so test fixtures set `app.state` directly on the app object (not via lifespan). This is the correct pattern for async ASGI tests without a running server.
- Latest viable score per player uses a `func.max(scored_at)` subquery filtered to `is_viable=True` — ensures stale/non-viable scores are excluded while preserving history.

## Deviations from Plan

**1. [Rule 1 - Bug] Fixed test fixture: lifespan not triggered by ASGITransport**
- **Found during:** Task 1 (GREEN phase — first test run)
- **Issue:** Plan's `@asynccontextmanager async def test_lifespan` pattern didn't work because `ASGITransport` doesn't invoke the lifespan ASGI protocol. `app.state.session_factory` was never set, causing `AttributeError` on first request.
- **Fix:** Replaced the lifespan-based fixture with direct `app.state` wiring in `make_test_app()` before any requests are made. No real lifespan is used in tests.
- **Files modified:** `tests/test_api.py`
- **Verification:** All 7 tests pass after fix
- **Committed in:** f4748ad (Task 1 commit)

---

**Total deviations:** 1 auto-fixed (Rule 1 - Bug)
**Impact on plan:** Fix was necessary for tests to work at all. Same functional behavior, different test scaffolding pattern. No scope creep.

## Issues Encountered
- httpx `ASGITransport` + Starlette 1.0.0 + FastAPI 0.135 does not invoke the lifespan protocol on the test app. The plan's suggested lifespan-override approach required adjustment. Resolved by direct state wiring.

## Known Stubs
None — all fields are wired to real DB queries and scanner state.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Phase 1 is functionally complete: `uvicorn src.server.main:app` starts the server, creates DB, starts scanner/scheduler, and launches bootstrap discovery.
- Both API endpoints are operational and tested.
- Full test suite (DB + circuit breaker + scanner + API) — 31 tests all passing.
- Ready to run the server locally and monitor via GET /api/v1/health.

---
*Phase: 01-persistent-scanner*
*Completed: 2026-03-25*
