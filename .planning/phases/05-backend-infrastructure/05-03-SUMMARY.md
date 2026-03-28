---
phase: 05-backend-infrastructure
plan: 03
subsystem: api
tags: [fastapi, sqlalchemy, profit-tracking, portfolio-management]

# Dependency graph
requires:
  - phase: 05-backend-infrastructure plan 01
    provides: TradeRecord, TradeAction, PortfolioSlot DB models
  - phase: 05-backend-infrastructure plan 02
    provides: portfolio GET endpoint and _build_scored_entry helper
provides:
  - GET /api/v1/profit/summary endpoint with EA tax-adjusted totals and per-player breakdown
  - DELETE /api/v1/portfolio/{ea_id} endpoint that removes slot, cancels actions, returns replacements
affects: [phase-06-extension, phase-07-automation]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - func.sum with case() for conditional aggregation in SQLAlchemy
    - Per-player aggregation in Python after DB group-by for name resolution
    - Optimizer re-use for replacement suggestions after portfolio swap

key-files:
  created:
    - src/server/api/profit.py
    - tests/test_profit.py
    - tests/test_portfolio_swap.py
  modified:
    - src/server/api/portfolio.py
    - src/server/main.py

key-decisions:
  - "Profit EA tax (5%) applied in Python after SQL group-by, not in SQL — simpler and avoids float precision issues in case() expressions"
  - "DELETE /portfolio/{ea_id} preserves TradeRecords — only removes active slot and cancels pending actions"
  - "Replacements via optimize_portfolio() on freed_budget — reuses existing optimizer without new logic"
  - "Player name fallback to 'Player {ea_id}' when PlayerRecord not found — profit records may predate discovery"

patterns-established:
  - "Conditional aggregation pattern: func.sum(case((Model.field == value, Model.price), else_=0))"
  - "Separate name-lookup query after aggregate query — avoids complex GROUP BY with JOIN"
  - "DELETE endpoints return structured response with removed_id, freed resources, and replacement suggestions"

requirements-completed: [BACK-04, BACK-06]

# Metrics
duration: 3min
completed: 2026-03-26
---

# Phase 05 Plan 03: Profit Summary and Portfolio Swap Summary

**GET /api/v1/profit/summary with EA-tax-adjusted coin tracking, and DELETE /api/v1/portfolio/{ea_id} that cancels pending actions and returns optimizer-powered replacement suggestions**

## Performance

- **Duration:** 3 min
- **Started:** 2026-03-26T06:58:05Z
- **Completed:** 2026-03-26T07:01:21Z
- **Tasks:** 2
- **Files modified:** 5

## Accomplishments

- Profit summary endpoint aggregates TradeRecord rows per ea_id using SQLAlchemy `func.sum` + `case()`, applies 5% EA tax to sold prices, and returns totals plus per-player breakdown with player names
- Portfolio swap DELETE endpoint atomically cancels PENDING/IN_PROGRESS TradeActions, deletes PortfolioSlot, runs optimizer on freed budget, and returns replacement candidates — without touching TradeRecords
- 10 new TDD tests (5 profit, 5 swap) all passing; full suite of 113 tests clean

## Task Commits

Each task was committed atomically:

1. **Task 1: Create profit summary endpoint with tests** - `b7bf74a` (feat)
2. **Task 2: Add player swap DELETE endpoint to portfolio router with tests** - `7fda49b` (feat)

**Plan metadata:** `d946b02` (docs: complete plan)

_Note: Both tasks used TDD (RED then GREEN)_

## Files Created/Modified

- `src/server/api/profit.py` - GET /api/v1/profit/summary with per-player coin tracking
- `src/server/api/portfolio.py` - Extended with DELETE /api/v1/portfolio/{ea_id} swap endpoint
- `src/server/main.py` - Registered profit_router
- `tests/test_profit.py` - 5 tests: empty, buy-only, full-cycle, per-player, multi-cycle
- `tests/test_portfolio_swap.py` - 5 tests: slot removal, action cancellation, trade record preservation, replacements, 404

## Decisions Made

- EA tax applied in Python after SQL aggregation (not in SQL `case()`) — cleaner and avoids float multiplication inside SQL
- Player name lookup is a separate query after the group-by aggregate — avoids complex LEFT JOIN + GROUP BY interaction
- DELETE preserves TradeRecords intentionally — they are the profit history ledger, not mutable state
- `optimize_portfolio()` reused directly for replacements — builds fresh `_build_scored_entry` dicts to avoid mutation contamination

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- BACK-04 (profit tracking API) complete — extension can query `/api/v1/profit/summary` for performance display
- BACK-06 (portfolio swap) complete — extension can DELETE a player and receive replacement suggestions
- Phase 05 backend infrastructure all 3 plans complete — backend is ready for Phase 06 Chrome extension
- No blockers

---
*Phase: 05-backend-infrastructure*
*Completed: 2026-03-26*
