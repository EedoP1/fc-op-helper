# Roadmap: FC26 OP Sell Platform

## Milestones

- **v1.0 MVP** — Phases 1-4 (shipped 2026-03-26) | [Archive](milestones/v1.0-ROADMAP.md)
- **v1.1 Chrome Extension** — Phases 5-8 (in progress)

## Phases

<details>
<summary>v1.0 MVP (Phases 1-4) — SHIPPED 2026-03-26</summary>

- [x] **Phase 1: Persistent Scanner** - Scanner backend with APScheduler, SQLite, circuit breaker — completed 2026-03-25
- [x] **Phase 2: Full API Surface** - REST API for portfolio, player detail, top players, health — completed 2026-03-25
- [x] **Phase 3: CLI as API Client** - CLI rewritten as thin API client, no direct fut.gg calls — completed 2026-03-25
- [x] **Phase 4: Refactor Scoring + DB** - Listing-tracking scorer (v2), D-10 formula, schema cleanup — completed 2026-03-25

</details>

### v1.1 Chrome Extension — Automated OP Sell Cycle (In Progress)

**Milestone Goal:** Chrome extension that automates the full buy/list/relist cycle on the EA Web App, powered by the backend's OP sell recommendations, with profit tracking.

- [ ] **Phase 5: Backend Infrastructure** - New DB tables and API endpoints for action queue, trade tracking, and profit summary
- [x] **Phase 6: Extension Architecture Foundation** - WXT scaffold, MV3 service worker with chrome.alarms polling, typed message protocol (completed 2026-03-26)
- [x] **Phase 7: Portfolio Management** - Portfolio generation endpoint, overlay panel showing portfolio, player swap UI (completed 2026-03-27)
- [ ] **Phase 8: DOM Automation Layer** - Buy/list/relist automation with price guard, human-paced delays, CAPTCHA detection, start/stop controls, status display

## Phase Details

### Phase 5: Backend Infrastructure
**Goal**: Backend is ready to serve the extension — action queue, trade recording, and profit summary are live and integrated with existing data
**Depends on**: Phase 4 (v1.0 complete)
**Requirements**: BACK-01, BACK-02, BACK-03, BACK-04, BACK-05, BACK-06
**Success Criteria** (what must be TRUE):
  1. `GET /api/v1/actions/pending` returns one pending action at a time; stale IN_PROGRESS records older than 5 minutes are auto-reset
  2. `POST /api/v1/actions/{id}/complete` accepts a buy/list/relist outcome and the record appears in the DB immediately
  3. `GET /api/v1/profit/summary` returns aggregated trade activity (total buys, coins spent, estimated profit)
  4. Chrome extension origin (`chrome-extension://*`) can make requests to the backend without CORS errors
  5. User can remove a player from the portfolio and the backend returns replacement player(s) within the freed budget
**Plans**: 3 plans
Plans:
- [x] 05-01-PLAN.md — DB models (TradeAction, TradeRecord, PortfolioSlot) + CORS middleware
- [x] 05-02-PLAN.md — Action queue endpoints (GET /pending, POST /complete) + tests
- [x] 05-03-PLAN.md — Profit summary endpoint + player swap DELETE endpoint + tests

### Phase 6: Extension Architecture Foundation
**Goal**: Extension scaffolding is proven — service worker communicates with backend, survives termination, and relays typed commands to the content script
**Depends on**: Phase 5
**Requirements**: ARCH-01, ARCH-02, ARCH-03, ARCH-04
**Success Criteria** (what must be TRUE):
  1. Extension loads in Chrome without manifest errors; service worker registers and stays alive via chrome.alarms keepalive
  2. Service worker fetches the pending action from the backend every 30 seconds and the result survives a DevTools-close + 60-second idle cycle
  3. A PING message from the service worker reaches the content script and a typed PONG response returns; shape mismatches are caught at compile time
  4. Navigating between EA Web App pages (SPA route change) does not orphan the content script — MutationObserver re-initializes listeners on navigation
**Plans**: 2 plans
Plans:
- [x] 06-01-PLAN.md — WXT scaffold, shared types/storage, service worker with alarm polling
- [x] 06-02-PLAN.md — Content script with typed message handling, SPA re-init, Chrome load verification
**UI hint**: yes

### Phase 7: Portfolio Management
**Goal**: User can generate an OP sell portfolio from the extension, view it in an overlay panel on the EA Web App, and swap out players — the foundation that automation builds on
**Depends on**: Phase 6
**Requirements**: PORT-01, UI-01, UI-03
**Success Criteria** (what must be TRUE):
  1. Backend exposes `POST /api/v1/portfolio/generate` that accepts a budget, runs the scorer/optimizer, and seeds portfolio_slots; the generated list is returned in the response
  2. Overlay panel appears on the EA Web App showing the portfolio (player name, buy price, OP sell price, margin) without disrupting existing page layout
  3. User can remove a player from the overlay list and a replacement player appears within the same panel (backend re-runs optimizer with freed budget)
  4. Portfolio persists across browser sessions — reopening the EA Web App shows the same portfolio until the user regenerates
**Plans**: 3 plans
Plans:
- [x] 07-01-PLAN.md — Backend endpoints: POST /generate, POST /confirm, POST /swap-preview, GET /confirmed + tests
- [x] 07-02-PLAN.md — Extension message types, storage types, service worker portfolio proxy handlers + tests
- [x] 07-03-PLAN.md — Overlay panel DOM injection, three-state UI, content script integration + visual verification
**UI hint**: yes

### Phase 07.1: Trade Reporting (INSERTED)

**Goal:** Extension passively reads EA Web App DOM (transfer list, trade pile) to detect and auto-report trade outcomes (bought/listed/sold/expired) for portfolio players. Includes DOM exploration to map selectors. Observation only — no automated clicking.
**Requirements**: AUTO-08 (centralized selectors — shared with Phase 8)
**Depends on:** Phase 7
**Plans:** 3/3 plans complete

Plans:
- [x] 07.1-01-PLAN.md — Selector scaffold + DOM exploration (human DevTools inspection)
- [x] 07.1-02-PLAN.md — Backend POST /trade-records/direct endpoint for bootstrap reporting
- [x] 07.1-03-PLAN.md — Trade observer module, message types, storage, SW handler, content script wiring + tests

### Phase 07.2: Portfolio Dashboard & Trade Tracking (INSERTED)

**Goal:** Dashboard tab in extension overlay showing per-player trade status, cumulative stats (times sold, total profit), realized + unrealized P&L, and trade counts. Backend provides dedicated status endpoint.
**Requirements**: D-01, D-02, D-03, D-04, D-05, D-06, D-07, D-08, D-09, D-10, D-11, D-12, D-13
**Depends on:** Phase 07.1
**Plans:** 2/2 plans complete

Plans:
- [x] 07.2-01-PLAN.md — Backend GET /portfolio/status endpoint with per-player status, cumulative stats, unrealized P&L + tests
- [x] 07.2-02-PLAN.md — Extension message types, service worker handler, tab bar, dashboard panel rendering + tests + visual verification

### Phase 8: DOM Automation Layer
**Goal**: Extension autonomously executes the full buy/list/relist cycle on the EA Web App with price guard, human-paced timing, CAPTCHA detection, and user controls for start/stop and status
**Depends on**: Phase 7
**Requirements**: AUTO-01, AUTO-02, AUTO-03, AUTO-04, AUTO-05, AUTO-06, AUTO-07, AUTO-08, UI-02, UI-04, UI-05
**Success Criteria** (what must be TRUE):
  1. Extension searches for a target player and executes Buy Now only when the live BIN is at or below the backend buy price; cards priced above are skipped without any action
  2. A purchased card is auto-listed at the locked OP price from the portfolio within one automation cycle
  3. An expired card is auto-relisted at the same locked OP price it was originally listed at (price does not update)
  4. All DOM interactions have randomized jitter (800-2500ms); no two consecutive action intervals are identical
  5. When automation encounters a CAPTCHA, it stops immediately and alerts the user; when any DOM element is missing, it fails loudly with the selector name rather than silently continuing
  6. User clicks Confirm to start the automated cycle; start/stop toggle halts automation mid-cycle and resumes from the correct next action
  7. Status display shows current action, last event, and running/stopped/error state at all times
**Plans**: TBD
**UI hint**: yes

## Progress

**Execution Order:**
Phases execute in numeric order: 5 → 6 → 7 → 07.1 → 07.2 → 8

| Phase | Milestone | Plans Complete | Status | Completed |
|-------|-----------|----------------|--------|-----------|
| 1. Persistent Scanner | v1.0 | 3/3 | Complete | 2026-03-25 |
| 2. Full API Surface | v1.0 | 2/2 | Complete | 2026-03-25 |
| 3. CLI as API Client | v1.0 | 1/1 | Complete | 2026-03-25 |
| 4. Refactor Scoring + DB | v1.0 | 4/4 | Complete | 2026-03-25 |
| 5. Backend Infrastructure | v1.1 | 1/3 | In Progress|  |
| 6. Extension Architecture Foundation | v1.1 | 2/2 | Complete   | 2026-03-26 |
| 7. Portfolio Management | v1.1 | 3/3 | Complete   | 2026-03-27 |
| 07.1 Trade Reporting | v1.1 | 3/3 | Complete    | 2026-03-27 |
| 07.2 Portfolio Dashboard | v1.1 | 2/2 | Complete   | 2026-03-27 |
| 8. DOM Automation Layer | v1.1 | 0/TBD | Not started | - |

### Phase 9: Comprehensive API Integration & Performance Test Suite

**Goal:** Real-server integration test suite that starts the REAL server (scanner, scheduler, circuit breaker) with a copy of the production DB, tests all 16 API endpoints via real HTTP, exercises real-world workflows (lifecycle flows, concurrent removes, rapid access), and enforces strict performance thresholds. Tests that fail = server bugs to fix.
**Requirements**: TEST-01, TEST-02, TEST-03, TEST-04
**Depends on:** Phase 5 (tests current backend surface; does not require Phase 8)
**Plans:** 2/3 plans executed

Plans:
- [x] 09-01-PLAN.md — Real server harness (no mocks), env-configurable DB, smoke tests for all 16 endpoints, performance thresholds
- [x] 09-02-PLAN.md — Cross-endpoint lifecycle flows (BUY->LIST->SOLD, EXPIRED->RELIST), concurrent remove duplicate bug, batch records, race conditions
- [x] 09-03-PLAN.md — Edge cases (CORS, invalid input, 404s, boundary conditions), data integrity (unique constraints, clean slate, stale reset)
