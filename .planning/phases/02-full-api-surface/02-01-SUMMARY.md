---
phase: 02-full-api-surface
plan: 01
subsystem: server-api
tags: [api, portfolio, optimizer, fastapi]
dependency_graph:
  requires: [01-persistent-scanner]
  provides: [portfolio-endpoint, budget-optimization-api]
  affects: [src/server/main.py]
tech_stack:
  added: []
  patterns: [PlayerProxy-bridge, latest-viable-score-subquery, budget-aware-optimization]
key_files:
  created:
    - src/server/api/portfolio.py
    - tests/test_portfolio.py
  modified:
    - src/server/main.py
decisions:
  - "_PlayerProxy bridges DB rows to optimize_portfolio() resource_id access pattern"
  - "Fresh dict construction per request to avoid optimizer mutation issues"
metrics:
  duration: "3min"
  completed: "2026-03-25"
  tasks_completed: 2
  tasks_total: 2
  test_count: 7
  test_pass: 7
---

# Phase 02 Plan 01: Portfolio Endpoint Summary

Budget-aware portfolio optimization endpoint using _PlayerProxy bridge to wire stored DB scores into the existing optimize_portfolio() engine.

## What Was Built

### GET /api/v1/portfolio?budget=X

Portfolio optimization endpoint that:
- Fetches latest viable scores per player from the DB (same subquery pattern as /players/top)
- Bridges DB rows to optimize_portfolio() via _PlayerProxy (satisfies entry["player"].resource_id)
- Returns optimized player list with budget summary (budget, budget_used, budget_remaining)
- Marks each player with is_stale flag based on STALE_THRESHOLD_HOURS
- Validates budget > 0 via FastAPI Query(gt=0), returning 422 for invalid input

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Portfolio endpoint with optimizer bridge and tests (TDD) | 7c1bd54, 9c6280b | src/server/api/portfolio.py, tests/test_portfolio.py |
| 2 | Register portfolio router and verify score history index | 9fb223e | src/server/main.py |

## Deviations from Plan

None - plan executed exactly as written.

## Verification Results

- `python -m pytest tests/test_portfolio.py -x -q` -- 7 passed
- `python -m pytest tests/ -x -q` -- 59 passed (no regressions)
- Portfolio router registered in main.py
- optimize_portfolio() bridged via _PlayerProxy
- SCAN-05 composite index confirmed in models_db.py __table_args__

## Known Stubs

None - all data flows are wired to real DB queries and the existing optimizer engine.

## Self-Check: PASSED

- All 3 key files exist on disk
- All 3 commit hashes verified in git log
