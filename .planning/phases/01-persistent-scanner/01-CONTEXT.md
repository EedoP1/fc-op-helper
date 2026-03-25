# Phase 1: Persistent Scanner - Context

**Gathered:** 2026-03-25
**Status:** Ready for planning

<domain>
## Phase Boundary

The backend runs continuously, scans all players in the 11k-200k price range on a priority-based schedule, stores scores and market data in SQLite, and serves a live top-players feed via REST API. Includes rate-limit resilience with circuit breaker, a health endpoint, and staleness tracking. No portfolio optimization endpoint (Phase 2), no CLI refactor (Phase 3).

</domain>

<decisions>
## Implementation Decisions

### Top-Players Endpoint
- **D-01:** Default return count is 100 players (matches TARGET_PLAYER_COUNT)
- **D-02:** Basic filtering: `price_min`, `price_max`, and `limit` query params
- **D-03:** Pagination via `offset`/`limit` params
- **D-04:** Response includes score summary per player: name, ea_id, price, margin_pct, op_ratio, expected_profit, efficiency, last_scanned

### Scan Priority Logic
- **D-05:** Priority determined by listing activity (live listing count, recent sales volume)
- **D-06:** 3 tiers: Hot (every 30 min), Normal (every 1 hr), Cold (every 2-3 hrs)
- **D-07:** Tier reassignment happens on every scan (check listing activity, reclassify if needed)
- **D-08:** Bootstrap on first startup: full discovery scan across 11k-200k range, score everyone once, then switch to priority-based scheduling

### Failure Transparency
- **D-09:** Health endpoint + log files for failure visibility (no push notifications)
- **D-10:** Health endpoint reports: scanner running/stopped, scan success rate (last hour), circuit breaker state (closed/open/half-open), last scan timestamp, players in DB, queue depth
- **D-11:** When circuit breaker is open, API serves stale data with `is_stale: true` flag and `last_scanned` timestamp — consumers decide how to handle

### Staleness Threshold
- **D-12:** Player data considered stale after 4 hours without a fresh scan
- **D-13:** Stale players included in results with `is_stale` flag (not excluded) — consumers decide
- **D-14:** Target pool coverage: 80% of discovered players should have fresh (non-stale) data at any given time

### Claude's Discretion
- Database schema design (tables, indexes, relationships)
- Rate limit backoff parameters and jitter implementation
- Circuit breaker implementation details (half-open probe count, reset timing)
- APScheduler job configuration and queue management
- Server startup/shutdown lifecycle hooks
- Project layout for new server code (`src/server/` or similar)

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Requirements
- `.planning/REQUIREMENTS.md` -- SCAN-01, SCAN-02, SCAN-04, API-03, API-04 are the requirements for this phase

### Architecture
- `.planning/codebase/ARCHITECTURE.md` -- Existing layered architecture, MarketDataClient protocol, data flow
- `.planning/codebase/STRUCTURE.md` -- Current file layout, import patterns, where to add new code
- `.planning/codebase/STACK.md` -- Current dependencies (httpx, pydantic, click, rich)

### Existing Code
- `src/protocols.py` -- MarketDataClient protocol definition (scanner must use this or extend it)
- `src/scorer.py` -- Existing score_player() function to reuse unchanged
- `src/optimizer.py` -- Existing optimize_portfolio() function (used by Phase 2, not Phase 1)
- `src/models.py` -- Pydantic models (Player, PlayerMarketData, SaleRecord, PricePoint) to extend for DB
- `src/config.py` -- Constants (EA_TAX_RATE, TARGET_PLAYER_COUNT) to extend for scanner config
- `src/futgg_client.py` -- FutGGClient implementation (scanner will use this for data fetching)

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `FutGGClient`: Full fut.gg API client with discovery, market data, price history — scanner wraps this
- `score_player()`: Pure scoring function, takes PlayerMarketData, returns scored dict — reuse as-is
- `MarketDataClient` protocol: Defines contract for data sources — scanner uses FutGGClient through this
- `Player`, `PlayerMarketData`, `SaleRecord`, `PricePoint` models: Extend for DB persistence

### Established Patterns
- Protocol-based abstraction for data sources (structural subtyping)
- Async/await with httpx and semaphore-based concurrency control
- Pydantic models for all data structures
- Logger per module: `logger = logging.getLogger(__name__)`
- Config constants in `src/config.py` as UPPER_CASE module-level variables

### Integration Points
- Scanner orchestrates: FutGGClient.discover_players() -> FutGGClient.get_batch_market_data() -> score_player()
- New FastAPI app wraps existing pipeline, adds persistence layer between fetch and score
- SQLAlchemy models will mirror/extend existing Pydantic models
- APScheduler manages scan scheduling, replaces the one-shot asyncio.run() pattern

</code_context>

<specifics>
## Specific Ideas

No specific requirements -- open to standard approaches for all implementation details.

</specifics>

<deferred>
## Deferred Ideas

None -- discussion stayed within phase scope.

</deferred>

---

*Phase: 01-persistent-scanner*
*Context gathered: 2026-03-25*
