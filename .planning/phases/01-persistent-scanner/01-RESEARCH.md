# Phase 01: Persistent Scanner - Research

**Researched:** 2026-03-25
**Domain:** FastAPI + APScheduler + SQLAlchemy async + SQLite — persistent background scanner with REST API
**Confidence:** HIGH

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Top-Players Endpoint**
- D-01: Default return count is 100 players (matches TARGET_PLAYER_COUNT)
- D-02: Basic filtering: `price_min`, `price_max`, and `limit` query params
- D-03: Pagination via `offset`/`limit` params
- D-04: Response includes score summary per player: name, ea_id, price, margin_pct, op_ratio, expected_profit, efficiency, last_scanned

**Scan Priority Logic**
- D-05: Priority determined by listing activity (live listing count, recent sales volume)
- D-06: 3 tiers: Hot (every 30 min), Normal (every 1 hr), Cold (every 2-3 hrs)
- D-07: Tier reassignment happens on every scan (check listing activity, reclassify if needed)
- D-08: Bootstrap on first startup: full discovery scan across 11k-200k range, score everyone once, then switch to priority-based scheduling

**Failure Transparency**
- D-09: Health endpoint + log files for failure visibility (no push notifications)
- D-10: Health endpoint reports: scanner running/stopped, scan success rate (last hour), circuit breaker state (closed/open/half-open), last scan timestamp, players in DB, queue depth
- D-11: When circuit breaker is open, API serves stale data with `is_stale: true` flag and `last_scanned` timestamp

**Staleness Threshold**
- D-12: Player data considered stale after 4 hours without a fresh scan
- D-13: Stale players included in results with `is_stale` flag (not excluded)
- D-14: Target pool coverage: 80% of discovered players should have fresh (non-stale) data at any given time

### Claude's Discretion
- Database schema design (tables, indexes, relationships)
- Rate limit backoff parameters and jitter implementation
- Circuit breaker implementation details (half-open probe count, reset timing)
- APScheduler job configuration and queue management
- Server startup/shutdown lifecycle hooks
- Project layout for new server code (`src/server/` or similar)

### Deferred Ideas (OUT OF SCOPE)
None — discussion stayed within phase scope.
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| SCAN-01 | Server runs a persistent scanner for all players in the 11k–200k price range | APScheduler AsyncIOScheduler with interval jobs + FutGGClient.discover_players() |
| SCAN-02 | Scanner stores player scores, market data, and price history in SQLite | SQLAlchemy 2.0 async + aiosqlite + WAL mode; ORM tables for players, scores, price_history |
| SCAN-04 | Scanner respects fut.gg rate limits with throttling, exponential backoff, and circuit breaker | tenacity for retry/backoff/jitter; manual circuit breaker state machine (3 states) |
| API-03 | REST API endpoint returns top OP sell players with scores, margins, and ratios | FastAPI GET /api/v1/players/top with SQLAlchemy async query from scores table |
| API-04 | Scanner prioritizes request budget — more frequent scans for high-value/high-activity players, less frequent for stale ones | APScheduler dynamic job scheduling per tier (Hot/Normal/Cold); reassign tier on each scan |
</phase_requirements>

---

## Summary

This phase transforms the existing one-shot CLI pipeline into a persistent server. The core components are: a FastAPI app (web server + REST API), an APScheduler `AsyncIOScheduler` (background scan loop), a SQLAlchemy 2.0 async ORM with aiosqlite (persistence), and a hand-rolled circuit breaker over `tenacity` retries (resilience). The existing `FutGGClient`, `score_player()`, and Pydantic models are reused unchanged — the new code wraps them with persistence and scheduling.

The stack choices are already locked in STATE.md: FastAPI 0.135, APScheduler 3.11 (pinned <4.0), SQLAlchemy 2.0 async, aiosqlite. These are verified as current, production-ready, and well-integrated. APScheduler 4.x is explicitly NOT production-ready — it is a ground-up rewrite with no migration path from 3.x.

The three-tier scan priority system (Hot/Normal/Cold) is implemented by maintaining a dict of player ea_id → next scan time, updated on every successful scan. APScheduler runs a single "dispatch" job on a short interval (e.g., 30 sec) that pulls due ea_ids from the queue and enqueues scan coroutines.

**Primary recommendation:** Use `AsyncIOScheduler` (APScheduler 3.11) inside FastAPI's `lifespan` context manager. SQLite in WAL mode handles concurrent reader + single writer safely. Implement circuit breaker as a small state machine class — do not use `pybreaker` or `aiobreaker` as they add unnecessary complexity for a single upstream (fut.gg).

---

## Standard Stack

### Core (new dependencies for this phase)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| fastapi | 0.135.2 | HTTP framework, REST API, lifespan hooks | Locked decision; async-native, Pydantic v2 native, standard for Python APIs |
| uvicorn | 0.42.0 | ASGI server to run FastAPI | Standard pairing; supports `--reload` for dev |
| apscheduler | 3.11.2 | Background job scheduling | Locked decision; `AsyncIOScheduler` runs on same event loop as FastAPI |
| sqlalchemy | 2.0.48 | Async ORM for SQLite persistence | Locked decision; 2.0 unified API, async session support |
| aiosqlite | 0.22.1 | Async SQLite driver | Locked decision; required for SQLAlchemy async + SQLite |
| tenacity | 9.1.4 | Retry with exponential backoff + jitter | Best-in-class retry library; async-native with `wait_exponential_jitter` |

### Existing (already in requirements.txt, no change)

| Library | Version | Purpose |
|---------|---------|---------|
| httpx | 0.28.1 | FutGGClient uses this; no change |
| pydantic | 2.12.5 | Models; FastAPI uses pydantic v2 natively |
| pytest | 9.0.2 | Test runner |
| pytest-asyncio | 1.3.0 | Async test support |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| APScheduler 3.x | APScheduler 4.x | 4.x is NOT production-ready (pre-release, breaking changes); 3.x is stable |
| APScheduler 3.x | Celery + Redis | Celery requires Redis broker — adds infra complexity; overkill for single-machine single-user |
| APScheduler 3.x | asyncio.create_task + sleep loop | More fragile, harder to observe, no built-in job tracking |
| tenacity (retries) + hand-rolled circuit breaker | pybreaker / aiobreaker | `aiobreaker` is unmaintained (last release 2019); pybreaker is sync-only; hand-rolled is simpler |
| SQLAlchemy ORM | raw aiosqlite | SQLAlchemy gives schema migrations path (Alembic), type safety, cleaner query API |

**Installation:**
```bash
pip install fastapi==0.135.2 uvicorn==0.42.0 "apscheduler>=3.11,<4.0" sqlalchemy==2.0.48 aiosqlite==0.22.1 tenacity==9.1.4
```

**Version verification (confirmed against PyPI 2026-03-25):**
- fastapi: 0.135.2 (latest)
- uvicorn: 0.42.0 (latest)
- apscheduler: 3.11.2 (latest stable 3.x; 4.x pre-release exists but not production-safe)
- sqlalchemy: 2.0.48 (latest 2.0.x)
- aiosqlite: 0.22.1 (latest)
- tenacity: 9.1.4 (latest)

---

## Architecture Patterns

### Recommended Project Structure

```
src/
├── __init__.py
├── config.py              — Add scanner constants (SCAN_INTERVAL_*, STALE_HOURS, CB_*)
├── models.py              — Existing Pydantic models (no change)
├── protocols.py           — Existing MarketDataClient protocol (no change)
├── scorer.py              — Existing score_player() (no change)
├── optimizer.py           — Existing optimize_portfolio() (not used in Phase 1)
├── futgg_client.py        — Existing FutGGClient (no change)
├── main.py                — Existing CLI entry point (no change)
└── server/
    ├── __init__.py
    ├── main.py            — FastAPI app, lifespan, app factory
    ├── db.py              — SQLAlchemy engine, session factory, Base, WAL setup
    ├── models_db.py       — SQLAlchemy ORM table definitions
    ├── scanner.py         — ScannerService: discovery, scan loop, tier management
    ├── circuit_breaker.py — CircuitBreaker state machine
    ├── scheduler.py       — APScheduler setup, job definitions
    └── api/
        ├── __init__.py
        ├── players.py     — GET /api/v1/players/top
        └── health.py      — GET /api/v1/health
```

Tests mirror:
```
tests/
├── __init__.py
├── mock_client.py         — Existing (no change)
├── test_scorer.py         — Existing (no change)
├── test_optimizer.py      — Existing (no change)
├── test_integration.py    — Existing (no change)
├── test_circuit_breaker.py — New: unit tests for CB state machine
├── test_scanner.py        — New: scanner service unit tests
└── test_api.py            — New: FastAPI endpoint tests
```

### Pattern 1: FastAPI Lifespan with Scheduler

Use `@asynccontextmanager` lifespan to manage scheduler + DB engine lifecycle. Do NOT use deprecated `@app.on_event()`.

```python
# src/server/main.py
# Source: FastAPI official docs https://fastapi.tiangolo.com/advanced/events/
from contextlib import asynccontextmanager
from fastapi import FastAPI
from src.server.db import create_engine_and_tables
from src.server.scheduler import create_scheduler

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: init DB, start scheduler
    engine = await create_engine_and_tables()
    scheduler = create_scheduler(engine)
    app.state.engine = engine
    app.state.scheduler = scheduler
    scheduler.start()
    yield
    # Shutdown: stop scheduler, dispose engine
    scheduler.shutdown(wait=False)
    await engine.dispose()

app = FastAPI(lifespan=lifespan)
```

### Pattern 2: SQLAlchemy Async Engine with WAL Mode

```python
# src/server/db.py
# Source: SQLAlchemy 2.0 asyncio docs + SQLAlchemy discussions/12767
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import event

DATABASE_URL = "sqlite+aiosqlite:///./op_seller.db"

engine = create_async_engine(DATABASE_URL, echo=False)

# WAL mode: must attach to sync_engine for aiosqlite
@event.listens_for(engine.sync_engine, "connect")
def enable_wal(dbapi_connection, connection_record):
    dbapi_connection.execute("PRAGMA journal_mode=WAL")
    dbapi_connection.execute("PRAGMA synchronous=NORMAL")

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,   # CRITICAL: prevents MissingGreenlet errors
)

async def get_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session
```

**Why `expire_on_commit=False` is mandatory:** After `session.commit()`, SQLAlchemy normally expires all loaded attributes so they reload on next access. In async context, that reload requires a new implicit I/O on an attribute access, which happens outside the session context manager — this raises `MissingGreenlet`. Setting `expire_on_commit=False` keeps the last-loaded values in memory instead.

**Why `sync_engine` for the event listener:** `aiosqlite` wraps `pysqlite` in a background thread. The `"connect"` event fires on the underlying `pysqlite` connection. When using `create_async_engine`, the event must be attached to `engine.sync_engine`, not `engine` directly.

### Pattern 3: APScheduler AsyncIOScheduler

```python
# src/server/scheduler.py
# Source: APScheduler 3.x docs https://apscheduler.readthedocs.io/en/3.x/
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

def create_scheduler(engine) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="UTC")
    # Dispatch loop: checks priority queue every 30 seconds
    scheduler.add_job(
        scanner_dispatch_job,
        trigger=IntervalTrigger(seconds=30),
        id="scan_dispatch",
        max_instances=1,       # Never run two dispatch cycles simultaneously
        coalesce=True,         # Skip missed fires, don't pile up
        replace_existing=True,
    )
    return scheduler
```

**Key APScheduler 3.x settings for a scanner:**
- `max_instances=1` on dispatch job — prevents concurrent scan waves
- `coalesce=True` — if system is slow and a job fires late, run once not multiple times
- `AsyncIOScheduler` (not `BackgroundScheduler`) — runs jobs directly on the FastAPI event loop so scanner coroutines can `await` freely

### Pattern 4: Three-Tier Priority Queue

The priority system is managed by `ScannerService`, not by multiple APScheduler jobs. A single dispatch job fires every 30 seconds. The dispatcher checks an in-memory dict `next_scan_at: dict[int, datetime]` and collects ea_ids that are due.

```python
# Tier intervals (in config.py)
SCAN_INTERVAL_HOT    = 30 * 60      # 30 minutes (seconds)
SCAN_INTERVAL_NORMAL = 60 * 60      # 1 hour
SCAN_INTERVAL_COLD   = 2.5 * 3600   # 2.5 hours (midpoint of 2-3 hr range)
STALE_THRESHOLD_HOURS = 4

# Tier assignment logic (in scanner.py)
def classify_tier(listing_count: int, sales_per_hour: float) -> str:
    if listing_count >= 50 or sales_per_hour >= 15:
        return "hot"
    if listing_count >= 20 or sales_per_hour >= 7:
        return "normal"
    return "cold"
```

### Pattern 5: Circuit Breaker State Machine

Implement as a plain Python class — no external library needed. Three states: CLOSED (normal), OPEN (failing, reject calls), HALF_OPEN (probe one call).

```python
# src/server/circuit_breaker.py
import time
from enum import Enum

class CBState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

class CircuitBreaker:
    def __init__(
        self,
        failure_threshold: int = 5,    # failures before opening
        success_threshold: int = 2,    # successes in half-open before closing
        recovery_timeout: float = 60.0, # seconds before trying half-open
    ):
        self.state = CBState.CLOSED
        self._failures = 0
        self._successes = 0
        self._opened_at: float | None = None
        ...

    def record_success(self): ...
    def record_failure(self): ...

    @property
    def is_open(self) -> bool:
        if self.state == CBState.OPEN:
            if time.monotonic() - self._opened_at >= self.recovery_timeout:
                self.state = CBState.HALF_OPEN
                return False
            return True
        return False
```

**Circuit breaker parameters (discretion):**
- `failure_threshold = 5` consecutive failures → OPEN
- `recovery_timeout = 60` seconds → try HALF_OPEN
- `success_threshold = 2` probe successes → CLOSED

### Pattern 6: Tenacity Retry for fut.gg Calls

Wrap `FutGGClient._get()` pattern or add a scanner-level retry decorator:

```python
# Source: tenacity docs https://tenacity.readthedocs.io/
from tenacity import (
    retry, stop_after_attempt, wait_exponential_jitter,
    retry_if_exception_type, before_sleep_log
)
import httpx
import logging

logger = logging.getLogger(__name__)

@retry(
    retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.TimeoutException)),
    stop=stop_after_attempt(3),
    wait=wait_exponential_jitter(initial=2, max=60, jitter=10),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)
async def fetch_with_retry(client, ea_id: int):
    return await client.get_market_data(ea_id)
```

**Backoff parameters (discretion):**
- Initial wait: 2 seconds after first failure
- Max wait: 60 seconds cap
- Jitter: ±10 seconds (prevents thundering herd if many players retry simultaneously)
- Max attempts: 3 (after which circuit breaker failure is recorded)

### Pattern 7: Database Schema

```python
# src/server/models_db.py
from datetime import datetime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import String, Integer, Float, DateTime, Index

class Base(DeclarativeBase):
    pass

class PlayerRecord(Base):
    __tablename__ = "players"
    ea_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    rating: Mapped[int] = mapped_column(Integer)
    position: Mapped[str] = mapped_column(String(10))
    nation: Mapped[str] = mapped_column(String(100))
    league: Mapped[str] = mapped_column(String(100))
    club: Mapped[str] = mapped_column(String(100))
    card_type: Mapped[str] = mapped_column(String(50))
    # Scan scheduling metadata
    scan_tier: Mapped[str] = mapped_column(String(10), default="normal")
    last_scanned_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    next_scan_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

class PlayerScore(Base):
    __tablename__ = "player_scores"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ea_id: Mapped[int] = mapped_column(Integer, index=True)
    scored_at: Mapped[datetime] = mapped_column(DateTime)
    buy_price: Mapped[int] = mapped_column(Integer)
    sell_price: Mapped[int] = mapped_column(Integer)
    net_profit: Mapped[int] = mapped_column(Integer)
    margin_pct: Mapped[int] = mapped_column(Integer)
    op_sales: Mapped[int] = mapped_column(Integer)
    total_sales: Mapped[int] = mapped_column(Integer)
    op_ratio: Mapped[float] = mapped_column(Float)
    expected_profit: Mapped[float] = mapped_column(Float)
    efficiency: Mapped[float] = mapped_column(Float)
    sales_per_hour: Mapped[float] = mapped_column(Float)
    is_viable: Mapped[bool] = mapped_column(default=True)  # False if score_player() returned None
    __table_args__ = (
        Index("ix_player_scores_ea_id_scored_at", "ea_id", "scored_at"),
    )
```

**Schema design rationale:**
- `players` table = one row per player, updated on every scan (current state + scheduling metadata)
- `player_scores` table = append-only score history (one row per scan, per player)
- `GET /api/v1/players/top` queries the LATEST score per ea_id — use a subquery or window function to find max `scored_at` per ea_id
- No separate `price_history` table in Phase 1 — fut.gg already stores hourly history; storing it would create massive write amplification. The scanner stores the current `buy_price` in `player_scores`. Phase 2 adds trend analysis.

### Pattern 8: Top Players Query (Latest Score per Player)

```python
# SELECT latest score per player, ordered by efficiency
from sqlalchemy import select, func
from sqlalchemy.orm import aliased

# Subquery: max scored_at per ea_id
latest = (
    select(PlayerScore.ea_id, func.max(PlayerScore.scored_at).label("max_scored_at"))
    .where(PlayerScore.is_viable == True)
    .group_by(PlayerScore.ea_id)
    .subquery()
)
# Join back to get the full score row
stmt = (
    select(PlayerScore, PlayerRecord)
    .join(latest, (PlayerScore.ea_id == latest.c.ea_id) &
                  (PlayerScore.scored_at == latest.c.max_scored_at))
    .join(PlayerRecord, PlayerRecord.ea_id == PlayerScore.ea_id)
    .order_by(PlayerScore.efficiency.desc())
    .limit(limit).offset(offset)
)
```

### Anti-Patterns to Avoid

- **Using `BackgroundScheduler` instead of `AsyncIOScheduler`:** `BackgroundScheduler` runs jobs in a thread pool. Async jobs (`async def`) work but lose the event loop context — coroutines are scheduled via `asyncio.run_coroutine_threadsafe()` which is fragile in this setup. Use `AsyncIOScheduler` to keep everything on the same loop.
- **Sharing a single `AsyncSession` across concurrent scans:** Each scan task must create its own session from `AsyncSessionLocal()`. A single shared session used by multiple concurrent tasks causes session state corruption.
- **Attaching `event.listens_for` to the async engine directly:** For aiosqlite, always use `engine.sync_engine` as the event target. Attaching to `engine` directly silently fails to execute.
- **Omitting `expire_on_commit=False`:** Will cause `MissingGreenlet` errors when accessing model attributes after commit. The STATE.md already flags this as a known concern.
- **Using APScheduler 4.x:** Do not upgrade to 4.x. It is a pre-release, the API is completely different, job stores are incompatible, and it may change without migration paths.
- **Storing all 100 completed sales and full price history in DB on every scan:** This creates O(n_players × 100) writes per hour. Store only the scored summary in `player_scores`. Raw market data stays in fut.gg's API.
- **Setting `max_instances` > 1 on the dispatch job:** Allows overlapping scan waves, creating duplicate DB writes and amplified API load.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Retry with backoff + jitter | Manual `asyncio.sleep(2**n)` loops | `tenacity` | Handles retry conditions, jitter, logging, reraise, async — without bugs |
| ASGI server setup | Raw `asyncio` socket server | `uvicorn` | Production ASGI standard; handles signals, reload, workers |
| HTTP request routing | Manual URL dispatch | `FastAPI` router | Query params, response models, OpenAPI docs for free |
| Session lifecycle (FastAPI) | Manual session pass-through | `Depends(get_session)` | FastAPI dependency injection handles session open/close per request |

**Key insight:** In this domain the main hand-roll trap is the circuit breaker. External CB libraries (`pybreaker`, `aiobreaker`) are either sync-only or unmaintained. A 60-line state machine class is safer, easier to test, and carries no dependency risk.

---

## Runtime State Inventory

> SKIPPED — this is a greenfield addition. No existing runtime state uses old names or needs migration.

---

## Common Pitfalls

### Pitfall 1: MissingGreenlet on Attribute Access After Commit
**What goes wrong:** After `await session.commit()`, accessing `row.some_attribute` raises `sqlalchemy.exc.MissingGreenlet: greenlet_spawn has not been called`.
**Why it happens:** SQLAlchemy expires attributes on commit by default; accessing them triggers implicit lazy I/O, which requires a greenlet context that no longer exists in async.
**How to avoid:** Set `expire_on_commit=False` in `async_sessionmaker(engine, expire_on_commit=False)`. This is already flagged in STATE.md as a known concern.
**Warning signs:** Errors only appear at runtime, not at model definition time. Often surfaces in scan result processing after the DB write.

### Pitfall 2: APScheduler 4.x API Confusion
**What goes wrong:** Web searches return APScheduler 4.x examples with `from apscheduler import AsyncScheduler` — a completely different API from 3.x.
**Why it happens:** APScheduler 4.x pre-release is indexed by PyPI and some tutorials use it.
**How to avoid:** Pin `apscheduler>=3.11,<4.0` in requirements.txt. Use `from apscheduler.schedulers.asyncio import AsyncIOScheduler`. The 3.x docs are at `apscheduler.readthedocs.io/en/3.x/`.
**Warning signs:** Import paths contain `from apscheduler import ...` (v4) vs `from apscheduler.schedulers.asyncio import ...` (v3).

### Pitfall 3: AsyncIOScheduler Jobs Blocking the Event Loop
**What goes wrong:** A scan job that is CPU-heavy or calls a blocking function stalls all FastAPI request handling.
**Why it happens:** `AsyncIOScheduler` runs jobs on the same event loop. Long-running sync code inside an `async def` job blocks the loop.
**How to avoid:** Keep scan jobs truly async (all I/O via `await`). The existing `FutGGClient` is already fully async. Do not call `time.sleep()`, blocking file I/O, or sync DB operations inside scanner coroutines.
**Warning signs:** API endpoints become unresponsive during scan cycles.

### Pitfall 4: Circuit Breaker Not Protecting Batch Operations
**What goes wrong:** Circuit breaker checks are per-player, but if the CB opens mid-batch, some players in the batch still fire requests.
**Why it happens:** Batch is launched before CB state is checked per-player.
**How to avoid:** Check `circuit_breaker.is_open` at the top of each individual scan coroutine, not just at the batch entry point. If open, skip and re-queue.
**Warning signs:** 429 rate limits continue firing even after CB opens.

### Pitfall 5: Bootstrap Discovery Scan Timing Out
**What goes wrong:** First startup triggers discovery of all players in 11k-200k range (~500-2000 players). This takes minutes and may hit fut.gg rate limits before the server is fully serving requests.
**Why it happens:** Discovery is launched synchronously in the lifespan startup block.
**How to avoid:** Launch bootstrap as an APScheduler one-shot job (`run_date=datetime.now()`) after the scheduler starts. Lifespan `yield` completes immediately. The API serves an empty result (or a "bootstrapping" status) until the first scan completes. Document this in the health endpoint (`scanner_status: "bootstrapping"`).
**Warning signs:** `uvicorn` appears to hang on startup; health endpoint not reachable for several minutes.

### Pitfall 6: Stale Player List After Price Range Boundary Changes
**What goes wrong:** Players that moved out of 11k-200k range remain in DB with stale scores and no new scans.
**Why it happens:** Discovery only adds new players; old players are never retired.
**How to avoid:** On each discovery cycle, mark players not returned by `discover_players()` as `scan_tier="cold"` and set `next_scan_at` far in the future. A DB-level `is_active` flag makes this explicit. Players below 11k or above 200k are set inactive.

### Pitfall 7: Single AsyncSession Shared Across Concurrent Scan Tasks
**What goes wrong:** `RuntimeError: This session is already flushing` or inconsistent reads when multiple scan coroutines share a session instance.
**Why it happens:** `AsyncSession` is not thread-safe or concurrency-safe. Per SQLAlchemy docs: "A single instance of AsyncSession is not safe for use in multiple, concurrent tasks."
**How to avoid:** Create a new session per scan task: `async with AsyncSessionLocal() as session:`. Never store a session as an instance variable on `ScannerService`.

---

## Code Examples

### FastAPI Route: GET /api/v1/players/top

```python
# src/server/api/players.py
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from src.server.db import get_session

router = APIRouter(prefix="/api/v1")

@router.get("/players/top")
async def get_top_players(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    price_min: int = Query(default=0, ge=0),
    price_max: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
):
    ...
```

### FastAPI Route: GET /api/v1/health

```python
# src/server/api/health.py
@router.get("/health")
async def health(request: Request):
    scanner: ScannerService = request.app.state.scanner
    cb: CircuitBreaker = request.app.state.circuit_breaker
    return {
        "scanner_status": "running" if scanner.is_running else "stopped",
        "circuit_breaker": cb.state.value,
        "scan_success_rate_1h": scanner.success_rate_1h(),
        "last_scan_at": scanner.last_scan_at.isoformat() if scanner.last_scan_at else None,
        "players_in_db": await scanner.count_players(),
        "queue_depth": scanner.queue_depth(),
    }
```

### Tenacity Retry Wrapper

```python
# src/server/scanner.py
from tenacity import retry, stop_after_attempt, wait_exponential_jitter, retry_if_exception_type
import httpx

@retry(
    retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.TimeoutException)),
    stop=stop_after_attempt(3),
    wait=wait_exponential_jitter(initial=2, max=60, jitter=10),
    reraise=True,
)
async def _fetch_market_data_with_retry(self, ea_id: int):
    return await self._client.get_market_data(ea_id)
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `@app.on_event("startup")` | `@asynccontextmanager async def lifespan(app)` | FastAPI 0.95.0 (2023) | `on_event` is deprecated; lifespan is the only way to avoid deprecation warnings |
| APScheduler `add_job` with `func=coroutine` | `AsyncIOScheduler` + `async def` jobs directly | APScheduler 3.6+ | Coroutines work natively as jobs; no `run_coroutine_threadsafe` needed |
| SQLAlchemy 1.4 async (beta) | SQLAlchemy 2.0 unified async API | SQLAlchemy 2.0.0 (2023) | `async_sessionmaker`, `Mapped[]` type annotations, cleaner session lifecycle |

**Deprecated/outdated:**
- `@app.on_event("startup")` / `@app.on_event("shutdown")`: Replaced by lifespan context manager in FastAPI 0.95+. Still works but generates deprecation warning.
- `Session.add()` + `Session.commit()` without `expire_on_commit=False` in async context: Causes `MissingGreenlet` in async SQLAlchemy.
- APScheduler `BackgroundScheduler` + `run_coroutine_threadsafe`: Pre-AsyncIOScheduler pattern; still works but is architecturally fragile.

---

## Open Questions

1. **fut.gg rate limit specifics**
   - What we know: FutGGClient adds 0.15s per request; `get_batch_market_data` uses concurrency=10 semaphore. STATE.md notes: "fut.gg has no published rate limits; 24/7 scanning behavior is untested."
   - What's unclear: What request rate triggers 429s? Is it per-IP, per-endpoint, per-hour?
   - Recommendation: Start with concurrency=5 (half current) for 24/7 mode. Monitor `scan_success_rate` in the health endpoint in the first week. Tune `SCAN_CONCURRENCY` in config. The circuit breaker will absorb bursts.

2. **Discovery scan frequency**
   - What we know: Bootstrap does full discovery on startup (D-08). No subsequent discovery schedule is specified.
   - What's unclear: How often should `discover_players()` be re-run to catch new players entering the 11k-200k range?
   - Recommendation: Run discovery once per hour as a separate scheduler job. This catches new cards entering the price range without re-scanning everyone.

3. **SQLite file location**
   - What we know: Discretion is Claude's. SQLAlchemy URL is `sqlite+aiosqlite:///./op_seller.db`.
   - What's unclear: Should this be configurable via env var or hardcoded?
   - Recommendation: Hardcode to `./op_seller.db` for Phase 1. Add env-var override in Phase 3 when CLI is refactored.

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python 3.12 | All | ✓ | 3.12.10 | — |
| pip | Package install | ✓ | present | — |
| fastapi | REST API | ✗ (not installed) | — | Install Wave 0 |
| uvicorn | ASGI server | ✗ (not installed) | — | Install Wave 0 |
| apscheduler 3.x | Scheduling | ✗ (not installed) | — | Install Wave 0 |
| sqlalchemy 2.0 | ORM | ✗ (not installed) | — | Install Wave 0 |
| aiosqlite | Async SQLite | ✗ (not installed) | — | Install Wave 0 |
| tenacity | Retry/backoff | ✗ (not installed) | — | Install Wave 0 |

**Missing dependencies with no fallback:**
- All six new packages are unavailable and must be installed in Wave 0. None have viable alternatives that avoid installation.

**Missing dependencies with fallback:**
- None.

**Wave 0 install command:**
```bash
pip install fastapi==0.135.2 uvicorn==0.42.0 "apscheduler>=3.11,<4.0" sqlalchemy==2.0.48 aiosqlite==0.22.1 tenacity==9.1.4
```

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest 9.0.2 + pytest-asyncio 1.3.0 |
| Config file | None detected — needs `pyproject.toml` or `pytest.ini` with `asyncio_mode = "auto"` |
| Quick run command | `pytest tests/ -x -q` |
| Full suite command | `pytest tests/ -v` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| SCAN-01 | Scanner starts and discovers players | integration | `pytest tests/test_scanner.py -x` | ❌ Wave 0 |
| SCAN-02 | Scores written to SQLite, readable back | integration | `pytest tests/test_scanner.py::test_score_persisted -x` | ❌ Wave 0 |
| SCAN-04 | Circuit breaker opens on 5 failures, recovers | unit | `pytest tests/test_circuit_breaker.py -x` | ❌ Wave 0 |
| SCAN-04 | Tenacity retries with backoff on 429/5xx | unit | `pytest tests/test_scanner.py::test_retry_on_429 -x` | ❌ Wave 0 |
| API-03 | GET /api/v1/players/top returns ranked list | integration | `pytest tests/test_api.py::test_top_players -x` | ❌ Wave 0 |
| API-03 | GET /api/v1/players/top respects price_min/max filters | unit | `pytest tests/test_api.py::test_top_players_filters -x` | ❌ Wave 0 |
| API-03 | GET /api/v1/health returns all required fields | unit | `pytest tests/test_api.py::test_health_endpoint -x` | ❌ Wave 0 |
| API-04 | Hot players are queued more frequently than cold | unit | `pytest tests/test_scanner.py::test_tier_classification -x` | ❌ Wave 0 |

### Sampling Rate
- **Per task commit:** `pytest tests/ -x -q`
- **Per wave merge:** `pytest tests/ -v`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_circuit_breaker.py` — covers SCAN-04 circuit breaker unit tests
- [ ] `tests/test_scanner.py` — covers SCAN-01, SCAN-02, SCAN-04 retry, API-04 tier logic
- [ ] `tests/test_api.py` — covers API-03 endpoint tests
- [ ] `pytest.ini` or `pyproject.toml` section — `asyncio_mode = "auto"` required for pytest-asyncio 1.x
- [ ] Framework install: `pip install fastapi==0.135.2 uvicorn==0.42.0 "apscheduler>=3.11,<4.0" sqlalchemy==2.0.48 aiosqlite==0.22.1 tenacity==9.1.4`

---

## Project Constraints (from CLAUDE.md)

| Directive | Implication for Phase 1 |
|-----------|------------------------|
| Data source: fut.gg API only | Scanner uses FutGGClient exclusively; no other data sources |
| Rate limiting: smart throttling for 24/7 operation | tenacity + circuit breaker mandatory; concurrency lower than CLI mode |
| Python backend (keep existing scoring) | `score_player()` and `FutGGClient` reused unchanged |
| SQLite for now, designed to migrate to PostgreSQL | Use SQLAlchemy ORM (not raw sqlite3); avoid SQLite-specific SQL syntax |
| Architecture must support cloud deployment | No hardcoded localhost assumptions; DB path should be configurable |
| snake_case functions, PascalCase classes | New code follows existing conventions |
| Logger per module: `logger = logging.getLogger(__name__)` | All new modules create their own logger |
| Absolute imports: `from src.server.db import ...` | No relative imports |
| `async_sessionmaker(expire_on_commit=False)` required | Already flagged in STATE.md; MUST be applied to all session factories |

---

## Sources

### Primary (HIGH confidence)
- SQLAlchemy 2.0 asyncio docs (https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html) — async_sessionmaker, expire_on_commit, concurrent task pattern
- SQLAlchemy discussions/12767 — aiosqlite PRAGMA via `engine.sync_engine` event listener
- APScheduler 3.x PyPI + version list — confirmed 3.11.2 is latest stable, 4.x is pre-release
- PyPI version registry (2026-03-25) — all package versions verified against registry

### Secondary (MEDIUM confidence)
- FastAPI official docs (https://fastapi.tiangolo.com/advanced/events/) — lifespan context manager pattern
- APScheduler GitHub issues/465 — confirmed 4.x is not production-ready
- SQLAlchemy GitHub discussions/11495 — `expire_on_commit=False` rationale in async context
- tenacity PyPI (https://pypi.org/project/tenacity/) — `wait_exponential_jitter`, async support confirmed

### Tertiary (LOW confidence — for general pattern guidance only)
- Web search results on circuit breaker patterns — pattern is well-established; specific parameter values (5 failures, 60s reset) are discretionary starting points

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all versions verified against PyPI registry 2026-03-25
- Architecture patterns: HIGH — FastAPI lifespan + AsyncIOScheduler + SQLAlchemy async all verified against official docs
- Circuit breaker parameters: MEDIUM — pattern is standard; specific thresholds are discretionary starting points
- APScheduler 3.x vs 4.x: HIGH — APScheduler GitHub issue #465 explicitly confirms 4.x is not production-ready

**Research date:** 2026-03-25
**Valid until:** 2026-04-25 (30 days; stable ecosystem)
