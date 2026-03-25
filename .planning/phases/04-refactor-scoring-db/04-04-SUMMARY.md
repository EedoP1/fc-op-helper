---
phase: 04-refactor-scoring-db
plan: 04
subsystem: scanner, scheduler, api
tags: [scanner, scheduler, api, listing-tracking, v2-scoring, adaptive-timing, integration]

# Dependency graph
requires:
  - phase: 04-01
    provides: ListingObservation ORM model, PlayerScore v2 columns, config constants
  - phase: 04-02
    provides: record_listings, resolve_outcomes, aggregate_daily_summaries
  - phase: 04-03
    provides: score_player_v2 with D-10 formula
provides:
  - listing-tracking integrated into scanner scan_player
  - v2 scorer wired into scan cycle with v1 fallback
  - adaptive timing uses listing expiry (D-05, D-06)
  - scheduled run_scoring and run_aggregation jobs
  - API endpoints expose expected_profit_per_hour and scorer_version
  - listing observation purge in cleanup
affects:
  - src/server/scanner.py
  - src/server/scheduler.py
  - src/server/api/players.py
  - src/server/api/portfolio.py
  - tests/test_scanner.py
  - tests/test_integration.py

# Tech stack
tech_stack:
  added: []
  patterns:
    - Bootstrapping fallback: v2 scorer returns None until BOOTSTRAP_MIN_OBSERVATIONS resolved; v1 score written regardless
    - Adaptive listing expiry: youngest listing expiry drives scan interval when shorter than tier-based default
    - Proportional outcome resolution: in-scan listing tracking via record_listings/resolve_outcomes

# Key files
key_files:
  created: []
  modified:
    - src/server/scanner.py
    - src/server/scheduler.py
    - src/server/api/players.py
    - src/server/api/portfolio.py
    - tests/test_scanner.py
    - tests/test_integration.py

# Decisions
decisions:
  - Import timezone inline inside _classify_and_schedule to compare tz-aware expiresOn datetimes from API against current UTC time
  - Integration test seeds ListingObservations directly and uses make_player with num_sales=100/op_sales_pct=0.15 to ensure v1 scorer produces is_viable=True, enabling v2 fields to be written

# Metrics
metrics:
  duration: ~10min
  completed: 2026-03-25
  tasks_completed: 2
  files_modified: 6
---

# Phase 04 Plan 04: Integration Summary

**One-liner:** Full integration of listing-tracking pipeline into scanner, scheduler, and API — v2 scoring live with v1 bootstrapping fallback, adaptive listing expiry timing, and new API fields.

## What Was Built

### Task 1: Integrate listing tracking and v2 scorer into scanner.py

Modified `scan_player()` to:
- Call `record_listings()` and `resolve_outcomes()` after each API fetch when `live_auctions_raw` is present
- Attempt `score_player_v2()` before writing `PlayerScore`, using current BIN as buy_price
- Write `expected_profit_per_hour` and `scorer_version` ("v2" when v2 result available, "v1" as bootstrapping fallback) on every `PlayerScore` row

Modified `_classify_and_schedule()` to:
- Accept optional `live_auctions_raw` parameter
- Parse `expiresOn`/`expires` ISO timestamps or `remainingTime`/`timeRemaining` seconds fields
- Use the youngest listing expiry minus `LISTING_SCAN_BUFFER_SECONDS` (240s) as an alternative interval, capped at `ADAPTIVE_MIN_INTERVAL_SECONDS` (300s)
- Take the shorter of tier-based interval vs listing-based interval

Added `run_scoring()`:
- Iterates all active players
- Fetches latest viable buy_price, calls `score_player_v2`, and updates `expected_profit_per_hour`/`scorer_version` on the latest viable `PlayerScore` row

Added `run_aggregation()`:
- Iterates all active players
- Calls `aggregate_daily_summaries()` for yesterday's date per player

Extended `run_cleanup()`:
- Purges `ListingObservation` rows where `resolved_at < cutoff` (7 days)
- Purges orphaned unresolved observations where `last_seen_at < cutoff`
- Logs resolved and orphaned purge counts

Added tests:
- `test_adaptive_next_scan`: verifies listing expiry drives shorter interval than tier default
- `test_listing_purge`: verifies both resolved and orphaned observations are deleted

### Task 2: Wire scheduler jobs and update API endpoints

Updated `scheduler.py`:
- Added `scoring_v2` job: `scanner.run_scoring` every `SCORING_JOB_INTERVAL_MINUTES` (15 min)
- Added `aggregation` job: `scanner.run_aggregation` every 24 hours

Updated `api/players.py`:
- `GET /api/v1/players/top`: added `expected_profit_per_hour` and `scorer_version` per player
- `GET /api/v1/players/{ea_id}`: added `expected_profit_per_hour` and `scorer_version` to `current_score`; added `expected_profit_per_hour` to each `score_history` entry

Updated `api/portfolio.py`:
- `_build_scored_entry()`: added `expected_profit_per_hour` and `scorer_version` to the internal dict
- `GET /api/v1/portfolio`: added both fields to each player in response `data`

Added integration test `test_v2_scorer_writes_score`:
- Seeds 20 resolved `ListingObservation` rows exceeding `BOOTSTRAP_MIN_OBSERVATIONS`
- Calls `scan_player()` with mock client returning viable market data
- Asserts written `PlayerScore` has `expected_profit_per_hour != None` and `scorer_version == "v2"`

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Timezone mismatch in listing expiry comparison**
- **Found during:** Task 1, implementing `_classify_and_schedule` adaptive timing
- **Issue:** `datetime.fromisoformat(expires.replace("Z", "+00:00"))` produces a tz-aware datetime; `datetime.utcnow()` is naive — subtraction raises `TypeError` at runtime
- **Fix:** Used `datetime.now(timezone.utc)` for the comparison inside the expiry parsing block
- **Files modified:** `src/server/scanner.py`
- **Commit:** 6d8c986

**2. [Rule 1 - Bug] Integration test used make_player defaults producing non-viable v1 score**
- **Found during:** Task 2, first run of `test_v2_scorer_writes_score`
- **Issue:** `make_player` with default `num_sales=50` and `hours_of_data=10.0` gives 5 sales/hour, below the 7/hour minimum — scorer returns None, no viable PlayerScore written, test query returned None
- **Fix:** Specified `num_sales=100`, `op_sales_pct=0.15`, `hours_of_data=10.0` to produce 10 sales/hr and 15 OP sales — both above minimums
- **Files modified:** `tests/test_integration.py`
- **Commit:** 1da3925

## Known Stubs

None — all fields are wired from real data sources:
- `expected_profit_per_hour`: written by `score_player_v2` during scan and scoring job
- `scorer_version`: always populated ("v1" or "v2") during every scan
- API endpoints pass through the DB values with no placeholder fallbacks

## Self-Check

Files exist:
- [x] `src/server/scanner.py` — modified
- [x] `src/server/scheduler.py` — modified
- [x] `src/server/api/players.py` — modified
- [x] `src/server/api/portfolio.py` — modified
- [x] `tests/test_scanner.py` — modified
- [x] `tests/test_integration.py` — modified

Commits exist:
- [x] 6d8c986 — feat(04-04): integrate listing tracking and v2 scorer into scanner
- [x] 1da3925 — feat(04-04): wire scheduler jobs and add expected_profit_per_hour to API

Test results: 107 passed, 0 failed
