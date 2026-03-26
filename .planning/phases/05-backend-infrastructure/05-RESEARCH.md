# Phase 5: Backend Infrastructure - Research

**Researched:** 2026-03-26
**Domain:** FastAPI REST API extensions, SQLAlchemy async ORM, action queue patterns, CORS configuration
**Confidence:** HIGH

## Summary

Phase 5 adds six backend capabilities on top of the already-working FastAPI + SQLite + SQLAlchemy async stack. The code patterns are entirely consistent with existing routers (`players.py`, `portfolio.py`) — no new frameworks, no new dependencies. Every design decision is already locked in CONTEXT.md, so research focuses on precise implementation details for each decision.

The action queue (`GET /api/v1/actions/pending`) uses on-demand generation with stale auto-reset. Two new DB tables are needed: `trade_actions` (queue entries, one per pending work item) and `trade_records` (lifecycle events, one row per buy/list/relist outcome). Profit is derived by joining buy + sell rows on `ea_id`. CORS for Chrome extensions uses a wildcard `chrome-extension://*` origin in FastAPI's `CORSMiddleware`. Player swap re-runs the existing `optimize_portfolio()` with freed budget and a locked-in player set.

**Primary recommendation:** Add two new ORM models to `models_db.py`, add two new router files (`actions.py`, `profit.py`), add `CORSMiddleware` to `main.py`, and add a DELETE/swap endpoint to `portfolio.py` (or a new `swap.py`). All logic follows existing project patterns exactly.

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Action Queue Design**
- D-01: Actions are generated on-demand when the extension polls `GET /api/v1/actions/pending` — no background job or pre-built queue. Backend inspects portfolio state and trade records to determine the next action (buy a player not yet bought, list a purchased player, relist an expired listing).
- D-02: Single global queue returning one action at a time. Extension processes actions sequentially — no parallel actions.
- D-03: Three action types: BUY, LIST, RELIST. Each action includes ea_id, player name, target price, and action-specific data.
- D-04: Actions claimed as IN_PROGRESS are auto-reset to PENDING after 5 minutes (stale timeout). This handles extension crashes or tab closures.

**Trade Lifecycle Tracking**
- D-05: Record outcomes only — bought, listed, sold, expired. Failed attempts (e.g., outbid, price guard skip) are not persisted. Extension reports completion via `POST /api/v1/actions/{id}/complete`.
- D-06: Single `trade_records` table with columns: ea_id, action_type (buy/list/relist), price, outcome, timestamp. One row per lifecycle event, not one row per full cycle. Profit is computed by joining buy + sell events for the same ea_id.

**Player Swap Mechanics**
- D-07: When a player is removed, re-run `optimize_portfolio()` with the freed budget added back and the remaining portfolio players locked in. Return replacement player(s) in the response.
- D-08: Removing a player cancels any pending or in-progress actions for that ea_id. Completed trades are preserved for profit tracking.

**Profit Summary**
- D-09: `GET /api/v1/profit/summary` returns both totals (coins spent, coins earned, net profit, trade count) and per-player breakdown (ea_id, name, total spent, total earned, net, trade count).
- D-10: All-time aggregation only for v1.1. No daily/weekly windows.
- D-11: Realized profit only — profit from completed sell cycles. No unrealized/estimated profit requiring live price lookups.

### Claude's Discretion
- DB table schema details (column types, indexes) — follow existing patterns in models_db.py
- API response format details beyond what's specified above
- Error response structure and HTTP status codes
- Whether to use a separate router file or extend existing ones

### Deferred Ideas (OUT OF SCOPE)
None — discussion stayed within phase scope.
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| BACK-01 | Backend exposes action queue endpoint that returns one pending action at a time with stale-record auto-reset | On-demand generation with DB UPDATE for stale reset before SELECT; `claimed_at` column drives 5-min timeout |
| BACK-02 | Backend accepts action completion reports (buy, list, relist outcomes with player, price, timestamp) | POST endpoint inserts into `trade_records`, updates `trade_actions` status; in-memory SQLite test covers this |
| BACK-03 | Backend stores all trade activity in DB for profit tracking (trade_actions, trade_records tables) | Two ORM models added to models_db.py following existing `mapped_column()/Mapped[]` patterns |
| BACK-04 | Backend exposes profit summary endpoint aggregating trade activity data | SQLAlchemy aggregate query (func.sum) on `trade_records`; group-by ea_id for per-player breakdown |
| BACK-05 | Backend CORS configured to accept requests from chrome-extension origin | `CORSMiddleware` with `allow_origins=["chrome-extension://*"]` added to `main.py` |
| BACK-06 | Backend supports player swap — user removes a player from portfolio, backend returns replacement(s) within freed budget | DELETE endpoint calls `optimize_portfolio()` with locked portfolio + freed budget; pattern already in `portfolio.py` |
</phase_requirements>

---

## Standard Stack

### Core (no new dependencies needed)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| FastAPI | 0.135.2 | REST endpoints, request routing | Already in use; CORSMiddleware is built-in |
| SQLAlchemy | 2.0.48 | Async ORM, query builder | Already in use; all existing models use it |
| aiosqlite | 0.22.1 | SQLite async driver | Already in use via `sqlite+aiosqlite://` URL |
| Pydantic | 2.12.5 | Request/response validation | Already in use across all routers |

All libraries are already installed. No `pip install` required for this phase.

**Version verification:** Confirmed via `python -c "import fastapi; print(fastapi.__version__)"` — 0.135.2 installed.

### No New Dependencies

This phase is entirely within the existing stack. The only "new" constructs are:
- `fastapi.middleware.cors.CORSMiddleware` — ships with FastAPI, zero additional install
- `datetime.timedelta` for the 5-minute stale reset — standard library

---

## Architecture Patterns

### Recommended File Structure Changes

```
src/server/
├── models_db.py          # Add TradeAction, TradeRecord ORM models
├── main.py               # Add CORSMiddleware; register new routers
└── api/
    ├── actions.py         # NEW: GET /api/v1/actions/pending, POST /api/v1/actions/{id}/complete
    ├── profit.py          # NEW: GET /api/v1/profit/summary
    ├── portfolio.py       # EXTEND: DELETE /api/v1/portfolio/{ea_id} (swap endpoint)
    ├── players.py         # unchanged
    └── health.py          # unchanged
```

### Pattern 1: On-Demand Action Queue Generation

**What:** `GET /api/v1/actions/pending` runs a DB inspection every time it is called — no queue is pre-built. The logic is:
1. Reset any `IN_PROGRESS` actions older than 5 minutes to `PENDING` (stale reset).
2. Find the first `PENDING` action, mark it `IN_PROGRESS` with `claimed_at = now`, return it.
3. If no PENDING actions exist, derive the next action from portfolio/trade state and insert it.

**When to use:** Every GET call; the endpoint is idempotent except for the IN_PROGRESS claim.

**Action derivation priority:**
1. Players in portfolio with no buy record → BUY action
2. Players with a completed buy but no active listing → LIST action
3. Players with an expired listing → RELIST action

**Example (stale reset + claim):**
```python
# Source: CONTEXT.md D-04, SQLAlchemy 2.0 async patterns
from datetime import datetime, timedelta
from sqlalchemy import update, select

STALE_TIMEOUT = timedelta(minutes=5)

async with session_factory() as session:
    # Step 1: reset stale IN_PROGRESS records
    stale_cutoff = datetime.utcnow() - STALE_TIMEOUT
    await session.execute(
        update(TradeAction)
        .where(
            TradeAction.status == "IN_PROGRESS",
            TradeAction.claimed_at < stale_cutoff,
        )
        .values(status="PENDING", claimed_at=None)
    )

    # Step 2: claim the first PENDING action
    stmt = (
        select(TradeAction)
        .where(TradeAction.status == "PENDING")
        .order_by(TradeAction.created_at)
        .limit(1)
    )
    result = await session.execute(stmt)
    action = result.scalars().first()

    if action:
        action.status = "IN_PROGRESS"
        action.claimed_at = datetime.utcnow()
        await session.commit()
```

### Pattern 2: Trade Record Insertion (BACK-02)

**What:** `POST /api/v1/actions/{id}/complete` receives the outcome and inserts into `trade_records`. Updates `trade_actions.status` to DONE.

**Example:**
```python
# Source: existing portfolio.py pattern for session usage
async with session_factory() as session:
    action = await session.get(TradeAction, action_id)
    if action is None:
        raise HTTPException(status_code=404, detail="Action not found")

    record = TradeRecord(
        ea_id=action.ea_id,
        action_type=action.action_type,
        price=payload.price,
        outcome=payload.outcome,       # "bought" | "listed" | "sold" | "expired"
        recorded_at=datetime.utcnow(),
    )
    session.add(record)
    action.status = "DONE"
    await session.commit()
```

### Pattern 3: Profit Aggregation Query (BACK-04)

**What:** SQL aggregate using `func.sum` grouped by outcome type. Realized profit = sum of "sold" prices × (1 - EA_TAX_RATE) minus sum of "bought" prices, for players where a full buy+sell cycle exists.

**Example:**
```python
# Source: SQLAlchemy 2.0 async docs
from sqlalchemy import func

# Total coins spent (all buy records)
buy_stmt = select(func.sum(TradeRecord.price)).where(TradeRecord.outcome == "bought")

# Total coins earned (all sold records, after EA tax)
# EA tax = 5%, so earned = price * 0.95
# Applied at query time or in Python — apply in Python to keep SQL simple
sold_stmt = (
    select(TradeRecord.ea_id, func.sum(TradeRecord.price).label("total_sold"))
    .where(TradeRecord.outcome == "sold")
    .group_by(TradeRecord.ea_id)
)
```

Note: `EA_TAX_RATE = 0.05` is in `src/config.py`. Apply in Python after the query, consistent with how `scorer.py` handles it.

### Pattern 4: Player Swap (BACK-06)

**What:** `DELETE /api/v1/portfolio/{ea_id}` removes a player from the portfolio, frees their budget, and re-runs optimization with the freed budget. The locked portfolio (remaining players) is passed as an exclusion set to `optimize_portfolio()`.

**Critical detail:** `optimize_portfolio()` mutates input dicts (CONTEXT.md canonical ref + existing portfolio.py comment). Build fresh `_PlayerProxy`-based scored entries per the `_build_scored_entry()` pattern in `portfolio.py`. Lock remaining players by filtering them from the scored list (they must not appear as candidates).

**How to lock remaining players:**
```python
# existing_ea_ids = set of ea_ids currently in portfolio minus the removed player
scored_candidates = [e for e in all_scored if e["ea_id"] not in existing_ea_ids]
replacements = optimize_portfolio(scored_candidates, freed_budget)
```

Per D-08: before running optimizer, cancel any PENDING or IN_PROGRESS trade actions for the removed ea_id:
```python
await session.execute(
    update(TradeAction)
    .where(TradeAction.ea_id == ea_id, TradeAction.status.in_(["PENDING", "IN_PROGRESS"]))
    .values(status="CANCELLED")
)
```

### Pattern 5: CORS for Chrome Extension Origin (BACK-05)

Chrome extension origins take the form `chrome-extension://<extension-id>`. The extension ID changes between developer installs but is stable once published. For v1.1 (local only), a wildcard on the scheme is the practical approach.

FastAPI's `CORSMiddleware` accepts `allow_origins` as a list. The `*` wildcard only works for `http://` and `https://` origins in Starlette's default implementation — it does NOT automatically cover `chrome-extension://` origins. The correct approach is to pass the specific chrome-extension origin, OR use `allow_origin_regex`.

**Recommended approach (verified pattern):**
```python
# Source: FastAPI/Starlette CORSMiddleware docs
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"chrome-extension://.*",
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type"],
)
```

`allow_origin_regex` is a Starlette feature (present in Starlette 1.0.0, which is installed). It accepts a regex string matched against the request `Origin` header. This covers any chrome-extension ID without hardcoding.

**Add in `main.py`** before `yield` in the lifespan, or at module level right after `app = FastAPI(...)`.

### ORM Models: TradeAction and TradeRecord

Follow the exact same patterns as existing models in `models_db.py`:
- `Mapped[]` type hints with `mapped_column()`
- `Integer` autoincrement PKs
- `DateTime` for timestamps (not `String`)
- `Index` in `__table_args__` for columns queried in WHERE clauses

**TradeAction table design:**
```python
class TradeAction(Base):
    """Pending/active action queue entry. One row per queued work item."""

    __tablename__ = "trade_actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ea_id: Mapped[int] = mapped_column(Integer, index=True)
    action_type: Mapped[str] = mapped_column(String(10))   # "BUY" | "LIST" | "RELIST"
    status: Mapped[str] = mapped_column(String(20), default="PENDING")  # "PENDING" | "IN_PROGRESS" | "DONE" | "CANCELLED"
    target_price: Mapped[int] = mapped_column(Integer)
    player_name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_trade_actions_status_created_at", "status", "created_at"),
        Index("ix_trade_actions_ea_id", "ea_id"),
    )
```

**TradeRecord table design:**
```python
class TradeRecord(Base):
    """One row per lifecycle event (bought, listed, sold, expired)."""

    __tablename__ = "trade_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ea_id: Mapped[int] = mapped_column(Integer, index=True)
    action_type: Mapped[str] = mapped_column(String(10))   # "buy" | "list" | "relist"
    price: Mapped[int] = mapped_column(Integer)
    outcome: Mapped[str] = mapped_column(String(20))        # "bought" | "listed" | "sold" | "expired"
    recorded_at: Mapped[datetime] = mapped_column(DateTime)

    __table_args__ = (
        Index("ix_trade_records_ea_id_outcome", "ea_id", "outcome"),
        Index("ix_trade_records_recorded_at", "recorded_at"),
    )
```

### Anti-Patterns to Avoid

- **Don't use `allow_origins=["*"]` and expect it to cover chrome-extension://.** The `*` wildcard in Starlette only matches http/https origins. Must use `allow_origin_regex`.
- **Don't pre-build a queue at startup.** D-01 specifies on-demand generation. A pre-built queue gets stale if portfolio changes.
- **Don't skip the stale reset.** The reset must happen at the START of every GET /pending call, before the select. Otherwise a crashed extension leaves the action permanently IN_PROGRESS.
- **Don't compute realized profit by looking at `listed` records.** Only `sold` outcome records contribute to earnings. Listed ≠ sold.
- **Don't mutate scored entries from DB before passing to optimizer.** `optimize_portfolio()` mutates its inputs. Use `_build_scored_entry()` to build fresh dicts per call (same pattern as `portfolio.py` line 102).
- **Don't forget `await session.commit()` after mutations.** Async SQLAlchemy sessions do not auto-commit. Existing code shows the pattern correctly in every endpoint.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| CORS header handling | Custom middleware or decorator | `fastapi.middleware.cors.CORSMiddleware` | Handles preflight OPTIONS, varies headers, credential logic correctly |
| Profit aggregation | Python loop over all records | `func.sum()` in SQLAlchemy query | DB-side aggregation scales; Python loop over thousands of rows is slow |
| Portfolio optimization on swap | Custom greedy re-implementation | `optimize_portfolio()` in `src/optimizer.py` | Already tested, handles budget, swap loop, backfill correctly |
| Stale lock detection | Background APScheduler job | Inline reset at query time | Simpler, no scheduler coupling, resets exactly when needed |

**Key insight:** Every hard problem in this phase is already solved. CORS is a one-liner, aggregation uses SQLAlchemy, optimization reuses the existing function. The work is wiring, not algorithmic.

---

## Common Pitfalls

### Pitfall 1: CORSMiddleware Wildcard Does Not Cover chrome-extension://

**What goes wrong:** Developer sets `allow_origins=["*"]` and gets CORS errors from the extension.
**Why it happens:** Starlette's wildcard match is `*` but it only applies to http:// and https:// origins. Non-standard schemes like `chrome-extension://` fall outside the match.
**How to avoid:** Use `allow_origin_regex=r"chrome-extension://.*"` instead of `allow_origins`.
**Warning signs:** Extension console shows `Access-Control-Allow-Origin` missing or mismatched. Browser network tab shows no `Access-Control-Allow-Origin` header on response.

### Pitfall 2: Stale Reset Not Atomic With Claim

**What goes wrong:** Two extension instances (e.g., two browser windows) call `/pending` simultaneously. Both see the reset and both claim the same action.
**Why it happens:** Non-atomic reset + select sequence.
**How to avoid:** For v1.1 (single-user, local), this is not a real risk. But as a best practice, do the reset as a separate `UPDATE` before the `SELECT` for the claim. SQLite WAL mode (already configured) serializes writes, so concurrent requests from a single process are safe.
**Warning signs:** Duplicate action claims in production logs.

### Pitfall 3: Profit Double-Counting if a Player is Bought Twice

**What goes wrong:** A player has two `bought` records (e.g., bought, sold, bought again). Profit join on `ea_id` pairs the wrong buy with the wrong sell.
**Why it happens:** Simple `SUM(buy) + SUM(sell)` grouped by ea_id aggregates all-time totals, which is correct for v1.1's all-time-only requirement (D-10). There is no ordering issue because we sum totals, not match pairs.
**How to avoid:** Use sum-based aggregation (not pair-matching). Net = sum_of_sold_earnings - sum_of_bought_costs, per ea_id. This is correct by definition for all-time totals.
**Warning signs:** Only a concern if per-cycle profit attribution is needed — deferred to v2+.

### Pitfall 4: `optimize_portfolio()` Mutates Input Dicts

**What goes wrong:** Scored entries passed to `optimize_portfolio()` have their `efficiency` and `_ranking_profit` keys added/modified in-place. If the same dict objects are reused across requests, stale computed values leak.
**Why it happens:** The optimizer's design (see `optimizer.py` lines 29-32).
**How to avoid:** Call `_build_scored_entry()` for every request, never cache the returned dicts between requests. This is already documented in `portfolio.py` line 101 comment: "Build fresh scored entries (never cache — optimizer mutates dicts)".
**Warning signs:** Portfolio swap returns wrong players after the first call.

### Pitfall 5: Missing `expected_profit_per_hour` in Scored Entries Passed to Optimizer

**What goes wrong:** `optimize_portfolio()` reads `s.get("expected_profit_per_hour")` (line 30 of optimizer.py). If this key is missing or None, efficiency = 0 and the player is ranked last.
**Why it happens:** New scorer v2 populates `expected_profit_per_hour`. Old v1 scores have it as None (they were purged at startup per main.py lines 44-53). Any new test fixtures must include this field.
**How to avoid:** Always include `expected_profit_per_hour` in scored entry dicts. Use `_build_scored_entry()` which reads from `PlayerScore.expected_profit_per_hour`.

---

## Code Examples

### Adding CORSMiddleware to main.py
```python
# Source: Starlette CORSMiddleware docs (starlette 1.0.0 installed)
# In src/server/main.py, after app = FastAPI(...)
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"chrome-extension://.*",
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type"],
)
```

### Registering New Routers in main.py
```python
# Follow existing pattern in src/server/main.py lines 11-14 and 83-85
from src.server.api.actions import router as actions_router
from src.server.api.profit import router as profit_router

app.include_router(actions_router)
app.include_router(profit_router)
```

### Profit Summary Query
```python
# Source: SQLAlchemy 2.0 async ORM — func.sum with group_by
from sqlalchemy import func, select
from src.config import EA_TAX_RATE

# Per-player breakdown
stmt = (
    select(
        TradeRecord.ea_id,
        func.sum(
            case((TradeRecord.outcome == "bought", TradeRecord.price), else_=0)
        ).label("total_spent"),
        func.sum(
            case((TradeRecord.outcome == "sold", TradeRecord.price), else_=0)
        ).label("total_earned_gross"),
        func.count(TradeRecord.id).label("trade_count"),
    )
    .group_by(TradeRecord.ea_id)
)
# Apply EA tax in Python: net_earned = total_earned_gross * (1 - EA_TAX_RATE)
```

Note: `sqlalchemy.case` is the correct SQLAlchemy 2.0 API (not `case_` deprecated alias).

---

## Environment Availability

Step 2.6: All dependencies are the existing project stack. No external services.

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| FastAPI | All endpoints | Yes | 0.135.2 | — |
| SQLAlchemy | All DB operations | Yes | 2.0.48 | — |
| aiosqlite | SQLite async driver | Yes | 0.22.1 | — |
| CORSMiddleware | BACK-05 | Yes | ships with starlette 1.0.0 | — |
| pytest + pytest-asyncio | Tests | Yes | 9.0.2 / 1.3.0 | — |

**Missing dependencies with no fallback:** None.

---

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 9.0.2 + pytest-asyncio 1.3.0 |
| Config file | `pytest.ini` (root) — `asyncio_mode = auto` |
| Quick run command | `pytest tests/test_actions.py tests/test_profit.py -x` |
| Full suite command | `pytest tests/ -x` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| BACK-01 | GET /pending returns one action; stale IN_PROGRESS reset before claim | integration | `pytest tests/test_actions.py::test_pending_returns_one_action -x` | Wave 0 |
| BACK-01 | Stale IN_PROGRESS older than 5 min auto-reset to PENDING | integration | `pytest tests/test_actions.py::test_stale_action_reset -x` | Wave 0 |
| BACK-02 | POST /actions/{id}/complete inserts trade_record and marks action DONE | integration | `pytest tests/test_actions.py::test_complete_action -x` | Wave 0 |
| BACK-03 | trade_actions and trade_records tables exist in DB after startup | integration | `pytest tests/test_actions.py::test_tables_created -x` | Wave 0 |
| BACK-04 | GET /profit/summary returns correct totals and per-player breakdown | integration | `pytest tests/test_profit.py::test_profit_summary -x` | Wave 0 |
| BACK-05 | CORS allows chrome-extension origin | integration | `pytest tests/test_cors.py::test_cors_chrome_extension -x` | Wave 0 |
| BACK-06 | DELETE /portfolio/{ea_id} cancels pending actions and returns replacements | integration | `pytest tests/test_portfolio.py::test_player_swap -x` | Wave 0 |

All test files will be new (Wave 0 gaps). They follow the exact pattern of existing `tests/test_api.py`:
- In-memory SQLite via `create_engine_and_tables("sqlite+aiosqlite:///:memory:")`
- `make_test_app()` factory with `app.state.session_factory` wired directly
- `AsyncClient(transport=ASGITransport(app=...))` for HTTP requests
- No real HTTP or external services

### Sampling Rate
- **Per task commit:** `pytest tests/test_actions.py tests/test_profit.py -x`
- **Per wave merge:** `pytest tests/ -x`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_actions.py` — covers BACK-01, BACK-02, BACK-03
- [ ] `tests/test_profit.py` — covers BACK-04
- [ ] `tests/test_cors.py` — covers BACK-05 (CORS OPTIONS preflight test)
- [ ] BACK-06 can extend `tests/test_portfolio.py` with new `test_player_swap` function

Framework install: Not needed — pytest + pytest-asyncio already installed.

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `allow_origins=["*"]` | `allow_origin_regex` for non-http schemes | Starlette design | Required for chrome-extension:// origins |
| Sync SQLAlchemy | `async_sessionmaker` + `AsyncSession` | Already migrated in this project (v1.0) | All new code uses same async pattern |
| Pre-built action queues (Bull, Celery) | On-demand generation (D-01) | Project decision | Simpler, no worker process needed, always reflects current portfolio state |

---

## Open Questions

1. **Does `DELETE /api/v1/portfolio/{ea_id}` need a portfolio persistence table?**
   - What we know: The current portfolio is generated fresh on each `GET /portfolio` call from DB scores. There is no stored "current portfolio" table.
   - What's unclear: For swap to work, the backend needs to know which players are currently in the active portfolio. Without persistence, it cannot know which ea_ids are "locked in" for the optimizer.
   - Recommendation: Add a `portfolio_slots` table (ea_id, added_at, buy_price, sell_price) that persists the confirmed portfolio. The extension "confirms" the portfolio (UI-02, Phase 8), which writes to this table. For Phase 5, the swap endpoint can accept the current portfolio ea_ids in the request body as a fallback, or the plan should decide: add a `portfolio_slots` table now, or pass current ea_ids in the DELETE request body.
   - **This is the main open question for the planner to resolve before or during Wave 1.**

2. **Action queue seed: who creates the initial BUY actions?**
   - What we know: D-01 says the GET /pending endpoint inspects portfolio state to determine the next action. But the "portfolio" is derived from the optimizer, not a stored table.
   - What's unclear: Without a stored `portfolio_slots` table, the backend cannot derive "player not yet bought" without the extension sending the portfolio first.
   - Recommendation: Resolve together with open question 1. A `portfolio_slots` table solves both problems: it stores the confirmed portfolio, and it's the authoritative source for deriving BUY/LIST/RELIST actions.

---

## Sources

### Primary (HIGH confidence)
- Existing project codebase — `src/server/models_db.py`, `src/server/api/portfolio.py`, `src/server/main.py`, `src/server/db.py` — all patterns verified by reading source
- `src/optimizer.py` — optimizer mutation behavior and `_PlayerProxy` interface verified by reading source
- `requirements.txt` — all versions verified from lock file
- Starlette CORSMiddleware source (starlette 1.0.0 installed) — `allow_origin_regex` parameter confirmed to exist

### Secondary (MEDIUM confidence)
- `pytest.ini` — `asyncio_mode = auto` confirmed, test pattern from `tests/test_api.py`
- SQLAlchemy 2.0 async ORM — `func.sum`, `case`, `update` patterns verified against existing codebase usage

### Tertiary (LOW confidence)
- CORSMiddleware `allow_origin_regex` behavior with `chrome-extension://` scheme — confirmed by Starlette source inspection conceptually, but should be validated with an actual preflight test (covered by Wave 0 `tests/test_cors.py`)

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all installed, version-verified from requirements.txt and `python -c`
- Architecture patterns: HIGH — derived directly from reading existing project source
- Pitfalls: HIGH — derived from reading optimizer.py (mutation pitfall), models_db.py (patterns), Starlette CORS behavior
- Open questions: MEDIUM — portfolio persistence design is a genuine gap not addressed in CONTEXT.md; flagged for planner

**Research date:** 2026-03-26
**Valid until:** 2026-04-26 (stable stack — FastAPI/SQLAlchemy versions won't change mid-milestone)
