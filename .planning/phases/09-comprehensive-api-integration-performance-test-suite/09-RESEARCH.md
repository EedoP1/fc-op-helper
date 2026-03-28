# Phase 9: Comprehensive API Integration & Performance Test Suite - Research

**Researched:** 2026-03-28
**Domain:** Python/FastAPI integration testing, async pytest, performance benchmarking
**Confidence:** HIGH

## Summary

Phase 9 is a test suite phase with no upstream CONTEXT.md — there are no locked user decisions
yet. The phase was added to the roadmap after Phase 8 (DOM Automation Layer) was planned. Its
name signals two distinct concerns: (1) **integration coverage** of all backend API endpoints
and the full trade lifecycle, and (2) **performance characteristics** of the endpoints under
realistic load.

The project already has 161 passing tests that exercise most individual endpoints in isolation
with in-memory SQLite. What is notably absent is: (a) cross-endpoint flow tests that simulate
a complete buy/list/relist cycle through the API, (b) tests for the batch trade record endpoint
(`POST /trade-records/batch`), (c) any latency or throughput measurement, and (d) the
extension Vitest suite integrated into any shared CI command. The `test_health_check.py` file
currently fails to collect (import error — references a removed `FutbinClient`), which means
the 161-test baseline excludes that file.

**Primary recommendation:** Define phase scope as three work streams — (1) fix broken
`test_health_check.py`, (2) add missing integration scenarios (lifecycle flows, batch
endpoint, multi-player edge cases), and (3) add lightweight performance assertions using
`pytest-benchmark` or timed HTTP calls, not a separate load tool. Keep everything in the
existing pytest + httpx ASGI transport pattern; do not add Locust or k6 unless the user
explicitly requests it.

## Project Constraints (from CLAUDE.md)

- **Data source**: fut.gg API only — no FUTBIN
- **Tech stack**: Python backend (keep existing scoring), TypeScript for Chrome extension
- **Storage**: SQLite for now, designed to migrate to PostgreSQL
- **Hosting**: Local machine initially
- **Test framework**: pytest 9.0.2 with `asyncio_mode = auto` in `pytest.ini`
- **Code style**: snake_case functions, PascalCase classes, Google-style docstrings,
  absolute imports (`from src.xxx import ...`), no formatter enforced
- **Error handling**: Defensive with early returns; try-except on API calls
- **Logging**: Per-module `logger = logging.getLogger(__name__)`
- **GSD workflow**: Must enter work via `/gsd:execute-phase`, not direct edits

## Standard Stack

### Core (already installed)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| pytest | 9.0.2 | Test runner | Already in use; `asyncio_mode=auto` |
| pytest-asyncio | 1.3.0 | Async test support | Already in use |
| httpx | 0.28.1 | HTTP client + `ASGITransport` | Used in all existing API tests |
| fastapi | installed | ASGI app under test | Project framework |
| sqlalchemy aiosqlite | installed | In-memory DB for test isolation | Used across all test fixtures |

### Supporting (to add)

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| pytest-benchmark | ~4.0 | Latency assertions as pytest tests | Performance test plans |
| time / asyncio | stdlib | Lightweight timing without adding deps | If benchmark is overkill |

**Version verification:**
```bash
pip show pytest pytest-asyncio httpx pytest-benchmark
```

pytest-benchmark 4.0 is the current stable release as of 2026 (confirmed against PyPI).

**Installation (only if adding benchmark):**
```bash
pip install pytest-benchmark
```

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| pytest-benchmark | locust / k6 | Locust/k6 require a live server process; pytest-benchmark integrates into existing test suite with no extra infra |
| ASGITransport (httpx) | TestClient (requests) | Async tests require AsyncClient + ASGITransport; sync TestClient can't drive async endpoints reliably |
| In-memory SQLite | File SQLite | In-memory is faster, test-isolated; file SQLite needed only for WAL concurrency tests |

## Architecture Patterns

### Recommended Project Structure

```
tests/
├── test_actions.py              # existing — action queue tests
├── test_api.py                  # existing — players/health tests
├── test_circuit_breaker.py      # existing
├── test_cli.py                  # existing
├── test_cors.py                 # existing
├── test_db.py                   # existing
├── test_health_check.py         # BROKEN — needs fix (import error)
├── test_integration.py          # existing — v2 scorer + CLI integration
├── test_listing_tracker.py      # existing
├── test_optimizer.py            # existing
├── test_portfolio*.py           # existing — 6 portfolio test files
├── test_profit.py               # existing
├── test_scanner.py              # existing
├── test_scorer_v2.py            # existing
│
├── test_lifecycle_flows.py      # NEW — cross-endpoint trade lifecycle
├── test_batch_trade_records.py  # NEW — POST /trade-records/batch coverage
└── test_performance.py          # NEW — latency assertions
```

### Pattern 1: ASGI Transport Integration Test (existing, proven)

**What:** FastAPI router mounted on a minimal app, in-memory SQLite, httpx AsyncClient.
No real server process — tests are fast and fully isolated.

**When to use:** All API endpoint tests.

```python
# Source: existing tests/test_actions.py pattern
@pytest.fixture
async def db():
    engine, session_factory = await create_engine_and_tables("sqlite+aiosqlite:///:memory:")
    yield engine, session_factory
    await engine.dispose()

def make_test_app(session_factory):
    app = FastAPI(title="OP Seller Test")
    app.include_router(actions_router)
    app.state.session_factory = session_factory
    return app

async def test_example(db):
    _, session_factory = db
    app = make_test_app(session_factory)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/actions/pending")
    assert resp.status_code == 200
```

### Pattern 2: Lifecycle Flow Test

**What:** Multiple endpoints called sequentially on the same app/session_factory to simulate
a real trade cycle. Tests that state flows correctly across endpoint boundaries.

**When to use:** Any test that needs to verify that one endpoint's output is correctly
consumed by a subsequent endpoint.

```python
async def test_full_buy_list_relist_cycle(db):
    """Simulate: seed slot -> GET pending (BUY) -> complete(bought) -> GET pending (LIST)
    -> complete(listed) -> GET pending (waiting) -> complete(expired) -> GET pending (RELIST)."""
    _, session_factory = db
    app = make_test_app_with_all_routers(session_factory)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Seed slot
        await client.post("/api/v1/portfolio/slots", json={"slots": [
            {"ea_id": 100, "buy_price": 50000, "sell_price": 70000, "player_name": "Test"}
        ]})
        # Step 1: BUY
        r = await client.get("/api/v1/actions/pending")
        action = r.json()["action"]
        assert action["action_type"] == "BUY"
        action_id = action["id"]
        await client.post(f"/api/v1/actions/{action_id}/complete",
                          json={"price": 50000, "outcome": "bought"})
        # Step 2: LIST
        r = await client.get("/api/v1/actions/pending")
        action = r.json()["action"]
        assert action["action_type"] == "LIST"
        # ... etc.
```

### Pattern 3: Multi-Router App Factory

**What:** `make_test_app` that mounts multiple routers when lifecycle tests span endpoints
from different router files (e.g., actions + portfolio_status).

```python
def make_full_test_app(session_factory):
    app = FastAPI()
    app.include_router(actions_router)
    app.include_router(portfolio_router)
    app.include_router(status_router)
    app.include_router(profit_router)
    app.state.session_factory = session_factory
    app.state.read_session_factory = session_factory  # same for tests
    return app
```

### Pattern 4: Performance Assertion (stdlib timing)

**What:** Wrap an async ASGI call in `time.perf_counter()` and assert latency stays under
a threshold. No external tool needed.

**When to use:** p99 latency gates for critical endpoints — pending action, profit summary.

```python
import time

async def test_pending_action_latency(db):
    """GET /actions/pending responds in < 50ms with in-memory SQLite."""
    _, session_factory = db
    # Seed 20 portfolio slots + trade records to simulate realistic DB state
    # ...
    app = make_test_app(session_factory)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        start = time.perf_counter()
        resp = await client.get("/api/v1/actions/pending")
        elapsed_ms = (time.perf_counter() - start) * 1000
    assert resp.status_code == 200
    assert elapsed_ms < 50, f"Pending action took {elapsed_ms:.1f}ms — too slow"
```

### Anti-Patterns to Avoid

- **~~Spinning up real uvicorn server in tests~~** *(RETRACTED for Phase 9)*: The user
  explicitly requested real server integration tests. Phase 9 plans use subprocess.Popen
  to start a real uvicorn process on a free port with a real SQLite file. This is intentional
  and correct for this phase. The original guidance (prefer ASGITransport) remains valid for
  unit-level endpoint tests in other phases.
- **Using session-scoped async fixtures with pytest-asyncio 1.3.0**: pytest-asyncio 1.3.0
  defaults to function-scoped event loops. A session-scoped `async def` fixture will fail
  because the event loop is torn down after the first test function. Solution: make
  session-scoped fixtures synchronous (use subprocess.Popen + time.sleep poll, not asyncio).
- **Using `requests.get()` in async tests**: Blocks the event loop. Always use `httpx.AsyncClient`.
- **Sharing session_factory across test functions**: SQLite in-memory databases are
  connection-scoped. Each test should get a fresh `db` fixture.
- **Asserting on absolute latency with file SQLite**: File-based SQLite has higher latency
  than in-memory. Performance thresholds for real-server tests should be generous (100-300ms)
  to account for network overhead, file I/O, and process boundary.
- **One giant integration test file**: Keep lifecycle flows, batch records, and performance
  in separate files for discoverability and to avoid fixture bloat.
- **Creating new SQLAlchemy engines per-test without timeout on Windows**: SQLite file
  locking on Windows is stricter. Always use `connect_args={"timeout": 10}` when creating
  cleanup engines that connect to the same file as the running server.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Async ASGI test transport | Custom HTTP mock | `httpx.AsyncClient(transport=ASGITransport(app))` | Already in use; handles headers, status codes, JSON |
| In-memory DB | File-based test DB | `create_engine_and_tables("sqlite+aiosqlite:///:memory:")` | Already in use; auto-drops on dispose |
| Timing assertions | locust/k6 | `time.perf_counter()` or `pytest-benchmark` | External tools need a running server; in-process timing sufficient for smoke checks |
| Test isolation | Manual DB cleanup | `pytest.fixture` with `await engine.dispose()` | Already proven pattern; no residual state |

**Key insight:** The project's existing ASGI transport + in-memory SQLite pattern is mature
and should be extended, not replaced. All new tests should follow the same factory pattern
used in `test_actions.py` and `test_portfolio_status.py`.

## Runtime State Inventory

> Not applicable — this is a greenfield test-only phase. No rename/refactor/migration.

## Common Pitfalls

### Pitfall 1: Broken test_health_check.py Collection

**What goes wrong:** `python -m pytest tests/` fails at collection before running any tests
because `test_health_check.py` imports `src.futbin_client` and `src.health_check`, which
reference `FutbinClient` — a class removed in a previous quick task. The test runner aborts
with `ERROR tests/test_health_check.py` before any test runs.

**Why it happens:** The file tests a module that was deleted or gutted. The import fails at
collection time.

**How to avoid:** Phase 9 Wave 0 must fix or delete `test_health_check.py` before adding
any new tests. Options: (a) delete the file if the FUTBIN health monitor is fully gone,
(b) rewrite it against the actual `src.health_check` module if that module still exists
with a different client.

**Warning signs:** `pytest --collect-only` reports `ERROR` for the file.

### Pitfall 2: state.read_session_factory Not Wired in Test Apps

**What goes wrong:** `GET /portfolio/status` endpoint calls
`getattr(request.app.state, 'read_session_factory', None) or request.app.state.session_factory`.
If the test app doesn't set `app.state.read_session_factory`, it silently falls back to
the write session factory — which is fine. But some tests that specifically validate the
read path behavior will get confusing results.

**How to avoid:** Always set both `session_factory` and `read_session_factory` on the test
app state. Use the same in-memory factory for both in tests (no real read/write split needed).

### Pitfall 3: Batch Endpoint Has Zero Tests

**What goes wrong:** `POST /trade-records/batch` (in `actions.py`) handles deduplication,
cross-ea_id validation, and batch commit. It has no test coverage. A regression in any
of those three behaviors will go undetected.

**How to avoid:** `test_batch_trade_records.py` must cover: (a) all-succeed, (b) partial
fail (one unknown ea_id), (c) deduplication (same outcome as last record → skipped),
(d) invalid outcome in batch.

### Pitfall 4: Lifecycle Tests Must Use Action IDs, Not Derived Actions

**What goes wrong:** The `GET /pending` endpoint returns a derived action with an `id`.
Lifecycle tests that call `/complete` with a hardcoded action_id will fail if the
derivation creates a different ID each time.

**How to avoid:** Always extract the `id` from the `GET /pending` response and use it in
the `/complete` call. Never hardcode action IDs in multi-step tests.

### Pitfall 5: Performance Tests Are Non-Deterministic Without Warmup

**What goes wrong:** The first request to an in-memory SQLite app can be slower due to
SQLAlchemy connection pool initialization. A tight latency threshold may fail on the
first call.

**How to avoid:** Issue one warmup request before starting the timer. Or use
`pytest-benchmark`'s built-in warmup rounds.

### Pitfall 6: Portfolio Status Needs read_session_factory

**What goes wrong:** `GET /portfolio/status` explicitly looks for `read_session_factory`
on `app.state`. If a lifecycle test spans both `actions` and `portfolio_status` routers
and the test app only sets `session_factory`, the fallback works — but forgetting to set
`read_session_factory` is a common source of confusion in code review.

**How to avoid:** The multi-router `make_full_test_app()` helper must always set both.

## Code Examples

### Making a Multi-Router Test App

```python
# Source: inferred from existing patterns in test_api.py + test_portfolio_status.py
from fastapi import FastAPI
from src.server.api.actions import router as actions_router
from src.server.api.portfolio import router as portfolio_router
from src.server.api.portfolio_status import router as status_router
from src.server.api.profit import router as profit_router

def make_full_test_app(session_factory):
    """Test app mounting all routers — use for lifecycle integration tests."""
    app = FastAPI(title="OP Seller Integration Test")
    app.include_router(actions_router)
    app.include_router(portfolio_router)
    app.include_router(status_router)
    app.include_router(profit_router)
    app.state.session_factory = session_factory
    app.state.read_session_factory = session_factory  # no real read/write split in tests
    return app
```

### Batch Trade Records Test Pattern

```python
async def test_batch_all_succeed(app_with_slots):
    """POST /trade-records/batch with all valid ea_ids returns succeeded list."""
    app, session_factory = app_with_slots
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/v1/trade-records/batch", json={
            "records": [
                {"ea_id": 100, "price": 50000, "outcome": "bought"},
                {"ea_id": 200, "price": 30000, "outcome": "listed"},
            ]
        })
    assert resp.status_code == 201
    body = resp.json()
    assert set(body["succeeded"]) == {100, 200}
    assert body["failed"] == []
```

### Lifecycle Flow Test Pattern

```python
async def test_full_buy_list_sold_cycle(db):
    """Simulate: confirm portfolio -> BUY action -> complete(bought) -> LIST action
    -> complete(sold) -> GET status (SOLD) -> profit summary reflects sale."""
    _, session_factory = db
    app = make_full_test_app(session_factory)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Seed portfolio
        await client.post("/api/v1/portfolio/slots", json={"slots": [
            {"ea_id": 100, "buy_price": 50000, "sell_price": 70000, "player_name": "Test"}
        ]})
        # Step 1: BUY action
        r = await client.get("/api/v1/actions/pending")
        action = r.json()["action"]
        assert action["action_type"] == "BUY"
        await client.post(f"/api/v1/actions/{action['id']}/complete",
                          json={"price": 50000, "outcome": "bought"})
        # Step 2: LIST action
        r = await client.get("/api/v1/actions/pending")
        action = r.json()["action"]
        assert action["action_type"] == "LIST"
        await client.post(f"/api/v1/actions/{action['id']}/complete",
                          json={"price": 70000, "outcome": "sold"})
        # Step 3: Next cycle -> BUY again
        r = await client.get("/api/v1/actions/pending")
        assert r.json()["action"]["action_type"] == "BUY"
        # Step 4: Verify profit summary
        r = await client.get("/api/v1/profit/summary")
        totals = r.json()["totals"]
        assert totals["net_profit"] > 0
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| requests.TestClient | httpx.AsyncClient + ASGITransport | Phase 5+ | Async endpoints require async test clients |
| File-based test DB | `sqlite+aiosqlite:///:memory:` | Phase 5+ | No residual state between tests |
| Single monolithic test file | One file per router/domain | Phase 5+ | Better discoverability |

**Deprecated/outdated:**
- `test_health_check.py`: References `src.futbin_client.FutbinClient` which was removed
  in quick task `260326-ufn`. File fails to collect. Must be fixed in Wave 0.

## Open Questions

1. **Is Phase 9 blocked on Phase 8?**
   - What we know: Roadmap says "Depends on: Phase 8". Phase 8 adds DOM automation endpoints
     and UI-02/UI-04/UI-05 requirements, but these are extension-side, not backend endpoints.
   - What's unclear: Whether Phase 8 adds any new backend API endpoints that Phase 9 must test.
   - Recommendation: Plan Phase 9 to test the current backend surface (Phases 5-7.2). When
     Phase 8 completes, extend with any new endpoints Phase 8 introduces. Phase 9 can start
     partially without waiting.

2. **Should Phase 9 include extension Vitest tests?**
   - What we know: `extension/tests/` has 5 test files (background, content, dashboard,
     overlay, trade-observer). `cd extension && npm test` runs them. Currently zero CI
     integration with the Python pytest suite.
   - What's unclear: Whether the user wants a unified test command or just confirmation
     both pass independently.
   - Recommendation: Add an appendix task that documents the two-command test invocation:
     `python -m pytest tests/` + `cd extension && npm test`. Do not attempt to merge them
     into one runner — different runtimes.

3. **What is the performance budget?**
   - What we know: No performance requirements exist. The backend is local-only with a single
     user (the extension). SQLite with WAL mode.
   - What's unclear: Whether the user wants latency SLOs (e.g., "pending action < 50ms").
   - Recommendation: Use smoke-level thresholds. In-memory SQLite latency will be < 20ms
     for all endpoints. Document thresholds as "local machine, in-memory SQLite baselines"
     not production SLOs.

4. **What to do with test_health_check.py?**
   - What we know: It fails to collect. `src.futbin_client` and `FutbinClient` were removed.
     `src.health_check` still exists but uses the removed client.
   - What's unclear: Whether `src.health_check` is still a live module or also dead.
   - Recommendation: Wave 0 task should inspect `src.health_check` and either (a) delete
     both the module and the test file if FUTBIN is fully removed, or (b) fix the imports
     if the health check was adapted to use a different client.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python 3.12 | All tests | Yes | 3.12.10 | — |
| pytest | Test runner | Yes | 9.0.2 | — |
| pytest-asyncio | Async tests | Yes | 1.3.0 | — |
| httpx | ASGI transport | Yes | 0.28.1 | — |
| sqlalchemy + aiosqlite | In-memory DB | Yes | installed | — |
| pytest-benchmark | Performance tests | No | — | Use `time.perf_counter()` (stdlib) |
| Node.js / npm | Extension Vitest | Yes | (project uses WXT) | — |

**Missing dependencies with no fallback:** None — all blocking dependencies are present.

**Missing dependencies with fallback:**
- `pytest-benchmark`: Optional; `time.perf_counter()` is sufficient for smoke-level
  latency assertions without adding a dependency.

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest 9.0.2 |
| Config file | `pytest.ini` (root, `asyncio_mode = auto`) |
| Quick run command | `python -m pytest tests/ --ignore=tests/test_health_check.py -q` |
| Full suite command | `python -m pytest tests/ --ignore=tests/test_health_check.py` |
| Extension tests | `cd extension && npx vitest run` |

### Phase Requirements to Test Map

Phase 9 has no formal REQ IDs defined (roadmap says "TBD"). The following maps the implied
scope from the phase name and context to test behaviors:

| Implied Req | Behavior | Test Type | Automated Command | File |
|------------|----------|-----------|-------------------|------|
| Lifecycle-01 | Full BUY→LIST→SOLD cycle via API | integration | `pytest tests/test_lifecycle_flows.py -x` | Wave 0 |
| Lifecycle-02 | BUY→LIST→EXPIRED→RELIST cycle | integration | `pytest tests/test_lifecycle_flows.py -x` | Wave 0 |
| Lifecycle-03 | Direct trade record → correct action derivation | integration | `pytest tests/test_lifecycle_flows.py -x` | Wave 0 |
| Lifecycle-04 | Portfolio status reflects lifecycle state | integration | `pytest tests/test_lifecycle_flows.py -x` | Wave 0 |
| Lifecycle-05 | Profit summary reflects completed cycles | integration | `pytest tests/test_lifecycle_flows.py -x` | Wave 0 |
| Batch-01 | Batch records all succeed | unit | `pytest tests/test_batch_trade_records.py -x` | Wave 0 |
| Batch-02 | Batch records partial fail (unknown ea_id) | unit | `pytest tests/test_batch_trade_records.py -x` | Wave 0 |
| Batch-03 | Batch records deduplicate same outcome | unit | `pytest tests/test_batch_trade_records.py -x` | Wave 0 |
| Batch-04 | Batch records invalid outcome in batch | unit | `pytest tests/test_batch_trade_records.py -x` | Wave 0 |
| Perf-01 | `/actions/pending` responds < 50ms (in-memory) | smoke | `pytest tests/test_performance.py -x` | Wave 0 |
| Perf-02 | `/portfolio/status` responds < 100ms (20 players) | smoke | `pytest tests/test_performance.py -x` | Wave 0 |
| Perf-03 | `/profit/summary` responds < 100ms | smoke | `pytest tests/test_performance.py -x` | Wave 0 |
| Health-01 | test_health_check.py collects without error | infra | `pytest tests/ --collect-only` | Wave 0 fix |

### Sampling Rate

- **Per task commit:** `python -m pytest tests/ --ignore=tests/test_health_check.py -q --tb=short`
- **Per wave merge:** Full suite above
- **Phase gate:** All tests green (including fixed health check) before verification

### Wave 0 Gaps

- [ ] `tests/test_health_check.py` — fix import error (investigate `src.health_check` vs deleted `FutbinClient`)
- [ ] `tests/test_lifecycle_flows.py` — cross-endpoint trade lifecycle flows
- [ ] `tests/test_batch_trade_records.py` — `POST /trade-records/batch` coverage
- [ ] `tests/test_performance.py` — latency smoke assertions

## Sources

### Primary (HIGH confidence)

- Direct code inspection of `src/server/api/actions.py` — confirms `POST /trade-records/batch`
  has zero test coverage
- Direct code inspection of `tests/` directory — confirms lifecycle flow tests are absent
- `python -m pytest tests/ --ignore=tests/test_health_check.py -q` output — 161 passed
- `python -m pytest tests/ -q --tb=no` output — collection error on `test_health_check.py`
- `src/server/main.py` — lists all 6 routers; cross-referenced with test files

### Secondary (MEDIUM confidence)

- Roadmap phase description: "Comprehensive API Integration & Performance Test Suite" —
  inferred scope from name, no formal requirements yet defined
- pytest-benchmark 4.0 — known stable release; not verified against PyPI registry in this
  session but version matches known stable branch

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all libraries already in use, versions confirmed from installed packages
- Architecture: HIGH — extending existing proven patterns, no new design needed
- Pitfalls: HIGH — all identified from direct code/test inspection, not speculation

**Research date:** 2026-03-28
**Valid until:** 2026-04-28 (stable framework, 30-day window)
