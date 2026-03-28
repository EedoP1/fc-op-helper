# Phase 9: Real-World Server Integration Tests - Context

**Gathered:** 2026-03-28
**Status:** Ready for planning

<domain>
## Phase Boundary

Test suite that exercises the server exactly like a real user — with real data, real scanner, concurrent access patterns, and real-world workflows. Tests that fail reveal server bugs to fix. The goal is NOT green tests — it's finding every broken behavior.

**Key shift from previous attempt:** The first attempt used mocked scanner, empty DB, and fake ea_ids. Tests passed because they didn't test anything real. This attempt uses the real server code, a copy of the production DB, and the actual workflows that break.

</domain>

<decisions>
## Implementation Decisions

### Server Under Test
- **D-01:** Use the REAL server code (`src/server/main.py`) — no MockScanner, no MockCircuitBreaker, no simplified harness. The test starts the actual server the user runs.
- **D-02:** Copy the real production DB (`D:/op-seller/op_seller.db`) to a temp file. Tests run against real scored players, real market data, real portfolio history. The original DB is never modified.
- **D-03:** If the real scanner needs fut.gg API access and that's not available in CI, the test should FAIL — that's a design issue to solve, not to mock around.

### Test Philosophy
- **D-04:** Tests that fail = server bugs. Do NOT weaken assertions to make tests pass. Every failure becomes a tracked issue to fix in the server code.
- **D-05:** Tests must exercise real-world workflows the user actually does, not synthetic happy paths with fake data.
- **D-06:** Concurrent access patterns are critical — the real extension sends rapid API calls and the server must handle them correctly.

### Workflows to Test
- **D-07:** Portfolio generation — generate with real budget against real scored data, verify it completes in reasonable time, verify no duplicates
- **D-08:** Concurrent removes — remove 2+ players from portfolio rapidly before first action completes, verify no duplicate players appear in the replacement list
- **D-09:** Remove/replace flow — remove a player, get replacement, verify replacement is valid and the list stays consistent
- **D-10:** Action lifecycle races — buy/list/sell cycle where actions overlap or complete out of order
- **D-11:** Rapid API access — hit the same endpoint many times fast, verify server doesn't corrupt data or crash
- **D-12:** Scanner interaction — test API behavior when the real scanner is running, idle, or mid-scan (different DB lock states)

### Performance
- **D-13:** Strict thresholds: health < 100ms, pending action < 200ms, portfolio status < 300ms, profit summary < 200ms (p95, real HTTP over loopback with real DB data)
- **D-14:** Portfolio generation time should be measured and have a threshold — if generating a list takes too long, that's a bug

### Cleanup Strategy
- **D-15:** Reset mutable tables (portfolio_slots, trade_actions, trade_records) between tests to prevent cross-test contamination
- **D-16:** PRESERVE read-only data (player_records, player_scores, market_snapshots, etc.) — this is the real data that makes tests meaningful
- **D-17:** Some tests should explicitly skip cleanup to test accumulation/state-leak bugs

### Infrastructure (kept from previous attempt)
- **D-18:** Keep `tests/integration/conftest.py` structure — session-scoped uvicorn subprocess on free port, httpx.AsyncClient, per-test cleanup. But the server it starts must be the REAL server, not a test harness.
- **D-19:** Client timeout = 30s (real DB queries can be slow; timeouts should be generous enough to detect real hangs vs normal slow queries)

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Server Code
- `src/server/main.py` — Real server lifespan, scanner startup, all routers
- `src/server/db.py` — Engine creation, WAL mode, session factories
- `src/server/api/portfolio.py` — Portfolio generation, confirm, swap-preview, delete
- `src/server/api/actions.py` — Action queue, pending derivation, completion, slots
- `src/server/api/profit.py` — Profit summary aggregation
- `src/server/api/portfolio_status.py` — Portfolio status with trade history
- `src/server/api/health.py` — Health check (uses real scanner)
- `src/server/api/players.py` — Top players, player detail

### Existing Test Infrastructure
- `tests/integration/conftest.py` — Session fixtures, DB copy, cleanup
- `tests/integration/server_harness.py` — Current (to be replaced with real server)

### Config
- `src/config.py` — EA_TAX_RATE, DATABASE_URL, all constants

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `tests/integration/conftest.py` — synchronous live_server fixture, free port discovery, DB copy mechanism, httpx client fixture, per-test cleanup. Core infrastructure is solid.
- Real DB at `D:/op-seller/op_seller.db` — contains hundreds of scored players, market snapshots, price history

### Established Patterns
- Server uses 3 separate SQLAlchemy engines (scanner write, API read, API write) with different timeouts — tests must account for this
- WAL mode on SQLite — concurrent reads work, but concurrent writes can still lock
- `_derive_next_action` iterates portfolio slots in insert order — ordering matters for interleaved tests

### Integration Points
- `server_harness.py` needs to be replaced/rewritten to start the real server (not mock scanner)
- conftest cleanup connects directly to SQLite file — can conflict with server's held connections
- Real scanner starts background tasks (APScheduler) — tests need to handle or disable the scheduler without mocking the scanner itself

</code_context>

<specifics>
## Specific Ideas

- User has experienced: fragile player record insertion, slow portfolio generation, slow player removal, duplicate players when removing 2 before first action completes
- "I am sure there are many many more problems" — test suite should be comprehensive enough to surface unknown issues
- User explicitly wants tests to find bugs, not verify correctness — the mindset is audit/stress-test, not regression

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 09-comprehensive-api-integration-performance-test-suite*
*Context gathered: 2026-03-28*
