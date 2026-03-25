---
phase: quick
plan: 260326-0lr
subsystem: scoring-pipeline
tags: [v2-scorer, cleanup, refactor, dead-code-removal]
dependency_graph:
  requires: [scorer_v2.py, listing_tracker]
  provides: [v2-only scan pipeline, simplified optimizer, simplified portfolio API]
  affects: [scanner.py, scheduler.py, optimizer.py, portfolio.py]
tech_stack:
  added: []
  patterns: [v2-only scoring, expected_profit_per_hour as sole ranking metric]
key_files:
  modified:
    - src/server/scanner.py
    - src/server/scheduler.py
    - src/server/api/portfolio.py
    - src/optimizer.py
    - src/config.py
    - tests/test_scanner.py
    - tests/test_optimizer.py
    - tests/test_portfolio.py
    - tests/test_integration.py
decisions:
  - "scorer_version column stays in DB but is no longer written — omitting the field from both PlayerScore construction paths"
  - "optimizer uses `or 0` guard on expected_profit_per_hour to handle None DB values from pre-existing rows"
metrics:
  duration: ~10 minutes
  completed: 2026-03-26
  tasks_completed: 3
  files_modified: 9
---

# Quick Task 260326-0lr: Remove V1 Scorer Entirely (V2-Only Pipeline)

**One-liner:** Removed v1 score_player from scan pipeline, deleted run_scoring() method and scoring_v2 scheduler job, rewrote PlayerScore construction from v2 result dict, simplified optimizer and portfolio endpoint to use expected_profit_per_hour exclusively.

## What Was Done

Eliminated all v1 scorer remnants from the server-side scan pipeline. The codebase previously ran v1 and v2 scorers in parallel, mixing results. Now every scan writes PlayerScore entirely from `score_player_v2()` output, or writes `is_viable=False` with zeroed fields when v2 returns None (insufficient observations).

### Task 1: Scanner and Scheduler (commit f5501c4)

- Removed `from src.scorer import score_player` import from scanner.py
- Removed `score_result = score_player(...)` call and all `score_result` references
- Rewrote PlayerScore construction: v2_result present -> all fields from v2 dict; v2_result None -> is_viable=False with zeroed fields
- Deleted `run_scoring()` method (49 lines) — the standalone v2 re-scoring job that was now redundant since every `scan_player()` call already runs v2
- Removed `scoring_v2` APScheduler job from scheduler.py
- Removed `SCORING_JOB_INTERVAL_MINUTES = 15` from config.py

### Task 2: Optimizer and Portfolio (commit d39afd0)

- Removed v1/v2 branching from `optimize_portfolio()` — now uses `expected_profit_per_hour or 0` for all players
- Updated module and function docstrings to remove v1 fallback description
- Removed `scorer_version`, `ranking_metric`, `scorer_mix` from portfolio response
- Added early error return when `scored_list` is empty: descriptive message explaining the system needs scan observations
- Applied Rule 1 fix: `s.get("expected_profit_per_hour") or 0` instead of `s.get(..., 0)` to handle `None` values stored in DB from pre-existing PlayerScore rows

### Task 3: Tests (commit 1de1740)

- Replaced `@patch("src.server.scanner.score_player")` with `@patch("src.server.scanner.score_player_v2", new_callable=AsyncMock)` in three snapshot tests
- Updated `test_scan_player_writes_score` to assert v2-mapped fields (buy_price=20000, expected_profit_per_hour=560.0)
- Removed `test_v1_fallback_when_no_epph` — no fallback exists
- Renamed `test_mixed_v1_v2_portfolio` to `test_portfolio_with_varied_efficiency`
- Renamed `test_v2_player_ranks_by_expected_profit_per_hour` to `test_ranks_by_expected_profit_per_hour`
- Removed `test_portfolio_returns_scorer_mix` and `test_portfolio_v2_scored_player`
- Updated `test_portfolio_empty_db` to assert `"error"` key in response
- Removed `scorer_version == 'v2'` assertion from `test_integration.py::test_v2_scorer_writes_score`

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] None guard in optimizer for pre-existing DB rows**
- **Found during:** Task 3 (test_portfolio_returns_200 failing)
- **Issue:** `s.get("expected_profit_per_hour", 0)` returns `None` when the DB column is explicitly set to NULL (SQLAlchemy returns None, not the default). The seeded test PlayerScore rows have no `expected_profit_per_hour`, so the column value is None — not missing from the dict.
- **Fix:** Changed `s.get("expected_profit_per_hour", 0)` to `s.get("expected_profit_per_hour") or 0`
- **Files modified:** src/optimizer.py
- **Commit:** d39afd0 (included in Task 2 commit)

**2. [Rule 1 - Bug] Integration test asserted scorer_version='v2'**
- **Found during:** Task 3 (full test suite run)
- **Issue:** `tests/test_integration.py::test_v2_scorer_writes_score` asserted `score_row.scorer_version == 'v2'`, but the plan explicitly removes scorer_version writes from scan_player().
- **Fix:** Removed the `scorer_version` assertion from the integration test
- **Files modified:** tests/test_integration.py
- **Commit:** 1de1740

## Commits

| Hash | Message |
|------|---------|
| f5501c4 | refactor(quick-260326-0lr): remove v1 scorer from scanner and scheduler |
| d39afd0 | refactor(quick-260326-0lr): simplify optimizer and portfolio to v2-only |
| 1de1740 | test(quick-260326-0lr): update tests for v2-only scoring pipeline |

## Verification Results

All 102 tests pass.

```
grep -r "score_player\b" src/server/scanner.py  → no matches
grep -r "scorer_version" src/server/api/portfolio.py src/optimizer.py  → no matches
grep -r "run_scoring" src/server/scanner.py src/server/scheduler.py  → no matches
grep -r "SCORING_JOB_INTERVAL_MINUTES" src/  → no matches
```

## Self-Check: PASSED

- src/server/scanner.py — modified, v2-only
- src/server/scheduler.py — modified, scoring_v2 job removed
- src/optimizer.py — modified, v2-only
- src/server/api/portfolio.py — modified, scorer_version/ranking_metric/scorer_mix removed
- src/config.py — modified, SCORING_JOB_INTERVAL_MINUTES removed
- commits f5501c4, d39afd0, 1de1740 all present in git log
