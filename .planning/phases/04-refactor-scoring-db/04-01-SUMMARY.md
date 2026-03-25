---
phase: 04-refactor-scoring-db
plan: 01
subsystem: database
tags: [sqlalchemy, sqlite, pydantic, orm, listing-tracking]

# Dependency graph
requires:
  - phase: 03-cli-as-api-client
    provides: CLI as API client, server as single source of truth
provides:
  - ListingObservation ORM model with fingerprint unique index
  - DailyListingSummary ORM model with composite index on (ea_id, date, margin_pct)
  - PlayerScore extended with expected_profit_per_hour and scorer_version nullable columns
  - PlayerMarketData.live_auctions_raw field with all raw liveAuctions entry data
  - Config constants for listing tracking (LISTING_RETENTION_DAYS, BOOTSTRAP_MIN_OBSERVATIONS, etc.)
affects: [04-02, 04-03, 04-04, listing-tracker, scorer-v2]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - New ORM models added after existing models in models_db.py — consistent pattern for table registration
    - Nullable columns use `Mapped[type | None] = mapped_column(..., nullable=True)` pattern
    - Composite indexes declared in `__table_args__` tuple
    - Raw API data preserved alongside parsed data in PlayerMarketData for downstream use

key-files:
  created: []
  modified:
    - src/server/models_db.py
    - src/server/db.py
    - src/config.py
    - src/models.py
    - src/futgg_client.py
    - tests/test_db.py

key-decisions:
  - "ListingObservation.fingerprint is String(128) with unique=True — fingerprint strategy TBD in plan 02 based on available liveAuctions fields"
  - "live_auctions_raw coexists with live_auction_prices — old scorer continues using live_auction_prices, listing tracker uses raw"
  - "scorer_version is String(5) to hold v1 or v2 as values"

patterns-established:
  - "TDD: test import of new models first (RED), then implement (GREEN) — ensures test coverage for new ORM models"
  - "log_live_auction_fields() static method pattern for field discovery without side effects"

requirements-completed: [SCAN-P4-01]

# Metrics
duration: 8min
completed: 2026-03-25
---

# Phase 4 Plan 01: Foundation — DB Tables, Config, and Raw API Data Summary

**ListingObservation and DailyListingSummary ORM tables, PlayerScore extended with scorer_version/expected_profit_per_hour, and FutGGClient returning raw liveAuctions dicts for fingerprinting**

## Performance

- **Duration:** ~8 min
- **Started:** 2026-03-25T21:15:00Z
- **Completed:** 2026-03-25T21:23:08Z
- **Tasks:** 2
- **Files modified:** 6

## Accomplishments
- Created ListingObservation ORM model with fingerprint unique index, ea_id index, and first_seen_at index
- Created DailyListingSummary ORM model with composite index on (ea_id, date, margin_pct)
- Extended PlayerScore with nullable expected_profit_per_hour (Float) and scorer_version (String) columns
- Added raw liveAuctions pass-through to FutGGClient and PlayerMarketData for downstream fingerprinting
- Added 6 listing tracking config constants to src/config.py
- All 91 tests pass with zero regressions

## Task Commits

Each task was committed atomically:

1. **TDD RED: Failing tests for new models** - `9dd163b` (test)
2. **Task 1: Add ListingObservation, DailyListingSummary ORM models and extend PlayerScore** - `e019f68` (feat)
3. **Task 2: Extend FutGGClient to return raw liveAuctions data** - `832f8b4` (feat)

_Note: TDD task 1 has two commits (test RED then feat GREEN)_

## Files Created/Modified
- `src/server/models_db.py` - Added ListingObservation, DailyListingSummary classes; extended PlayerScore with 2 new nullable columns
- `src/server/db.py` - Updated create_engine_and_tables import to include new models
- `src/config.py` - Added LISTING_RETENTION_DAYS, BOOTSTRAP_MIN_OBSERVATIONS, LISTING_SCAN_BUFFER_SECONDS, SCORING_JOB_INTERVAL_MINUTES, AGGREGATION_HOUR_UTC, MIN_OP_OBSERVATIONS constants
- `src/models.py` - Added live_auctions_raw: list[dict] field to PlayerMarketData
- `src/futgg_client.py` - Updated get_player_market_data() to populate live_auctions_raw; added log_live_auction_fields() static method
- `tests/test_db.py` - Added 7 new tests (tests 6-11) covering all new models, columns, and CRUD

## Decisions Made
- ListingObservation.fingerprint is String(128) — the actual fingerprinting strategy (which fields to hash) is deferred to plan 02, which will inspect live liveAuctions field availability via log_live_auction_fields()
- live_auctions_raw coexists with live_auction_prices to maintain backward compatibility with the existing scorer
- scorer_version is String(5) to hold "v1" or "v2" label values

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- All contracts for plans 02-04 are now in place: ORM models, config constants, and raw API data
- Plan 02 (listing tracker) can now implement ListingObservation upsert using fingerprints
- Plan 03 (scorer v2) can read DailyListingSummary and write scorer_version/expected_profit_per_hour to PlayerScore
- Plan 04 (integration) can tie everything together with the scheduler

---
*Phase: 04-refactor-scoring-db*
*Completed: 2026-03-25*

## Self-Check: PASSED

- FOUND: src/server/models_db.py
- FOUND: src/server/db.py
- FOUND: src/config.py
- FOUND: src/models.py
- FOUND: src/futgg_client.py
- FOUND: .planning/phases/04-refactor-scoring-db/04-01-SUMMARY.md
- Commit 9dd163b verified (test RED)
- Commit e019f68 verified (feat GREEN Task 1)
- Commit 832f8b4 verified (feat Task 2)
