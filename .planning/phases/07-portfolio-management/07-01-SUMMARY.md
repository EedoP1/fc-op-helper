---
phase: 07-portfolio-management
plan: 01
subsystem: api
tags: [fastapi, sqlalchemy, pydantic, portfolio, optimizer]

# Dependency graph
requires:
  - phase: 05-backend-infrastructure
    provides: PortfolioSlot model, optimize_portfolio(), existing GET /portfolio endpoint pattern

provides:
  - POST /api/v1/portfolio/generate — optimizer preview without DB writes
  - POST /api/v1/portfolio/confirm — clean-slate seed of portfolio_slots (D-06)
  - POST /api/v1/portfolio/swap-preview — stateless replacement candidates excluding specified ea_ids
  - GET /api/v1/portfolio/confirmed — load current portfolio_slots with player metadata
  - GenerateRequest, ConfirmRequest, ConfirmPlayer, SwapPreviewRequest Pydantic models

affects:
  - 07-02 (extension message types for portfolio flow use these endpoints)
  - 07-03 (automation layer calls generate, confirm, swap-preview)

# Tech tracking
tech-stack:
  added: []
  patterns:
    - Two-step generate/confirm flow: preview runs optimizer without writing, confirm clears and seeds
    - Stateless swap-preview: excludes specified ea_ids before running optimizer, no PortfolioSlot reads
    - Clean-slate confirm: DELETE all PortfolioSlot rows before INSERT to avoid unique constraint violations

key-files:
  created:
    - tests/test_portfolio_generate.py
    - tests/test_portfolio_confirm.py
    - tests/test_portfolio_swap_preview.py
    - tests/test_portfolio_confirmed.py
  modified:
    - src/server/api/portfolio.py

key-decisions:
  - "Two-step flow: generate endpoint is read-only (no DB writes); confirm does the clean-slate seed"
  - "swap-preview is stateless: does not read existing PortfolioSlot rows, caller provides excluded_ea_ids"
  - "confirmed endpoint joins PortfolioSlot with PlayerRecord for name/rating/position metadata"

patterns-established:
  - "GenerateRequest/ConfirmRequest/SwapPreviewRequest: Pydantic models with Field(gt=0) validation for budget fields"
  - "Clean-slate confirm: await session.execute(delete(PortfolioSlot)) before insert loop"

requirements-completed: [PORT-01]

# Metrics
duration: 15min
completed: 2026-03-27
---

# Phase 7 Plan 01: Portfolio Management Endpoints Summary

**Four new FastAPI portfolio endpoints — generate/confirm two-step flow, stateless swap-preview, and confirmed GET — with 22 integration tests covering all behaviors**

## Performance

- **Duration:** ~15 min
- **Started:** 2026-03-27T07:15:00Z
- **Completed:** 2026-03-27T07:30:00Z
- **Tasks:** 2
- **Files modified:** 5

## Accomplishments

- POST /portfolio/generate: runs optimizer preview without DB writes (pure read + compute)
- POST /portfolio/confirm: clears all existing PortfolioSlot rows then seeds new ones (D-06 clean slate)
- POST /portfolio/swap-preview: stateless replacement candidates, filters out excluded_ea_ids before optimizer
- GET /portfolio/confirmed: returns current portfolio_slots joined with PlayerRecord metadata
- 22 new tests + 7 existing portfolio tests = 29 total portfolio tests passing

## Task Commits

1. **Task 1: Add four portfolio endpoints** - `38ffd7a` (feat)
2. **Task 2: Add swap-preview and confirmed tests** - `4c13362` (test)

## Files Created/Modified

- `src/server/api/portfolio.py` - Added GenerateRequest, ConfirmRequest, ConfirmPlayer, SwapPreviewRequest models + 4 new endpoints
- `tests/test_portfolio_generate.py` - 6 tests for POST /generate (200, no slots, 422 variants, empty DB, fields)
- `tests/test_portfolio_confirm.py` - 3 tests for POST /confirm (seed slots, clean slate, empty list)
- `tests/test_portfolio_swap_preview.py` - 5 tests for POST /swap-preview (replacements, fields, exclusion, empty DB, validation)
- `tests/test_portfolio_confirmed.py` - 3 tests for GET /confirmed (seeded slots, empty, player field values)

## Decisions Made

- Two-step flow: generate is read-only, confirm does clean-slate seed. Separation allows user to review before committing.
- swap-preview is stateless: does not read existing PortfolioSlot rows; caller provides excluded_ea_ids. This supports draft-phase swaps (D-07/D-08) before a confirm has been made.
- confirmed endpoint joins PortfolioSlot with PlayerRecord for player metadata (name, rating, position) — slots themselves only store ea_id, buy_price, sell_price.

## Deviations from Plan

**1. [Rule 3 - Blocking] Worktree rebased to main before implementation**
- **Found during:** Task 1 setup
- **Issue:** Worktree branch was forked from old commit pre-dating src/server/ directory
- **Fix:** Ran `git rebase main` to bring worktree to current codebase state
- **Verification:** src/server/api/portfolio.py accessible after rebase
- **Impact:** No functional changes, setup-only fix

## Issues Encountered

None during implementation. Worktree rebase was required due to worktree being initialized from old commit.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- All four portfolio management endpoints live and tested
- Chrome extension (07-02/07-03) can now call generate, confirm, swap-preview, confirmed
- PORT-01 requirement fulfilled

---
*Phase: 07-portfolio-management*
*Completed: 2026-03-27*
