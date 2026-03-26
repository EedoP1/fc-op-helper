# Project Research Summary

**Project:** FC26 OP Sell Platform — Chrome Extension Milestone (v1.1)
**Domain:** Chrome Extension + FastAPI backend for EA Web App automation (buy/list/relist cycle)
**Researched:** 2026-03-26
**Confidence:** MEDIUM-HIGH

## Executive Summary

This milestone adds a Chrome extension on top of an already-production-ready Python backend (v1.0). The v1.0 backend runs 24/7, scans ~1800 players every 5 minutes, scores them for OP sell opportunity, and exposes a REST API. The extension's sole job is to read backend-ranked recommendations and execute the buy/list/relist cycle on the EA Web App. All intelligence stays in the backend — the extension is a thin executor. This architecture is the right call: it avoids the dead-ends other FUT automation tools hit by coupling pricing logic to the extension itself and bypasses the rate-limit fragmentation that comes from having the extension call fut.gg directly.

The dominant technical challenge is not the extension's UI or API communication — those patterns are well-documented with official Chrome sources. The challenge is navigating two hard constraints simultaneously: Chrome Manifest V3's ephemeral service worker lifecycle, and EA's bot detection that bans accounts for inhuman timing patterns. These two constraints are in tension: keeping automation running long enough to matter requires careful service worker keepalive, while keeping it safe requires deliberate human-paced slowness. Both must be designed in from the first commit — they cannot be retrofitted. The recommended approach (service worker polling via `chrome.alarms`, action state persisted to `chrome.storage.local` and backend DB, human-paced delays with jitter) solves both simultaneously.

The extension build sequence has a clear critical path: backend additions first (3 new DB tables, 4 new endpoints, CORS config), then extension scaffolding with service worker architecture, then DOM automation. The EA Web App DOM interaction layer carries the highest ongoing risk — EA deploys silent updates that break CSS selectors without notice. Centralizing all selectors in one file and implementing loud failures (stop automation, notify user) rather than silent no-ops is the mitigation. Expect maintenance work after every EA Web App deploy; a dry-run mode is essential for post-deploy verification.

---

## Key Findings

### Recommended Stack

The existing Python stack (FastAPI 0.135, APScheduler 3.11, SQLAlchemy 2.0 + aiosqlite 0.22, Alembic 1.18) is already deployed and unchanged for this milestone. The new work is the Chrome extension and its supporting backend additions only.

For the extension, WXT (~0.20.20) is the clear choice over Plasmo (maintenance lag, 2x larger bundles, Parcel bundler) and CRXJS (posted archival notice if no new maintainer by June 2025; status uncertain March 2026). WXT provides Manifest V3 native scaffolding, correct service worker lifecycle handling, and HMR — the things that are hardest to get right building raw MV3. The content script stays vanilla TypeScript with no React to keep it lightweight; React is used only in the extension popup. All backend communication goes through the service worker, never content scripts.

**Core technologies:**
- WXT ~0.20.20: Extension build framework — MV3 native, Vite-based, actively maintained; replaces Plasmo and CRXJS which both have viability concerns
- TypeScript ^5.0.0: Extension language — type safety for DOM selectors and chrome.* APIs; catches shape mismatches against backend response types at compile time
- React 19.x: Popup UI only — not loaded in content scripts; consistent with broader extension ecosystem
- zod ^3.23.0: Runtime validation of backend API responses in service worker — prevents crashes from API shape changes
- FastAPI 0.135 (existing, unchanged): Backend API — 4 new routes added as an `actions.py` router; existing routes untouched
- APScheduler 3.11 (existing, unchanged): Scanner jobs — do NOT upgrade to 4.x (pre-release, breaking API rewrite with no job store migration path)
- SQLAlchemy 2.0 + aiosqlite 0.22 (existing, unchanged): ORM — 3 new tables appended; existing schema untouched

See `.planning/research/STACK.md` for full dependency listings, version compatibility notes, and monorepo structure.

---

### Expected Features

The extension is scoped to v1.1. Research confirms every feature in the MVP list is table stakes for FUT automation tools — missing any one of them makes the extension unusable for real money operations. The key differentiator over generic FUT bots is that all prices are backend-driven and refreshed on every relist cycle, not user-set or FUTBIN-sourced.

**Must have (table stakes for v1.1):**
- Backend API connection (service worker → localhost:8000) — the critical path; everything else depends on it
- Overlay panel in EA Web App showing ranked portfolio (player name, buy price, OP price, margin)
- Start/stop automation toggle with status display
- Price guard before every buy — compare live BIN against backend buy_price, skip if market moved up
- Buy automation: search by ea_id, verify price, click Buy Now
- Auto-list purchased cards at backend-recommended OP price
- Auto-relist expired cards with fresh OP price fetched from backend on each relist cycle
- Human-like delays with jitter (800ms–2500ms per action; never fixed intervals)
- Error handling: CAPTCHA stops automation and alerts user; DOM mismatch fails loudly with specifics
- Activity reporting: POST buy/list/relist events to new backend endpoint

**Should have (add after v1.1 validation):**
- Session profit summary in extension panel
- Budget input in extension panel (change budget without restarting backend CLI)
- Sound/visual notification on successful buy
- Session stats display (buys, coins spent, estimated profit)

**Defer (v2+):**
- Separate web dashboard for analytics — needs multi-user infrastructure to justify the work
- Cloud-hosted backend with configurable extension URL — requires user accounts and deployment work
- Multi-account support — significantly increases ban surface and complexity

**Anti-features (never build for this project):**
- Headless/background tab operation — EA detects hidden tabs; ban risk is severe
- Parallel buying — EA rate-limits transfer market API; triggers soft bans in minutes
- CAPTCHA auto-solving — adds external dependency; robotic patterns still trigger detection after solve
- Auto-bidding — introduces cost unpredictability incompatible with the OP sell strategy
- FUTBIN price cross-reference — previously removed from codebase; adds rate limits and stale data risk

See `.planning/research/FEATURES.md` for full dependency graph and competitor analysis.

---

### Architecture Approach

The architecture separates concerns cleanly: the backend owns the action queue and all intelligence; the extension service worker handles polling and backend communication; the content script handles DOM execution only. This backend-driven queue pattern eliminates split-brain state and makes the extension resumable across service worker restarts — if the service worker is killed mid-cycle, it checks the backend for stale `IN_PROGRESS` records on next startup.

**Major components:**

1. **Service Worker (background.ts)** — Polls backend via `chrome.alarms` every 30s; fetches one pending action at a time; sends typed command to content script; receives result; POSTs outcome to backend. Keeps all state in `chrome.storage.local` and backend DB — zero global variables for state that must survive a restart.

2. **Content Script (content.ts)** — Executes DOM operations on webapp.ea.com. Uses `MutationObserver` to wait for EA's SPA to render target elements before acting. All DOM interactions sequential with human-paced delays. Reports outcomes back to service worker via `chrome.runtime.sendMessage`. Never communicates with backend directly.

3. **Backend Actions Router (src/server/api/actions.py)** — 4 new endpoints: `GET /api/v1/actions/pending` (returns 1 action atomically, auto-resets stale `IN_PROGRESS` records >5 min), `POST /api/v1/actions/{id}/complete`, `GET /api/v1/profit/summary`, `POST /api/v1/actions/queue`. 3 new DB tables: `trade_actions`, `trade_records`, `profit_snapshots`.

4. **Popup (popup.html + popup.ts)** — Minimal status display: running/stopped/error state, last sync time, pending action count, start/stop toggle. Plain TypeScript only — no React in the popup.

5. **Backend CORS config** — `CORSMiddleware` allowing `chrome-extension://*` origin. Required for service worker-to-backend requests from the extension's origin.

**Message protocol** uses TypeScript discriminated unions (`ExtensionCommand` / `ExtensionResult` types) for all service-worker ↔ content-script messages, preventing shape mismatches at compile time.

**Zero changes to** scorer_v2.py, optimizer.py, scanner.py, scheduler.py, listing_tracker.py, futgg_client.py, or any existing API routes.

See `.planning/research/ARCHITECTURE.md` for full data flow diagrams, all message types, and the 7-step build order.

---

### Critical Pitfalls

1. **EA ban from automation volume and uniform timing** — Fixed-interval timing, rapid buy sequences, and background-tab automation are the primary ban triggers. EA documents a daily bid/buy limit; exceeding it escalates to market bans and account suspension. Mitigation: randomized jitter (800ms–2500ms per action, not fixed), daily buy cap well below 1,000 operations, session breaks every 1–2 hours, automation only in foreground tab, dedicated throwaway test account for all development. Must be baked in from the first commit — retrofitting is error-prone and misses edge-case code paths.

2. **MV3 service worker state loss** — Chrome terminates idle service workers after 30 seconds; all memory globals are wiped. The bug is masked during development because DevTools open prevents worker termination. Mitigation: store ALL task state in `chrome.storage.local` or backend DB; use `chrome.alarms` (not `setTimeout`) for recurring work; model every automation step as resumable — read state, execute one action, write updated state back. Verify by closing DevTools, idling 60 seconds, then confirming automation resumes.

3. **EA Web App SPA navigation orphaning content script** — EA's Angular SPA replaces DOM subtrees on route changes without a page reload; content script event listeners silently break. Mitigation: `MutationObserver` on `document.body` to detect route changes; re-initialize all listeners on navigation back to Transfer Market; delegate event listeners to `document.body` rather than leaf elements.

4. **EA Web App DOM changes breaking automation silently** — EA deploys updates mid-season; CSS class names are minified and rotate between deploys. Silent `null` querySelector results look like working automation. Mitigation: never target by CSS class; use ARIA roles, `data-*` attributes, or label text; implement loud failures (stop + notify user with specifics) when any `waitForElement` times out; centralize all selectors in `lib/ea-selectors.ts`; implement dry-run mode for post-deploy verification.

5. **CORS blocking content script backend requests** — Content scripts run in the host page's CORS context; `fetch()` to localhost from content script is blocked even with `host_permissions` declared. Mitigation: route ALL backend calls through service worker via `chrome.runtime.sendMessage`; content scripts never call backend directly. This is a constraint of Chrome's security model, not configurable.

See `.planning/research/PITFALLS.md` for technical debt patterns, integration gotchas, performance traps, security mistakes, and a full "Looks Done But Isn't" checklist.

---

## Implications for Roadmap

Based on the research dependency graph and pitfall-to-phase mapping, a 4-phase structure is recommended for the v1.1 milestone.

### Phase 1: Backend Infrastructure
**Rationale:** The extension cannot be tested end-to-end without the backend endpoints. Backend work has no external unknowns — it extends an existing codebase with well-understood patterns. Building it first means the service worker has a real API to test against, not mocks. The CORS configuration must also land here before any extension work begins.
**Delivers:** 3 new DB tables (`trade_actions`, `trade_records`, `profit_snapshots`), 4 new API endpoints (pending actions with stale-reset logic, complete action, profit summary, queue generation), CORS middleware for `chrome-extension://*` origin, Alembic migration for new tables.
**Addresses:** Activity reporting to backend, profit analytics via CLI.
**Avoids:** CORS misconfiguration (locked in here), stale `IN_PROGRESS` records (stale-reset logic in pending-actions endpoint), split-brain state between extension and backend.
**Research flag:** Standard patterns — FastAPI router addition, SQLAlchemy table append, Alembic migration with `render_as_batch=True`. No phase research needed; all HIGH confidence.

### Phase 2: Extension Architecture Foundation
**Rationale:** The MV3 service worker lifecycle constraints and CORS routing pattern must be established before any automation logic is written. Adding these constraints as a retrofit to code designed around global variables requires a full rewrite. This phase builds the skeleton with no automation logic, proving the backend communication path and storage-backed state model work correctly.
**Delivers:** WXT project scaffolded in `extension/`; manifest.json (MV3, permissions, host_permissions, content_scripts match pattern); typed message protocol (`types/messages.ts`, `types/api.ts`, `types/actions.ts`); service worker with `chrome.alarms` polling loop; backend API client wrappers (`lib/backend.ts`); `chrome.storage.local` state schema defined; PING/PONG health check between service worker and content script; service worker keepalive alarm registered.
**Uses:** WXT ~0.20.20, TypeScript ^5.0.0, @types/chrome ^0.1.38, zod ^3.23.0.
**Avoids:** Service worker state loss (storage-backed from the start), CORS content script requests (message routing established before any automation is added), duplicate alarms on restart (check `chrome.alarms.get(name)` before creating).
**Research flag:** No additional research needed. Chrome MV3 patterns are HIGH confidence from official docs. WXT setup is documented. The communication architecture is fully specified in ARCHITECTURE.md.

### Phase 3: DOM Automation Layer
**Rationale:** This is the highest-risk phase due to EA Web App DOM instability. Building it last means backend and extension infrastructure are proven before tackling the hardest part. The `waitForElement()` / MutationObserver scaffold and centralized selector file must be the first work in this phase — all specific automation flows build on top of them.
**Delivers:** `lib/dom-utils.ts` (`waitForElement`, `clickElement`, `setInputValue`, `humanDelay`); `lib/ea-selectors.ts` (all selectors centralized as named constants); `lib/ea-navigator.ts` (buy flow, list flow, relist flow as reusable sequences); buy automation with price guard; auto-list after purchase; auto-relist expired cards with fresh price; sold detection; dry-run mode; error handling (CAPTCHA detection, DOM mismatch loud failure); SPA navigation re-initialization via MutationObserver; full overlay panel injected into EA Web App.
**Addresses:** All table-stakes v1.1 features (buy, list, relist, price guard, overlay, delays, error handling, activity reporting).
**Avoids:** EA ban timing (jitter baked into `humanDelay` from start), DOM silent failures (loud failure in `waitForElement` timeout), SPA navigation orphaning (MutationObserver-based listener re-init), DOM selector changes (centralized `ea-selectors.ts` + dry-run mode), main-world injection security risk (use isolated world by default; main-world injection only if service-level API access is required and confirmed safe).
**Research flag:** NEEDS DEEPER RESEARCH before implementation. The specific EA Web App service names (`window.services.Auction`, `window.repositories.Item`), current method signatures for buy-now and relist in FC26, and which DOM elements have stable ARIA attributes vs. minified class names must be verified by inspecting the live web app in browser DevTools before writing any selectors. This is explicitly LOW confidence in the research; allocate a dedicated exploration task at the start of this phase.

### Phase 4: Popup + End-to-End Validation
**Rationale:** The popup is the simplest component and should be built last after the automation cycle is proven working. End-to-end validation with a real EA account (dedicated test account, never the main account) closes out the milestone.
**Delivers:** Extension popup with status panel (running/stopped/error state, last sync, pending count, start/stop toggle); profit summary pulled from backend `/api/v1/profit/summary`; full buy/list/relist cycle test with real coins on dedicated test account; dry-run mode verification; timing distribution validation over 4-hour test session (no "limit reached" messages, action timing is non-uniform).
**Avoids:** EA ban from volume (cap and jitter validated under real conditions), task resumability failure (crash-and-restart test), selector breakage (dry-run confirms all selectors resolve), configurable backend URL (options page verified to persist and propagate correctly).
**Research flag:** No additional research. This phase is manual validation and integration, not new implementation.

### Phase Ordering Rationale

- Backend must precede extension (Phase 1 before 2 and 3) — the service worker cannot be integration-tested without real endpoints responding.
- Extension architecture must precede DOM automation (Phase 2 before 3) — the MV3 service worker constraints make retrofitting state management impossible without a full rewrite; the correct foundation must be the starting point.
- DOM automation is isolated to Phase 3 because it is the only phase with a critical unknown (EA Web App DOM internals). Isolating it means all infrastructure risk is resolved before tackling the part that requires live exploration.
- The popup is intentionally last (Phase 4): lowest complexity component, no blocking risk. Deferring it keeps Phase 3 focused on the hard work.

### Research Flags

Needs deeper research before planning:
- **Phase 3 (DOM Automation):** EA Web App DOM specifics — which selectors are stable, which service/repository names are current in FC26, which ARIA attributes are present on transfer market UI elements. Must be verified by live inspection before writing any automation code. FEATURES.md assigns LOW confidence to EA DOM stability explicitly.

Standard patterns (skip research-phase):
- **Phase 1 (Backend Infrastructure):** FastAPI router addition, SQLAlchemy table append, Alembic migration — all HIGH confidence from official docs with working examples already in this codebase.
- **Phase 2 (Extension Architecture):** Chrome MV3 service worker patterns, WXT setup, `chrome.alarms`, `chrome.storage.local` — HIGH confidence from official Chrome docs. WXT documentation is complete.
- **Phase 4 (Popup + Validation):** Plain TypeScript popup, manual validation — no unknowns.

---

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | All versions confirmed from PyPI and npm as of 2026-03-26. WXT is MEDIUM-HIGH (pre-1.0 semver but stable API). APScheduler 4.x avoidance well-evidenced from migration docs. CRXJS archival risk confirmed from GitHub discussions. |
| Features | MEDIUM | Extension feature list well-researched from open-source FUT tools (EasyFUT, MagicBuyer-UT, FUT-Trader, Futinator). EA DOM internals are LOW confidence — no official documentation; must be validated against live web app before Phase 3 implementation begins. |
| Architecture | HIGH (Chrome patterns), MEDIUM (EA DOM) | Chrome MV3 patterns from official docs are HIGH. FastAPI integration is HIGH. EA-specific service injection pattern (`window.services`) confirmed from FSU script source on GreasyFork — MEDIUM because FC26 method names are not individually confirmed. |
| Pitfalls | MEDIUM-HIGH | Chrome MV3 pitfalls (service worker lifecycle, CORS) are HIGH from official docs. EA ban patterns are MEDIUM — community evidence is strong but EA does not publish detection thresholds. DOM change risk is HIGH (confirmed EA behavior) but mitigation effectiveness is MEDIUM (depends on implementation discipline). |

**Overall confidence:** MEDIUM-HIGH

### Gaps to Address

- **EA Web App selector strategy for FC26:** FEATURES.md and ARCHITECTURE.md both flag that specific DOM selectors, ARIA roles, and the stability of `window.services` method names in FC26 cannot be confirmed without live inspection. Phase 3 planning must begin with an exploration task: open the EA Web App with DevTools, document stable attributes on all target elements (search form, buy now button, list price input, transfer list items), and verify `window.services` structure before any automation code is written.

- **EA daily transaction cap thresholds:** PITFALLS.md documents a community-consensus ceiling of under 1,000 buy/bid operations per day, but EA does not publish the exact threshold. Set the automation config conservatively (500/day) during initial testing and adjust empirically based on test account behavior.

- **Service worker keepalive strategy under real load:** Both alarm-based keepalive and WebSocket-based keepalive are documented. The alarm approach is the simpler starting point. The WebSocket approach (active connection resets the 30s timer per Chrome 116+) is an available fallback if alarm-based keepalive proves unreliable during Phase 4 testing.

- **EA Web App CSP headers:** The EA Web App may have Content Security Policy headers that affect content script behavior. Verify during Phase 2 extension scaffolding that no CSP headers block the content script's ability to inject the overlay panel or dispatch DOM events.

---

## Sources

### Primary (HIGH confidence)
- [FastAPI Release Notes](https://fastapi.tiangolo.com/release-notes/) — version 0.135.2 confirmed
- [APScheduler 3.x Docs](https://apscheduler.readthedocs.io/en/3.x/) — version 3.11.2.post1 stable
- [APScheduler Migration Guide](https://apscheduler.readthedocs.io/en/master/migration.html) — 4.x instability confirmed
- [SQLAlchemy PyPI](https://pypi.org/project/SQLAlchemy/) — version 2.0.48
- [aiosqlite PyPI](https://pypi.org/project/aiosqlite/) — version 0.22.1
- [Alembic Docs](https://alembic.sqlalchemy.org/en/latest/front.html) — version 1.18.4
- [Chrome MV3 Service Worker Lifecycle (official)](https://developer.chrome.com/docs/extensions/develop/concepts/service-workers/lifecycle) — 30-second idle timer, keepalive patterns
- [Cross-Origin Requests in Chrome Extensions (official)](https://developer.chrome.com/docs/extensions/develop/concepts/network-requests) — content script CORS restriction confirmed
- [Migrate to Service Workers — MV3 (official)](https://developer.chrome.com/docs/extensions/develop/migrate/to-service-workers) — state loss constraints
- [FastAPI CORS Middleware (official)](https://fastapi.tiangolo.com/tutorial/cors/) — CORS configuration
- [Chrome Message Passing (official)](https://developer.chrome.com/docs/extensions/develop/concepts/messaging) — service worker ↔ content script protocol
- [MutationObserver (MDN)](https://developer.mozilla.org/en-US/docs/Web/API/MutationObserver) — SPA navigation detection
- [@types/chrome npm](https://www.npmjs.com/package/@types/chrome) — version 0.1.38
- [TanStack Query npm](https://www.npmjs.com/package/@tanstack/react-query) — version 5.95.0
- [Recharts npm](https://www.npmjs.com/package/recharts) — version 3.8.0

### Secondary (MEDIUM confidence)
- [WXT Framework](https://wxt.dev/) — version 0.20.18/0.20.20, MV3 native, actively maintained
- [WXT vs Plasmo vs CRXJS comparison](https://redreamality.com/blog/the-2025-state-of-browser-extension-frameworks-a-comparative-analysis-of-plasmo-wxt-and-crxjs/) — community framework analysis
- [CRXJS archival discussion](https://github.com/crxjs/chrome-extension-tools/discussions/872) — maintenance uncertainty confirmed
- [FSU EAFC FUT Web Enhancer source (GreasyFork)](https://greasyfork.org/en/scripts/431044-fsu-eafc-fut-web-%E5%A2%9E%E5%BC%BA%E5%99%A8/code) — main-world service injection with `window.services`, `window.repositories` confirmed in real FUT script
- [EasyFUT GitHub](https://github.com/Kava4/EasyFUT) — buy/list/relist architecture patterns
- [MagicBuyer-UT GitHub](https://github.com/AMINE1921/MagicBuyer-UT) — price guard pattern, relist flow, human delay strategies
- [EA Forums FC26 bid/buy limit](https://forums.ea.com/discussions/fc-26-general-discussion-en/limit-on-bidbuy-actions-after-finally-getting-access-to-the-transfer-market/12599137) — daily transaction cap evidence
- [EA Forums FC26 transfer market ban](https://forums.ea.com/discussions/ea-forums-general-discussion-en/false-transfer-market-ban-fc-26/12655634) — ban pattern evidence
- [FutBotManager ban wave avoidance](https://futbotmanager.com/ea-ban-wave-avoidance-futbotmanager/) — automation detection pattern analysis
- [FastAPI + Async SQLAlchemy 2.0 Setup](https://medium.com/@tclaitken/setting-up-a-fastapi-app-with-async-sqlalchemy-2-0-pydantic-v2-e6c540be4308) — session patterns, `expire_on_commit=False`
- [Alembic + SQLite batch mode](https://blog.greeden.me/en/2025/08/12/no-fail-guide-getting-started-with-database-migrations-fastapi-x-sqlalchemy-x-alembic/) — `render_as_batch=True` requirement

### Tertiary (LOW confidence — requires live validation)
- EA Web App DOM structure — no official documentation; must be verified by inspecting live webapp.ea.com with DevTools before Phase 3 implementation
- EA bot detection thresholds — community-sourced ceiling of ~1,000 buy/bid/day; exact threshold unpublished by EA

---
*Research completed: 2026-03-26*
*Ready for roadmap: yes*
