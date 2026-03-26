---
phase: 05-backend-infrastructure
plan: 02
subsystem: api
tags: [fastapi, sqlalchemy, sqlite, aiosqlite, pydantic, action-queue, trade-records]

# Dependency graph
requires:
  - phase: 05-01
    provides: "TradeAction, TradeRecord, PortfolioSlot ORM models in models_db.py"
provides:
  - "GET /api/v1/actions/pending — stale reset, idempotent claim, lifecycle derivation (BUY/LIST/RELIST)"
  - "POST /api/v1/actions/{id}/complete — inserts TradeRecord, marks TradeAction DONE"
  - "POST /api/v1/portfolio/slots — upserts portfolio_slots so the action queue has data"
  - "src/server/api/actions.py router registered in main.py"
  - "12 integration tests covering full action queue and slot seeding lifecycle"
affects: [06-chrome-extension, 07-ea-webapp-automation]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Action lifecycle derivation from trade_records: no records→BUY, bought→LIST, expired→RELIST, sold→new BUY cycle, listed→skip"
    - "Stale reset: IN_PROGRESS actions with claimed_at older than 5 min reset to PENDING on next GET /pending"
    - "Idempotent claim: if an IN_PROGRESS action already exists (non-stale), return it immediately without creating new"

key-files:
  created:
    - src/server/api/actions.py
    - tests/test_actions.py
  modified:
    - src/server/main.py

key-decisions:
  - "Idempotent GET /pending returns existing IN_PROGRESS action rather than creating duplicate — checked before PENDING query"
  - "POST /portfolio/slots returns 200 for empty list (no-op) and 201 for non-empty list (created rows)"
  - "player_name on derived TradeActions uses 'Player {ea_id}' placeholder — PortfolioSlot has no name column; real names come from Chrome extension seeding via POST /portfolio/slots with player_name field"

patterns-established:
  - "Action derivation: query portfolio_slots, check most recent trade_record per ea_id, derive action type from outcome"
  - "Upsert pattern: SELECT then INSERT or UPDATE (no dialect-specific ON CONFLICT — works with SQLite and Postgres)"

requirements-completed: [BACK-01, BACK-02]

# Metrics
duration: 18min
completed: 2026-03-26
---

# Phase 05 Plan 02: Action Queue Router Summary

**FastAPI action queue with stale-reset GET /pending, outcome-recording POST /complete, and upsert POST /portfolio/slots — all backed by 12 integration tests covering the full BUY/LIST/RELIST lifecycle**

## Performance

- **Duration:** 18 min
- **Started:** 2026-03-26T07:00:00Z
- **Completed:** 2026-03-26T07:18:00Z
- **Tasks:** 1 (TDD — RED + GREEN)
- **Files modified:** 3

## Accomplishments

- Action queue router provides the core Chrome extension automation loop backend
- GET /pending derives actions from portfolio slot lifecycle, resets stale claims, is fully idempotent
- POST /complete atomically records trade outcomes as TradeRecord rows and marks action DONE
- POST /portfolio/slots enables seeding the portfolio (with upsert to handle repeated calls)
- Full test coverage: 12 tests, all pass, no regressions in the 103-test suite

## Task Commits

1. **Task 1 RED: Failing tests** - `5373954` (test)
2. **Task 1 GREEN: actions.py + main.py registration** - `0106691` (feat)

## Files Created/Modified

- `src/server/api/actions.py` - Action queue router with 3 endpoints + lifecycle derivation helpers
- `tests/test_actions.py` - 12 integration tests covering all endpoint behaviors
- `src/server/main.py` - Added `from src.server.api.actions import router as actions_router` + `app.include_router(actions_router)`

## Decisions Made

- **Idempotent claim order**: Check for existing IN_PROGRESS action *before* looking for PENDING — ensures a second poll from the extension returns the same action, not a new derivation.
- **Empty slots returns 200, not 201**: The route default is 201 (creation), but an empty list does nothing, so a `Response(status_code=200)` override is used for that case.
- **player_name placeholder**: `PortfolioSlot` has no `player_name` column. Derived actions use `"Player {ea_id}"`. The Chrome extension will provide the real name via POST /portfolio/slots (which has `player_name` in the `SlotEntry` model) — but that name is not stored on the slot, only on the TradeAction at derivation time. A future plan may add `player_name` to `PortfolioSlot` for persistence.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Idempotency fix — second GET /pending created duplicate action**
- **Found during:** Task 1 GREEN (test_pending_claims_action failed)
- **Issue:** The endpoint checked for PENDING actions, found none (the first call left an IN_PROGRESS action), then derived a *new* action from the portfolio slot — creating a duplicate. The extension would receive different action IDs on successive polls.
- **Fix:** Added an early check for existing IN_PROGRESS actions before the PENDING query. If one exists (non-stale), return it immediately without touching the DB.
- **Files modified:** src/server/api/actions.py
- **Verification:** test_pending_claims_action passes; test_stale_action_reset still passes (stale reset runs before the IN_PROGRESS check)
- **Committed in:** 0106691 (Task 1 GREEN commit)

---

**Total deviations:** 1 auto-fixed (Rule 1 - Bug)
**Impact on plan:** Essential correctness fix. Idempotency is explicitly required by the plan spec ("returns the same IN_PROGRESS action (idempotent claim)"). No scope creep.

## Issues Encountered

None beyond the idempotency bug documented above.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- Action queue API is complete and tested; Chrome extension can begin polling
- POST /portfolio/slots is the seed endpoint — extension must call this before GET /pending will return actions
- PortfolioSlot has no `player_name` column; player names on actions are `"Player {ea_id}"` placeholders until a future plan adds that column or the derivation is updated
- Existing 91 tests still pass — no regressions

## Self-Check: PASSED

- FOUND: src/server/api/actions.py
- FOUND: tests/test_actions.py
- FOUND: .planning/phases/05-backend-infrastructure/05-02-SUMMARY.md
- FOUND commit: 5373954 (test RED)
- FOUND commit: 0106691 (feat GREEN)

---
*Phase: 05-backend-infrastructure*
*Completed: 2026-03-26*
