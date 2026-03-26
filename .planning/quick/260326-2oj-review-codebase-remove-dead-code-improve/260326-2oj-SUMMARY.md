---
phase: quick
plan: 260326-2oj
subsystem: codebase-cleanup
tags: [dead-code, refactor, cleanup]
dependency_graph:
  requires: []
  provides: [clean-codebase, MARGINS-in-config]
  affects: [src/config.py, src/server/scorer_v2.py, src/server/listing_tracker.py, src/futgg_client.py, src/server/api/players.py, src/server/api/portfolio.py, src/server/scanner.py]
tech_stack:
  added: []
  patterns: [single-source-of-truth for MARGINS constant]
key_files:
  created: []
  modified:
    - src/config.py
    - src/server/scorer_v2.py
    - src/server/listing_tracker.py
    - src/server/api/players.py
    - src/server/api/portfolio.py
    - src/server/scanner.py
    - src/futgg_client.py
    - tests/test_api.py
    - tests/test_portfolio.py
  deleted:
    - src/scorer.py
    - tests/test_scorer.py
    - src/server/scorer_v2.py.bak
    - tests/test_scorer_v2.py.bak
decisions:
  - MARGINS constant now lives in src/config.py as the single source of truth; scorer_v2.py and listing_tracker.py import it from there
  - scan_tier kept in DB model (PlayerRecord) for potential future use, only removed from API responses
  - scorer_version removed from all API responses since it was never meaningfully populated (always null)
metrics:
  duration: 113s
  completed_date: "2026-03-26"
  tasks_completed: 2
  files_modified: 9
  files_deleted: 4
---

# Phase quick Plan 260326-2oj: Review Codebase Remove Dead Code Summary

**One-liner:** Deleted dead v1 scorer module and relocated MARGINS to config.py; stripped three vestigial API response fields (scorer_version, scan_tier) and one dead diagnostic method.

## What Was Done

### Task 1: Remove dead v1 scorer and relocate MARGINS constant

- Added `MARGINS = [40, 35, 30, 25, 20, 15, 10, 8, 5, 3]` to `src/config.py` as the single source of truth
- Updated `src/server/scorer_v2.py` to import MARGINS from `src.config` (removed `from src.scorer import MARGINS`)
- Updated `src/server/listing_tracker.py` to import MARGINS from `src.config`; removed unused `MIN_OP_OBSERVATIONS` import (it was marked `# noqa: F401 — exported constant` but was never used in this module)
- Deleted `src/scorer.py` — `score_player()` was never called from any production code path after the v2 migration; only the MARGINS constant was still needed
- Deleted `tests/test_scorer.py` — tested the deleted `score_player()` function
- Deleted `src/server/scorer_v2.py.bak` and `tests/test_scorer_v2.py.bak` (leftover backup files)

**Commit:** `7ba2571`

### Task 2: Remove dead methods, vestigial fields, and clean up scanner

- Deleted `FutGGClient.log_live_auction_fields()` static method — was a diagnostic tool from Phase 04 plan 01, superseded by the fingerprint strategy in plan 02, never called in production
- Removed `scorer_version` from `get_top_players()` and `get_player()` API responses — field was always null (no code ever set it) and defaulted to `"v1"` as a placeholder
- Removed `scan_tier` from `get_top_players()`, `get_player()`, and portfolio API responses — field is always empty string now; kept in DB model for possible future use
- Removed redundant `record.sales_per_hour = 0.0` write in `scanner.py` PlayerRecord update (already 0.0 from bootstrap, never changes)
- Updated `tests/test_api.py`: removed `scan_tier` from `top_keys` set in `test_player_detail_fields`
- Updated `tests/test_portfolio.py`: removed `scan_tier` from `required_fields` set in `test_portfolio_player_fields`

**Commit:** `1314cca`

## Verification

```
88 passed, 84 warnings in 16.09s
```

All 88 tests pass. No regressions.

Additional checks:
- `grep -r "from src.scorer import" src/` → NO matches
- `grep -r "log_live_auction_fields" src/` → NO matches
- `grep -r "scorer_version" src/server/api/` → NO matches
- No .bak files remain in the repo

## Deviations from Plan

**1. [Rule 1 - Bug] MIN_OP_OBSERVATIONS import cleanup in listing_tracker.py**
- **Found during:** Task 1
- **Issue:** The plan said to check if `MIN_OP_OBSERVATIONS` was actually used in `listing_tracker.py`. It is not — it's only used in `scorer_v2.py`. The comment claimed it was "an exported constant" which was misleading.
- **Fix:** Removed the `from src.config import MIN_OP_OBSERVATIONS` import entirely from `listing_tracker.py` (as the plan instructed after investigation)
- **Files modified:** `src/server/listing_tracker.py`
- **Commit:** `7ba2571`

## Known Stubs

None.

## Self-Check: PASSED

Files confirmed present:
- `src/config.py` — FOUND (contains MARGINS)
- `src/server/scorer_v2.py` — FOUND (imports MARGINS from src.config)
- `src/server/listing_tracker.py` — FOUND (imports MARGINS from src.config)

Files confirmed deleted:
- `src/scorer.py` — DELETED (confirmed via importlib.util.find_spec)
- `tests/test_scorer.py` — DELETED

Commits confirmed:
- `7ba2571` — Task 1
- `1314cca` — Task 2
