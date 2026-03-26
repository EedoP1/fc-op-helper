---
phase: quick
plan: 260326-vkj
subsystem: database
tags: [listing-tracker, resolve-outcomes, double-counting, sqlite]

provides:
  - "Timestamp-filtered outcome resolution preventing double-counting of completedAuctions"
affects: [listing_tracker, scanner, scorer_v2]

tech-stack:
  added: []
  patterns: ["last_resolved_at cutoff for sliding-window deduplication"]

key-files:
  created: []
  modified:
    - src/server/listing_tracker.py
    - tests/test_listing_tracker.py

key-decisions:
  - "Use MAX(resolved_at) per ea_id as cutoff -- simple, no new columns needed"
  - "Filter completed_sales in Python (list comprehension) rather than SQL -- sales are Pydantic models, not DB rows"

requirements-completed: []

duration: 1min
completed: 2026-03-26
---

# Quick Task 260326-vkj: Fix resolve_outcomes double-counting Summary

**Timestamp-filtered outcome resolution: query MAX(resolved_at) per player and exclude stale completedAuctions to prevent inflated sold rates**

## Performance

- **Duration:** 1 min
- **Tasks:** 2 (TDD: test + fix)
- **Files modified:** 2

## Accomplishments
- Fixed double-counting bug where same completedAuctions were re-counted across consecutive resolution batches
- Added regression test proving multi-batch correctness (5 sold batch 1, then 0 sold batch 2 with same sales)
- Bootstrap case preserved: first-ever resolution for a player counts all available sales

## Task Commits

Each task was committed atomically:

1. **Task 1: Add regression test for double-counting bug** - `46bc686` (test) -- TDD RED phase
2. **Task 2: Fix resolve_outcomes to filter completed_sales by timestamp** - `0af76c8` (fix) -- TDD GREEN phase

## Files Created/Modified
- `src/server/listing_tracker.py` - Added `func` import, MAX(resolved_at) query, and timestamp filter before price grouping
- `tests/test_listing_tracker.py` - Added test_resolve_outcomes_no_double_counting and test_resolve_outcomes_first_resolution_counts_all

## Decisions Made
- Used MAX(resolved_at) per ea_id as the cutoff timestamp -- no new DB columns or tables needed
- Filtered completed_sales list in Python (list comprehension) since SaleRecord objects are Pydantic models from the API, not DB rows

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None.

## Known Stubs
None.

## User Setup Required
None - no external service configuration required.

---
*Plan: 260326-vkj*
*Completed: 2026-03-26*
