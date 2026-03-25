---
gsd_state_version: 1.0
milestone: v1.0
milestone_name: milestone
status: Ready to plan
stopped_at: Phase 3 context gathered
last_updated: "2026-03-25T19:56:11.321Z"
last_activity: "2026-03-25 - Completed quick task 260325-tu9: Retain raw market data per player for 1 month instead of overwriting on each scan"
progress:
  total_phases: 3
  completed_phases: 2
  total_plans: 5
  completed_plans: 5
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-03-25)

**Core value:** Always-fresh, data-driven OP sell recommendations — the server continuously scores every player in the 11k–200k range so you never miss a profitable opportunity.
**Current focus:** Phase 02 — full-api-surface

## Current Position

Phase: 3
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

### Pending Todos

None yet.

### Blockers/Concerns

- fut.gg has no published rate limits; 24/7 scanning behavior is untested. Monitor `scan_success_rate` in Phase 1 first week and tune throttling empirically.
- `async_sessionmaker(expire_on_commit=False)` must be applied to all session factories in Phase 1 — omitting this causes subtle `MissingGreenlet` errors at scale.

### Quick Tasks Completed

| # | Description | Date | Commit | Directory |
|---|-------------|------|--------|-----------|
| 260325-pki | Speed up initial server heating to under 5 minutes for all player data loading | 2026-03-25 | 065d4ac | [260325-pki-speed-up-initial-server-heating-to-under](./quick/260325-pki-speed-up-initial-server-heating-to-under/) |
| 260325-tu9 | Retain raw market data per player for 1 month (snapshots, sales, price history) | 2026-03-25 | 4229a2d | [260325-tu9-retain-raw-market-data-per-player-for-1-](./quick/260325-tu9-retain-raw-market-data-per-player-for-1-/) |

## Session Continuity

Last activity: 2026-03-25 - Completed quick task 260325-tu9: Retain raw market data per player for 1 month instead of overwriting on each scan
Last session: 2026-03-25T19:56:11.314Z
Stopped at: Phase 3 context gathered
Resume file: .planning/phases/03-cli-as-api-client/03-CONTEXT.md
