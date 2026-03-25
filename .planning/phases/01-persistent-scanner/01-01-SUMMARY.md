---
phase: 01-persistent-scanner
plan: 01
subsystem: database
tags: [sqlalchemy, aiosqlite, sqlite, fastapi, apscheduler, tenacity, circuit-breaker, orm]

# Dependency graph
requires: []
provides:
  - Async SQLAlchemy engine with WAL mode on SQLite
  - PlayerRecord and PlayerScore ORM models
  - CircuitBreaker state machine (CLOSED/OPEN/HALF_OPEN)
  - Scanner configuration constants (SCAN_INTERVAL_*, CB_*, DATABASE_URL, TIER_PROFIT_THRESHOLD)
  - pytest.ini configured for async tests (asyncio_mode=auto)
affects:
  - 01-02-PLAN (scanner service uses DB layer and CircuitBreaker)
  - 01-03-PLAN (API endpoints use PlayerScore ORM model)

# Tech tracking
tech-stack:
  added:
    - fastapi==0.135.2
    - uvicorn==0.42.0
    - apscheduler==3.11.2
    - sqlalchemy==2.0.48
    - aiosqlite==0.22.1
    - tenacity==9.1.4
  patterns:
    - "Async SQLAlchemy with async_sessionmaker(expire_on_commit=False) to prevent MissingGreenlet errors"
    - "WAL mode enabled via sync_engine event listener on connect"
    - "Circuit breaker as pure state machine — no decorators, callers decide when to check is_open"
    - "TDD: RED (failing tests) -> GREEN (implementation) per task"

key-files:
  created:
    - src/server/__init__.py
    - src/server/db.py
    - src/server/models_db.py
    - src/server/circuit_breaker.py
    - tests/test_db.py
    - tests/test_circuit_breaker.py
    - pytest.ini
  modified:
    - src/config.py
    - requirements.txt

key-decisions:
  - "expire_on_commit=False on all async session factories — required to prevent MissingGreenlet at scale"
  - "WAL mode via event listener on connect, not via SQL after engine creation — reliable across connections"
  - "Circuit breaker is_open is a property (not method) — transitioning OPEN->HALF_OPEN happens lazily on check"
  - "Test WAL mode uses file-based SQLite (tmp dir), not in-memory — WAL requires on-disk storage"

patterns-established:
  - "Pattern 1: async_sessionmaker with expire_on_commit=False for all session factories in this project"
  - "Pattern 2: Scanner constants in src/config.py with UPPER_CASE names following existing conventions"
  - "Pattern 3: ORM Base imported from src.server.db (single source of truth for metadata)"

requirements-completed: [SCAN-02, SCAN-04]

# Metrics
duration: 3min
completed: 2026-03-25
---

# Phase 01 Plan 01: Persistence Foundation and Resilience Layer Summary

**SQLAlchemy async ORM with WAL mode (PlayerRecord + PlayerScore tables), CircuitBreaker state machine, and scanner config constants — the persistence foundation for the 24/7 scanner service**

## Performance

- **Duration:** 3 min
- **Started:** 2026-03-25T15:50:36Z
- **Completed:** 2026-03-25T15:53:21Z
- **Tasks:** 2
- **Files modified:** 9

## Accomplishments

- Async SQLite database layer with WAL mode, session factory, and ORM table creation via `create_engine_and_tables()`
- `PlayerRecord` and `PlayerScore` ORM models covering all fields needed for scan scheduling and score storage
- `CircuitBreaker` state machine with CLOSED/OPEN/HALF_OPEN transitions, configurable thresholds, and `time.monotonic`-based recovery timeout
- All new scanner config constants added to `src/config.py` including `SCAN_INTERVAL_HOT`, `CB_FAILURE_THRESHOLD`, `DATABASE_URL`, and `TIER_PROFIT_THRESHOLD`
- 13 tests pass across both test files (5 DB tests + 8 circuit breaker tests)

## Task Commits

Each task was committed atomically:

1. **Task 1: Install dependencies, add config constants, create DB layer, ORM models, and DB tests** - `71ec227` (feat)
2. **Task 2: Create and test the circuit breaker state machine** - `3a8d304` (feat)

**Plan metadata:** _(final docs commit hash — recorded after this file is committed)_

_Note: TDD tasks follow RED -> GREEN commit sequence within each task_

## Files Created/Modified

- `src/server/__init__.py` - Empty package init for server subpackage
- `src/server/db.py` - Async engine creation, WAL mode, session factory, `create_engine_and_tables()`, `get_session()`
- `src/server/models_db.py` - `PlayerRecord` (players table) and `PlayerScore` (player_scores table) ORM definitions
- `src/server/circuit_breaker.py` - `CBState` enum and `CircuitBreaker` class with state machine logic
- `tests/test_db.py` - 5 tests: engine creation, WAL mode, PlayerRecord CRUD, PlayerScore CRUD, session yield
- `tests/test_circuit_breaker.py` - 8 tests covering all state transitions and edge cases
- `pytest.ini` - `asyncio_mode = auto` for async test support
- `src/config.py` - Extended with 14 new scanner constants
- `requirements.txt` - Updated with pinned versions of all new dependencies

## Decisions Made

- `expire_on_commit=False` on all async session factories — required to prevent `MissingGreenlet` at scale (per STATE.md blocker note)
- WAL mode enabled via `event.listens_for(engine.sync_engine, "connect")` — reliable across all connections, not just the first
- `is_open` implemented as a property (not method) — lazy OPEN->HALF_OPEN transition happens only when caller checks
- WAL mode test uses file-based SQLite in a `tempfile.TemporaryDirectory()` — in-memory SQLite does not support WAL

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required. Database file (`op_seller.db`) is created at runtime.

## Next Phase Readiness

- DB layer ready: `create_engine_and_tables()` accepts any URL (in-memory for tests, file-based for production)
- CircuitBreaker ready for wrapping `FutGGClient` calls in the scanner service (plan 01-02)
- All config constants available via `from src.config import ...`
- Scanner service (01-02) can import `PlayerRecord`, `PlayerScore`, `CircuitBreaker`, and session factory immediately

---
*Phase: 01-persistent-scanner*
*Completed: 2026-03-25*

## Self-Check: PASSED

- src/server/db.py: FOUND
- src/server/models_db.py: FOUND
- src/server/circuit_breaker.py: FOUND
- tests/test_db.py: FOUND
- tests/test_circuit_breaker.py: FOUND
- pytest.ini: FOUND
- 01-01-SUMMARY.md: FOUND
- Task commit 71ec227: FOUND
- Task commit 3a8d304: FOUND
- 13 tests pass
