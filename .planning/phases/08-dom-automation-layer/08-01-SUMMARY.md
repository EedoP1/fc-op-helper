---
phase: 08-dom-automation-layer
plan: "01"
subsystem: backend-api
tags: [automation, daily-cap, price-guard, endpoints]
dependency_graph:
  requires: []
  provides:
    - DailyTransactionCount ORM model
    - GET /api/v1/automation/daily-cap
    - POST /api/v1/automation/daily-cap/increment
    - GET /api/v1/portfolio/player-price/{ea_id}
  affects:
    - src/server/main.py (router registered)
    - src/server/db.py (table auto-creation)
tech_stack:
  added: []
  patterns:
    - FastAPI APIRouter with prefix /api/v1
    - SQLAlchemy text() upsert for cross-dialect conflict resolution
    - _read_session_factory helper for read/write session routing
key_files:
  created:
    - src/server/api/automation.py
  modified:
    - src/server/models_db.py
    - src/server/main.py
    - src/server/db.py
decisions:
  - "Raw SQL text() upsert used for daily-cap increment — SQLAlchemy ORM on_conflict_do_update is dialect-specific; text() works for both PostgreSQL and SQLite"
  - "DailyTransactionCount uses unique=True on date column — one row per UTC calendar day"
  - "_DEFAULT_CAP = 500 per D-24/D-25 conservative initial threshold"
metrics:
  duration_seconds: 152
  completed_date: "2026-03-30"
  tasks_completed: 1
  tasks_total: 2
  files_created: 1
  files_modified: 3
---

# Phase 8 Plan 01: DOM Automation Layer — Foundation Summary

**One-liner:** Backend automation endpoints for daily cap enforcement (D-24/D-25/D-32) and fresh price guard (D-13/D-31), with raw SQL upsert for cross-dialect safety.

## Status

Partially complete. Task 2 (backend endpoints) executed and committed. Task 1 (DOM exploration) is a human-action checkpoint awaiting live DevTools inspection from user.

## Tasks Executed

### Task 2: Backend endpoints — daily cap tracking and fresh price lookup (COMPLETE)

**Commit:** 8acfdbd

Created `src/server/api/automation.py` with three endpoints:

- `GET /api/v1/automation/daily-cap` — returns `{count, cap, capped, date}` for today UTC. Returns defaults (count=0, cap=500, capped=false) if no row exists.
- `POST /api/v1/automation/daily-cap/increment` — upserts today's row using `INSERT ... ON CONFLICT (date) DO UPDATE SET count = count + 1`. Returns same shape as GET.
- `GET /api/v1/portfolio/player-price/{ea_id}` — returns `{ea_id, buy_price, sell_price}` from `portfolio_slots`. Returns 404 if player not in portfolio.

Added `DailyTransactionCount` ORM model to `models_db.py` with `date` (unique), `count`, and `cap` columns. Registered `automation.router` in `main.py`. Added model to `create_engine_and_tables` import in `db.py` so the table is auto-created on startup.

**Verification:** `python -c "from src.server.api.automation import router; print(len(router.routes), 'routes')"` → `3 routes`.

## Tasks Pending (Human Action Required)

### Task 1: DOM Exploration — Map automation selectors via live DevTools (AWAITING USER)

The EA Web App DOM must be inspected live. Training-data confidence for EA Web App FC26 selectors is LOW (see STATE.md blocker). The user needs to:

1. Open https://www.ea.com/ea-sports-fc/ultimate-team/web-app/ in Chrome with DevTools
2. Inspect 24 elements across Transfer Market Search, Post-Buy/Listing, Transfer List, Navigation, and Session pages
3. Write findings into `extension/src/selectors.ts` as named exports (UPPER_SNAKE_CASE)

Once complete, `selectors.ts` will have the full selector set for Phase 8 Plans 02–05 (buy, list, relist, UI automation).

## Deviations from Plan

### Auto-applied

**1. [Rule 3 - Blocking] Raw SQL text() upsert instead of ORM dialect-specific upsert**
- **Found during:** Task 2 implementation
- **Issue:** SQLAlchemy's `insert().on_conflict_do_update()` is dialect-specific (postgresql_on_conflict vs sqlite_on_conflict). The project targets both PostgreSQL production and SQLite tests.
- **Fix:** Used `text("INSERT ... ON CONFLICT (date) DO UPDATE SET count = count + 1")` which works across both dialects.
- **Files modified:** src/server/api/automation.py
- **Commit:** 8acfdbd

## Known Stubs

None. All three endpoints are fully wired — the daily cap reads/writes from `daily_transaction_count` table, and the price endpoint reads from `portfolio_slots`.

## Self-Check

- [x] `src/server/api/automation.py` exists (3 routes)
- [x] `DailyTransactionCount` in `src/server/models_db.py`
- [x] `automation.router` registered in `src/server/main.py`
- [x] Commit 8acfdbd exists
- [x] Extension tests still pass (57/57)
- [x] Pre-existing test failures unaffected (pg_advisory_xact_lock errors predate this plan)

## Self-Check: PASSED
