# Architecture Patterns

**Domain:** Trading bot platform — Python backend + Chrome extension + web dashboard
**Researched:** 2026-03-25
**Overall confidence:** HIGH (core patterns), MEDIUM (EA Web App-specific DOM automation)

---

## Recommended Architecture

The target system is a three-tier platform with a persistent Python backend at the center. All intelligence lives in the backend; the Chrome extension and dashboard are thin clients that consume it.

```
┌─────────────────────────────────────────────────────────────┐
│                      fut.gg API                             │
│               (external data source)                        │
└──────────────────────┬──────────────────────────────────────┘
                       │ HTTP (hourly polling, rate-limited)
┌──────────────────────▼──────────────────────────────────────┐
│                 Python Backend (FastAPI)                     │
│                                                             │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────────┐ │
│  │  Scheduler   │  │  Scorer /    │  │   REST API        │ │
│  │ (APScheduler)│  │  Optimizer   │  │  /api/v1/...      │ │
│  └──────┬───────┘  └──────┬───────┘  └────────┬──────────┘ │
│         │                 │                   │             │
│  ┌──────▼─────────────────▼───────────────────▼──────────┐ │
│  │               SQLite Database                          │ │
│  │   players | scores | trades | profit_records          │ │
│  └────────────────────────────────────────────────────────┘ │
└───────────────────────┬───────────────────────┬─────────────┘
                        │ HTTP/REST             │ HTTP/REST
           ┌────────────▼──────────┐  ┌─────────▼──────────────┐
           │   Chrome Extension    │  │   Web Dashboard        │
           │                       │  │   (analytics / monitor)│
           │  service worker       │  │                        │
           │  (background.js)      │  │  Fetch top players,    │
           │    ↕ messages         │  │  profit history,       │
           │  content script       │  │  score trends          │
           │  (ea-webapp.js)       │  └────────────────────────┘
           │    ↕ DOM automation   │
           │  EA Web App           │
           │  (webapp.ea.com)      │
           └───────────────────────┘
```

---

## Component Boundaries

### 1. Python Backend (FastAPI + APScheduler + SQLite)

**Responsibility:** All business logic, all data, all intelligence.

| Sub-component | Responsibility | Communicates With |
|---------------|---------------|-------------------|
| `Scheduler` (APScheduler) | Fires hourly scan jobs per player in 11k–200k range | Scorer, fut.gg API |
| `FutGGClient` | Fetches player data from fut.gg API with rate-limit throttling | fut.gg API (external) |
| `Scorer` / `Optimizer` | Existing OP detection + portfolio ranking logic (unchanged) | SQLite (reads/writes scores) |
| `REST API` (FastAPI routes) | Exposes player recommendations, trade commands, profit records | Chrome extension, Dashboard |
| `SQLite` | Stores players, scores, score history, trade records, profit records | All internal components |

The backend owns the scheduler. The extension never initiates a scan — it only reads recommendations already computed.

**Key constraint:** The scorer and optimizer are existing Python code. They stay Python. The backend is not a microservices split — one process runs the API + scheduler together using FastAPI's lifespan context manager.

---

### 2. Chrome Extension (Manifest V3, TypeScript)

**Responsibility:** Automate buy/list/relist actions on the EA Web App. Bridge between backend recommendations and EA's UI.

The extension has three distinct script contexts, each with different capabilities:

| Script | Runs In | Capabilities | Responsibility |
|--------|---------|-------------|----------------|
| `background.js` (service worker) | Extension context | `fetch()` to localhost (CORS bypassed via `host_permissions`), persistent state across tabs | Polls backend REST API for pending trade actions, stores action queue, orchestrates content scripts |
| `content-script.js` | EA Web App page context | Full DOM read/write on webapp.ea.com | Clicks buttons, reads player listings, fills price fields, triggers buy/sell/relist sequences |
| `popup.html` (optional) | Extension popup | Limited, ephemeral | Status display only — shows current action queue / last sync |

**Message flow within the extension:**

```
Backend REST API
      │
      │ HTTP fetch (every N seconds, in service worker)
      ▼
background.js (service worker)
      │
      │ chrome.tabs.sendMessage({ action: "BUY_PLAYER", ... })
      ▼
content-script.js
      │
      │ DOM manipulation (querySelector, click, dispatchEvent)
      ▼
EA Web App DOM
      │
      │ chrome.runtime.sendMessage({ result: "BOUGHT", price: 15000 })
      ▼
background.js (service worker)
      │
      │ HTTP POST to backend /api/v1/trades (record outcome)
      ▼
Backend REST API
```

**Critical Manifest V3 constraint:** Service workers are ephemeral — they can be terminated by Chrome when idle. For an automation loop, the background service worker must either:
1. Use `chrome.alarms` API (fires alarms that wake the service worker) for periodic polling, OR
2. Keep a WebSocket connection alive by exchanging messages within every 30-second window (supported from Chrome 116+)

Recommendation: Use `chrome.alarms` for polling cadence (simpler, more reliable than fighting the 30-second keepalive). The alarm fires every 30–60 seconds, wakes the service worker, fetches pending actions, and dispatches them to the content script.

**CORS bypass for localhost:** Declare `"http://localhost:8000/*"` in `host_permissions` in `manifest.json`. The service worker can then `fetch()` the local backend without CORS errors. Content scripts cannot make cross-origin requests on their own — all backend communication routes through the service worker.

---

### 3. Web Dashboard (static or served by FastAPI)

**Responsibility:** Analytics, monitoring, historical performance. Read-only consumer of the backend REST API.

| Section | Data Source | Update Cadence |
|---------|-------------|---------------|
| Top OP sell list | `GET /api/v1/players/top` | Polling every 60s or manual refresh |
| Player score history | `GET /api/v1/players/{id}/history` | On demand |
| Trade log | `GET /api/v1/trades` | Polling every 30s |
| Profit summary | `GET /api/v1/profit/summary` | Polling every 60s |
| Scheduler status | `GET /api/v1/health` | Polling every 10s |

**Technology:** React (Vite) or plain HTML + Alpine.js for a simple personal tool. For personal use, the dashboard can be a static file served directly by FastAPI's `StaticFiles` mount — no separate deployment.

---

## Data Flow

### Scan Flow (backend, runs hourly)

```
APScheduler fires hourly job
    → FutGGClient.discover_players(11k–200k range)
    → FutGGClient.get_batch_market_data(ea_ids)     [concurrency=10, 0.15s delay/req]
    → score_player(market_data)                      [existing scorer, unchanged]
    → SQLite: upsert players, insert score records
    → optimize_portfolio(scored_players, budget)
    → SQLite: upsert recommendation set
```

### Read Flow (on-demand, extension + dashboard)

```
Extension / Dashboard: GET /api/v1/players/top?budget=1000000
    → FastAPI route handler
    → SQLite: SELECT latest recommendation set
    → Return ranked player list (JSON)
```

### Trade Flow (extension-driven)

```
Extension polls: GET /api/v1/trades/pending
    → If pending actions exist:
        → service worker sends message to content script
        → content script performs DOM automation on EA Web App
        → Result sent back to service worker
    → POST /api/v1/trades (record actual buy price, outcome)
    → SQLite: insert trade record
```

### Profit Flow (scheduled reconciliation)

```
APScheduler fires daily reconciliation job
    → Read all open trade records from SQLite
    → FutGGClient.get_current_price(ea_id) for each open trade
    → Calculate realized/unrealized P&L
    → SQLite: update profit_records
```

---

## Patterns to Follow

### Pattern 1: Backend as Single Source of Truth

**What:** All state lives in SQLite. The extension and dashboard are stateless renderers.

**Why:** Avoids state synchronization bugs. If the extension crashes mid-trade, the backend still has the correct trade record. The extension re-polls and resumes.

**Implementation:** Extension stores NO persistent state (no `chrome.storage` for business data). It polls the backend REST API for what to do next, and reports results back. `chrome.storage` is used only for UI preferences (theme, polling interval).

---

### Pattern 2: Command Queue Pattern for Extension Actions

**What:** Backend maintains a `pending_actions` queue in SQLite. Extension polls and consumes actions one at a time.

**Why:** EA Web App automation requires sequential, human-paced interactions. Concurrent DOM operations cause UI state corruption. A queue enforces order and allows retry.

```
pending_actions table:
  id, action_type (BUY|LIST|RELIST), ea_player_id,
  target_price, status (PENDING|IN_PROGRESS|DONE|FAILED),
  created_at, attempted_at, completed_at, error_msg
```

The extension claims an action (sets `IN_PROGRESS`), executes it, then reports `DONE` or `FAILED`. If the extension crashes, the backend can detect stale `IN_PROGRESS` records (older than N minutes) and reset them to `PENDING`.

---

### Pattern 3: Rate-Limited Scan with Adaptive Backoff

**What:** The hourly scanner respects a 0.15s delay per request and a concurrency limit of 10. If fut.gg returns 429 (rate limit), the scheduler backs off before retrying.

**Why:** fut.gg has no published rate limits. The existing 0.15s delay has worked at one-shot scale. 24/7 operation multiplies request volume dramatically — conservative throttling is required.

**Implementation:** APScheduler's `misfire_grace_time` prevents job pile-up if a scan runs long. If a scan takes more than 1 hour, the next run is skipped (not queued).

---

### Pattern 4: Stateless Scorer in Persistent Context

**What:** The existing scorer and optimizer functions are pure (stateless). They can run inside the scheduler without modification.

**Why:** The existing architecture already separates data fetching from scoring logic. The persistence layer (SQLite) wraps around the existing functions — the functions themselves don't change.

**Implementation:** The scheduler calls the same `score_player()` and `optimize_portfolio()` functions, then writes results to SQLite. The REST API then reads from SQLite. No scoring logic moves into the API layer.

---

### Pattern 5: DOM Automation via MutationObserver + Event Dispatch

**What:** The content script uses `MutationObserver` to detect when the EA Web App has rendered the UI element it needs, then dispatches synthetic events (click, input change) to trigger transitions.

**Why:** EA's web app is a single-page app with async rendering. Querying the DOM immediately after navigation will often find elements not yet rendered. MutationObserver fires precisely when the target element appears.

**EA Web App automation pattern** (based on existing community implementations like EasyFUT, futbot):
1. Navigate to the transfer market search page (via `window.location`)
2. Wait for the search form to render (MutationObserver)
3. Fill player name / price range fields (dispatchEvent on input)
4. Submit search, wait for results
5. Click BIN button on target listing
6. Confirm purchase dialog
7. Navigate to transfer list (relist or manage)

---

## Anti-Patterns to Avoid

### Anti-Pattern 1: Scoring in the API Layer

**What:** Running `score_player()` on-demand when the extension requests `/api/v1/players/top`.

**Why bad:** Scoring requires 100+ API calls to fut.gg per player. Running this on-demand would violate rate limits immediately, make the API unresponsive for 30+ seconds, and prevent the 24/7 background scanning from working correctly.

**Instead:** Scores are always pre-computed by the scheduler and stored in SQLite. The REST API is read-only for scoring data.

---

### Anti-Pattern 2: Extension-Initiated fut.gg Calls

**What:** The extension fetching player data directly from fut.gg on its own.

**Why bad:** Duplicates rate limit consumption, splits the scoring logic across Python and TypeScript, creates state inconsistency between what the backend knows and what the extension acts on.

**Instead:** The extension only talks to the local backend. The backend is the sole fut.gg client.

---

### Anti-Pattern 3: Persistent Background Page (Manifest V2 Pattern)

**What:** Using a persistent background page instead of a service worker for the extension.

**Why bad:** Manifest V2 is end-of-life. Chrome is removing MV2 support. New extensions must use Manifest V3 service workers.

**Instead:** Use `chrome.alarms` for periodic polling (wakes the service worker on schedule). Use the service worker's `install`/`activate` events for initialization.

---

### Anti-Pattern 4: Storing Credentials in Extension

**What:** Storing EA session cookies or backend auth tokens in `chrome.storage`.

**Why bad:** Extension storage is accessible to the page context if misconfigured. Session theft is a known attack against trading extensions.

**Instead:** For personal use, no auth is required (backend is localhost-only). When adding user accounts, backend tokens should be short-lived and the extension should use the service worker (not content script) for all credential handling.

---

### Anti-Pattern 5: One Monolithic Scan Job

**What:** A single APScheduler job that scans all players in the 11k–200k range in one pass.

**Why bad:** With thousands of players and 2–3 API calls per player, a single pass could take hours. This blocks the scheduler, creates uneven freshness (first players scored at t=0, last at t=2h), and risks rate-limit bans from sustained high request volume.

**Instead:** Stagger the scans. Divide the player pool into batches and schedule overlapping jobs across the hour. Use `coalesce=False` in APScheduler to allow concurrent job runs with different player subsets.

---

## Build Order (Phase Dependency Graph)

The components have hard dependencies that dictate build order:

```
Phase 1: Backend Foundation
  → FastAPI app skeleton + SQLite schema
  → APScheduler integrated via lifespan context manager
  → Existing scorer/optimizer wrapped in scheduler job
  → REST API: GET /players/top, GET /players/{id}, GET /health
  [No extension, no dashboard needed — CLI can test the API]

Phase 2: CLI becomes API client
  → CLI refactored to query Phase 1 REST API
  → Validates API contract before extension depends on it
  [Extension not started yet — avoids building against a moving API]

Phase 3: Chrome Extension (automation core)
  → Manifest V3 scaffolding (service worker + content script)
  → Background service worker polls Phase 1 REST API
  → Content script DOM automation for EA Web App
  → Pending actions queue (POST /trades, GET /trades/pending)
  [Depends on: Phase 1 API stable]

Phase 4: Profit Tracking
  → Trade records table in SQLite
  → Profit reconciliation scheduler job
  → REST API: GET /trades, GET /profit/summary
  [Depends on: Phase 3 reporting trade outcomes]

Phase 5: Web Dashboard
  → Static dashboard consuming Phase 1 + Phase 4 REST API
  → Served as StaticFiles from FastAPI (no separate deployment)
  [Depends on: Phase 1 + Phase 4 APIs stable]

Phase 6: Multi-user / Cloud
  → PostgreSQL migration (SQLite schema designed to be compatible)
  → Auth layer (JWT, paid tiers)
  → Separate dashboard deployment
  [Optional — only if personal tool becomes a product]
```

**Critical dependency:** The REST API contract (Phase 1) must be stable before the extension (Phase 3) and dashboard (Phase 5) are built. Build Phase 1 first, stabilize the API, then build consumers.

---

## Scalability Considerations

| Concern | Personal Use (1 user) | Product (10–100 users) | Scale (1k+ users) |
|---------|----------------------|----------------------|-------------------|
| Database | SQLite (single writer) | PostgreSQL (concurrent writes) | PostgreSQL + read replicas |
| Scheduler | APScheduler in-process | Dedicated worker process (Celery + Redis) | Multiple worker nodes |
| Rate limiting | Single fut.gg client, 0.15s delay | Per-user scan isolation, request queues | Distributed rate-limit coordination |
| Extension backend | localhost:8000 | Deployed FastAPI (HTTPS) | Load-balanced API |
| Dashboard | Static files, FastAPI | Same | CDN-fronted |

The SQLite → PostgreSQL migration is the only hard architectural constraint. Design the schema without SQLite-specific features (no AUTOINCREMENT quirks, use standard INTEGER PRIMARY KEY). The rest scales by adding infrastructure, not rewriting application code.

---

## Sources

- [Chrome Extension Message Passing (Official Docs)](https://developer.chrome.com/docs/extensions/develop/concepts/messaging) — HIGH confidence
- [Building Chrome Extensions in 2026: Manifest V3 Guide](https://dev.to/ryu0705/building-chrome-extensions-in-2026-a-practical-guide-with-manifest-v3-12h2) — MEDIUM confidence
- [Cross-origin Network Requests in Extensions](https://developer.chrome.com/docs/extensions/develop/concepts/network-requests) — HIGH confidence (official)
- [Use WebSockets in Service Workers](https://developer.chrome.com/docs/extensions/how-to/web-platform/websockets) — HIGH confidence (official)
- [Implementing Background Job Scheduling in FastAPI with APScheduler](https://rajansahu713.medium.com/implementing-background-job-scheduling-in-fastapi-with-apscheduler-6f5fdabf3186) — MEDIUM confidence
- [futbot (FIFA 20 Chrome extension + Node server architecture)](https://github.com/dogancana/futbot) — MEDIUM confidence (reference implementation, older)
- [EasyFUT (EA FC automation Chrome extension)](https://github.com/Kava4/EasyFUT) — MEDIUM confidence (reference implementation)
- [CORS in Chrome Extensions (Reintech)](https://reintech.io/blog/cors-chrome-extensions) — MEDIUM confidence

---

*Architecture analysis: 2026-03-25*
