---
gsd_state_version: 1.0
milestone: v1.0
milestone_name: milestone
status: Milestone complete
stopped_at: Completed 04-04-PLAN.md
last_updated: "2026-03-25T21:47:50.571Z"
last_activity: 2026-03-25
progress:
  total_phases: 4
  completed_phases: 4
  total_plans: 10
  completed_plans: 10
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-03-25)

**Core value:** Always-fresh, data-driven OP sell recommendations — the server continuously scores every player in the 11k–200k range so you never miss a profitable opportunity.
**Current focus:** Phase 04 — refactor-scoring-db

## Current Position

Phase: 04
Plan: Not started

## Performance Metrics

**Velocity:**

- Total plans completed: 0
- Average duration: —
- Total execution time: 0 hours

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| - | - | - | - |

**Recent Trend:**

- Last 5 plans: —
- Trend: —

*Updated after each plan completion*
| Phase 01-persistent-scanner P01 | 3 | 2 tasks | 9 files |
| Phase 01-persistent-scanner P02 | 10min | 2 tasks | 3 files |
| Phase 01-persistent-scanner P03 | 4min | 1 tasks | 5 files |
| Phase 02 P01 | 3min | 2 tasks | 3 files |
| Phase 02 P02 | 8min | 2 tasks | 5 files |
| Phase 03-cli-as-api-client P01 | 103s | 1 tasks | 2 files |
| Phase 04-refactor-scoring-db P01 | 8min | 2 tasks | 6 files |
| Phase 04-refactor-scoring-db P02 | 3min | 1 tasks | 2 files |
| Phase 04 P03 | 156s | 1 tasks | 2 files |
| Phase 04 P04 | 10min | 2 tasks | 6 files |

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- Architecture: Backend as single source of truth — scorer/optimizer unchanged, plugged into APScheduler
- Stack: FastAPI 0.135 + APScheduler 3.11 (pinned <4.0) + SQLAlchemy 2.0 async + aiosqlite
- Concurrency: SQLite WAL mode, `async_sessionmaker(expire_on_commit=False)` required
- [Phase 01-persistent-scanner]: expire_on_commit=False on all async session factories prevents MissingGreenlet at scale
- [Phase 01-persistent-scanner]: WAL mode enabled via sync_engine event listener on connect for reliability across all connections
- [Phase 01-persistent-scanner]: CircuitBreaker is_open is a property for lazy OPEN->HALF_OPEN transition on check
- [Phase 01-persistent-scanner]: Tier classification checks last_expected_profit >= TIER_PROFIT_THRESHOLD first, so high-value low-volume players get hot priority (API-04)
- [Phase 01-persistent-scanner]: Tenacity retry wraps API call as inner async _fetch_with_retry() decorated with @retry to enable function-level retry behavior
- [Phase 01-persistent-scanner]: ASGITransport does not trigger FastAPI lifespan — tests wire app.state directly on the app object before requests
- [Phase 01-persistent-scanner]: Latest viable score per player uses func.max(scored_at) subquery filtered to is_viable=True — ensures history is preserved while only current score is served
- [Phase 02]: _PlayerProxy bridges DB rows to optimize_portfolio() resource_id access pattern
- [Phase 02]: Trend direction uses 0.005 efficiency delta threshold to avoid noise from minor fluctuations
- [Phase 02]: Adaptive scheduling uses offset(1) to skip current scan score when comparing to previous
- [Phase 03-cli-as-api-client]: CLI becomes pure API client — DEFAULT_SERVER_URL=localhost:8000, --budget and --player mutually exclusive, display_results adapted for portfolio API fields (no sell_price/net_profit)
- [Phase 04-refactor-scoring-db]: ListingObservation.fingerprint is String(128) — actual fingerprint strategy deferred to plan 02 based on available liveAuctions fields
- [Phase 04-refactor-scoring-db]: live_auctions_raw coexists with live_auction_prices in PlayerMarketData to maintain backward compatibility with existing scorer
- [Phase 04-refactor-scoring-db]: Fingerprint uses tradeId when present (ea_id:tradeId); falls back to (ea_id:buyNowPrice:10min-bucket) for entries without tradeId
- [Phase 04-refactor-scoring-db]: Proportional outcome resolution: min(matching_sales, n_listings) sold, rest expired — handles same-price ambiguity without 1-to-1 matching
- [Phase 04]: scorer_v2 evaluates all MARGINS tiers and picks max expected_profit_per_hour — OP sell rate uses only OP cohort denominator (op_sold + op_expired), not all resolved listings
- [Phase 04]: Import timezone inline inside _classify_and_schedule to compare tz-aware expiresOn datetimes from API against current UTC time
- [260326-2aw]: resolve_outcomes gates on expected_expiry_at IS NOT NULL AND < now — prevents false resolution of listings rotating off API window
- [260326-2aw]: SCAN_INTERVAL_SECONDS=300 replaces adaptive expiry-based scheduling; _classify_and_schedule deleted entirely

### Pending Todos

None yet.

### Roadmap Evolution

- Phase 4 added: refactor scoring + db

### Blockers/Concerns

- fut.gg has no published rate limits; 24/7 scanning behavior is untested. Monitor `scan_success_rate` in Phase 1 first week and tune throttling empirically.
- `async_sessionmaker(expire_on_commit=False)` must be applied to all session factories in Phase 1 — omitting this causes subtle `MissingGreenlet` errors at scale.

### Quick Tasks Completed

| # | Description | Date | Commit | Directory |
|---|-------------|------|--------|-----------|
| 260325-pki | Speed up initial server heating to under 5 minutes for all player data loading | 2026-03-25 | 065d4ac | [260325-pki-speed-up-initial-server-heating-to-under](./quick/260325-pki-speed-up-initial-server-heating-to-under/) |
| 260325-tu9 | Retain raw market data per player for 1 month (snapshots, sales, price history) | 2026-03-25 | 4229a2d | [260325-tu9-retain-raw-market-data-per-player-for-1-](./quick/260325-tu9-retain-raw-market-data-per-player-for-1-/) |
| 260325-v54 | Improve scorer to pick margin maximizing expected_profit instead of greedy-highest | 2026-03-25 | ec95d2c | [260325-v54-improve-scorer-to-produce-more-optimal-p](./quick/260325-v54-improve-scorer-to-produce-more-optimal-p/) |
| 260326-00a | Make v2 scorer drive portfolio selection — rank by expected_profit_per_hour | 2026-03-26 | c716e68 | [260326-00a-make-v2-scorer-drive-portfolio-selection](./quick/260326-00a-make-v2-scorer-drive-portfolio-selection/) |
| 260326-0d0 | Remove tier-based scanning, replace with listing-expiry scheduling | 2026-03-26 | 85f8dba | [260326-0d0-remove-tier-based-scanning-replace-with-](./quick/260326-0d0-remove-tier-based-scanning-replace-with-/) |
| 260326-0lr | Remove v1 scorer entirely — v2-only pipeline with simplified optimizer and portfolio | 2026-03-26 | 1de1740 | [260326-0lr-remove-v1-scorer-entirely-v2-only-with-b](./quick/260326-0lr-remove-v1-scorer-entirely-v2-only-with-b/) |
| 260326-12g | Update CLI labels for v2 metrics (EP/hr, Sell%, OP Sales) and purge stale v1 scores at startup | 2026-03-26 | e857c19 | [260326-12g-update-cli-labels-for-v2-metrics-and-pur](./quick/260326-12g-update-cli-labels-for-v2-metrics-and-pur/) |
| 260326-1r2 | Fix scorer_v2 formula to net_profit * sell_rate, remove op_sales_per_hour | 2026-03-26 | 416bc9d | [260326-1r2-fix-scorer-v2-formula-to-net-profit-sell](./quick/260326-1r2-fix-scorer-v2-formula-to-net-profit-sell/) |
| 260326-2aw | Fix listing outcome resolution with expiry gating + fixed 5-min scan interval | 2026-03-26 | 2a25124 | [260326-2aw-fix-listing-outcome-resolution-with-expi](./quick/260326-2aw-fix-listing-outcome-resolution-with-expi/) |
| 260326-2oj | Review codebase, remove dead code: delete v1 scorer, relocate MARGINS to config, strip vestigial API fields | 2026-03-26 | 1314cca | [260326-2oj-review-codebase-remove-dead-code-improve](./quick/260326-2oj-review-codebase-remove-dead-code-improve/) |

## Session Continuity

Last activity: 2026-03-26
Last session: 2026-03-26T00:00:00.000Z
Stopped at: Completed quick task 260326-2oj
Resume file: None
