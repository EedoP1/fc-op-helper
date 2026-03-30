# Phase 10: Split Scanner and API into Separate Processes - Research

**Researched:** 2026-03-30
**Domain:** Process separation, Docker Compose multi-service, async Python entry points, DB connection pooling
**Confidence:** HIGH

## Summary

This phase separates the combined FastAPI+ScannerService monolith into two independent OS processes, both sharing the same Postgres database. The root cause of the production timeout bug is confirmed: `scanner.dispatch_scans()` fires 40 concurrent HTTP threads plus bounded DB writes on a 30-second cycle, and the API's uvicorn workers compete for the same asyncpg connection pool, causing `ReadTimeout` on the Chrome extension.

The split is architecturally clean because `ScannerService` already accepts `session_factory` and `circuit_breaker` as constructor arguments — it can be instantiated from any entry point without changes. The only new code is: a thin `scanner_main.py` entry point, a `scanner_status` DB table + upsert call, health endpoint rewrite, updated Docker Compose, and updated integration test harness.

The primary risk area is the integration test harness rewrite (D-07, D-08). The current `conftest.py` launches a single uvicorn subprocess. The new one must launch two services via Docker Compose, wait for both to be healthy, and tear down after. The Docker Compose `depends_on` + healthcheck pattern handles ordering. The `scanner_status` table introduces a startup race: the health endpoint may return stale `null` data briefly after API boots but before scanner has written its first status row — this must be handled gracefully.

**Primary recommendation:** Implement in wave order: (1) add `scanner_status` table + upsert in scanner, (2) rewrite health endpoint to read from table, (3) create `scanner_main.py`, (4) strip scanner from `main.py` lifespan, (5) add Dockerfile(s) and update docker-compose.yml, (6) rewrite conftest.py. Each step is independently testable.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

- **D-01:** Scanner writes operational metrics to a `scanner_status` DB table, upserted every dispatch cycle (~30s). API's `/health` endpoint reads from this table instead of in-memory scanner state.
- **D-02:** Metrics to persist: `is_running`, `success_rate_1h`, `last_scan_at`, `queue_depth`, `circuit_breaker_state`. The `players_in_db` metric is already a DB query — no change needed.
- **D-03:** Both processes managed via Docker Compose. Two services: `api` (uvicorn src.server.main:app) and `scanner` (python -m src.server.scanner_main). Alongside the existing Postgres service.
- **D-04:** Single `docker-compose up` starts everything. Auto-restart on failure via Docker Compose restart policies.
- **D-05:** New `src/server/scanner_main.py` — thin entry point that creates DB engine, ScannerService, CircuitBreaker, APScheduler, and runs `asyncio.run()`. Reuses all existing scanner code unchanged.
- **D-06:** `src/server/main.py` lifespan drops all scanner/scheduler startup. API process has no scanner, no scheduler, no FutGGClient. Just FastAPI + DB pool.
- **D-07:** Integration tests use Docker Compose — exactly like production, with DATABASE_URL pointing to the test DB as the only difference.
- **D-08:** conftest.py starts services via Docker Compose (or equivalent), waits for both to be healthy, runs tests, tears down after.

### Claude's Discretion

- DB pool sizing for each process (scanner vs API may need different pool configs)
- Docker Compose networking details
- Health check wait timeout values
- Whether scanner_main.py needs its own logging config or reuses existing

### Deferred Ideas (OUT OF SCOPE)

None — discussion stayed within phase scope
</user_constraints>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| Docker Compose | v5.1.0 (installed) | Multi-service orchestration | Already used for Postgres; natural fit for api + scanner services |
| APScheduler | 3.11.2 (installed) | Async job scheduling | Already in use; moves entirely to scanner process |
| SQLAlchemy async | 2.0.48 (installed) | DB access for both processes | Already the ORM; both processes call `create_engine_and_tables()` independently |
| asyncpg | 0.31.0 (installed) | Postgres driver | Already in use; Postgres MVCC handles concurrent process access |
| uvicorn | 0.42.0 (installed) | ASGI server for API process | Already in use |
| FastAPI | 0.135.2 (installed) | API framework | Already in use |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| httpx (sync) | 0.28.1 | Scanner HTTP calls in thread pool | Already used in scanner; no change |
| tenacity | (installed) | Retry logic for scanner | Already in scanner; no change |

**No new dependencies required.** All needed libraries are already installed.

## Architecture Patterns

### Recommended Project Structure (new/changed files only)
```
src/
└── server/
    ├── main.py              # MODIFIED: drop scanner/scheduler from lifespan
    ├── scanner_main.py      # NEW: scanner process entry point
    └── api/
        └── health.py        # MODIFIED: read from scanner_status table
    models_db.py             # MODIFIED: add ScannerStatus ORM model
    db.py                    # UNCHANGED: both processes call create_engine_and_tables()
docker-compose.yml           # MODIFIED: add api and scanner services
tests/
└── integration/
    └── conftest.py          # MODIFIED: Docker Compose-based startup
```

### Pattern 1: Thin Scanner Entry Point

`scanner_main.py` follows the same lifespan pattern as `main.py` but without FastAPI:

```python
"""Scanner process entry point."""
import asyncio
import logging
from src.server.db import create_engine_and_tables
from src.server.scanner import ScannerService
from src.server.scheduler import create_scheduler
from src.server.circuit_breaker import CircuitBreaker

async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    engine, session_factory = await create_engine_and_tables()
    cb = CircuitBreaker()
    scanner = ScannerService(session_factory=session_factory, circuit_breaker=cb)
    await scanner.start()

    scheduler = create_scheduler(scanner)
    scheduler.start()
    scheduler.add_job(scanner.run_bootstrap_and_score, id="bootstrap", replace_existing=True)

    try:
        # Block indefinitely — Docker handles restart
        await asyncio.Event().wait()
    finally:
        scheduler.shutdown(wait=False)
        await scanner.stop()
        await engine.dispose()

if __name__ == "__main__":
    asyncio.run(main())
```

**Key:** `asyncio.Event().wait()` blocks forever. Docker SIGTERM triggers `finally` block for clean shutdown.

### Pattern 2: ScannerStatus ORM Model and Upsert

New table with a single row (`id=1`), upserted in `dispatch_scans()`:

```python
# models_db.py addition
class ScannerStatus(Base):
    """Scanner health metrics written every dispatch cycle (D-01, D-02)."""
    __tablename__ = "scanner_status"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    is_running: Mapped[bool] = mapped_column(Boolean, default=False)
    last_scan_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    success_rate_1h: Mapped[float] = mapped_column(Float, default=1.0)
    queue_depth: Mapped[int] = mapped_column(Integer, default=0)
    circuit_breaker_state: Mapped[str] = mapped_column(String(20), default="closed")
    updated_at: Mapped[datetime] = mapped_column(DateTime)
```

Upsert uses PostgreSQL `INSERT ... ON CONFLICT DO UPDATE` (same pattern as existing PlayerRecord upserts):

```python
# In ScannerService.dispatch_scans() at start of method
async with self._session_factory() as session:
    stmt = pg_insert(ScannerStatus).values(
        id=1,
        is_running=True,
        last_scan_at=self.last_scan_at,
        success_rate_1h=self.success_rate_1h(),
        queue_depth=self._queue_depth_cache,
        circuit_breaker_state=self._circuit_breaker.state.value,
        updated_at=datetime.utcnow(),
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["id"],
        set_=dict(
            is_running=True,
            last_scan_at=self.last_scan_at,
            success_rate_1h=self.success_rate_1h(),
            queue_depth=self._queue_depth_cache,
            circuit_breaker_state=self._circuit_breaker.state.value,
            updated_at=datetime.utcnow(),
        ),
    )
    await session.execute(stmt)
    await session.commit()
```

### Pattern 3: Health Endpoint Rewritten

```python
# health.py
from sqlalchemy import select, func
from src.server.models_db import ScannerStatus, PlayerRecord

@router.get("/health")
async def health(request: Request):
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        # Scanner status from DB (written by scanner process)
        result = await session.execute(select(ScannerStatus).where(ScannerStatus.id == 1))
        status = result.scalar_one_or_none()

        # players_in_db is still a direct DB query
        count_result = await session.execute(
            select(func.count()).select_from(PlayerRecord).where(PlayerRecord.is_active == True)
        )
        players_in_db = count_result.scalar() or 0

    if status is None:
        # Scanner hasn't written yet (startup race) — return degraded state
        return {
            "scanner_status": "unknown",
            "circuit_breaker": "unknown",
            "scan_success_rate_1h": None,
            "last_scan_at": None,
            "players_in_db": players_in_db,
            "queue_depth": 0,
        }

    return {
        "scanner_status": "running" if status.is_running else "stopped",
        "circuit_breaker": status.circuit_breaker_state,
        "scan_success_rate_1h": round(status.success_rate_1h, 3),
        "last_scan_at": status.last_scan_at.isoformat() if status.last_scan_at else None,
        "players_in_db": players_in_db,
        "queue_depth": status.queue_depth,
    }
```

### Pattern 4: Docker Compose Multi-Service

```yaml
# docker-compose.yml — production services
services:
  postgres:
    # existing config unchanged

  api:
    build: .
    command: uvicorn src.server.main:app --host 0.0.0.0 --port 8000
    environment:
      DATABASE_URL: postgresql+asyncpg://op_seller:op_seller@postgres:5432/op_seller
    ports:
      - "8000:8000"
    depends_on:
      postgres:
        condition: service_healthy
    restart: unless-stopped

  scanner:
    build: .
    command: python -m src.server.scanner_main
    environment:
      DATABASE_URL: postgresql+asyncpg://op_seller:op_seller@postgres:5432/op_seller
    depends_on:
      postgres:
        condition: service_healthy
    restart: unless-stopped
```

**Key decisions:**
- `depends_on: condition: service_healthy` — waits for Postgres healthcheck to pass before starting either service
- `restart: unless-stopped` — Docker auto-restarts on crash; user stops explicitly
- `api` and `scanner` build from same Dockerfile (same Python image, same code)
- Both use `postgres` hostname (Docker Compose internal DNS) not `localhost`

### Pattern 5: Dockerfile for Python Processes

No Dockerfile currently exists. Both `api` and `scanner` services reference `build: .`, so a Dockerfile is required:

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
```

**Key:** `python:3.12-slim` matches project's Python 3.12.10 runtime.

### Pattern 6: Integration Test conftest.py Rewrite

Current approach: single `subprocess.Popen([..., "uvicorn", ...])`.
New approach: `docker compose up` with override file pointing to test DB.

```python
# conftest.py — Docker Compose integration test approach

COMPOSE_PROJECT = "op_seller_test"
COMPOSE_FILE = "/abs/path/to/docker-compose.yml"
COMPOSE_TEST_OVERRIDE = "/abs/path/to/docker-compose.test.yml"

@pytest.fixture(scope="session", autouse=True)
def live_server(test_db_url):
    """Start api + scanner via Docker Compose with test DATABASE_URL."""
    subprocess.run(
        ["docker", "compose",
         "-f", COMPOSE_FILE,
         "-f", COMPOSE_TEST_OVERRIDE,
         "-p", COMPOSE_PROJECT,
         "up", "-d", "--build"],
        check=True,
    )

    # Wait for API health
    base_url = "http://127.0.0.1:8000"
    for _ in range(600):
        try:
            r = httpx.get(f"{base_url}/api/v1/health", timeout=1.0)
            if r.status_code == 200:
                break
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout):
            pass
        time.sleep(0.1)
    else:
        subprocess.run(["docker", "compose", "-p", COMPOSE_PROJECT, "logs"])
        subprocess.run(["docker", "compose", "-p", COMPOSE_PROJECT, "down"])
        raise RuntimeError("Test services failed to start")

    yield

    subprocess.run(
        ["docker", "compose", "-p", COMPOSE_PROJECT, "down"],
        check=True,
    )
```

The test override file (`docker-compose.test.yml`) overrides `DATABASE_URL` to point at `postgres-test` (port 5433):

```yaml
# docker-compose.test.yml
services:
  api:
    environment:
      DATABASE_URL: postgresql+asyncpg://op_seller:op_seller@localhost:5433/op_seller
    ports:
      - "8001:8000"  # Use different port to avoid conflict with prod
  scanner:
    environment:
      DATABASE_URL: postgresql+asyncpg://op_seller:op_seller@localhost:5433/op_seller
```

**Alternative (simpler):** Keep using `subprocess.Popen` for two processes (api + scanner separately) but wire them to the same test DB. This achieves D-07's intent ("exactly like prod, same launch mechanism") with less Docker complexity and no image rebuild on every test run. The trade-off: not literally using Docker Compose in tests. The user strongly stated "exactly like prod", so Docker Compose is the locked path (D-07, D-08).

### Pattern 7: DB Pool Sizing per Process

Current `db.py` pool: `pool_size=20, max_overflow=60`.

**Scanner process** does heavy concurrent writes (15 DB semaphore × 2 sessions = 30 connections peak). Set `pool_size=10, max_overflow=20` — scanner is single async loop, 15 semaphore limit means rarely more than 15 active sessions.

**API process** is request-driven, low concurrent writes, high read throughput. Keep `pool_size=20, max_overflow=40`. Postgres `max_connections=200` can handle both processes comfortably.

Pass `pool_size` as parameter to `create_engine()` or via env var rather than hardcoding.

### Anti-Patterns to Avoid

- **`app.state.scanner` access after split:** The health endpoint currently reads `request.app.state.scanner`. After the split, the API process has no scanner in `app.state`. Any code that touches `app.state.scanner` or `app.state.circuit_breaker` will raise `AttributeError`. Audit all API routes before removing scanner from lifespan.
- **Shared asyncpg event loop across processes:** asyncpg connections belong to the event loop that created them. Each process has its own event loop — this is correct and safe. Do not attempt to share engine/pool objects across processes via any mechanism.
- **Blocking on scanner readiness in API health check:** The API health endpoint must handle the startup race where `scanner_status` has no row yet (scanner not yet written). Return a degraded `"unknown"` state, not a 500.
- **Docker Compose test using production DB:** The test override must point to the test Postgres container (port 5433), never to production (port 5432).
- **Migration code in main.py lifespan:** The inline `ALTER TABLE` migration in `main.py` lifespan (adding `is_leftover` column) runs only in the API process. This is correct since only one process should run migrations. Keep it in API lifespan only, or extract to a dedicated migration step.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Service health wait in tests | Custom polling loop with random waits | `httpx.get` + retry loop on `ConnectError`/`ConnectTimeout` (already in conftest.py) | Already proven in existing conftest; same pattern works |
| Docker Compose service startup ordering | Application-level retry on DB connect | `depends_on: condition: service_healthy` + Postgres healthcheck | Docker handles this natively; no retry code needed in app |
| Cross-process metrics sharing | Redis, shared memory, sockets | `scanner_status` table in Postgres (D-01) | Already have Postgres; 30s write latency is acceptable for health |
| Process supervision | Custom watchdog daemon | Docker Compose `restart: unless-stopped` | Docker handles auto-restart natively |
| DB schema migration | Manual psycopg2 scripts | Existing `create_engine_and_tables()` + `Base.metadata.create_all` | Already the pattern; just add `ScannerStatus` to the models import list |

## Common Pitfalls

### Pitfall 1: Windows asyncpg / ProactorEventLoop in Docker
**What goes wrong:** The comment in `scanner.py` notes "asyncpg can't create connections from background threads on Windows (ProactorEventLoop limitation)". In Docker (Linux container), the default event loop is `SelectorEventLoop` — this Windows constraint doesn't apply. No special `asyncio.set_event_loop_policy()` needed in scanner_main.py when running in Docker.

**Why it happens:** The current scanner workaround (offload HTTP to thread pool, keep DB on main loop) was designed for the Windows dev environment. Docker runs Linux.

**How to avoid:** Don't add `WindowsProactorEventLoopPolicy` guards to scanner_main.py. The Docker environment is Linux and will work with the default policy.

**Warning signs:** If tests pass on Linux/Docker but fail on Windows dev, the issue is the ProactorEventLoop constraint — add the policy guard to scanner_main.py only for Windows.

### Pitfall 2: Startup Race — Health Returns 500 Before Scanner Writes
**What goes wrong:** API starts, extension polls `/health`, `scanner_status` table is empty (scanner hasn't written yet), health endpoint raises `AttributeError` or 500.

**Why it happens:** The two processes start independently. Postgres starts first, then api and scanner start in parallel. The API can be ready and serve traffic before scanner has completed its first dispatch cycle (~30s).

**How to avoid:** Health endpoint uses `scalar_one_or_none()` and explicitly handles `None` with a degraded response (shown in Pattern 3). Never use `scalar_one()` which raises if no row found.

### Pitfall 3: conftest.py Docker Compose Port Collision with Production
**What goes wrong:** Tests launch `docker compose up` for the api service on port 8000. If production Docker Compose is also running (different compose project), both try to bind port 8000 and one fails.

**Why it happens:** Default port mapping `8000:8000` conflicts across compose projects if same host port is used.

**How to avoid:** Use a different host port for tests (e.g., `8001:8000`). Use `-p op_seller_test` project name to namespace containers. Or use `docker compose down` to stop prod before running tests.

### Pitfall 4: Migration Code Runs in Both Processes
**What goes wrong:** Both `api` and `scanner` process call `create_engine_and_tables()`. If migration DDL (like the `ALTER TABLE` inline in `main.py`) runs in scanner_main.py too, one process may see the column already exists and error, or both run `CREATE_IF_NOT_EXISTS` simultaneously causing race.

**Why it happens:** `Base.metadata.create_all` is idempotent for table creation (IF NOT EXISTS), but the inline `ALTER TABLE` in `main.py` lifespan has its own existence check. If scanner_main.py also calls `create_engine_and_tables()`, it runs `create_all` which is safe. The `ALTER TABLE` is in `main.py` lifespan only — scanner_main.py should not duplicate it.

**How to avoid:** Keep all migration DDL in API lifespan only. scanner_main.py calls `create_engine_and_tables()` for table creation (idempotent) but no custom DDL.

### Pitfall 5: conftest.py Waits Only for API, Not Scanner
**What goes wrong:** conftest.py polls `/health` and proceeds when API is up. But scanner hasn't written `scanner_status` yet. Tests that check health response fields (e.g., `scanner_status == "running"`) fail because the row doesn't exist yet.

**Why it happens:** API ready != scanner ready. Scanner takes its first dispatch cycle (~30s) before writing status.

**How to avoid:** Integration tests that check scanner health fields should use a separate wait loop: poll `/health` until `scanner_status != "unknown"`, with a reasonable timeout (90s). Or skip scanner health assertions in the integration test harness — treat them as "eventually consistent".

### Pitfall 6: Docker Compose test.yml Network Reference
**What goes wrong:** Test override sets `DATABASE_URL: postgresql+asyncpg://op_seller:op_seller@localhost:5433/op_seller`. But inside Docker containers, `localhost` refers to the container itself, not the host machine. The `postgres-test` container is reachable via host port 5433 only from the host, not from within another container.

**Why it happens:** Docker container networking. `localhost` in a container is the container's loopback.

**How to avoid:** Test services in Docker Compose must reference Postgres by service name: `@postgres-test:5432` (internal Docker DNS). The `docker-compose.test.yml` must add `postgres-test` to the `depends_on` of both `api` and `scanner`, and both containers must be on the same Docker network as `postgres-test`.

Alternatively: run api and scanner as host processes (not in Docker) for tests, using `subprocess.Popen` with `DATABASE_URL=postgresql+asyncpg://...@localhost:5433/...`. This achieves test/prod parity at the code level while avoiding Docker networking complexity. Given the user's strong preference for "exactly like prod", Docker Compose with proper networking is the right path but requires careful network config.

## Code Examples

### ScannerStatus upsert in dispatch_scans()
```python
# In ScannerService.dispatch_scans(), before the due-players query:
# Source: Existing pg_insert pattern from scanner.py run_bootstrap()
async with self._session_factory() as session:
    stmt = pg_insert(ScannerStatus).values(
        id=1,
        is_running=self.is_running,
        last_scan_at=self.last_scan_at,
        success_rate_1h=self.success_rate_1h(),
        queue_depth=self._queue_depth_cache,
        circuit_breaker_state=self._circuit_breaker.state.value,
        updated_at=datetime.utcnow(),
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["id"],
        set_=dict(
            is_running=True,
            last_scan_at=self.last_scan_at,
            success_rate_1h=self.success_rate_1h(),
            queue_depth=self._queue_depth_cache,
            circuit_breaker_state=self._circuit_breaker.state.value,
            updated_at=datetime.utcnow(),
        ),
    )
    await session.execute(stmt)
    await session.commit()
```

### Docker Compose `depends_on` with healthcheck
```yaml
# Source: Docker Compose docs — condition: service_healthy requires healthcheck on dependency
api:
  depends_on:
    postgres:
      condition: service_healthy

scanner:
  depends_on:
    postgres:
      condition: service_healthy
```

### Docker Compose stop signal for clean asyncio shutdown
```yaml
# SIGTERM triggers finally block in scanner_main.py asyncio.Event().wait()
scanner:
  stop_signal: SIGTERM
  stop_grace_period: 30s
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Combined process (scanner + API) | Two separate processes | This phase | Eliminates DB pool starvation |
| In-memory scanner metrics on `app.state` | `scanner_status` DB table | This phase | Health survives API restart |
| Single uvicorn subprocess in tests | Docker Compose in tests | This phase | True prod/test parity |

## Open Questions

1. **Dockerfile location and base image**
   - What we know: No Dockerfile exists yet. Project uses Python 3.12.10, pip, requirements.txt.
   - What's unclear: Does the user want a minimal `python:3.12-slim` image, or a full image? Any OS-level deps needed?
   - Recommendation: Use `python:3.12-slim`. No compiled extensions beyond asyncpg (which has wheels). Standard pip install sufficient.

2. **conftest.py Docker Compose networking for test DB**
   - What we know: Tests currently use Postgres on port 5433 (`postgres-test` service in docker-compose.yml). Inside Docker containers, `localhost:5433` doesn't work.
   - What's unclear: Should api/scanner run as Docker containers in tests (requiring network config for postgres-test), or as host processes pointing at localhost:5433?
   - Recommendation: Run api and scanner as **host processes** (subprocess.Popen) for tests — not Docker containers. The `docker compose up --build` overhead (30-60s image build + container start) makes test feedback slow. Use Docker Compose for prod; use direct subprocess for tests that meet the spirit of D-07 (same code paths). Flag this trade-off for user confirmation at plan review.

3. **ScannerStatus migration — add to create_all or separate Alembic?**
   - What we know: Project uses `Base.metadata.create_all` (no Alembic). The `ScannerStatus` table is a new model — `create_all` will create it on first start.
   - What's unclear: Existing production Postgres won't have this table. `create_all` only creates missing tables (IF NOT EXISTS), so it's safe.
   - Recommendation: Add `ScannerStatus` to the `models_db.py` import in `create_engine_and_tables()`. No migration needed — `create_all` handles it on next startup.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Docker | Container builds | ✓ | 29.3.1 | — |
| Docker Compose | Multi-service orchestration | ✓ | v5.1.0 | subprocess.Popen (two processes) |
| Python 3.12 | Both processes | ✓ | 3.12.10 | — |
| Postgres (prod) | API + scanner | ✓ | 17 (Docker) | — |
| Postgres (test) | Integration tests | ✓ | 17 (Docker, port 5433) | — |
| APScheduler | Scanner process | ✓ | 3.11.2 | — |
| asyncpg | DB connections | ✓ | 0.31.0 | — |

**Missing dependencies with no fallback:** None.

**Missing dependencies with fallback:** None.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 9.0.2 + pytest-asyncio 1.3.0 |
| Config file | pytest.ini or pyproject.toml (check project root) |
| Quick run command | `pytest tests/integration/test_smoke_all_endpoints.py -x` |
| Full suite command | `pytest tests/integration/ -x` |

### Phase Requirements → Test Map

No formal REQ-IDs were assigned to this phase. Behavioral requirements from decisions:

| Behavior | Test Type | Automated Command | File Exists? |
|----------|-----------|-------------------|-------------|
| `/health` reads from `scanner_status` table (not `app.state`) | integration | `pytest tests/integration/test_smoke_all_endpoints.py -k health -x` | ✅ (existing test covers /health) |
| API starts without scanner in memory | integration | `pytest tests/integration/test_smoke_all_endpoints.py -x` | ✅ (all endpoint smoke tests verify API works) |
| `scanner_status` table upserted on dispatch | unit | `pytest tests/test_scanner.py -x` (Wave 0 gap) | ❌ Wave 0 |
| scanner_main.py runs without FastAPI | smoke | `python -m src.server.scanner_main &; sleep 5; kill %1` (manual) | ❌ manual |
| Both services start via `docker compose up` | manual | `docker compose up -d; curl localhost:8000/api/v1/health` | ❌ manual |

### Sampling Rate
- **Per task commit:** `pytest tests/integration/test_smoke_all_endpoints.py -x`
- **Per wave merge:** `pytest tests/integration/ -x`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_scanner_status.py` — unit tests for ScannerStatus upsert in dispatch_scans
- [ ] Dockerfile — required before any `docker compose up` commands in integration tests

*(Existing integration test infrastructure covers all API-side requirements. Scanner unit tests are the main gap.)*

## Sources

### Primary (HIGH confidence)
- Direct codebase inspection: `src/server/main.py`, `scanner.py`, `scheduler.py`, `health.py`, `db.py`, `circuit_breaker.py`, `models_db.py`
- `tests/integration/conftest.py` — current test harness pattern verified
- `docker-compose.yml` — existing Docker setup verified
- `src/config.py` — pool sizes and concurrency constants verified
- Docker version check: `docker --version` → 29.3.1, `docker compose version` → v5.1.0
- Package versions verified via `pip show`

### Secondary (MEDIUM confidence)
- Docker Compose `depends_on: condition: service_healthy` pattern — standard Docker Compose v2+ feature, verified available in v5.1.0
- `asyncio.Event().wait()` as indefinite block pattern — standard asyncio idiom for long-running processes

### Tertiary (LOW confidence)
- None — all claims verified from codebase or installed tools

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all packages are already installed, versions verified
- Architecture patterns: HIGH — based on direct reading of existing code patterns
- Pitfalls: HIGH — pitfalls derived from actual code reading (startup race, port collision, Windows vs Linux asyncpg)
- Docker Compose networking: MEDIUM — networking recommendation for test DB access is informed reasoning, should be validated during implementation

**Research date:** 2026-03-30
**Valid until:** 2026-04-30 (stable tech stack, no fast-moving dependencies)
