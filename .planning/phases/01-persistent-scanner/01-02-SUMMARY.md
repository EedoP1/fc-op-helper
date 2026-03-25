---
phase: 01-persistent-scanner
plan: 02
subsystem: scanner
tags: [apscheduler, tenacity, sqlalchemy, circuit-breaker, tdd]

# Dependency graph
requires:
  - phase: 01-persistent-scanner-plan-01
    provides: DB layer (create_engine_and_tables, async_sessionmaker), PlayerRecord/PlayerScore ORM models, CircuitBreaker state machine

provides:
  - ScannerService with bootstrap discovery, per-player scan with retry, tier classification, dispatch
  - APScheduler scheduler with 30s dispatch and 1h discovery jobs
  - 11 scanner unit tests covering all behaviors including value-based tier promotion (API-04)

affects: [01-persistent-scanner-plan-03, api-layer, fastapi-app]

# Tech tracking
tech-stack:
  added: [tenacity 9.1.4 (retry/exponential backoff), APScheduler 3.11.2 (async interval jobs)]
  patterns:
    - Tenacity retry with retry_if_exception_type, stop_after_attempt(3), wait_exponential_jitter
    - Circuit breaker checked before every API call, success/failure recorded after
    - Tier classification with value-based promotion (TIER_PROFIT_THRESHOLD) per API-04
    - sqlite_insert with on_conflict_do_update for upsert pattern
    - asyncio.Semaphore for concurrency-limited dispatch

key-files:
  created:
    - src/server/scanner.py
    - src/server/scheduler.py
    - tests/test_scanner.py
  modified: []

key-decisions:
  - "Tier classification checks last_expected_profit >= TIER_PROFIT_THRESHOLD FIRST before activity metrics, so high-value low-volume players get hot priority (API-04)"
  - "run_bootstrap and run_discovery use sqlite_insert.on_conflict_do_update for idempotent upserts"
  - "dispatch_scans queries due players per session then releases session before launching tasks — avoids long-held sessions during concurrent scans"

patterns-established:
  - "Tenacity retry wrapper: inner async def _fetch_with_retry() decorated with @retry, called from scan_player"
  - "Circuit breaker pattern: is_open check -> API call -> record_success/failure in try/except"
  - "Upsert pattern: sqlite_insert().on_conflict_do_update(index_elements=['ea_id'], set_=dict(...))"

requirements-completed: [SCAN-01, SCAN-04, API-04]

# Metrics
duration: ~10min
completed: 2026-03-25
---

# Phase 01 Plan 02: Scanner Service and Scheduler Summary

**ScannerService with tenacity retry, circuit breaker integration, and value-based hot-tier promotion (API-04), driven by an APScheduler 30s dispatch + 1h discovery cycle**

## Performance

- **Duration:** ~10 min
- **Started:** 2026-03-25T15:50:00Z
- **Completed:** 2026-03-25T16:00:05Z
- **Tasks:** 2
- **Files modified:** 3

## Accomplishments

- ScannerService implements full scan lifecycle: bootstrap, per-player scan with retry, tier classification, dispatch, and metrics
- Tier classification correctly promotes high-value players to "hot" regardless of activity (TIER_PROFIT_THRESHOLD=500), per API-04
- Scheduler wires two APScheduler jobs: scan_dispatch every 30s and discovery every 1h with max_instances=1 and coalesce=True
- 11 tests pass covering all 10 planned behaviors plus success_rate_1h empty case

## Task Commits

Each task was committed atomically:

1. **Task 1: Scanner tests (RED)** - `50d6c1b` (test)
2. **Task 1: ScannerService implementation (GREEN)** - `ed0230c` (feat)
3. **Task 2: Scheduler module** - `b88848b` (feat)

_Note: TDD task split into RED (test) and GREEN (implementation) commits_

## Files Created/Modified

- `src/server/scanner.py` - ScannerService: discovery, scan, tier classification, dispatch, metrics
- `src/server/scheduler.py` - APScheduler create_scheduler() with dispatch and discovery jobs
- `tests/test_scanner.py` - 11 unit tests for scanner behaviors

## Decisions Made

- Tier classification evaluates profit threshold BEFORE activity metrics — high-value players are always hot regardless of listing_count or sales_per_hour (per API-04 requirement)
- Tenacity retry wraps the API call as an inner async function decorated with @retry, then called via `await _fetch_with_retry()` from scan_player
- DB session released BEFORE launching concurrent scan tasks in dispatch_scans to prevent long-held connections

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

- Worktree was branched before Plan 01 commits — merged main to get server files (db.py, models_db.py, circuit_breaker.py, config constants). Not a code issue, just git state.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- ScannerService and scheduler are ready for Plan 03 (FastAPI server integration)
- ScannerService exposes start/stop, run_bootstrap, dispatch_scans, count_players, queue_depth, success_rate_1h for the API health endpoint
- No blockers

---
*Phase: 01-persistent-scanner*
*Completed: 2026-03-25*

## Self-Check: PASSED

- scanner.py: FOUND
- scheduler.py: FOUND
- test_scanner.py: FOUND
- SUMMARY.md: FOUND
- Commit 50d6c1b: FOUND
- Commit ed0230c: FOUND
- Commit b88848b: FOUND
