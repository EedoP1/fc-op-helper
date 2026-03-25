---
phase: 02-full-api-surface
plan: 02
subsystem: api
tags: [fastapi, player-detail, adaptive-scheduling, trend-indicators, score-history]

# Dependency graph
requires:
  - phase: 01-persistent-scanner
    provides: "PlayerRecord/PlayerScore models, scanner service, API router"
  - phase: 02-full-api-surface/01
    provides: "Base API endpoints (top players, health)"
provides:
  - "GET /api/v1/players/{ea_id} endpoint with full player detail, score history, and trend"
  - "Adaptive scan scheduling with activity-based interval adjustment"
  - "_compute_trend() helper for trend direction calculation"
  - "ADAPTIVE_CHANGE_THRESHOLD and ADAPTIVE_MIN_INTERVAL_SECONDS constants"
affects: [chrome-extension, cli-client, dashboard]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Trend computation from newest-first score history (direction, price_change, efficiency_change)"
    - "Adaptive scheduling via offset(1) previous score lookup in _classify_and_schedule"
    - "Score history limited to 24 entries regardless of is_viable"

key-files:
  created: []
  modified:
    - src/server/api/players.py
    - src/server/scanner.py
    - src/config.py
    - tests/test_api.py
    - tests/test_scanner.py

key-decisions:
  - "Trend direction thresholds: >0.005 efficiency delta = up, <-0.005 = down, otherwise stable"
  - "Adaptive scheduling uses offset(1) to skip current scan's score when comparing to previous"
  - "Score history returns all entries (viable and non-viable) while trend uses only viable scores"

patterns-established:
  - "Player detail endpoint pattern: record lookup -> latest viable score -> history query -> trend computation"
  - "Adaptive scheduling pattern: compare current vs previous sales_per_hour, halve interval if delta >= 25%"

requirements-completed: [API-02, SCAN-03]

# Metrics
duration: 8min
completed: 2026-03-25
---

# Phase 02 Plan 02: Player Detail and Adaptive Scheduling Summary

**GET /api/v1/players/{ea_id} with 24-entry score history and trend indicators, plus adaptive scan scheduling that halves intervals when sales activity changes 25%+**

## Performance

- **Duration:** 8 min
- **Started:** 2026-03-25T19:06:09Z
- **Completed:** 2026-03-25T19:14:05Z
- **Tasks:** 2
- **Files modified:** 5

## Accomplishments
- Player detail endpoint returns full metadata, current viable score, 24-entry score history, and trend indicators (direction/price_change/efficiency_change)
- 404 with "Player not found" for unknown ea_ids
- Adaptive scheduling compares current sales_per_hour to previous scan; 25%+ change halves the tier interval (floor 5 min)
- All 62 tests pass (13 API tests + 15 scanner tests + existing)

## Task Commits

Each task was committed atomically:

1. **Task 1: Player detail endpoint (RED)** - `f3a7185` (test)
2. **Task 1: Player detail endpoint (GREEN)** - `a7fd040` (feat)
3. **Task 2: Adaptive scheduling (RED)** - `2de62b8` (test)
4. **Task 2: Adaptive scheduling (GREEN)** - `cc132fb` (feat)

## Files Created/Modified
- `src/server/api/players.py` - Added GET /api/v1/players/{ea_id} endpoint, _compute_trend() helper, HTTPException import
- `src/server/scanner.py` - Added adaptive scheduling logic in _classify_and_schedule with previous score comparison
- `src/config.py` - Added ADAPTIVE_CHANGE_THRESHOLD (0.25) and ADAPTIVE_MIN_INTERVAL_SECONDS (300)
- `tests/test_api.py` - 6 new player detail tests with seeded_app_with_history fixture
- `tests/test_scanner.py` - 4 new adaptive scheduling tests

## Decisions Made
- Trend direction uses 0.005 efficiency delta threshold (not zero) to avoid noise
- Score history includes non-viable entries for completeness; trend computation filters to viable only
- Adaptive scheduling uses offset(1).limit(1) to skip the current scan's just-written score row

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed test data for adaptive scheduling shortens interval test**
- **Found during:** Task 2 (GREEN phase)
- **Issue:** Test passed sales_per_hour=15.0 which promoted player to "hot" tier instead of "normal", and originally passed wrong parameter value to _classify_and_schedule
- **Fix:** Changed current sales_per_hour to 13.0 (30% delta, still above 25% threshold) keeping player in normal tier
- **Files modified:** tests/test_scanner.py
- **Verification:** All 15 scanner tests pass
- **Committed in:** cc132fb (Task 2 GREEN commit)

---

**Total deviations:** 1 auto-fixed (1 bug in test data)
**Impact on plan:** Minor test data correction. No scope creep.

## Issues Encountered
None beyond the test data fix noted above.

## Known Stubs
None - all endpoints return real data from database queries.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Player detail API complete, ready for Chrome extension or CLI client consumption
- Adaptive scheduling active, will tune thresholds based on production observation
- Budget portfolio endpoint (if planned) can follow the same pattern

## Self-Check: PASSED

All files exist, all commits verified, all key patterns confirmed in source files.

---
*Phase: 02-full-api-surface*
*Completed: 2026-03-25*
