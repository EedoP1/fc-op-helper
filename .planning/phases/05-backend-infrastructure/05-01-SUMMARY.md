---
phase: 05-backend-infrastructure
plan: 01
subsystem: database
tags: [sqlalchemy, fastapi, cors, orm, sqlite, chrome-extension]

# Dependency graph
requires: []
provides:
  - TradeAction ORM model for buy/list/relist action queue
  - TradeRecord ORM model for lifecycle event history
  - PortfolioSlot ORM model for confirmed active portfolio
  - CORSMiddleware with chrome-extension:// regex origin support
  - CORS integration tests (preflight, blocked-origin, simple-request)
affects: [05-02, 05-03, actions-router, profit-router, player-swap, chrome-extension]

# Tech tracking
tech-stack:
  added: [fastapi.middleware.cors.CORSMiddleware]
  patterns:
    - ORM models use Mapped[] + mapped_column() with explicit Index in __table_args__
    - CORSMiddleware uses allow_origin_regex (not allow_origins wildcard) for chrome-extension:// scheme support
    - CORS tests use minimal test app with MockScannerService/MockCircuitBreaker (not real lifespan)

key-files:
  created:
    - tests/test_cors.py
  modified:
    - src/server/models_db.py
    - src/server/db.py
    - src/server/main.py

key-decisions:
  - "Use allow_origin_regex for CORS — allow_origins wildcard does not cover chrome-extension:// scheme"
  - "PortfolioSlot uses unique=True on column (no separate __table_args__ index) to avoid duplicate index creation"

patterns-established:
  - "CORS tests: create minimal test app mirroring production middleware config, not import main.py app directly"

requirements-completed: [BACK-03, BACK-05]

# Metrics
duration: 3min
completed: 2026-03-26
---

# Phase 05 Plan 01: ORM Models and CORS Middleware Summary

**TradeAction/TradeRecord/PortfolioSlot ORM models added to models_db.py, CORSMiddleware registered with chrome-extension:// regex, and 3 CORS integration tests passing**

## Performance

- **Duration:** ~3 min
- **Started:** 2026-03-26T06:48:07Z
- **Completed:** 2026-03-26T06:50:59Z
- **Tasks:** 3
- **Files modified:** 4

## Accomplishments

- Three new ORM models (TradeAction, TradeRecord, PortfolioSlot) added with composite indexes and correct nullable fields
- CORSMiddleware registered with `allow_origin_regex=r"chrome-extension://.*"` — enables all downstream extension-to-backend calls
- CORS integration tests verify chrome-extension preflight accepted, evil.com preflight blocked, and simple GET request includes allow-origin header
- All 91 existing tests continue to pass

## Task Commits

1. **Task 1: Add TradeAction, TradeRecord, PortfolioSlot ORM models** - `8f07119` (feat)
2. **Task 2: Add CORSMiddleware for chrome-extension origin** - `0287394` (feat)
3. **Task 3: Create CORS integration tests** - `336118a` (test)

## Files Created/Modified

- `src/server/models_db.py` - Added PortfolioSlot, TradeAction, TradeRecord classes at end of file
- `src/server/db.py` - Extended create_all import to include all three new models
- `src/server/main.py` - Added CORSMiddleware import and registration after app = FastAPI(...)
- `tests/test_cors.py` - New: 3 CORS integration tests using minimal test app

## Decisions Made

- Used `allow_origin_regex` for CORS — `allow_origins=["*"]` does not cover `chrome-extension://` scheme (per research pitfall 1 in plan)
- `PortfolioSlot.ea_id` uses `unique=True, index=True` on the column definition only; removed `__table_args__` Index entry that duplicated it and caused `OperationalError: index already exists` in tests

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed duplicate index on PortfolioSlot.ea_id**
- **Found during:** Task 3 (CORS integration tests — running full test suite)
- **Issue:** Plan specified `unique=True, index=True` on the `ea_id` column AND `Index("ix_portfolio_slots_ea_id", "ea_id")` in `__table_args__`. SQLAlchemy creates a unique index from the column constraint; the explicit `__table_args__` entry tried to create a second index with the same name, causing `OperationalError: index ix_portfolio_slots_ea_id already exists` in the in-memory test DB.
- **Fix:** Removed the `__table_args__` tuple from `PortfolioSlot` — `unique=True, index=True` on the column is sufficient.
- **Files modified:** `src/server/models_db.py`
- **Verification:** `python -m pytest tests/ -x` — all 91 tests pass
- **Committed in:** `336118a` (Task 3 commit)

---

**Total deviations:** 1 auto-fixed (Rule 1 — bug)
**Impact on plan:** Required fix — would have caused table creation to fail in production startup. No scope creep.

## Issues Encountered

- `test_cors_simple_request` initially used `src.server.main.app` directly. The real app's health endpoint accesses `app.state.scanner`, which isn't set without the lifespan running. Resolved by creating a minimal test app mirroring the CORS config (same pattern as `test_api.py`).

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- TradeAction, TradeRecord, PortfolioSlot tables will be auto-created on next server startup via `create_all`
- CORSMiddleware is live for all routes — no further configuration needed for chrome-extension requests
- Plan 02 (actions router) and Plan 03 (profit router) can import and use these models immediately

---
*Phase: 05-backend-infrastructure*
*Completed: 2026-03-26*
