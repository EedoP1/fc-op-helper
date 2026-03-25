# Phase 04: refactor-scoring-db - Research

**Researched:** 2026-03-25
**Domain:** SQLAlchemy async ORM, listing fingerprint tracking, adaptive scan scheduling, Python scoring architecture
**Confidence:** HIGH

## Summary

Phase 4 replaces the current OP sell scorer — which infers OP sales from completed auction history — with a listing-tracking system that records every individual live listing per player across scan snapshots, determines whether each listing sold or expired, and computes a true OP sell conversion rate. The new scoring formula outputs `expected_profit_per_hour` instead of `expected_profit`, making it a fundamentally more actionable and accurate metric.

The existing infrastructure is well-suited to this refactor. The scanner already has the data collection loop, the DB already uses SQLAlchemy async ORM with WAL mode, and the API endpoints already read from `PlayerScore`. The work breaks into three clear tracks: (1) extend `FutGGClient` to extract all liveAuctions fields, (2) add new DB tables for listing tracking, and (3) replace the `score_player()` function with a new scorer that reads from accumulated listing data rather than the completedAuctions API response.

The biggest design decision left to Claude's discretion is the fingerprint matching algorithm. Decision D-04 requires a research step into available liveAuctions API fields before finalizing the fingerprint strategy — this must happen at implementation time by logging a real API response, since the field set is not publicly documented.

**Primary recommendation:** Implement listing tracking as new DB tables alongside existing tables (not replacing them), run both scorers in parallel during a bootstrapping window (see open question #1), then retire the old scorer once enough listing data has accumulated. This avoids a hard cutover that would produce no scores for 24+ hours during the transition.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

- **D-01:** Track every individual listing using fingerprint matching — match by (ea_id + buyNowPrice + timing context) across scan snapshots
- **D-02:** When a listing disappears, cross-reference with completedAuctions to determine if it sold or expired
- **D-03:** FC26's 1-hour minimum listing duration enables adaptive scan timing — scan before the youngest listing expires, with a safety buffer
- **D-04:** liveAuctions API fields beyond buyNowPrice must be discovered at implementation time (auction IDs, expiry timestamps, listing duration, etc.)
- **D-05:** Replace fixed tier-based scanning with adaptive "next expiry" scheduling per player
- **D-06:** Safety buffer of 3-5 minutes before youngest listing expiry, to account for API latency and clock drift
- **D-07:** OP listing classification uses same margin logic as current scorer: buyNowPrice >= market_price × (1 + margin%)
- **D-08:** Market price at time of listing observation = current_lowest_bin from that scan snapshot
- **D-09:** Replace the current `score_player()` entirely — old scorer is retired
- **D-10:** New formula: `expected_profit_per_hour = net_profit × OP_sell_rate × OP_sales_per_hour`
  - `OP_sell_rate` = (OP listings that sold) / (total OP listings observed)
  - `OP_sales_per_hour` = OP sold count / hours of tracking data
  - Evaluated per margin tier, pick the margin maximizing `expected_profit_per_hour`
- **D-11:** Accounts for OP listings that expired (failed to sell) — not just successful OP sales
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

### Deferred Ideas (OUT OF SCOPE)

None — discussion stayed within phase scope.
</user_constraints>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| SQLAlchemy | 2.0.48 (installed) | Async ORM, new listing tables | Already used; Base, mapped_column, Index patterns established |
| aiosqlite | installed | Async SQLite driver | Already used; WAL mode already configured |
| APScheduler | 3.11.2 (installed) | Adaptive per-player scan jobs | Already used; `IntervalTrigger` and `DateTrigger` available |
| pytest-asyncio | 1.3.0 (installed) | Async test support | Already used for all scanner/DB tests |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| tenacity | installed | Retry on API failure | Reuse existing `@retry` wrapper in `scan_player()` |
| httpx | 0.28.1 (installed) | HTTP client for liveAuctions field discovery | Reuse existing `FutGGClient._get()` |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| In-process listing dedup | Separate dedup table | Table approach gives auditability and is query-friendly; in-process dict would be lost on restart |
| APScheduler per-player jobs | Custom asyncio.sleep loop | APScheduler already integrated; DateTrigger jobs per player would bloat job list for 11k players — use the dispatch loop approach instead (see Architecture Patterns) |

**Installation:** No new packages needed. All dependencies already installed.

**Version verification:** All versions confirmed from installed environment (SQLAlchemy 2.0.48, APScheduler 3.11.2, pytest 9.0.2, pytest-asyncio 1.3.0).

## Architecture Patterns

### Recommended Project Structure
```
src/
├── server/
│   ├── models_db.py      # ADD: ListingObservation, ListingOutcome, DailyListingSummary
│   ├── scanner.py        # REFACTOR: listing collection, outcome resolution, adaptive scheduling
│   ├── scorer_v2.py      # NEW: new score_player_v2() reads from listing tracking tables
│   ├── db.py             # MINOR: register new tables in create_engine_and_tables()
│   └── api/
│       ├── players.py    # UPDATE: add expected_profit_per_hour field, keep backward compat
│       └── portfolio.py  # UPDATE: adapt _build_scored_entry() for new score schema
├── config.py             # ADD: LISTING_RETENTION_DAYS=7, BOOTSTRAP_MIN_HOURS
└── scorer.py             # KEEP: used during bootstrapping window, retired after
```

### Pattern 1: Listing Fingerprint Matching

**What:** Identify individual listings across sequential scan snapshots using a composite fingerprint. Since FC26's liveAuctions response likely contains auction IDs or expiry timestamps (D-04), the fingerprint should use the most stable available identifier.

**Fingerprint strategy (order of preference):**
1. If `id` / `auctionId` field available: use `(ea_id, auction_id)` — exact match
2. If `expiresAt` / `remainingTime` field available: derive expiry bucket, use `(ea_id, buyNowPrice, expiry_bucket)`
3. Fallback (buyNowPrice only, no timestamps): use `(ea_id, buyNowPrice)` with temporal deduplication — only create a new observation if no matching listing was seen in the last N minutes

**D-04 field discovery:** At implementation start, log one raw `get_player_prices()` response and enumerate `liveAuctions[0].keys()`. The fingerprint strategy is chosen based on what fields are found.

**When to use:** Every scan cycle, for every liveAuctions entry.

**Example (APScheduler-aware, dedup on insert):**
```python
# Source: project patterns (scanner.py SQLAlchemy upsert pattern)
stmt = sqlite_insert(ListingObservation).values(
    fingerprint=fp,
    ea_id=ea_id,
    buy_now_price=auction["buyNowPrice"],
    market_price_at_obs=current_lowest_bin,
    first_seen_at=now,
    last_seen_at=now,
    scan_count=1,
    outcome=None,  # unresolved
)
stmt = stmt.on_conflict_do_update(
    index_elements=["fingerprint"],
    set_=dict(last_seen_at=now, scan_count=ListingObservation.scan_count + 1),
)
```

### Pattern 2: Outcome Resolution

**What:** After each scan, compare the previous scan's visible listings for a player against the current scan. Listings that disappeared are candidates for outcome resolution.

**Resolution logic (D-02):**
1. Query `completedAuctions` from the same `get_player_prices()` response
2. For each disappeared listing: check if a sale at that price exists in `completedAuctions` within the listing's expected time window
3. If match found: mark `outcome = "sold"`
4. If no match: mark `outcome = "expired"`
5. Listings that are still visible: no action (still open)

**Timing guarantee (D-03):** Because FC26 enforces a 1-hour minimum listing duration, any listing visible in scan N must still be visible in scan N+1 if scan N+1 happens within the remaining time. The adaptive scan timer (D-05) enforces this.

### Pattern 3: Adaptive Scan Timing Per Player

**What:** Replace the fixed tier-based intervals (30min/1hr/2.5hr) with per-player scheduling that scans before the youngest listing expires.

**Algorithm:**
```python
# After each scan, compute next_scan_at for the player
remaining_times = [extract_remaining_seconds(a) for a in live_auctions]
if remaining_times:
    youngest_remaining = min(remaining_times)
    safety_buffer = LISTING_SCAN_BUFFER_SECONDS  # 3-5 minutes = 180-300s
    next_scan_in = max(youngest_remaining - safety_buffer, ADAPTIVE_MIN_INTERVAL_SECONDS)
else:
    # No live listings — use normal tier interval as fallback
    next_scan_in = SCAN_INTERVAL_NORMAL

record.next_scan_at = datetime.utcnow() + timedelta(seconds=next_scan_in)
```

**Fallback when no timestamp available (D-04):** If liveAuctions entries have no expiry info, maintain the existing adaptive tier-based scheduling. The new system degrades gracefully.

**Key insight:** This is a modification to `_classify_and_schedule()`, not a replacement of the dispatch loop. The dispatch loop (`dispatch_scans()` + APScheduler `IntervalTrigger`) continues to work unchanged. Only the `next_scan_at` computation changes.

### Pattern 4: New Scorer Reading From DB

**What:** `score_player_v2(ea_id, session)` is an async function that reads accumulated `ListingObservation` rows for a player and computes the D-10 formula.

**Separation from scan:** The scoring job runs separately from the scan job. Scans collect data; a separate scheduled job (e.g., every 15 minutes) reads accumulated data and writes `PlayerScore` rows. This decouples data collection from score computation.

```python
# scorer_v2.py
async def score_player_v2(ea_id: int, session: AsyncSession, buy_price: int) -> dict | None:
    """Read accumulated listing observations and compute new-model score."""
    cutoff = datetime.utcnow() - timedelta(days=LISTING_RETENTION_DAYS)
    observations = await session.execute(
        select(ListingObservation)
        .where(
            ListingObservation.ea_id == ea_id,
            ListingObservation.outcome.isnot(None),  # resolved only
            ListingObservation.first_seen_at >= cutoff,
        )
    )
    rows = observations.scalars().all()
    if not rows:
        return None
    # ... margin evaluation loop using D-10 formula
```

### Pattern 5: DB Schema for Listing Tracking

**What:** Three new tables alongside existing tables. No existing tables modified.

**Recommended schema:**

```python
class ListingObservation(Base):
    """Individual listing tracked across scan snapshots."""
    __tablename__ = "listing_observations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fingerprint: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    ea_id: Mapped[int] = mapped_column(Integer, index=True)
    buy_now_price: Mapped[int] = mapped_column(Integer)
    market_price_at_obs: Mapped[int] = mapped_column(Integer)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime)
    scan_count: Mapped[int] = mapped_column(Integer, default=1)
    outcome: Mapped[str | None] = mapped_column(String(10), nullable=True)  # "sold"|"expired"|None
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class DailyListingSummary(Base):
    """Aggregated daily stats per player per margin tier — kept indefinitely (D-14)."""
    __tablename__ = "daily_listing_summaries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ea_id: Mapped[int] = mapped_column(Integer, index=True)
    date: Mapped[str] = mapped_column(String(10), index=True)  # YYYY-MM-DD
    margin_pct: Mapped[int] = mapped_column(Integer)
    op_listed_count: Mapped[int] = mapped_column(Integer, default=0)
    op_sold_count: Mapped[int] = mapped_column(Integer, default=0)
    op_expired_count: Mapped[int] = mapped_column(Integer, default=0)
    total_listed_count: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (
        Index("ix_daily_summary_ea_id_date_margin", "ea_id", "date", "margin_pct"),
    )
```

**Note:** A separate `PlayerScore` schema update is needed to add `expected_profit_per_hour` column or replace `expected_profit`. The recommended approach: add `expected_profit_per_hour` as a new nullable column and populate it once listing data is sufficient, keeping `expected_profit` populated by the old scorer during the transition.

### Anti-Patterns to Avoid

- **Replacing dispatch loop with per-player APScheduler jobs:** For ~11k players this would create 11,000 individual APScheduler jobs, memory overhead is significant. Keep the dispatch loop + `next_scan_at` column approach.
- **Resolving outcomes in a separate HTTP call:** Outcome resolution uses the `completedAuctions` data already fetched in `get_player_prices()`. Do not make a separate API call for resolution.
- **Storing all liveAuctions entries as MarketSnapshot JSON:** The existing `live_auction_prices` TEXT column stores a JSON array of prices only. For fingerprinting, the new `ListingObservation` table replaces this storage for structural data. Keeping both is fine during transition but long-term the JSON column becomes redundant.
- **Hard-retiring old scorer on day 1:** The bootstrapping problem (open question #1) means the new scorer will return None for all players until ~24-48 hours of data accumulates. Keep old scorer running in parallel during transition.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Upsert for listing fingerprints | Custom SELECT + INSERT/UPDATE | `sqlite_insert().on_conflict_do_update()` | Already used in `run_bootstrap()` — same pattern, works atomically |
| Async DB sessions in async tests | pytest fixtures with manual connection management | `create_engine_and_tables("sqlite+aiosqlite:///:memory:")` | Already established in `test_db.py` and `test_scanner.py` — exact same fixture pattern |
| Retry logic for scan API calls | Custom try/except with sleep | `tenacity @retry` + `_fetch_with_retry()` inner function | Already used in `scan_player()` — identical pattern |
| Scheduled scoring job | Custom asyncio task | APScheduler `IntervalTrigger` in `create_scheduler()` | Already used for dispatch, discovery, cleanup — add scoring job to same scheduler |
| Time-windowed data purge | Custom DELETE query logic | `DELETE WHERE first_seen_at < cutoff` pattern | Already used in `run_cleanup()` for `MarketSnapshot` — identical pattern, different table |

**Key insight:** All infrastructure patterns for this phase already exist in the codebase. This phase is extending proven patterns, not introducing new ones.

## Common Pitfalls

### Pitfall 1: liveAuctions Field Set Unknown

**What goes wrong:** The fingerprint matching strategy (D-04) depends on fields that may or may not exist in the API response. Building a fingerprinter for fields that don't exist causes silent NullPointerErrors or falls back to a degenerate strategy.

**Why it happens:** `futgg_client.py` currently only reads `buyNowPrice` from `liveAuctions` (line 124). The full field set has never been logged.

**How to avoid:** First task in Wave 1 must be a logging/inspection step: make one real API call and `print(prices["liveAuctions"][0].keys())`. Document the available fields before writing the fingerprint function. The fingerprint logic branches on what's available (see Architecture Patterns - Pattern 1).

**Warning signs:** Fingerprint function that only uses `buyNowPrice` with no dedup — this will create duplicate observations for listings that persist across scans.

### Pitfall 2: Listing Outcome Ambiguity for Same-Price Listings

**What goes wrong:** Multiple listings at the same price exist simultaneously. When one disappears, matching it to a completedAuctions sale is ambiguous — which one sold?

**Why it happens:** Buyers prefer the cheapest BIN. Multiple listings at the lowest price may have one sell and others expire, but the completedAuctions log shows a sale at that price without specifying which listing.

**How to avoid:** Accept the ambiguity — use a proportional model: if N listings at price P disappeared and M sales at price P are found, attribute M as sold and N-M as expired. Do not try to achieve exact match per listing at the same price.

**Warning signs:** Outcome resolution code that requires a 1-to-1 match and errors on duplicates.

### Pitfall 3: Bootstrapping Gap Produces Empty Scores

**What goes wrong:** After deploying the new scanner, `score_player_v2()` returns None for all players because no listing observations have outcomes yet. The API returns an empty portfolio and top players list.

**Why it happens:** The new scorer requires accumulation time: at least 1 scan cycle to see listings, and another cycle (1 hour later) to detect disappeared listings and resolve outcomes.

**How to avoid:** During bootstrapping period, fall back to `score_player()` (old scorer) until `BOOTSTRAP_MIN_OBSERVATIONS` resolved listings exist per player. Config constant: `BOOTSTRAP_MIN_OBSERVATIONS = 10`. Expose a health/bootstrap endpoint or log statement showing % of players with sufficient data.

**Warning signs:** `/api/v1/portfolio` returns `count: 0` immediately after deployment.

### Pitfall 4: Adaptive Scan Timer Drives Excessive Scan Rate

**What goes wrong:** A player with many listings, some expiring soon, triggers very frequent scans — potentially every 3-5 minutes per player. With 11k players, this could overwhelm the API and hit rate limits.

**Why it happens:** The per-player timer responds to the youngest listing's expiry, ignoring the global scan rate across all players.

**How to avoid:** Floor the per-player next scan interval at `ADAPTIVE_MIN_INTERVAL_SECONDS` (already exists at 300s / 5 minutes). The dispatch loop already has `SCAN_CONCURRENCY = 5` as a semaphore ceiling. These two limits together bound the worst-case API call rate.

**Warning signs:** `scan_success_rate` dropping below 0.90, or log spam of "Circuit breaker OPEN".

### Pitfall 5: 7-Day Rolling Window Purge Deletes Unresolved Listings

**What goes wrong:** The purge job deletes `ListingObservation` rows older than 7 days, including rows with `outcome = None` (still open or orphaned). This produces inflated expiry rates on the next compute cycle.

**Why it happens:** Purge query filters on `first_seen_at` regardless of `outcome`.

**How to avoid:** Purge query should filter on `resolved_at` (not `first_seen_at`) for outcome-resolved rows, and use `last_seen_at` for unresolved rows older than the retention window (they are likely orphaned). Separate purge logic per outcome type.

### Pitfall 6: PlayerScore Schema Breaking API Layer

**What goes wrong:** Adding or renaming columns in `PlayerScore` causes KeyError in `_build_scored_entry()` in `portfolio.py` and the player detail response in `players.py`.

**Why it happens:** Both API endpoints hardcode specific `PlayerScore` column names.

**How to avoid:** Add `expected_profit_per_hour` as a new nullable column rather than replacing `expected_profit`. During transition, the new scorer writes both columns. API layer additions are additive (new response keys), not breaking changes. Old scanner continues writing to existing columns.

### Pitfall 7: expire_on_commit=False Not Applied to New Sessions

**What goes wrong:** New session factories or code paths that create sessions without `expire_on_commit=False` cause `MissingGreenlet` errors when accessing loaded model attributes after `session.commit()` in async context.

**Why it happens:** Established in Phase 1 context as a known pitfall. New code written for listing tracking tables may inadvertently use a raw `create_session_factory()` call that omits this setting.

**How to avoid:** All session creation must go through `create_session_factory()` in `src/server/db.py`, which already sets `expire_on_commit=False`. Never inline session factory creation in new modules.

## Code Examples

Verified patterns from existing codebase (all HIGH confidence):

### Upsert with on_conflict_do_update (fingerprint dedup pattern)
```python
# Source: src/server/scanner.py run_bootstrap() — existing production pattern
stmt = sqlite_insert(ListingObservation).values(**row)
stmt = stmt.on_conflict_do_update(
    index_elements=["fingerprint"],
    set_=dict(last_seen_at=now, scan_count=ListingObservation.scan_count + 1),
)
await session.execute(stmt)
```

### Async test with in-memory DB (established fixture pattern)
```python
# Source: tests/test_scanner.py and tests/test_db.py
@pytest.fixture
async def db():
    engine, session_factory = await create_engine_and_tables("sqlite+aiosqlite:///:memory:")
    yield engine, session_factory
    await engine.dispose()
```

### APScheduler job addition (follow existing scheduler.py pattern)
```python
# Source: src/server/scheduler.py — adding a new job
scheduler.add_job(
    scanner.run_scoring,
    trigger=IntervalTrigger(minutes=15),
    id="scoring",
    max_instances=1,
    coalesce=True,
    replace_existing=True,
    name="Listing scorer",
)
```

### Margin evaluation loop (carry forward from scorer.py)
```python
# Source: src/scorer.py lines 61-99 — same logic, new data source
for margin_pct in MARGINS:
    margin = margin_pct / 100.0
    op_sold = sum(1 for obs in observations if obs.outcome == "sold" and is_op(obs, margin))
    op_total = sum(1 for obs in observations if is_op(obs, margin))
    if op_total < MIN_OP_OBSERVATIONS:
        continue
    op_sell_rate = op_sold / op_total
    # hours_of_data from (max(first_seen_at) - min(first_seen_at))
    op_sales_per_hour = op_sold / hours_of_data
    expected_profit_per_hour = net_profit * op_sell_rate * op_sales_per_hour
```

### Cleanup DELETE (follow existing run_cleanup() pattern)
```python
# Source: src/server/scanner.py run_cleanup()
cutoff = datetime.utcnow() - timedelta(days=LISTING_RETENTION_DAYS)
await session.execute(
    delete(ListingObservation).where(
        ListingObservation.resolved_at < cutoff,
        ListingObservation.outcome.isnot(None),
    )
)
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Infer OP from completedAuctions only | Track every listing, observe outcomes directly | Phase 4 | Eliminates selection bias — expired OP listings are no longer invisible |
| Fixed tier intervals (30min/1hr/2.5hr) | Adaptive per-listing expiry timing | Phase 4 | Guarantees zero-miss listing coverage, scan frequency adjusts to market activity |
| `expected_profit = net_profit × op_ratio` | `expected_profit_per_hour = net_profit × op_sell_rate × op_sales_per_hour` | Phase 4 | Per-hour metric is more actionable; accounts for failed OP listings |
| `op_ratio` = OP sales / total sales | `op_sell_rate` = OP sold / OP listed | Phase 4 | Isolates OP listing success rate from overall trading volume |

**Current system limitations being fixed:**
- Old `op_ratio`: numerator is OP sales (confirmed successes), denominator is all sales (including non-OP). This means `op_ratio` is diluted by non-OP trades and can't distinguish "lots of OP sales" from "lots of total trades."
- Old system: blind to OP listings that expired — a player where 90% of OP listings expire would score identically to one where 90% sell.

## Open Questions

1. **Bootstrapping transition strategy**
   - What we know: New scorer returns None until ~24-48h of listing data accumulates; old scorer works fine on existing completedAuctions data
   - What's unclear: At what threshold should the new scorer's output supersede the old? Fixed time? Min observations per player?
   - Recommendation: Add `BOOTSTRAP_MIN_OBSERVATIONS = 10` (resolved listings). Run both scorers; write `expected_profit_per_hour` from new scorer when available, fall back to old scorer for `PlayerScore.is_viable`. Mark `PlayerScore.scorer_version = "v1" | "v2"` column for auditability. Retire v1 scorer after 7 days of consistent v2 data.

2. **liveAuctions field set (D-04)**
   - What we know: Current code only reads `buyNowPrice`; other fields likely exist
   - What's unclear: Whether `id`, `expiresAt`, `remainingTime`, or equivalent fields are available
   - Recommendation: First implementation task must log a raw API response and enumerate fields. The fingerprint strategy branches on this discovery. Do not write the fingerprint function before this step.

3. **Daily summary aggregation timing**
   - What we know: D-13 requires daily summaries for observations older than 7 days; D-14 keeps them indefinitely
   - What's unclear: Should aggregation run at the time of purge, or as a separate nightly job?
   - Recommendation: Separate nightly job that aggregates the prior day's resolved observations into `DailyListingSummary`, then the weekly purge deletes raw rows. Sequence: aggregate yesterday → verify summary written → delete raw rows for that day.

4. **PlayerRecord.scan_tier column: keep or replace?**
   - What we know: `scan_tier` (hot/normal/cold) drives the existing tier-based intervals; phase replaces this with per-listing expiry timing
   - What's unclear: Does `scan_tier` remain useful after adaptive timing takes over?
   - Recommendation: Keep `scan_tier` for diagnostic/monitoring purposes (it's read by the health API and API responses). Stop using it to compute `next_scan_at`. The tier classification stays for value-based prioritization (high-profit players still get higher priority in dispatch ordering).

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python 3.12 | Runtime | Yes | 3.12.10 | — |
| SQLAlchemy async | Listing tables | Yes | 2.0.48 | — |
| aiosqlite | SQLite driver | Yes | installed | — |
| APScheduler | Scoring job scheduling | Yes | 3.11.2 | — |
| pytest-asyncio | Async tests | Yes | 1.3.0 | — |
| tenacity | API retry | Yes | installed | — |
| fut.gg API | liveAuctions field discovery | Yes (assumed) | — | Log warning, use fallback fingerprint |

**Missing dependencies with no fallback:** None.

**Missing dependencies with fallback:** None.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 9.0.2 + pytest-asyncio 1.3.0 |
| Config file | `pytest.ini` or inline via `pyproject.toml` (asyncio_mode=auto configured) |
| Quick run command | `python -m pytest tests/test_scorer_v2.py -q` |
| Full suite command | `python -m pytest tests/ -q` |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| SCAN-P4-01 | Listing fingerprint created/updated on scan | unit | `pytest tests/test_listing_tracker.py::test_fingerprint_upsert -x` | Wave 0 |
| SCAN-P4-02 | Disappeared listing resolved as sold when completedAuctions match found | unit | `pytest tests/test_listing_tracker.py::test_outcome_sold -x` | Wave 0 |
| SCAN-P4-03 | Disappeared listing resolved as expired when no completedAuctions match | unit | `pytest tests/test_listing_tracker.py::test_outcome_expired -x` | Wave 0 |
| SCAN-P4-04 | New scorer computes expected_profit_per_hour correctly | unit | `pytest tests/test_scorer_v2.py::test_expected_profit_per_hour -x` | Wave 0 |
| SCAN-P4-05 | New scorer picks margin maximizing expected_profit_per_hour | unit | `pytest tests/test_scorer_v2.py::test_margin_selection -x` | Wave 0 |
| SCAN-P4-06 | New scorer returns None when fewer than BOOTSTRAP_MIN_OBSERVATIONS | unit | `pytest tests/test_scorer_v2.py::test_bootstrap_min -x` | Wave 0 |
| SCAN-P4-07 | Adaptive scan timing uses youngest listing expiry | unit | `pytest tests/test_scanner.py::test_adaptive_next_scan -x` | Wave 0 |
| SCAN-P4-08 | 7-day rolling window purge removes old resolved listings | unit | `pytest tests/test_scanner.py::test_listing_purge -x` | Wave 0 |
| SCAN-P4-09 | Daily summary aggregation writes correct counts per margin tier | unit | `pytest tests/test_listing_tracker.py::test_daily_summary -x` | Wave 0 |
| SCAN-P4-10 | PlayerScore rows written using new scorer once sufficient data exists | integration | `pytest tests/test_integration.py::test_v2_scorer_writes_score -x` | Wave 0 |

### Sampling Rate
- **Per task commit:** `python -m pytest tests/test_scorer_v2.py tests/test_listing_tracker.py -q`
- **Per wave merge:** `python -m pytest tests/ -q`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_listing_tracker.py` — covers SCAN-P4-01 through SCAN-P4-03, SCAN-P4-09
- [ ] `tests/test_scorer_v2.py` — covers SCAN-P4-04 through SCAN-P4-06
- [ ] Additional test cases in `tests/test_scanner.py` — covers SCAN-P4-07, SCAN-P4-08
- [ ] Additional test case in `tests/test_integration.py` — covers SCAN-P4-10

## Project Constraints (from CLAUDE.md)

- **Data source**: fut.gg API only — no FUTBIN, no EA API direct access
- **Tech stack**: Python backend; no new dependencies to add (existing stack covers all needs)
- **Storage**: SQLite with SQLAlchemy async ORM; WAL mode already enabled; `expire_on_commit=False` required on all session factories
- **Import style**: Absolute imports from repo root: `from src.server.models_db import ...` (not relative)
- **Function naming**: snake_case; private functions prefixed with `_`; async functions use same naming as sync
- **Module design**: No `__all__`; no barrel files; direct imports
- **Error handling**: Defensive early returns; try-except with logged errors; `continue` on per-record parse failures
- **Logging**: Per-module `logger = logging.getLogger(__name__)`; INFO for major steps, DEBUG for verbosity, ERROR for exceptions
- **GSD workflow**: All file edits via GSD commands; no direct repo edits outside a GSD workflow

## Sources

### Primary (HIGH confidence)
- `src/server/scanner.py` — scan loop, tier classification, dispatch, `_classify_and_schedule()`, SQLite upsert pattern
- `src/server/models_db.py` — existing ORM table patterns for new table design
- `src/server/db.py` — WAL mode, `expire_on_commit=False`, `create_engine_and_tables()` pattern
- `src/scorer.py` — margin evaluation loop; D-10 formula extends this logic
- `src/server/api/portfolio.py` — `_build_scored_entry()` and `_PlayerProxy` patterns for API compatibility
- `src/server/scheduler.py` — APScheduler job addition pattern
- `.planning/phases/04-refactor-scoring-db/04-CONTEXT.md` — all locked decisions (D-01 through D-14)

### Secondary (MEDIUM confidence)
- SQLAlchemy 2.0 async docs — `sqlite_insert().on_conflict_do_update()` pattern (verified against installed 2.0.48)
- APScheduler 3.x docs — `DateTrigger` availability for one-shot jobs (verified against installed 3.11.2)

### Tertiary (LOW confidence)
- fut.gg liveAuctions field set — UNKNOWN; must be discovered empirically at implementation time (D-04)

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all libraries installed and confirmed
- Architecture patterns: HIGH — derived from existing codebase patterns; no new patterns introduced
- DB schema design: MEDIUM — recommended schema is reasonable but field names may shift based on D-04 field discovery
- New scoring formula: HIGH — formula fully specified in D-10; margin loop pattern from existing scorer.py
- liveAuctions fields: LOW — unknown until D-04 discovery step; fingerprint strategy has documented fallback

**Research date:** 2026-03-25
**Valid until:** 2026-04-25 (stable Python/SQLAlchemy stack; fut.gg API field set valid until EA changes the API)
