# Project Research Summary

**Project:** FC26 OP Sell Platform — Backend + API + Chrome Extension + Web Dashboard
**Domain:** FUT Ultimate Team trading automation platform
**Researched:** 2026-03-25
**Confidence:** HIGH (stack + architecture), MEDIUM (EA automation specifics)

## Executive Summary

The FC26 OP Sell Platform evolves an existing Python CLI scoring tool into a persistent, always-on trading platform with three new tiers: a FastAPI backend with hourly scheduled scanning, a Chrome extension that automates buy/relist actions on the EA Web App, and an optional web dashboard for profit analytics. The established pattern for this class of tool is to centralize all intelligence in a Python backend and treat the extension and dashboard as thin clients — the backend is the sole fut.gg consumer, sole scorer, and single source of truth. This architecture avoids the rate-limit fragmentation and state-sync bugs that plague tools which distribute intelligence across multiple components.

The recommended approach builds in a strict dependency order: backend API first, then CLI adapts to consume it, then extension automation on top of a stable API contract, then profit tracking, then dashboard. The most valuable differentiator — price-at-time OP verification — is already implemented and carries over unchanged. The core scorer and optimizer are pure Python functions that plug directly into the scheduler without modification. The principal technical bets are FastAPI 0.135 + APScheduler 3.x (not 4.x pre-release) + SQLAlchemy 2.0 async on SQLite, with a WXT-based Manifest V3 extension in TypeScript/React.

The two highest-risk areas are EA account banning from machine-speed automation and fut.gg API fragility at 24/7 scanning scale. Both must be addressed architecturally in Phase 1 — they cannot be retrofitted. Randomized delays and daily transaction caps prevent account bans; exponential backoff with a circuit breaker and scan-health metrics in the DB prevent silent scoring failures. The DOM automation layer of the Chrome extension carries medium-confidence because EA actively deploys Web App updates to break third-party tools; the extension must fail loudly on missing selectors and support a dry-run mode.

---

## Key Findings

### Recommended Stack

The existing Python stack (3.12, httpx, pydantic, click, rich) is unchanged. The new backend layer adds FastAPI 0.135 + uvicorn 0.34 for the HTTP API, APScheduler 3.11 (pinned to `<4.0` — v4 is a breaking pre-release rewrite) for hourly scanning, and SQLAlchemy 2.0 + aiosqlite 0.22 + Alembic 1.18 for async SQLite persistence with a clean PostgreSQL migration path.

The Chrome extension uses WXT 0.20 (Vite-based, MV3-native, actively maintained) with TypeScript and React 19. Plasmo is explicitly avoided due to community-reported maintenance lag. The web dashboard uses React 19 + Vite 6 + Recharts 3.8 + TanStack Query 5.95 + Tailwind 4. All versions are confirmed from PyPI/npm as of 2026-03-25.

**Core technologies:**
- FastAPI 0.135 + uvicorn 0.34: HTTP API layer — native Pydantic v2 integration means existing models plug in directly; lifespan context manager owns scheduler lifecycle
- APScheduler 3.11 (`<4.0`): Hourly scan jobs — `AsyncIOScheduler` shares FastAPI's event loop; do not upgrade to 4.x (no stable release, breaking API rewrite)
- SQLAlchemy 2.0 + aiosqlite 0.22 + Alembic 1.18: Async SQLite persistence — industry standard; swap to PostgreSQL by changing one connection URL; use `render_as_batch=True` for Alembic on SQLite
- WXT 0.20 + TypeScript + React 19: Chrome extension — MV3-native, Vite-based; pin to `~0.20.x` until v1.0 stabilizes
- Recharts 3.8 + TanStack Query 5.95: Dashboard charts + server state — React-native component API; handles polling with built-in caching

See `.planning/research/STACK.md` for full dependency listings and alternatives considered.

---

### Expected Features

The core value loop — buy player cards, list above market price, repeat — requires five features before it can run unattended: a persistent hourly scanner, a REST API serving scored players by budget, extension auto-relist, extension buy-from-list, and a transaction log with P&L display. Everything else is valuable but not blocking.

**Must have (table stakes):**
- Fresh hourly player rankings — stale scores are the primary trust failure mode
- Budget-aware portfolio output — users need an executable list, not a global leaderboard
- Per-player score detail (margin, op_ratio, expected profit, efficiency) — users validate before committing coins
- Auto-relist expired listings — lowest-risk automation; the most time-consuming manual step
- Buy automation from recommendation list — closes the score-to-execution loop
- Profit tracking per session — validates the strategy is working
- Transfer list status visibility — is my list full, sold, expired?
- Rate-limit-safe 24/7 scanning — if the scanner is blocked, nothing else works

**Should have (competitive differentiators):**
- Price-at-time OP verification display — already implemented; surface as visible proof of accuracy
- Score confidence indicator (sale count backing each score)
- Historical score tracking — reveals SBC/promo demand spikes over time
- Market momentum alerts — early signal when a player's OP score jumps between cycles
- Session profit dashboard — ROI %, coins gained, cards sold vs listed
- Scan coverage indicator — what % of the 11k-200k pool scored in last N hours

**Defer (v2+):**
- Web dashboard — CLI + extension popup covers personal use initially; dashboard is a paid-product-tier concern
- Market momentum alerts — requires historical data to accumulate first
- Player filter presets / saved configs — UX convenience, not blocking
- Multi-user / cloud hosting — only if personal tool becomes a product

**Anti-features (explicitly out of scope):** sniping, mass bidding, SBC solver, multi-account, FUTBIN re-integration, social/community signals, mobile app.

See `.planning/research/FEATURES.md` for full dependency graph and rationale.

---

### Architecture Approach

The architecture is a three-tier hub-and-spoke: Python backend at center, Chrome extension and web dashboard as stateless consumers. All intelligence, all state, and the sole fut.gg connection live in the backend. The extension never calls fut.gg directly and stores no business state — it polls the backend for pending actions, executes DOM automation on the EA Web App, and reports outcomes back. SQLite is the single source of truth for players, scores, score history, trade records, and pending action queue.

**Major components:**
1. Python Backend (FastAPI + APScheduler + SQLite) — all business logic, scoring, scheduling, and data; exposes REST API at `/api/v1/`
2. Chrome Extension (WXT, MV3, TypeScript) — service worker polls backend for pending actions; content script automates EA Web App DOM; popup for status display only
3. Web Dashboard (React + Vite) — read-only analytics consumer of backend REST API; served as FastAPI StaticFiles, no separate deployment

**Key patterns to follow:**
- Backend as single source of truth — extension stores NO business state; all trade records live in SQLite
- Command queue pattern — `pending_actions` table with `PENDING/IN_PROGRESS/DONE/FAILED` status; enforces sequential EA Web App automation and enables retry
- Staggered scan batches — never one monolithic job; divide player pool into batches, use `coalesce=False`, set `max_instances=1`
- MutationObserver for SPA navigation — EA Web App is an SPA; content scripts must re-initialize on route change, not rely on static DOM
- chrome.alarms for service worker keepalive — MV3 service workers are ephemeral; alarms wake the worker reliably, setTimeout/setInterval do not survive termination

**Anti-patterns explicitly called out:** scoring in the API layer on-demand, extension calling fut.gg directly, persistent MV2 background page, storing credentials in extension storage, one monolithic scan job.

See `.planning/research/ARCHITECTURE.md` for full data flow diagrams and component boundaries.

---

### Critical Pitfalls

1. **EA transaction-volume ban** — Machine-speed automation triggers EA's bot detection. Prevention: randomized 800ms–2500ms delays between all actions, daily transaction cap under 1,000 operations, 15–30 minute session breaks every 1–2 hours, and a dedicated throwaway test account during all development. Must be baked in from the first extension commit — not retrofittable.

2. **fut.gg API breaking silently at 24/7 scale** — No published rate limits, no SLA. The existing code already has silent 429 failures and bare `except Exception` blocks (logged in CONCERNS.md). Prevention: fix exponential backoff with jitter before adding the scheduler; circuit breaker aborts the cycle if >20% of requests fail; `last_scan_at` / `scan_success_rate` / `parse_failure_count` tracked in DB and surfaced on the API.

3. **MV3 service worker state loss** — Chrome kills idle service workers after 30 seconds; all global variables are wiped. Prevention: store all task state in `chrome.storage.local` or the backend; use `chrome.alarms` (not setTimeout) for polling cadence; design every task as a resumable job that reads state, executes one action, writes state back.

4. **EA Web App SPA navigation breaking content scripts** — SPA route changes do not re-inject content scripts; DOM listeners become orphaned. Prevention: MutationObserver on document body for route-indicator changes; re-initialize listeners on route change; target stable ARIA roles / data attributes, not minified CSS class names.

5. **EA Web App DOM changes post-deploy** — EA actively patches the Web App to break automation. Prevention: never click by coordinate; build a selector registry that fails loudly when a selector is missing; implement dry-run mode for post-deploy validation before resuming automation.

See `.planning/research/PITFALLS.md` for moderate and minor pitfalls (SQLite WAL concurrency, stale scores, APScheduler multi-instance on restart, price-at-time fallback inflation, localhost URL hardcoding).

---

## Implications for Roadmap

### Phase 1: Persistent Backend Foundation

**Rationale:** Everything downstream — extension, dashboard, CLI refactor — depends on a stable REST API backed by live data. The hourly scanner must exist before any freshness guarantee can be made. This is the highest-leverage phase: it converts a one-shot CLI into a continuous intelligence engine.

**Delivers:** FastAPI app with SQLite schema; APScheduler running hourly scans using the existing scorer/optimizer unchanged; REST API endpoints: `GET /api/v1/players/top`, `GET /api/v1/players/{id}`, `GET /api/v1/health`; scan health metrics in DB; exponential backoff on all fut.gg requests.

**Addresses (from FEATURES.md):** Fresh hourly rankings, budget-aware portfolio output, per-player score detail, rate-limit-safe 24/7 scanning.

**Avoids (from PITFALLS.md):** fut.gg silent failures (circuit breaker + health metrics), APScheduler multi-instance on restart (SQLite job store + `coalesce=True` + `max_instances=1`), SQLite write contention (WAL mode + small batch commits), price-at-time fallback inflation (`fallback_used_count` tracked in schema), magic number thresholds hardcoded (move to config + env vars).

---

### Phase 2: CLI Becomes API Client

**Rationale:** The CLI is the only consumer of the backend today. Refactoring it to query the Phase 1 REST API validates the API contract before the extension depends on it. Bugs caught here are cheap; bugs caught after the extension is built are expensive.

**Delivers:** CLI queries `GET /api/v1/players/top?budget=X` instead of running scoring live; CSV output moved to `results/` subdirectory with cleanup; `--no-csv` flag; configures against a running backend instance.

**Addresses (from FEATURES.md):** Configurable budget input, scan coverage indicator (surfaced from API health endpoint).

**Avoids (from PITFALLS.md):** CSV file accumulation, regression in existing CLI UX.

---

### Phase 3: Chrome Extension — Automation Core

**Rationale:** With the API stable and contract validated by the CLI, the extension can be built against a known interface. The extension is the highest-risk component (EA bans, MV3 constraints, SPA DOM complexity) and needs the most careful architectural setup.

**Delivers:** WXT + TypeScript scaffolding; `background.js` service worker polling `GET /api/v1/trades/pending` via `chrome.alarms`; `content-script.js` with MutationObserver for SPA navigation; auto-relist expired listings; buy from recommendation list with target-price enforcement; configurable backend URL in options page; dry-run mode.

**Addresses (from FEATURES.md):** Auto-relist expired listings, buy automation from recommendation list, transfer list status visibility.

**Avoids (from PITFALLS.md):** EA transaction-volume ban (randomized delays, daily cap, session breaks, test account), MV3 service worker state loss (alarms + storage-backed state), SPA navigation breaking content scripts (MutationObserver + listener re-init), DOM changes post-deploy (loud selector failures + dry-run), localhost URL hardcoded (configurable options from day one).

---

### Phase 4: Profit Tracking

**Rationale:** The extension can report trade outcomes only after the trade record schema exists. Profit tracking requires actual buy/sell price data accumulated by the extension — it cannot be back-filled.

**Delivers:** `trades` and `profit_records` tables in SQLite; daily reconciliation scheduler job; REST API endpoints: `GET /api/v1/trades`, `GET /api/v1/profit/summary`; per-card P&L in CLI output.

**Addresses (from FEATURES.md):** Profit tracking per session, session P&L display.

**Avoids (from PITFALLS.md):** Stale scores presented as current (pre-buy live price check added in extension before executing buy action).

---

### Phase 5: Web Dashboard

**Rationale:** The dashboard is purely additive — it consumes stable Phase 1 + Phase 4 APIs and adds no new backend complexity. It is deferred because CLI + extension popup covers all personal-use needs first.

**Delivers:** React + Vite + Recharts + TanStack Query dashboard served as FastAPI StaticFiles; top OP player list with score freshness indicators; trade log; profit summary charts; score history trends; scheduler health status.

**Addresses (from FEATURES.md):** Session profit dashboard, historical score tracking, scan coverage indicator.

**Avoids (from PITFALLS.md):** Not applicable — read-only consumer, no new risk surfaces.

---

### Phase 6: Historical Analysis and Alerts (v2+)

**Rationale:** Requires score history to have accumulated over multiple weeks. Market momentum alerts are only useful once there are multiple time-series data points per player — building this in Phase 1 would produce an empty, untestable feature.

**Delivers:** Score history charts per player; momentum delta detection between scan cycles; notification delivery for high-delta players.

**Addresses (from FEATURES.md):** Market momentum alerts, historical score tracking (full implementation).

---

### Phase Ordering Rationale

- Phases 1 → 2 → 3 follow the hard dependency graph from ARCHITECTURE.md: API must be stable before extension is built; CLI validation of the API contract before extension depends on it is a cheap insurance step.
- Phase 4 (profit tracking) follows Phase 3 because it requires the extension to be reporting trade outcomes — the schema can be created in Phase 1 but populated data does not exist until Phase 3 is running.
- Phase 5 (dashboard) is last of the core phases because it is purely additive and personal use does not require it.
- Phase 6 deferred because historical data must accumulate before the feature is testable or useful.

### Research Flags

Phases likely needing deeper research during planning:

- **Phase 3 (Chrome extension):** EA Web App DOM structure for the transfer market, buy-now flow, and relist flow is undocumented and changes with EA deploys. Will need to inspect the live EA Web App and reference EasyFUT/futbot source to map current selectors before implementation. Dry-run mode is essential for ongoing discovery.
- **Phase 3 (Chrome extension):** MV3 service worker + content script message passing for sequential automation is nuanced; the offscreen document API may be required for certain DOM interactions. Needs a spike to confirm the exact content-script injection approach works with the EA Web App's CSP headers.

Phases with well-documented patterns (skip research-phase unless issues arise):

- **Phase 1 (Backend):** FastAPI + APScheduler + SQLAlchemy 2.0 async patterns are well-documented with high-quality community examples. Standard setup.
- **Phase 2 (CLI refactor):** Straightforward; no new technology.
- **Phase 4 (Profit tracking):** Standard DB schema + scheduler job; no novel patterns.
- **Phase 5 (Dashboard):** React + Vite + Recharts + TanStack Query are individually well-documented and commonly composed together.

---

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | All versions confirmed from PyPI/npm. APScheduler 4.x instability confirmed from migration docs. WXT vs Plasmo confirmed from multiple community comparisons. |
| Features | HIGH | Feature categories derived from competitive analysis of 10+ existing FUT tools plus domain-specific OP sell guides. Anti-feature rationale is principled and consistent with scope constraints. |
| Architecture | HIGH (core), MEDIUM (EA DOM) | Three-tier pattern, command queue, MV3 constraints — all verified from official Chrome docs and established reference implementations. EA Web App DOM selectors are undocumented and subject to change. |
| Pitfalls | MEDIUM | Ban risk patterns from community sources (non-deterministic EA enforcement); SQLite concurrency from official docs (HIGH); MV3 service worker lifecycle from official docs (HIGH); EA SPA navigation from community sources (MEDIUM). |

**Overall confidence:** HIGH for the backend and dashboard. MEDIUM for the Chrome extension automation layer due to EA's opaque and changing Web App.

### Gaps to Address

- **EA Web App DOM selectors:** No documented, stable selector list exists for the transfer market buy/relist flow. Must be discovered by inspecting the live EA Web App during Phase 3 planning. Build the selector registry with versioning from day one.
- **fut.gg rate limits:** No published rate limits. The 0.15s delay + concurrency=10 approach has worked in one-shot mode; 24/7 behavior is untested. Monitor `scan_success_rate` in the first week of Phase 1 and adjust throttling empirically.
- **EA CSP headers on Web App:** The EA Web App may have Content Security Policy headers that restrict what the content script can do. This needs a spike test during Phase 3 to confirm `fetch()` from content scripts to localhost works as expected (it likely routes through the service worker, but verify).
- **`async_sessionmaker(expire_on_commit=False)` requirement:** Must be applied to all SQLAlchemy session factories in Phase 1. Lazy-loading after commit in async context causes `MissingGreenlet` errors that are subtle and hard to debug at scale.

---

## Sources

### Primary (HIGH confidence)
- [FastAPI Release Notes](https://fastapi.tiangolo.com/release-notes/) — version 0.135.2
- [APScheduler 3.x Docs](https://apscheduler.readthedocs.io/en/3.x/) — version 3.11.2.post1 stable
- [APScheduler Migration Guide](https://apscheduler.readthedocs.io/en/master/migration.html) — 4.x instability confirmed
- [SQLAlchemy PyPI](https://pypi.org/project/SQLAlchemy/) — version 2.0.48
- [aiosqlite PyPI](https://pypi.org/project/aiosqlite/) — version 0.22.1
- [Alembic Docs](https://alembic.sqlalchemy.org/en/latest/front.html) — version 1.18.4
- [Chrome Extension Message Passing](https://developer.chrome.com/docs/extensions/develop/concepts/messaging) — MV3 official docs
- [Cross-origin Network Requests in Extensions](https://developer.chrome.com/docs/extensions/develop/concepts/network-requests) — CORS bypass via host_permissions
- [Use WebSockets in Service Workers](https://developer.chrome.com/docs/extensions/how-to/web-platform/websockets) — keepalive patterns
- [Chrome MV3: migrate to service workers](https://developer.chrome.com/docs/extensions/develop/migrate/to-service-workers) — state loss constraints
- [SQLite WAL official docs](https://sqlite.org/wal.html) — concurrency design

### Secondary (MEDIUM confidence)
- [WXT Framework](https://wxt.dev/) + [2025 Extension Framework Comparison](https://redreamality.com/blog/the-2025-state-of-browser-extension-frameworks-a-comparative-analysis-of-plasmo-wxt-and-crxjs/) — WXT vs Plasmo analysis
- [TanStack Query npm](https://www.npmjs.com/package/@tanstack/react-query) — version 5.95.0
- [Recharts npm](https://www.npmjs.com/package/recharts) — version 3.8.0
- [FastAPI + Async SQLAlchemy 2.0 Setup](https://medium.com/@tclaitken/setting-up-a-fastapi-app-with-async-sqlalchemy-2-0-pydantic-v2-e6c540be4308) — session patterns
- [Alembic + SQLite batch mode](https://blog.greeden.me/en/2025/08/12/no-fail-guide-getting-started-with-database-migrations-fastapi-x-sqlalchemy-x-alembic/) — render_as_batch requirement
- [EasyFUT (EA FC automation Chrome extension)](https://github.com/Kava4/EasyFUT) — reference implementation for DOM automation patterns
- [FutBotManager EA ban wave guide](https://futbotmanager.com/ea-ban-wave-avoidance-futbotmanager/) — transaction volume thresholds
- [Making Chrome Extension Smart for SPA websites](https://medium.com/@softvar/making-chrome-extension-smart-by-supporting-spa-websites-1f76593637e8) — SPA navigation handling

### Tertiary (LOW confidence)
- Community FUT trading guides — OP sell mechanics (consistent across multiple sources; HIGH confidence for strategy, LOW for exact thresholds)
- EA enforcement non-determinism — ban triggers are inferred from community reports, not documented by EA

---
*Research completed: 2026-03-25*
*Ready for roadmap: yes*
