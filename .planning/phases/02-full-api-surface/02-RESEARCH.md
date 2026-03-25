# Phase 2: Full API Surface - Research

**Researched:** 2026-03-25
**Domain:** FastAPI REST endpoints, portfolio optimization bridge, adaptive scan scheduling, score history indexing
**Confidence:** HIGH

## Summary

Phase 2 extends the existing FastAPI server (Phase 1) with two new REST endpoints and two scanner enhancements. All required infrastructure is already in place: the DB schema accumulates one `PlayerScore` row per scan cycle (SCAN-05 is structurally complete — only the index is missing), and the optimizer function is ready to bridge from DB rows (API-01). The main implementation work is three modules: a portfolio router, a player-detail router, and enhancements to `ScannerService._classify_and_schedule()` for adaptive scheduling (SCAN-03).

No new framework dependencies are needed. The established patterns from Phase 1 — `APIRouter(prefix="/api/v1")`, `request.app.state.session_factory`, the latest-viable-score subquery, `ASGITransport` test setup — all apply directly. The only non-trivial design decision is the bridge from `PlayerScore` DB rows to the `optimize_portfolio()` input format, which requires a lightweight proxy object with a `.resource_id` attribute to satisfy the dedup logic.

The adaptive scheduling enhancement is a contained change to `_classify_and_schedule()`: compare current `listing_count` / `sales_per_hour` to values from the most recent previous `PlayerScore` row, compute a percent delta, and shorten the interval within-tier if the delta exceeds a threshold. The tier boundaries (hot/normal/cold) remain unchanged.

**Primary recommendation:** Implement as three incremental units — (1) DB index migration, (2) portfolio + player-detail endpoints, (3) adaptive scheduling logic — each independently testable.

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Portfolio Endpoint (API-01)**
- D-01: `GET /api/v1/portfolio?budget=X` computes portfolio on-demand per request — no caching
- D-02: Query latest viable scores from DB, build lightweight dicts matching `optimize_portfolio()` input format, run optimizer in-process
- D-03: Reuse `optimize_portfolio()` from `src/optimizer.py` unchanged — bridge DB rows to the dict format it expects (needs `Player`-like object with `resource_id` for dedup)
- D-04: Response mirrors the top-players endpoint structure but with portfolio-specific fields: total budget, budget used, budget remaining, player count, and the optimized player list

**Player Detail Endpoint (API-02)**
- D-05: `GET /api/v1/players/{ea_id}` returns full score breakdown: all PlayerScore fields + PlayerRecord metadata
- D-06: Include recent score history — last 24 entries for trend visualization
- D-07: Do NOT include raw sales data (not stored in DB)
- D-08: Include computed trend indicators: score direction (up/down/stable), price change over last N scores

**Adaptive Scan Scheduling (SCAN-03)**
- D-09: Enhance existing tier system with per-player interval adjustment based on listing activity patterns
- D-10: If listing_count or sales_per_hour changed significantly since last scan, shorten the interval; if stable, keep tier default
- D-11: Keep hot/normal/cold as base tiers — adaptive scheduling adjusts within tier bounds, not across them

**Score History Retention (SCAN-05)**
- D-12: Keep all PlayerScore history indefinitely
- D-13: Add DB index on (ea_id, scored_at DESC) for efficient time-range queries
- D-14: Score history rows already accumulate from Phase 1 scanner — no schema change needed for accumulation itself

### Claude's Discretion
- Portfolio endpoint query optimization (how to efficiently load scores for optimization)
- Player detail response serialization format (Pydantic response model vs dict)
- Trend calculation algorithm (simple delta vs moving average)
- Adaptive scheduling formula (how to compute interval adjustment from activity delta)
- Any new Pydantic response models for API endpoints
- Error responses for invalid budget, missing player, etc.

### Deferred Ideas (OUT OF SCOPE)
None — discussion stayed within phase scope.
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| API-01 | REST API endpoint returns optimized OP sell portfolio for a given budget, built on-demand from accumulated historical data | D-01 through D-04; optimizer bridge pattern documented below |
| API-02 | REST API endpoint returns detailed score breakdown for a specific player (margin, op_ratio, expected_profit, efficiency, sales history) | D-05 through D-08; score history query pattern documented below |
| SCAN-03 | Scanner uses adaptive scheduling per player based on listing activity | D-09 through D-11; delta-based interval formula documented below |
| SCAN-05 | Historical score data accumulates over time per player for trend analysis | D-12 through D-14; index already defined in models_db.py but needs DESC direction confirmed |
</phase_requirements>

---

## Standard Stack

All libraries are already installed. No new dependencies required.

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| FastAPI | 0.135.2 | REST endpoint routing | Established in Phase 1 |
| SQLAlchemy | 2.0.48 | Async ORM queries | Established in Phase 1 |
| aiosqlite | 0.22.1 | Async SQLite driver | Established in Phase 1 |
| Pydantic | 2.12.5 | Response model validation | Established in Phase 1 |

### No New Installs Needed
All dependencies for Phase 2 are already in `requirements.txt`. The optimizer, scorer, session factory, and router registration pattern all exist.

---

## Architecture Patterns

### Recommended Project Structure (additions only)
```
src/server/api/
├── players.py          # existing — GET /players/top
├── portfolio.py        # new — GET /portfolio?budget=X
├── health.py           # existing
└── __init__.py

src/server/
├── scanner.py          # modify _classify_and_schedule() for adaptive intervals
└── models_db.py        # confirm index; add DESC direction if needed
```

### Pattern 1: New Router Registration
**What:** Add router to `src/server/main.py` `include_router()` calls
**When to use:** Every new endpoint module
**Example:**
```python
# src/server/main.py
from src.server.api.portfolio import router as portfolio_router
from src.server.api.players import router as players_router  # also add player detail here
app.include_router(portfolio_router)
```

### Pattern 2: Latest-Viable-Score Subquery (established)
**What:** Subquery gets `func.max(scored_at)` per `ea_id` where `is_viable=True`, then join to get full row
**When to use:** Any endpoint that needs the current score per player
**Example:**
```python
# Source: src/server/api/players.py (Phase 1, verified)
latest_subq = (
    select(
        PlayerScore.ea_id,
        func.max(PlayerScore.scored_at).label("max_scored_at"),
    )
    .where(PlayerScore.is_viable == True)  # noqa: E712
    .group_by(PlayerScore.ea_id)
    .subquery()
)

stmt = (
    select(PlayerScore, PlayerRecord)
    .join(
        latest_subq,
        (PlayerScore.ea_id == latest_subq.c.ea_id)
        & (PlayerScore.scored_at == latest_subq.c.max_scored_at),
    )
    .join(PlayerRecord, PlayerRecord.ea_id == PlayerScore.ea_id)
    .where(PlayerRecord.is_active == True)  # noqa: E712
)
```

### Pattern 3: Portfolio Optimizer Bridge
**What:** `optimize_portfolio()` calls `entry["player"].resource_id` for dedup. DB rows have no `Player` object, so a minimal proxy must be created.
**When to use:** Portfolio endpoint only
**Example:**
```python
# Minimal proxy — only resource_id is accessed by optimize_portfolio()
class _PlayerProxy:
    def __init__(self, ea_id: int):
        self.resource_id = ea_id

# Bridge from PlayerScore + PlayerRecord rows to optimizer input format
def _build_scored_entry(score: PlayerScore, record: PlayerRecord) -> dict:
    return {
        "player": _PlayerProxy(score.ea_id),
        "buy_price": score.buy_price,
        "sell_price": score.sell_price,
        "net_profit": score.net_profit,
        "margin_pct": score.margin_pct,
        "op_sales": score.op_sales,
        "total_sales": score.total_sales,
        "op_ratio": score.op_ratio,
        "expected_profit": score.expected_profit,
        "efficiency": score.efficiency,
        "sales_per_hour": score.sales_per_hour,
        # Metadata for response serialization
        "ea_id": record.ea_id,
        "name": record.name,
        "rating": record.rating,
        "position": record.position,
        "scan_tier": record.scan_tier,
    }
```

**Critical:** `optimize_portfolio()` mutates the `efficiency` key in the input dicts (line 24 of optimizer.py: `s["efficiency"] = ...`). Since efficiency is already stored in DB, this is a harmless overwrite, but the bridge dict must include the key or the mutation will add it — either way is safe.

### Pattern 4: Score History Query (for player detail)
**What:** Fetch last N `PlayerScore` rows for a single `ea_id`, ordered by `scored_at DESC`
**When to use:** `GET /api/v1/players/{ea_id}` history section
**Example:**
```python
history_stmt = (
    select(PlayerScore)
    .where(PlayerScore.ea_id == ea_id)
    .order_by(PlayerScore.scored_at.desc())
    .limit(24)
)
result = await session.execute(history_stmt)
history_rows = result.scalars().all()
```

### Pattern 5: Trend Calculation (Claude's discretion)
**What:** Compute direction (up/down/stable) and price change from score history
**Recommendation:** Simple delta between newest and oldest of the last 24 scores is sufficient for a single-user tool. No moving average needed at this stage.
```python
def _compute_trend(history: list[PlayerScore]) -> dict:
    """history is ordered newest-first (DESC query)."""
    if len(history) < 2:
        return {"direction": "stable", "price_change": 0, "efficiency_change": 0.0}
    newest = history[0]
    oldest = history[-1]
    price_delta = newest.buy_price - oldest.buy_price
    eff_delta = round(newest.efficiency - oldest.efficiency, 4)
    if eff_delta > 0.005:
        direction = "up"
    elif eff_delta < -0.005:
        direction = "down"
    else:
        direction = "stable"
    return {"direction": direction, "price_change": price_delta, "efficiency_change": eff_delta}
```

### Pattern 6: Adaptive Scheduling Formula (Claude's discretion)
**What:** Shorten scan interval within tier bounds when activity delta exceeds threshold
**Recommendation:** Compare current `listing_count` to the most recent previous `PlayerScore`'s `sales_per_hour`. A 25%+ change in either metric triggers interval halving (clamped to a minimum of 5 minutes).

```python
# In ScannerService._classify_and_schedule()
ADAPTIVE_CHANGE_THRESHOLD = 0.25   # 25% change triggers adjustment
ADAPTIVE_MIN_INTERVAL_SECONDS = 300  # 5 minutes floor

async def _classify_and_schedule(self, ea_id, listing_count, sales_per_hour,
                                   last_expected_profit, session):
    tier = self._classify_tier(listing_count, sales_per_hour, last_expected_profit)
    interval_map = {"hot": SCAN_INTERVAL_HOT, "normal": SCAN_INTERVAL_NORMAL, "cold": SCAN_INTERVAL_COLD}
    base_interval = interval_map[tier]

    # Adaptive adjustment: compare to previous scan's activity (D-10)
    prev_score = await session.execute(
        select(PlayerScore)
        .where(PlayerScore.ea_id == ea_id, PlayerScore.is_viable == True)
        .order_by(PlayerScore.scored_at.desc())
        .limit(1)
    )
    prev = prev_score.scalars().first()

    interval = base_interval
    if prev is not None:
        prev_sph = prev.sales_per_hour
        if prev_sph > 0:
            delta = abs(sales_per_hour - prev_sph) / prev_sph
            if delta >= ADAPTIVE_CHANGE_THRESHOLD:
                interval = max(base_interval // 2, ADAPTIVE_MIN_INTERVAL_SECONDS)

    next_scan = datetime.utcnow() + timedelta(seconds=interval)
    record = await session.get(PlayerRecord, ea_id)
    if record is not None:
        record.scan_tier = tier
        record.next_scan_at = next_scan
    await session.commit()
```

**Note:** This reads ONE previous score row — a single indexed query on `(ea_id, scored_at)`. The index from D-13 makes this O(log n).

### Anti-Patterns to Avoid
- **Live scoring in the portfolio endpoint:** Never call `score_player()` or `FutGGClient` from an API request handler. All data comes from stored `PlayerScore` rows. (D-01, D-02)
- **Fetching all PlayerScore history for portfolio:** The portfolio endpoint needs only the latest viable score per player — use the same `func.max(scored_at)` subquery. Fetching all rows would be slow at 11k players.
- **Missing `resource_id` proxy:** Passing a plain `ea_id` integer as `entry["player"]` to `optimize_portfolio()` will raise `AttributeError` because line 36 accesses `.resource_id`.
- **Adaptive scheduling crossing tier boundaries:** The formula must clamp to `base_interval // 2` as the minimum — not to a global minimum. Hot tier should not drop below half of `SCAN_INTERVAL_HOT` (15 minutes). (D-11)
- **New config constants without adding to `src/config.py`:** All thresholds (adaptive change threshold, minimum adaptive interval) must live in `src/config.py` as `UPPER_CASE` constants.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Response validation | Custom dict serialization | Pydantic response dicts (already in project) or FastAPI response_model | Pydantic handles None serialization, type coercion |
| Async query patterns | Raw SQL strings | SQLAlchemy 2.0 async ORM select() | Established pattern, WAL mode already configured |
| Portfolio dedup logic | Custom dedup in endpoint | `optimize_portfolio()` (existing) | Contains swap loop, backfill, efficiency sorting |
| Test DB setup | Real SQLite file | `sqlite+aiosqlite:///:memory:` + `create_engine_and_tables()` | Established pattern in `test_api.py` and `test_scanner.py` |

**Key insight:** The portfolio endpoint is a thin query layer + bridge call into the existing optimizer. The optimizer is not changed. The bridge is ~10 lines.

---

## SCAN-05 Status Assessment

**Finding (HIGH confidence):** `PlayerScore` already accumulates one row per scan cycle (Phase 1 implementation). `SCAN-05` is structurally complete as an accumulation mechanism. The remaining work is:

1. **Index direction:** `models_db.py` line 49-51 defines `Index("ix_player_scores_ea_id_scored_at", "ea_id", "scored_at")`. This index already covers the pattern `WHERE ea_id = X ORDER BY scored_at DESC LIMIT 24`. SQLite uses composite indexes for both ASC and DESC on the trailing column, so the existing index is functionally sufficient for the history query. No schema change is needed.

2. **Verification step:** Confirm the index exists in the live DB with `PRAGMA index_list(player_scores)`. If the DB was created before the index definition was added to the model, `create_all` (idempotent) won't add it — an explicit `CREATE INDEX IF NOT EXISTS` may be needed.

**Recommendation:** Add a Wave 0 task to verify the index exists in the live `op_seller.db` file and add a migration step if absent.

---

## Common Pitfalls

### Pitfall 1: `optimize_portfolio()` Mutates Input Dicts
**What goes wrong:** `optimize_portfolio()` writes `s["efficiency"] = ...` on every input dict (line 24). If the same list of dicts is reused across requests, the second call starts with corrupted efficiency values.
**Why it happens:** The optimizer was designed for single-use in the CLI pipeline, not for repeated use in a long-running server.
**How to avoid:** Build a fresh list of dicts from the DB query on every portfolio request. Never cache the scored list.
**Warning signs:** Portfolio results vary unexpectedly across identical requests in the same server session.

### Pitfall 2: Missing `expire_on_commit=False`
**What goes wrong:** Accessing ORM attributes after `session.commit()` raises `MissingGreenlet` or returns expired `None`.
**Why it happens:** SQLAlchemy's default `expire_on_commit=True` marks all attributes as expired after commit, requiring a new DB round-trip. In async contexts without a running event loop at attribute access time, this fails silently or raises.
**How to avoid:** The existing `create_session_factory()` already sets `expire_on_commit=False`. All new endpoint code uses `session_factory = request.app.state.session_factory` — this is safe.
**Warning signs:** Attributes read as `None` immediately after a commit that should have values.

### Pitfall 3: Score History Returns Newest-First, Trend Needs Oldest-to-Newest
**What goes wrong:** Trend direction computed as `history[0] - history[-1]` but query returns DESC order — so `history[0]` is newest, `history[-1]` is oldest. This is correct for "current minus past = delta" but easy to reverse accidentally.
**Why it happens:** `ORDER BY scored_at DESC LIMIT 24` is the efficient query, but callers may expect chronological order.
**How to avoid:** Document in the trend function that input is newest-first. Or reverse the list: `history = list(reversed(history_rows))` for chronological access.
**Warning signs:** Trend shows "up" when the player is clearly declining.

### Pitfall 4: `ASGITransport` Does Not Trigger Lifespan in Tests
**What goes wrong:** Test app fails because `app.state.session_factory` is not set, or scanner is not attached.
**Why it happens:** `ASGITransport` bypasses FastAPI's lifespan context manager. State must be set directly on the app object before making test requests.
**How to avoid:** Use the established `make_test_app(session_factory)` pattern from `tests/test_api.py`. Wire `app.state` directly. This is already the project pattern.
**Warning signs:** `AttributeError: 'State' object has no attribute 'session_factory'` in tests.

### Pitfall 5: Budget Validation on Portfolio Endpoint
**What goes wrong:** `budget=0` or `budget=-1` causes `optimize_portfolio()` to return an empty list with confusing error message.
**Why it happens:** No input validation in the optimizer — it silently returns empty if no player fits the budget.
**How to avoid:** Add FastAPI `Query(gt=0)` constraint: `budget: int = Query(..., gt=0, description="Budget in coins")`. Return 422 automatically.
**Warning signs:** Frontend shows empty portfolio with no error for zero budget.

### Pitfall 6: Player Detail 404 vs Empty History
**What goes wrong:** `GET /api/v1/players/{ea_id}` returns 200 with null fields when the player exists in `PlayerRecord` but has no viable scores. Or returns 404 when the player exists but was never successfully scored.
**Why it happens:** `PlayerRecord` and `PlayerScore` are separate tables; a player can exist in one without entries in the other.
**How to avoid:** Return 404 only if `PlayerRecord` row doesn't exist. Return 200 with empty `score_history` and null current score fields if no viable `PlayerScore` exists.

---

## Code Examples

Verified patterns from the existing codebase:

### Portfolio Endpoint Skeleton
```python
# src/server/api/portfolio.py
"""Portfolio optimization endpoint."""
from fastapi import APIRouter, Query, Request, HTTPException

from src.server.api.players import router as _  # reuse same prefix approach
from src.server.models_db import PlayerRecord, PlayerScore
from src.optimizer import optimize_portfolio
# ... (see Pattern 3 above for _PlayerProxy and _build_scored_entry)

router = APIRouter(prefix="/api/v1")

@router.get("/portfolio")
async def get_portfolio(
    request: Request,
    budget: int = Query(..., gt=0, description="Total budget in coins"),
):
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        # 1. Latest viable score per player (established subquery pattern)
        # 2. Bridge to optimize_portfolio() input format
        # 3. Run optimizer
        # 4. Serialize response with budget summary
    ...
```

### Player Detail Endpoint Skeleton
```python
# src/server/api/players.py  (or a separate players_detail.py)
@router.get("/players/{ea_id}")
async def get_player(request: Request, ea_id: int):
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        record = await session.get(PlayerRecord, ea_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Player not found")
        # Fetch latest viable score + last 24 history rows
        # Compute trend indicators
    return { ... }
```

### Accessing Session Factory (established pattern)
```python
# Source: src/server/api/players.py, verified
session_factory = request.app.state.session_factory
async with session_factory() as session:
    result = await session.execute(stmt)
```

### Test App for New Endpoints
```python
# Source: tests/test_api.py — ASGITransport pattern, verified
app = FastAPI(title="OP Seller Test")
app.include_router(portfolio_router)
app.state.session_factory = session_factory
app.state.scanner = MockScannerService()
app.state.circuit_breaker = MockCircuitBreaker()

async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
    resp = await client.get("/api/v1/portfolio?budget=1000000")
```

---

## Environment Availability

Step 2.6: SKIPPED — Phase 2 is purely code/config changes. No new external services, CLI tools, or runtimes are required beyond what Phase 1 already uses. All dependencies are installed.

---

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 9.0.2 + pytest-asyncio 1.3.0 |
| Config file | `pytest.ini` (`asyncio_mode = auto`) |
| Quick run command | `pytest tests/test_api.py tests/test_scanner.py -x -q` |
| Full suite command | `pytest -x -q` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| API-01 | `GET /api/v1/portfolio?budget=1000000` returns optimized list with budget summary | integration | `pytest tests/test_api.py::test_portfolio_returns_200 -x` | Wave 0 |
| API-01 | Portfolio respects budget constraint (budget_used <= budget) | integration | `pytest tests/test_api.py::test_portfolio_budget_constraint -x` | Wave 0 |
| API-01 | Portfolio with zero/negative budget returns 422 | integration | `pytest tests/test_api.py::test_portfolio_invalid_budget -x` | Wave 0 |
| API-02 | `GET /api/v1/players/{ea_id}` returns all score fields + history | integration | `pytest tests/test_api.py::test_player_detail_fields -x` | Wave 0 |
| API-02 | `GET /api/v1/players/999999` returns 404 for unknown player | integration | `pytest tests/test_api.py::test_player_detail_not_found -x` | Wave 0 |
| API-02 | Player detail includes trend direction and price_change | integration | `pytest tests/test_api.py::test_player_detail_trend -x` | Wave 0 |
| SCAN-03 | `next_scan_at` is shorter for player with high activity delta vs stable player | unit | `pytest tests/test_scanner.py::test_adaptive_scheduling_shortens_interval -x` | Wave 0 |
| SCAN-03 | Adaptive interval does not cross tier boundary (stays >= base/2) | unit | `pytest tests/test_scanner.py::test_adaptive_scheduling_respects_floor -x` | Wave 0 |
| SCAN-05 | Multiple scan cycles accumulate multiple rows per player | unit | `pytest tests/test_scanner.py::test_score_history_accumulates -x` | Likely exists (test_db.py / test_scanner.py) — verify |

### Sampling Rate
- **Per task commit:** `pytest tests/test_api.py tests/test_scanner.py -x -q`
- **Per wave merge:** `pytest -x -q`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_api.py` — add `test_portfolio_*` and `test_player_detail_*` test functions (existing file, add to it)
- [ ] `tests/test_scanner.py` — add `test_adaptive_scheduling_*` test functions (existing file, add to it)
- [ ] Verify `test_score_history_accumulates` coverage — check `tests/test_db.py` and `tests/test_scanner.py` for existing accumulation test; add if absent

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Score on every API request (CLI pattern) | Score in background, serve from DB | Phase 1 | API is non-blocking, always fast |
| Fixed scan intervals | Tier-based intervals (hot/normal/cold) | Phase 1 | Efficient coverage of 11k player pool |
| Tier-based only | Tier + per-player activity delta (Phase 2) | Phase 2 | Hot players that spike get even faster re-scans |

---

## Open Questions

1. **DB index direction for history query**
   - What we know: `Index("ix_player_scores_ea_id_scored_at", "ea_id", "scored_at")` exists in `models_db.py`. SQLite can use this index for `ORDER BY scored_at DESC` on a specific `ea_id`.
   - What's unclear: Whether the index was created in the live `op_seller.db` if the file predates the index definition.
   - Recommendation: Wave 0 task — inspect `op_seller.db` with `PRAGMA index_list(player_scores)` and add `CREATE INDEX IF NOT EXISTS` if absent.

2. **`listing_count` not in `PlayerScore` (adaptive scheduling data source)**
   - What we know: `listing_count` is stored in `PlayerRecord` (updated on each scan), not in `PlayerScore`. The `PlayerScore` model has `sales_per_hour` but not `listing_count`.
   - What's unclear: Whether to compare `listing_count` deltas via `PlayerRecord` historical values (not stored) or use `sales_per_hour` from `PlayerScore` as the sole activity signal.
   - Recommendation: Use `sales_per_hour` from the most recent previous `PlayerScore` row as the activity signal for adaptive scheduling (it is stored per scan cycle). `listing_count` changes can be detected by comparing `PlayerRecord.listing_count` (current) against the previous `PlayerScore.sales_per_hour` as a proxy. Alternatively, add `listing_count` to `PlayerScore` — but D-14 says no schema change is needed. Use `sales_per_hour` delta only to keep the implementation within locked decisions.

3. **Portfolio endpoint load at scale**
   - What we know: The pool is ~11k players. The latest-viable-score subquery returns at most 11k rows — all loaded into memory, bridged to dicts, then passed to the optimizer.
   - What's unclear: Memory and latency at 11k rows.
   - Recommendation: At ~11k rows with simple scalar fields, this is approximately 1-2 MB of data — well within acceptable bounds for a single-user tool. No optimization needed for Phase 2. If latency exceeds 500ms, add a `LIMIT` to the pre-filter (only load players above a minimum efficiency threshold from the DB).

---

## Sources

### Primary (HIGH confidence)
- `src/server/api/players.py` — Latest-viable-score subquery pattern (direct code read)
- `src/server/models_db.py` — ORM schema, existing index definition (direct code read)
- `src/optimizer.py` — Input dict format, `resource_id` access, mutation behavior (direct code read)
- `src/server/scanner.py` — `_classify_and_schedule()` pattern, tier constants (direct code read)
- `src/config.py` — All interval constants, tier thresholds (direct code read)
- `tests/test_api.py` — ASGITransport test pattern, `make_test_app()` (direct code read)
- `src/server/db.py` — `expire_on_commit=False`, WAL mode, session factory (direct code read)

### Secondary (MEDIUM confidence)
- SQLAlchemy 2.0 async docs — composite index usability for ORDER BY DESC (training knowledge, consistent with code behavior)

### Tertiary (LOW confidence)
- None — all critical claims verified against existing codebase

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all libraries are installed, versions confirmed from requirements.txt
- Architecture patterns: HIGH — all patterns verified against existing Phase 1 code
- Pitfalls: HIGH — pitfalls derived from existing code behavior (optimizer mutation verified on line 24, session factory pattern verified in db.py)
- Adaptive scheduling formula: MEDIUM — the threshold values (25%, 5-minute floor) are Claude's discretion, not yet validated against real market data

**Research date:** 2026-03-25
**Valid until:** 2026-04-25 (stable stack, 30-day window)
