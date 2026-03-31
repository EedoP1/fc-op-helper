# Requirements: FC26 OP Sell Platform

**Defined:** 2026-03-26
**Core Value:** Always-fresh, data-driven OP sell recommendations — the server continuously scores every player in the 11k-200k range so you never miss a profitable opportunity.

## v1.1 Requirements

Requirements for Chrome Extension — Automated OP Sell Cycle. Each maps to roadmap phases.

### Backend Infrastructure

- [x] **BACK-01**: Backend exposes action queue endpoint that returns one pending action at a time with stale-record auto-reset
- [x] **BACK-02**: Backend accepts action completion reports (buy, list, relist outcomes with player, price, timestamp)
- [x] **BACK-03**: Backend stores all trade activity in DB for profit tracking (trade_actions, trade_records tables)
- [x] **BACK-04**: Backend exposes profit summary endpoint aggregating trade activity data
- [x] **BACK-05**: Backend CORS configured to accept requests from chrome-extension origin
- [x] **BACK-06**: Backend supports player swap — user removes a player from portfolio, backend returns replacement(s) within freed budget

### Extension Architecture

- [x] **ARCH-01**: Chrome extension built with Manifest V3, service worker handles all backend communication
- [x] **ARCH-02**: Service worker uses chrome.alarms for polling and chrome.storage.local for state (survives worker termination)
- [x] **ARCH-03**: Typed message protocol between service worker and content script (discriminated unions)
- [x] **ARCH-04**: Content script uses MutationObserver for SPA navigation detection and listener re-initialization

### Portfolio Management

- [x] **PORT-01**: Backend exposes endpoint to generate OP sell portfolio for a given budget (runs scorer/optimizer) and seeds portfolio_slots
- [x] **UI-01**: Overlay panel injected into EA Web App showing backend-recommended portfolio (player name, buy price, OP price, margin)
- [x] **UI-03**: User can remove a player from the list and receive replacement player(s) from the backend

### Automation

- [ ] **AUTO-01**: Extension searches transfer market for target player and executes Buy Now when BIN is at or below expected buy price
- [ ] **AUTO-02**: Extension skips player if current BIN exceeds backend buy price (price guard)
- [ ] **AUTO-03**: Extension auto-lists purchased cards at the locked OP price from the portfolio
- [x] **AUTO-04**: Extension auto-relists expired cards at the same locked OP price they were originally listed at (price does not change)
- [x] **AUTO-05**: All DOM interactions use human-like delays with randomized jitter (800-2500ms)
- [ ] **AUTO-06**: Extension detects CAPTCHA and stops automation immediately, alerting the user
- [ ] **AUTO-07**: Extension fails loudly on DOM mismatch (missing elements) rather than silently continuing
- [x] **AUTO-08**: All selectors centralized in one file for maintainability against EA Web App updates
- [ ] **UI-02**: User can confirm the portfolio list to start the automated buy/list/relist cycle
- [ ] **UI-04**: Start/stop automation toggle in overlay panel
- [ ] **UI-05**: Status display showing current action, last event, and running/stopped/error state

### Process Separation (Phase 10)

- [x] **SPLIT-01**: Scanner runs as an independent OS process via `python -m src.server.scanner_main`, with its own DB engine, ScannerService, CircuitBreaker, and APScheduler
- [x] **SPLIT-02**: API process starts without scanner, scheduler, FutGGClient, or CircuitBreaker in memory — only DB pool and FastAPI routers
- [x] **SPLIT-03**: Scanner writes operational metrics (is_running, success_rate_1h, last_scan_at, queue_depth, circuit_breaker_state) to a `scanner_status` DB table every dispatch cycle
- [x] **SPLIT-04**: Health endpoint (`/api/v1/health`) reads scanner metrics from the `scanner_status` DB table instead of in-memory `app.state.scanner`, returning degraded "unknown" state when scanner has not yet written
- [x] **SPLIT-05**: Both processes managed via Docker Compose — `docker compose up` starts postgres, api, and scanner services with auto-restart on failure
- [x] **SPLIT-06**: Integration tests use Docker Compose (docker-compose.test.yml override) to start api and scanner containers against the test database, matching production deployment exactly

## v2 Requirements

Deferred to future release. Tracked but not in current roadmap.

### Dashboard & Analytics

- **DASH-01**: Separate web dashboard for profit analytics and monitoring
- **DASH-02**: Configurable backend URL for remote/cloud deployment

### Multi-User

- **USER-01**: User accounts and paid tiers
- **USER-02**: Multi-account management in extension

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
|---------|--------|
| Separate popup UI | Overlay panel covers all needed controls |
| Fresh OP price on relist | Price locked at original margin — user committed coins at known target |
| Web dashboard (this milestone) | Deferred to v2+ — API/CLI sufficient for profit visibility |
| Configurable backend URL | Localhost only for v1.1 |
| Multi-account support | Dramatically increases ban surface and complexity |
| Auto-bidding | Cost unpredictability incompatible with OP sell strategy |
| CAPTCHA auto-solving | External dependency, robotic patterns still trigger detection |
| Headless/background tab operation | EA detects hidden tabs, severe ban risk |
| Parallel buying | EA rate-limits transfer market; triggers soft bans in minutes |
| FUTBIN price cross-reference | Previously removed; adds rate limits and stale data risk |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| BACK-01 | Phase 5 | Complete |
| BACK-02 | Phase 5 | Complete |
| BACK-03 | Phase 5 | Complete |
| BACK-04 | Phase 5 | Complete |
| BACK-05 | Phase 5 | Complete |
| BACK-06 | Phase 5 | Complete |
| ARCH-01 | Phase 6 | Complete |
| ARCH-02 | Phase 6 | Complete |
| ARCH-03 | Phase 6 | Complete |
| ARCH-04 | Phase 6 | Complete |
| PORT-01 | Phase 7 | Complete |
| UI-01 | Phase 7 | Complete |
| UI-03 | Phase 7 | Complete |
| AUTO-01 | Phase 8 | Pending |
| AUTO-02 | Phase 8 | Pending |
| AUTO-03 | Phase 8 | Pending |
| AUTO-04 | Phase 8 | Complete |
| AUTO-05 | Phase 8 | Complete |
| AUTO-06 | Phase 8 | Pending |
| AUTO-07 | Phase 8 | Pending |
| AUTO-08 | Phase 8 | Complete |
| UI-02 | Phase 8 | Pending |
| UI-04 | Phase 8 | Pending |
| UI-05 | Phase 8 | Pending |
| SPLIT-01 | Phase 10 | Complete |
| SPLIT-02 | Phase 10 | Complete |
| SPLIT-03 | Phase 10 | Complete |
| SPLIT-04 | Phase 10 | Complete |
| SPLIT-05 | Phase 10 | Complete |
| SPLIT-06 | Phase 10 | Complete |

**Coverage:**
- v1.1 requirements: 30 total
- Mapped to phases: 30
- Unmapped: 0

---
*Requirements defined: 2026-03-26*
*Last updated: 2026-03-30 after Phase 10 planning — SPLIT requirements added*
