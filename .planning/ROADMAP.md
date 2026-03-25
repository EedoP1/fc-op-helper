# Roadmap: FC26 OP Sell Platform

## Overview

Starting from a working one-shot Python CLI scorer, this roadmap converts it into a persistent, always-on backend that continuously scores the 11k–200k player pool, exposes a REST API, and makes the CLI a thin consumer of that API. All three phases are purely backend/CLI work — the Chrome extension and web dashboard are v2.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [ ] **Phase 1: Persistent Scanner** - FastAPI app with SQLite, APScheduler hourly scanning, rate-limit-safe 24/7 operation, and the top-players endpoint
- [ ] **Phase 2: Full API Surface** - Budget portfolio endpoint, per-player detail endpoint, adaptive scheduling, and historical score accumulation
- [ ] **Phase 3: CLI as API Client** - Refactor CLI to query the API instead of scoring directly; expose portfolio, player detail, and scan coverage via terminal

## Phase Details

### Phase 1: Persistent Scanner
**Goal**: The backend runs continuously, scans all players in the 11k–200k range every hour, stores scores and market data in SQLite, and serves a live top-players feed without ever crashing on rate limits
**Depends on**: Nothing (first phase)
**Requirements**: SCAN-01, SCAN-02, SCAN-04, API-03, API-04
**Success Criteria** (what must be TRUE):
  1. Running `uvicorn src.server.main:app` starts the server and the scanner begins processing players without manual intervention
  2. `GET /api/v1/players/top` returns a ranked list of OP sell players with scores, margins, and ratios drawn from the SQLite store
  3. The scanner survives a 429 or 5xx from fut.gg without crashing — it backs off, retries with jitter, and trips the circuit breaker if failure rate exceeds 20%
  4. High-activity players are queued for more frequent scans than low-activity ones, observable via scan metadata in the DB
  5. `GET /api/v1/health` returns scheduler status, scan success rate, and last-scan timestamps
**Plans**: 3 plans

Plans:
- [x] 01-01-PLAN.md — Foundation: dependencies, DB layer (SQLAlchemy async + WAL), ORM models, circuit breaker, config constants
- [ ] 01-02-PLAN.md — Scanner service: discovery, scoring, tier-based priority scheduling, retry with tenacity, circuit breaker integration
- [ ] 01-03-PLAN.md — FastAPI app: lifespan wiring, GET /api/v1/players/top endpoint, GET /api/v1/health endpoint, integration tests

### Phase 2: Full API Surface
**Goal**: The backend exposes a complete API covering budget-aware portfolio optimization and per-player drill-down, backed by accumulating historical score data and adaptive per-player scan cadence
**Depends on**: Phase 1
**Requirements**: API-01, API-02, SCAN-03, SCAN-05
**Success Criteria** (what must be TRUE):
  1. `GET /api/v1/portfolio?budget=1000000` returns an optimized list of players within the budget, built from stored scores, not live scoring on request
  2. `GET /api/v1/players/{id}` returns the full score breakdown for a player including margin, op_ratio, expected_profit, efficiency, and recent sales history
  3. A player's next scan time adjusts automatically based on its listing activity — observable by comparing `next_scan_at` across active vs stale players in the DB
  4. Each player accumulates a score history row per scan cycle, so trend data grows over time without manual intervention
**Plans**: TBD
**UI hint**: no

### Phase 3: CLI as API Client
**Goal**: The CLI is a thin display layer that queries the running backend, so all scoring and portfolio logic executes on the server and the terminal just presents results
**Depends on**: Phase 2
**Requirements**: CLI-01, CLI-02, CLI-03
**Success Criteria** (what must be TRUE):
  1. `python -m src.main --budget 1000000` fetches and displays the portfolio from the API — no direct fut.gg calls happen from the CLI process
  2. `python -m src.main --player {id}` displays the detailed score breakdown returned by the API, matching the server-side calculation
  3. Running the CLI when the server is offline produces a clear error message identifying the backend as unreachable, not a Python traceback
**Plans**: TBD

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Persistent Scanner | 1/3 | In Progress|  |
| 2. Full API Surface | 0/? | Not started | - |
| 3. CLI as API Client | 0/? | Not started | - |
