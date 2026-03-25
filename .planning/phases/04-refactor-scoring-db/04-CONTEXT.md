# Phase 4: Refactor Scoring + DB - Context

**Gathered:** 2026-03-25
**Status:** Ready for planning

<domain>
## Phase Boundary

Replace the current scoring model (which estimates OP sales from completed auction history) with a listing-tracking system that records every individual listing for every player over time, determines which listings sold vs expired, and computes a true OP sell conversion rate. The new scorer uses this accumulated listing data to produce expected profit per hour — a fundamentally better metric for OP sell profitability. No new API endpoints, no Chrome extension work, no CLI changes.

</domain>

<decisions>
## Implementation Decisions

### Listing Tracking
- **D-01:** Track every individual listing for every player using fingerprint matching — match by (ea_id + buyNowPrice + timing context) across scan snapshots
- **D-02:** When a listing disappears between scans, cross-reference with completedAuctions to determine if it sold (matching sale at that price in the timeframe) or expired (no matching sale found)
- **D-03:** Exploit FC26's 1-hour minimum listing duration for scan timing: the youngest listing's remaining time tells us exactly when the next scan must happen to guarantee zero missed listings. Add a safety buffer (a few minutes early) to avoid edge cases.
- **D-04:** The liveAuctions API response likely contains more fields than we currently extract (we only use buyNowPrice). Research step must discover available fields — auction IDs, expiry timestamps, listing duration, etc. This determines the fingerprinting strategy.

### Adaptive Scan Timing
- **D-05:** Replace fixed tier-based scanning (30min/1hr/2.5hr) with adaptive "next expiry" scheduling per player: scan before the youngest listing expires, ensuring complete listing coverage
- **D-06:** Safety buffer before the youngest listing's expiry (e.g., scan 3-5 minutes early) to account for API latency and clock drift

### OP Listing Classification
- **D-07:** A listing is classified as OP using the same margin logic as the current scorer: buyNowPrice >= market_price × (1 + margin%), evaluated at multiple margin tiers (3% to 40%)
- **D-08:** Market price at time of listing observation = current_lowest_bin from that scan snapshot

### New Scoring Formula
- **D-09:** Replace the current scorer entirely — old `score_player()` is retired
- **D-10:** New expected profit formula: `expected_profit_per_hour = net_profit × OP_sell_rate × OP_sales_per_hour`
  - `OP_sell_rate` = (OP listings that sold) / (total OP listings observed) — true conversion rate
  - `OP_sales_per_hour` = OP sold count / hours of tracking data
  - Evaluated per margin tier, pick the margin that maximizes expected_profit_per_hour
- **D-11:** This is fundamentally better than the current model because it accounts for OP listings that expired (failed to sell), not just successful OP sales

### Data Retention
- **D-12:** Rolling window: keep last 7 days of individual listing tracking data
- **D-13:** Aggregate older data into daily summaries per player: OP listed count, OP sold count, expired count, by margin tier
- **D-14:** Daily summaries kept indefinitely for long-term trend analysis

### Claude's Discretion
- DB schema design for listing tracking tables (fingerprint storage, observation records, outcome classification)
- Fingerprint matching algorithm details (exact vs fuzzy matching, deduplication)
- How to handle the bootstrapping period (before enough listing data accumulates for reliable scores)
- Migration strategy for existing PlayerScore data
- Purge job implementation for the 7-day rolling window
- How to restructure scanner.py to support adaptive per-player scan timing

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Requirements
- `.planning/REQUIREMENTS.md` — All v1 requirements complete; this phase is a scoring model improvement

### Prior Phase Context
- `.planning/phases/01-persistent-scanner/01-CONTEXT.md` — Scanner architecture, tier system (being replaced), circuit breaker, staleness handling
- `.planning/phases/02-full-api-surface/02-CONTEXT.md` — Portfolio endpoint, player detail endpoint, _PlayerProxy bridge, adaptive scheduling (being enhanced)
- `.planning/phases/03-cli-as-api-client/03-CONTEXT.md` — CLI is pure API client, no changes needed

### Existing Code (Critical)
- `src/futgg_client.py` — liveAuctions parsing (lines 121-124, 217-219). Currently only extracts buyNowPrice. **MUST discover all available fields in the liveAuctions API response** (auction IDs, expiry timestamps, etc.)
- `src/scorer.py` — Current scorer to be replaced. Understand the margin logic (lines 61-99) as the same approach applies to OP listing classification
- `src/server/scanner.py` — Scanner service to be refactored for adaptive scan timing and listing data collection
- `src/server/models_db.py` — Current DB models (PlayerRecord, PlayerScore, MarketSnapshot, SnapshotSale, SnapshotPricePoint)
- `src/config.py` — Scan interval constants (to be replaced with adaptive timing), MARKET_DATA_RETENTION_DAYS

### Architecture
- `.planning/codebase/ARCHITECTURE.md` — Layered architecture, data flow
- `.planning/codebase/STRUCTURE.md` — File layout, where to add new code

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `FutGGClient.get_player_prices()`: Already fetches the liveAuctions data — just needs to extract more fields
- `MarketSnapshot` + `SnapshotSale` + `SnapshotPricePoint`: Existing raw data storage pattern — extend for listing tracking
- `ScannerService`: Orchestration framework for discovery + scoring + scheduling — refactor internals, keep structure
- `CircuitBreaker`: Rate-limit resilience — reuse unchanged

### Established Patterns
- SQLAlchemy async ORM with WAL mode and expire_on_commit=False
- Tenacity retry for API calls with exponential backoff + jitter
- APScheduler for job scheduling (needs adaptation for per-player adaptive timing)
- `async_sessionmaker` + semaphore-based concurrency control

### Integration Points
- Scanner collects listing data during each scan → stored in new tracking tables
- Separate scoring job reads accumulated listing data → computes new scores → writes to PlayerScore
- Portfolio endpoint already reads from PlayerScore — works unchanged if schema is compatible
- Player detail endpoint may need updates if score fields change (sell_price, net_profit replaced by expected_profit_per_hour)

</code_context>

<specifics>
## Specific Ideas

- **FC26 1-hour minimum listing rule**: Every card must be listed for at least 1 hour. This is the key insight that makes zero-miss listing tracking possible — scan before the youngest listing expires.
- **Expected profit per hour**: The user specifically wants a per-hour metric, not just total expected profit. This is more actionable because it tells you how much you'll actually earn in a trading session.
- **The youngest listing's remaining time drives scan timing**: If 16 listings are visible and the youngest has 26 minutes left, schedule the next scan in ~23 minutes (26 minus safety buffer).

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 04-refactor-scoring-db*
*Context gathered: 2026-03-25*
