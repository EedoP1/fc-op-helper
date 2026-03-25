# Phase 2: Full API Surface - Context

**Gathered:** 2026-03-25
**Status:** Ready for planning

<domain>
## Phase Boundary

The backend exposes a complete REST API covering budget-aware portfolio optimization and per-player drill-down, backed by accumulating historical score data and adaptive per-player scan cadence. No CLI refactor (Phase 3), no Chrome extension (v2), no new scanning infrastructure (Phase 1 complete).

</domain>

<decisions>
## Implementation Decisions

### Portfolio Endpoint (API-01)
- **D-01:** `GET /api/v1/portfolio?budget=X` computes portfolio on-demand per request — no caching (budget varies per request, single-user tool)
- **D-02:** Query latest viable scores from DB, build lightweight dicts matching `optimize_portfolio()` input format, run optimizer in-process
- **D-03:** Reuse `optimize_portfolio()` from `src/optimizer.py` unchanged — bridge DB rows to the dict format it expects (needs `Player`-like object with `resource_id` for dedup)
- **D-04:** Response mirrors the top-players endpoint structure but with portfolio-specific fields: total budget, budget used, budget remaining, player count, and the optimized player list

### Player Detail Endpoint (API-02)
- **D-05:** `GET /api/v1/players/{ea_id}` returns full score breakdown: all PlayerScore fields + PlayerRecord metadata
- **D-06:** Include recent score history — last 24 entries (covers ~24 hours for hot players, ~48-72 for cold) for trend visualization
- **D-07:** Do NOT include raw sales data (not stored in DB — comes from fut.gg live API, not persisted)
- **D-08:** Include computed trend indicators: score direction (up/down/stable), price change over last N scores

### Adaptive Scan Scheduling (SCAN-03)
- **D-09:** Enhance existing tier system (hot/normal/cold from Phase 1) with per-player interval adjustment based on listing activity patterns
- **D-10:** If a player's listing_count or sales_per_hour changed significantly since last scan, shorten the interval; if stable, keep tier default
- **D-11:** Keep hot/normal/cold as base tiers — adaptive scheduling adjusts within tier bounds, not across them

### Score History Retention (SCAN-05)
- **D-12:** Keep all PlayerScore history indefinitely — SQLite can handle the volume for a single-user tool
- **D-13:** Add DB index on (ea_id, scored_at DESC) for efficient time-range queries on score history
- **D-14:** Score history rows already accumulate from Phase 1 scanner (one row per scan) — no schema change needed for accumulation itself

### Claude's Discretion
- Portfolio endpoint query optimization (how to efficiently load scores for optimization)
- Player detail response serialization format (Pydantic response model vs dict)
- Trend calculation algorithm (simple delta vs moving average)
- Adaptive scheduling formula (how to compute interval adjustment from activity delta)
- Any new Pydantic response models for API endpoints
- Error responses for invalid budget, missing player, etc.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Requirements
- `.planning/REQUIREMENTS.md` — API-01, API-02, SCAN-03, SCAN-05 are the requirements for this phase

### Phase 1 Context
- `.planning/phases/01-persistent-scanner/01-CONTEXT.md` — Prior decisions on endpoint design, scan tiers, staleness handling

### Architecture
- `.planning/codebase/ARCHITECTURE.md` — Layered architecture, MarketDataClient protocol, data flow
- `.planning/codebase/STRUCTURE.md` — File layout, where to add new endpoints

### Existing Code
- `src/server/api/players.py` — Existing top-players endpoint pattern to follow for new endpoints
- `src/server/models_db.py` — PlayerRecord and PlayerScore ORM models (extend if needed)
- `src/optimizer.py` — Portfolio optimizer to reuse (takes `list[dict]` with Player objects)
- `src/scorer.py` — Score function (already integrated in scanner, not called from API)
- `src/server/scanner.py` — ScannerService with tier classification logic to enhance for adaptive scheduling
- `src/config.py` — Constants for scan intervals, thresholds

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `optimize_portfolio()`: Takes `list[dict]` with keys `player`, `buy_price`, `expected_profit`, etc. Returns optimized selection. Needs bridge from DB rows to this format.
- `src/server/api/players.py` GET /players/top: Pattern for querying latest viable scores with subquery — reuse same pattern for portfolio endpoint.
- `PlayerScore` ORM model: Already stores all scoring fields per scan cycle. Score history is built-in.
- `ScannerService._classify_tier()`: Existing tier logic to extend for adaptive scheduling.

### Established Patterns
- Router modules in `src/server/api/` with `APIRouter(prefix="/api/v1")`
- Session factory via `request.app.state.session_factory`
- Latest-score subquery pattern: `func.max(scored_at)` grouped by `ea_id` where `is_viable=True`
- Staleness calculation: `datetime.utcnow() - timedelta(hours=STALE_THRESHOLD_HOURS)`

### Integration Points
- New endpoints register on the existing FastAPI app via router includes in `src/server/main.py`
- Portfolio endpoint needs to bridge `PlayerScore` rows to `optimize_portfolio()` input format — the optimizer expects `entry["player"].resource_id` for dedup
- Adaptive scheduling modifies `ScannerService._classify_and_schedule()` to consider activity deltas
- Score history index added via Alembic migration or direct schema update

</code_context>

<specifics>
## Specific Ideas

No specific requirements — open to standard approaches for all implementation details.

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 02-full-api-surface*
*Context gathered: 2026-03-25*
