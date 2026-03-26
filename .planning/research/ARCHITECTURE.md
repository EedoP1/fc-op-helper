# Architecture Research

**Domain:** Chrome extension for EA Web App automation — buy/list/relist cycle + profit tracking
**Researched:** 2026-03-26
**Confidence:** HIGH (Chrome extension MV3 patterns), HIGH (FastAPI integration), MEDIUM (EA Web App DOM specifics)

---

## Context: What Already Exists

The v1.0 backend is production-ready and must not be structurally changed. This research is scoped to what the Chrome extension milestone adds.

**Existing backend (unchanged):**
- FastAPI app at `src/server/main.py` — single process, lifespan-managed
- APScheduler scanning ~1800 players every 5 minutes
- REST API at `/api/v1/` — portfolio, players/top, players/{id}, health
- SQLite WAL mode via SQLAlchemy async, `aiosqlite`
- DB tables: `players`, `player_scores`, `market_snapshots`, `snapshot_sales`, `snapshot_price_points`, `listing_observations`, `daily_listing_summaries`

**What the milestone adds:**
- Chrome extension (new project in `extension/`)
- 3 new backend API endpoints (pending actions, report outcome, profit summary)
- 3 new DB tables (trade_actions, trade_records, profit_snapshots)
- CORS configuration on the backend
- Activity reporting pipeline from extension to backend

---

## System Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                         fut.gg API                                  │
│                    (external, rate-limited)                          │
└────────────────────────────┬────────────────────────────────────────┘
                             │ HTTP (5-min scanner, unchanged)
┌────────────────────────────▼────────────────────────────────────────┐
│                  Python Backend (FastAPI — UNCHANGED CORE)           │
│                                                                      │
│  ┌───────────────┐  ┌──────────────────┐  ┌────────────────────┐    │
│  │  APScheduler  │  │  Scorer V2 /     │  │  REST API          │    │
│  │  (scanner)    │  │  Optimizer       │  │  /api/v1/          │    │
│  └──────┬────────┘  └────────┬─────────┘  └─────────┬──────────┘    │
│         │                   │                       │                │
│  ┌──────▼───────────────────▼───────────────────────▼────────────┐  │
│  │                     SQLite (WAL mode)                          │  │
│  │  [existing] players | player_scores | listing_observations     │  │
│  │  [NEW]      trade_actions | trade_records | profit_snapshots   │  │
│  └────────────────────────────────────────────────────────────────┘  │
└────────────────────────────────┬────────────────────────────────────┘
                                 │ HTTP/REST (localhost:8000)
                                 │ CORS: chrome-extension://
                    ┌────────────▼────────────────────┐
                    │      Chrome Extension (NEW)      │
                    │                                  │
                    │  ┌──────────────────────────┐    │
                    │  │  Service Worker          │    │
                    │  │  (background.ts)         │    │
                    │  │  - chrome.alarms polling │    │
                    │  │  - fetch() to backend    │    │
                    │  │  - action queue mgmt     │    │
                    │  └────────────┬─────────────┘    │
                    │               │ chrome.tabs       │
                    │               │ .sendMessage()    │
                    │  ┌────────────▼─────────────┐    │
                    │  │  Content Script          │    │
                    │  │  (content.ts)            │    │
                    │  │  - DOM automation        │    │
                    │  │  - MutationObserver      │    │
                    │  │  - click/input dispatch  │    │
                    │  └────────────┬─────────────┘    │
                    │               │ DOM events        │
                    │  ┌────────────▼─────────────┐    │
                    │  │  EA Web App              │    │
                    │  │  (webapp.ea.com)         │    │
                    │  └──────────────────────────┘    │
                    │                                  │
                    │  ┌──────────────────────────┐    │
                    │  │  Popup (popup.html)       │    │
                    │  │  - status display only   │    │
                    │  │  - start/stop controls   │    │
                    │  └──────────────────────────┘    │
                    └──────────────────────────────────┘
```

---

## New Components

### 1. Chrome Extension — Service Worker (`background.ts`)

**What it is:** Manifest V3 background service worker. The brain of the extension.

**Responsibilities:**
- Wake on `chrome.alarms` (every 30 seconds) to poll backend for pending actions
- `fetch()` the backend REST API — only the service worker can do cross-origin requests to localhost
- Maintain a local action queue in `chrome.storage.local` (survives service worker restarts)
- Send action commands to content script via `chrome.tabs.sendMessage()`
- Receive outcome reports from content script
- POST outcomes to backend `/api/v1/actions/{id}/complete`

**Why service worker, not persistent background page:** Manifest V3 requires service workers. Persistent background pages are MV2 and Chrome is deprecating them. Service workers are ephemeral — use `chrome.alarms` for periodic work, `chrome.storage` for state.

**Critical MV3 constraint:** Service workers terminate after 30 seconds of inactivity. Using `chrome.alarms` is the reliable keepalive pattern — the alarm fires, wakes the service worker, completes its task, then the worker sleeps again. This is correct behavior, not a bug to work around.

---

### 2. Chrome Extension — Content Script (`content.ts`)

**What it is:** JavaScript injected into the EA Web App page context. The hands of the extension.

**Responsibilities:**
- Execute DOM operations on `webapp.ea.com`
- Use `MutationObserver` to detect when EA's SPA has rendered target UI elements
- Simulate user interactions (click, input value change with `dispatchEvent`)
- Navigate between EA Web App screens (search → buy → list → transfer list)
- Report action outcomes back to service worker via `chrome.runtime.sendMessage()`

**Key EA Web App DOM patterns (MEDIUM confidence — requires validation during Phase 1 of extension build):**
- EA's webapp is a single-page Angular-style app with async rendering
- DOM elements appear/disappear as the user navigates — cannot query immediately after navigation
- MutationObserver on `document.body` (subtree: true) detects element insertion
- Buttons have class-based selectors that are more stable than ID-based (EA uses generated IDs)
- Price input fields require both `.value = X` and `dispatchEvent(new Event('input', {bubbles: true}))` to register in the framework's change detection

**Timing discipline:** All DOM interactions must be sequenced with human-paced delays (300–800ms between steps). EA's rate detection is behavioral — too-fast programmatic clicks trigger detection. Use `setTimeout`-based sequential steps, not `Promise.all` parallel execution.

---

### 3. Chrome Extension — Popup (`popup.html` + `popup.ts`)

**What it is:** The small panel that opens when the user clicks the extension icon.

**Responsibilities (minimal):**
- Show current automation status (running / paused / stopped)
- Show last sync time with backend
- Show count of pending actions
- Start / stop automation toggle
- NOT a full dashboard — just status and control

---

### 4. New Backend DB Tables

**`trade_actions` — the pending actions queue:**
```
id           INTEGER PK autoincrement
ea_id        INTEGER (FK to players.ea_id)
action_type  TEXT    -- "BUY" | "LIST" | "RELIST"
target_price INTEGER -- price extension should use
status       TEXT    -- "PENDING" | "IN_PROGRESS" | "DONE" | "FAILED" | "SKIPPED"
created_at   DATETIME
claimed_at   DATETIME nullable  -- when extension claimed it
completed_at DATETIME nullable
actual_price INTEGER nullable   -- what extension actually paid/listed at
error_msg    TEXT nullable
```

**`trade_records` — completed trade history:**
```
id              INTEGER PK autoincrement
ea_id           INTEGER
action_type     TEXT       -- "BUY" | "LIST" | "RELIST" | "SOLD"
price           INTEGER    -- actual price at execution
recommended_price INTEGER  -- what backend recommended
executed_at     DATETIME
session_id      TEXT nullable  -- group trades in one extension session
trade_action_id INTEGER nullable  -- FK to trade_actions
```

**`profit_snapshots` — periodically calculated P&L:**
```
id             INTEGER PK autoincrement
snapshot_at    DATETIME
total_invested INTEGER  -- sum of BUY prices for open positions
total_returned INTEGER  -- sum of SOLD prices (after EA 5% tax)
realized_pnl   INTEGER  -- returned - invested for closed positions
open_positions INTEGER  -- count of BUY without matching SOLD
```

---

### 5. New Backend API Endpoints

Three new routes are needed. All added to `src/server/api/` as a new `actions.py` router.

**`GET /api/v1/actions/pending`**
Returns the next batch of actions the extension should execute. The extension calls this on every alarm tick.

Response:
```json
{
  "actions": [
    {
      "id": 42,
      "ea_id": 12345678,
      "action_type": "BUY",
      "target_price": 15000,
      "name": "Bukayo Saka",
      "margin_pct": 12
    }
  ]
}
```

Design decisions:
- Returns max 1 action at a time (sequential execution enforced server-side)
- Marks returned action as `IN_PROGRESS` atomically
- Detects stale `IN_PROGRESS` records (> 5 minutes) and resets them to `PENDING` before querying

**`POST /api/v1/actions/{action_id}/complete`**
Extension reports the outcome of an action.

Request body:
```json
{
  "status": "DONE",
  "actual_price": 14800,
  "error_msg": null
}
```

Side effects:
- Updates `trade_actions.status`
- Inserts into `trade_records`
- Triggers profit snapshot recalculation if action was a `SOLD`

**`GET /api/v1/profit/summary`**
Returns aggregate profit metrics for the CLI and dashboard.

Response:
```json
{
  "total_buys": 23,
  "total_listed": 19,
  "total_sold": 11,
  "realized_pnl": 145000,
  "open_positions": 8,
  "avg_margin_achieved_pct": 9.2,
  "last_updated": "2026-03-26T14:30:00"
}
```

**`POST /api/v1/actions/queue`**
Backend-internal: generates pending actions from the latest portfolio recommendation. Called by the scheduler or manually by the user when they confirm the extension's buy list.

---

### 6. Backend CORS Configuration

The FastAPI app needs CORS middleware to accept requests from the Chrome extension. Chrome extensions make requests from the `chrome-extension://` origin.

Add to `src/server/main.py`:
```python
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["chrome-extension://*"],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)
```

The extension manifest also declares `host_permissions` for `http://localhost:8000/*` to allow the service worker to fetch the local backend without CORS blocking.

---

## Recommended Extension Project Structure

```
extension/
├── manifest.json          — MV3 manifest: permissions, content_scripts, background
├── package.json           — TypeScript + Vite + CRXJS dependencies
├── vite.config.ts         — CRXJS Vite plugin configuration
├── tsconfig.json          — TypeScript config
├── src/
│   ├── background.ts      — Service worker: polling, queue, backend communication
│   ├── content.ts         — Content script: DOM automation on EA webapp
│   ├── popup/
│   │   ├── popup.html     — Extension popup page
│   │   └── popup.ts       — Popup logic: status display, start/stop
│   ├── types/
│   │   ├── messages.ts    — Discriminated union types for all chrome messages
│   │   ├── api.ts         — Types matching backend API response shapes
│   │   └── actions.ts     — TradeAction type, ActionStatus enum
│   └── lib/
│       ├── backend.ts     — fetch() wrappers for all backend API calls
│       ├── dom-utils.ts   — waitForElement(), clickElement(), setInputValue()
│       └── ea-navigator.ts — EA Web App navigation sequences (buy flow, list flow)
```

**Build toolchain:** Vite + CRXJS plugin. CRXJS reads `manifest.json` and bundles service worker, content scripts, and popup as separate entry points with proper MV3 compliance. Output goes to `extension/dist/` which is loaded as an unpacked extension in Chrome.

**No React in the popup.** The popup is minimal status text + a toggle — plain HTML + TypeScript is sufficient. Adding React brings complexity without benefit for a personal tool.

---

## Data Flow: Full Buy/List/Relist Cycle

### Step 1 — User Confirms Buy List

```
User opens popup → clicks "Start Session"
    → popup.ts POSTs to service worker: { action: "START_SESSION", budget: 500000 }
    → service worker: GET /api/v1/portfolio?budget=500000
    → service worker: POST /api/v1/actions/queue  (generates pending BUY actions from portfolio)
    → backend: inserts N trade_actions rows with status=PENDING
    → service worker: stores session state in chrome.storage.local
```

### Step 2 — Buy Loop

```
chrome.alarms fires every 30 seconds
    → service worker wakes
    → GET /api/v1/actions/pending
    → backend: finds next PENDING action, marks IN_PROGRESS, returns it
    → service worker: sendMessage to content script { type: "EXECUTE_BUY", ea_id, target_price }
    → content script: MutationObserver navigates to transfer market search
    → content script: searches for player by ea_id
    → content script: checks lowest BIN against target_price (price guard)
        → if BIN > target_price × 1.02: skip, report SKIPPED
        → if BIN <= target_price: click Buy Now → confirm
    → content script: sendMessage back { type: "BUY_RESULT", status: "DONE"|"SKIPPED"|"FAILED", actual_price }
    → service worker: POST /api/v1/actions/{id}/complete
```

### Step 3 — List Loop

```
After BUY succeeds:
    → backend creates matching LIST action with target_price = buy_price × (1 + margin_pct)
    → Next alarm tick picks up LIST action
    → service worker: sendMessage { type: "EXECUTE_LIST", ea_id, list_price }
    → content script: navigates to Transfer List in EA webapp
    → content script: finds the just-purchased card (by ea_id)
    → content script: sets list price, duration 1hr, submits
    → content script: reports { type: "LIST_RESULT", status: "DONE" }
    → service worker: POST /api/v1/actions/{id}/complete
```

### Step 4 — Relist Loop (periodic check)

```
On every alarm tick (every 30s), in addition to pending actions:
    → service worker: GET /api/v1/actions/pending (type=RELIST)
    → If any RELIST actions exist:
        → content script: navigates to Transfer List
        → content script: scans for expired listings
        → For each expired listing matching an ea_id in RELIST queue:
            → GET fresh price from backend (via service worker)
            → Relist at updated OP price, duration 1hr
        → service worker: POST /api/v1/actions/{id}/complete for each relisted
```

### Step 5 — Sale Detection + Profit Recording

```
On every alarm tick:
    → service worker: GET /api/v1/actions/pending (type=CHECK_SALES)
    (OR: content script periodically scans Transfer List for sold items)
    → content script: checks Transfer List for cards marked as "SOLD"
    → For each sold card: sendMessage { type: "SALE_DETECTED", ea_id, sold_price }
    → service worker: POST /api/v1/actions/sales { ea_id, sold_price }
    → backend: inserts trade_record (type=SOLD), updates profit_snapshots
```

---

## Message Protocol (Service Worker ↔ Content Script)

All messages use TypeScript discriminated unions for type safety.

**Service worker → content script (commands):**
```typescript
type ExtensionCommand =
  | { type: "EXECUTE_BUY"; ea_id: number; target_price: number; player_name: string }
  | { type: "EXECUTE_LIST"; ea_id: number; list_price: number }
  | { type: "EXECUTE_RELIST"; ea_id: number; new_price: number }
  | { type: "CHECK_SOLD_ITEMS" }
  | { type: "PING" }
```

**Content script → service worker (results):**
```typescript
type ExtensionResult =
  | { type: "BUY_RESULT"; status: "DONE" | "SKIPPED" | "FAILED"; actual_price?: number; error?: string }
  | { type: "LIST_RESULT"; status: "DONE" | "FAILED"; error?: string }
  | { type: "RELIST_RESULT"; status: "DONE" | "FAILED"; count: number }
  | { type: "SOLD_ITEMS"; items: Array<{ ea_id: number; sold_price: number }> }
  | { type: "PONG" }
```

---

## Integration Points: New vs Modified

### New (does not touch existing code)

| Component | Location | What |
|-----------|----------|------|
| Chrome extension | `extension/` (new directory) | Entire extension codebase |
| Actions router | `src/server/api/actions.py` | 3 new endpoints |
| DB tables | `src/server/models_db.py` | 3 new ORM classes appended |
| DB migration | `src/server/db.py` (minor) | Tables created at startup via SQLAlchemy metadata |

### Modified (minimal changes to existing code)

| File | Change | Risk |
|------|--------|------|
| `src/server/main.py` | Add `CORSMiddleware`, include `actions_router` | Low — additive only |
| `src/server/models_db.py` | Append 3 new table classes | Low — existing tables untouched |
| `src/server/api/portfolio.py` | No change needed | None |
| `src/server/api/players.py` | No change needed | None |
| `src/config.py` | Add `STALE_ACTION_MINUTES = 5`, `EA_WEBAPP_URL` constant | Low — additive |

**Zero changes to:** scorer_v2.py, optimizer.py, scanner.py, scheduler.py, listing_tracker.py, futgg_client.py, circuit_breaker.py.

---

## Patterns to Follow

### Pattern 1: Backend-Driven Action Queue

**What:** The backend owns the action queue. The extension is a consumer, not a scheduler.

**When to use:** Always. Extension never decides what to buy/list/relist — it only executes what the backend says.

**Trade-offs:** Slightly more round-trips (extension polls instead of working from cached state), but eliminates split-brain scenarios where extension and backend disagree on state.

**Implementation:**
```typescript
// Service worker alarm handler
chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name !== "poll-actions") return;
  const action = await backend.getPendingAction();
  if (!action) return;
  const [tab] = await chrome.tabs.query({ url: "*://webapp.ea.com/*" });
  if (!tab?.id) return;
  const result = await chrome.tabs.sendMessage(tab.id, toCommand(action));
  await backend.completeAction(action.id, result);
});
```

### Pattern 2: Sequential DOM Automation with MutationObserver

**What:** Content script never queries DOM immediately. Always waits for target element via MutationObserver before acting.

**When to use:** All DOM interactions on EA's SPA.

**Trade-offs:** Adds ~50-200ms per step for element detection, but eliminates "element not found" failures from race conditions with EA's async rendering.

**Implementation:**
```typescript
function waitForElement(selector: string, timeout = 5000): Promise<Element> {
  return new Promise((resolve, reject) => {
    const el = document.querySelector(selector);
    if (el) return resolve(el);
    const observer = new MutationObserver(() => {
      const el = document.querySelector(selector);
      if (el) { observer.disconnect(); resolve(el); }
    });
    observer.observe(document.body, { childList: true, subtree: true });
    setTimeout(() => { observer.disconnect(); reject(new Error(`Timeout: ${selector}`)); }, timeout);
  });
}
```

### Pattern 3: Price Guard in Content Script

**What:** Before executing a BUY, content script reads the actual current BIN from the EA Web App search result and compares against backend's `target_price`.

**When to use:** Every buy action.

**Trade-offs:** Adds one extra DOM read per buy, but prevents paying above market if the price moved between backend scoring and extension execution.

**Rule:** If actual BIN > `target_price × 1.02`, report `SKIPPED` (not `FAILED`). Backend marks as skipped, not a retry candidate. The scanner will re-score the player on the next pass.

### Pattern 4: Human-Paced Timing

**What:** All sequential steps in the buy/list flow use `await sleep(randomBetween(300, 800))` between DOM interactions.

**When to use:** Every click sequence in the content script.

**Trade-offs:** Slows automation cycle (each buy takes ~3-5 seconds instead of 0.5 seconds), but reduces EA behavioral detection surface.

**Implementation:**
```typescript
const sleep = (ms: number) => new Promise(r => setTimeout(r, ms));
const humanDelay = () => sleep(300 + Math.random() * 500);
```

---

## Anti-Patterns to Avoid

### Anti-Pattern 1: Content Script Making Backend Requests

**What people do:** Call `fetch("http://localhost:8000/...")` directly from the content script.

**Why it's wrong:** Content scripts run in the page's CORS context. Requests to localhost from `webapp.ea.com` are cross-origin and will be blocked unless the backend adds `webapp.ea.com` to CORS allow_origins — which is wrong because then ANY page at that domain could talk to your personal backend.

**Do this instead:** All backend HTTP calls go through the service worker. Content script sends results to service worker via `chrome.runtime.sendMessage`. Service worker makes the backend call.

### Anti-Pattern 2: Storing Action State Only in chrome.storage

**What people do:** Keep the pending action queue only in `chrome.storage.local`, never syncing back to backend.

**Why it's wrong:** If the user disables the extension, reinstalls Chrome, or clears extension storage, all pending actions are lost. Backend never knows what happened.

**Do this instead:** Backend is the source of truth. `chrome.storage.local` holds only the currently-claimed action (the one `IN_PROGRESS`). If the service worker restarts mid-action, it checks backend for stale `IN_PROGRESS` records on startup.

### Anti-Pattern 3: Parallel DOM Operations

**What people do:** Process multiple buy/list actions concurrently from one content script.

**Why it's wrong:** EA's Web App is a single-view SPA. Attempting to navigate to the transfer market and the transfer list simultaneously causes DOM state corruption. The SPA can only be in one view at a time.

**Do this instead:** The backend returns maximum 1 pending action per poll. All actions execute sequentially.

### Anti-Pattern 4: Polling from Content Script

**What people do:** Content script runs a `setInterval` loop to poll the backend or check for new actions.

**Why it's wrong:** Content scripts only run while the EA Web App tab is open and the page is rendered. If the user navigates away, the interval stops. The service worker is the correct place for polling because it has an independent lifecycle from any tab.

**Do this instead:** Service worker uses `chrome.alarms` for polling. Service worker activates the content script only when an action needs execution.

### Anti-Pattern 5: Hardcoded EA Web App Selectors

**What people do:** Hardcode CSS selectors like `.ut-transfer-list-view .btn-buy` throughout the content script.

**Why it's wrong:** EA updates the Web App regularly. Hardcoded selectors break silently — the action appears to execute but nothing happens.

**Do this instead:** Centralize all selectors in `lib/ea-selectors.ts` as a named constants object. When EA updates the app, only one file needs updating. Add logging when `waitForElement` times out so failures are visible.

---

## Scaling Considerations

| Scale | Architecture Adjustments |
|-------|--------------------------|
| Personal (1 user, localhost) | Current architecture — localhost:8000, no auth, SQLite |
| Small product (10-100 users) | Deploy backend, add auth token to extension, PostgreSQL, HTTPS |
| Large product (1k+ users) | Separate extension backend from scanner, multi-tenant DB, subscription billing |

### Scaling Priorities

1. **First bottleneck at multi-user:** SQLite single-writer cannot handle concurrent extension polling from multiple users. Migrate to PostgreSQL (schema is compatible — no SQLite-specific constructs used). This is the only hard architectural change required.

2. **Second bottleneck at multi-user:** fut.gg rate limits. Each user currently runs their own scanning client. At 10+ users sharing a server, need a shared scanning pool with per-user rate limit budgets.

---

## Build Order for This Milestone

Dependencies within the extension milestone:

```
Step 1: New DB tables + migrations
  → trade_actions, trade_records, profit_snapshots
  → Add ORM models to models_db.py (append only — no existing table changes)
  [Tests: create tables, verify schema]

Step 2: New backend API endpoints
  → GET /api/v1/actions/pending (with stale-reset logic)
  → POST /api/v1/actions/{id}/complete
  → GET /api/v1/profit/summary
  → POST /api/v1/actions/queue (portfolio → pending actions)
  → Add CORS middleware
  [Tests: API contract tests, stale reset behavior]

Step 3: Extension scaffolding
  → Vite + CRXJS + TypeScript setup in extension/
  → manifest.json (MV3, permissions, content_scripts, host_permissions)
  → Typed message protocol (types/)
  → Backend API client wrappers (lib/backend.ts)
  [Tests: load unpacked extension in Chrome, verify service worker starts]

Step 4: Service worker (background.ts)
  → chrome.alarms setup (every 30 seconds)
  → Poll GET /api/v1/actions/pending
  → Dispatch command to content script
  → Receive result, POST to complete endpoint
  [Tests: mock content script responses, verify backend posts]

Step 5: Content script (content.ts) — EA Web App DOM automation
  → waitForElement() utility
  → humanDelay() utility
  → ea-selectors.ts (all CSS selectors centralized)
  → Buy flow: search → find listing → price check → buy → confirm
  → List flow: navigate to transfer list → find card → set price → submit
  → Relist flow: find expired → relist at new price
  → Sold detection: scan transfer list for sold items
  [Tests: manual testing on EA Web App — no unit tests possible for DOM]

Step 6: Popup (popup.html + popup.ts)
  → Status display (last sync, pending count, session state)
  → Start/stop toggle
  [Tests: manual]

Step 7: Profit reporting integration
  → CLI: add profit summary to existing display
  → API: verify /api/v1/profit/summary is queryable
  [Tests: integration test with seeded trade_records]
```

**Critical path:** Steps 1 and 2 (backend) must complete before Step 4 (service worker) can be tested end-to-end. Steps 3-4 can be scaffolded in parallel with Step 2.

---

## Integration Points Summary

| Boundary | Communication | Notes |
|----------|--------------|-------|
| Extension service worker ↔ Backend | HTTP REST (localhost:8000) | Service worker only — content script never talks to backend directly |
| Extension service worker ↔ Content script | `chrome.tabs.sendMessage` / `chrome.runtime.sendMessage` | Typed discriminated union protocol |
| Content script ↔ EA Web App | DOM events, MutationObserver | Centralized selectors, human-paced timing |
| Backend scanner ↔ Backend API | Shared SQLite session_factory | Already wired via `app.state.session_factory` |
| Backend actions API ↔ Backend scorer | Read-only: actions API reads player scores from SQLite | No coupling to scorer logic |
| CLI ↔ Backend | HTTP (existing — unchanged) | Add profit summary command to existing CLI |

---

## Sources

- [Chrome Extension Service Worker Lifecycle (Official Docs)](https://developer.chrome.com/docs/extensions/develop/concepts/service-workers/lifecycle) — HIGH confidence
- [Cross-Origin Network Requests in Chrome Extensions (Official Docs)](https://developer.chrome.com/docs/extensions/develop/concepts/network-requests) — HIGH confidence
- [Migrate to Service Workers — MV3 (Official Docs)](https://developer.chrome.com/docs/extensions/develop/migrate/to-service-workers) — HIGH confidence
- [Building Chrome Extensions with MV3 and TypeScript (hemaks.org)](https://hemaks.org/posts/building-chrome-extensions-with-manifest-v3-and-typescript-a-modern-developers-guide/) — MEDIUM confidence
- [CRXJS Vite Plugin for Chrome Extensions (2026)](https://optymized.net/blog/building-chrome-extensions) — MEDIUM confidence
- [MutationObserver for DOM change detection (MDN)](https://developer.mozilla.org/en-US/docs/Web/API/MutationObserver) — HIGH confidence
- [FastAPI CORS Middleware (Official Docs)](https://fastapi.tiangolo.com/tutorial/cors/) — HIGH confidence
- [EasyFUT — EA FC automation extension (GitHub reference)](https://github.com/Kava4/EasyFUT) — MEDIUM confidence (reference implementation, architecture patterns)
- [Chrome Extension CORS Behavior (Reintech)](https://reintech.io/blog/cors-chrome-extensions) — MEDIUM confidence

---

*Architecture research: Chrome extension integration for EA Web App automation*
*Researched: 2026-03-26*
