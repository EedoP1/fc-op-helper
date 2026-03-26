# Technology Stack

**Project:** FC26 OP Sell Platform — Backend + API + Chrome Extension + Web Dashboard
**Researched:** 2026-03-26
**Scope:** New components only. Existing stack (Python 3.12, httpx, pydantic, click, rich) is not re-evaluated.
**Focus update:** Chrome extension section expanded with EA Web App DOM interaction patterns, MV3 service worker lifecycle, and content script architecture.

---

## Recommended Stack

### Backend API — FastAPI

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| FastAPI | 0.135.x | HTTP API framework | De facto Python async API standard; native Pydantic v2 integration means existing models plug in directly; automatic OpenAPI docs aid Chrome extension development; lifespan context manager cleanly owns scheduler startup/shutdown |
| uvicorn | 0.34.x | ASGI server | Only production-grade ASGI server for FastAPI; minimal configuration for local-first hosting |

FastAPI 0.135 now enforces `Content-Type: application/json` by default on POST routes — set `strict_content_type=False` if the Chrome extension posts without explicit headers.

**Confidence:** HIGH — verified against FastAPI release notes and PyPI (0.135.2 current).

---

### Scheduled Jobs — APScheduler 3.x

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| APScheduler | 3.11.x | Hourly player scan jobs | In-process scheduler; `AsyncIOScheduler` shares FastAPI's event loop; cron triggers map directly to hourly cadence; well-understood 3.x API with broad FastAPI examples |

**Do not use APScheduler 4.x yet.** Version 4.x is still pre-release (no stable tag as of March 2026), the API is a breaking rewrite (concept of "Job" is split into Task/Schedule/Job), and migration from 3.x job stores is explicitly not yet supported per the APScheduler migration docs.

Integration pattern: Start/stop scheduler inside FastAPI's `@asynccontextmanager lifespan` function, not `@app.on_event` (deprecated). Use `AsyncIOScheduler` (not `BackgroundScheduler`) to keep jobs on the same event loop.

**Confidence:** HIGH — APScheduler 3.11.2.post1 confirmed current stable on PyPI. 4.x migration guide confirms instability.

---

### Database — SQLite via SQLAlchemy 2.0 + aiosqlite

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| SQLAlchemy | 2.0.48 | ORM + query layer | Industry standard; 2.0 API is stable and async-native; designed to swap SQLite for PostgreSQL by changing one connection URL |
| aiosqlite | 0.22.1 | Async SQLite driver | Required bridge for `sqlite+aiosqlite://` URLs in async SQLAlchemy; lightweight, no server process |
| Alembic | 1.18.x | Database migrations | The only migration tool for SQLAlchemy; prevents ad-hoc `create_all()` calls that break history |

Key SQLite-specific requirement: Always set `render_as_batch=True` in `alembic/env.py`. SQLite does not support `ALTER COLUMN` natively; batch mode rewrites the table instead.

Session pattern: Use `async_sessionmaker(expire_on_commit=False)` — required to prevent SQLAlchemy from trying to lazy-load detached objects after commit in async context.

PostgreSQL migration path: Change `sqlite+aiosqlite:///./app.db` to `postgresql+asyncpg://user:pass@host/db` and install `asyncpg`. No ORM code changes required if models use standard SQLAlchemy types.

**Confidence:** HIGH — versions confirmed from PyPI search results (SQLAlchemy 2.0.48, aiosqlite 0.22.1, Alembic 1.18.4).

---

### Chrome Extension — WXT + TypeScript (no UI framework in content scripts)

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| WXT | ~0.20.20 | Extension build framework | Vite-based, Manifest V3 native, HMR for service workers, ~43% smaller bundles than Plasmo; actively maintained unlike CRXJS (archival risk) and Plasmo (maintenance lag) |
| TypeScript | ^5.0.0 | Extension language | Type safety for DOM selectors and chrome.* APIs; catches structural mismatches against backend API response types at compile time |
| @types/chrome | ^0.1.38 | Chrome API typings | Community-maintained DefinitelyTyped package; most up-to-date for autocomplete on `chrome.runtime`, `chrome.storage`, `chrome.tabs` |
| React | 19.x | Popup UI only | Used in the extension popup for displaying the recommended sell list; NOT used inside content scripts (content script is vanilla TS only) |

#### Why WXT over alternatives

- **vs Plasmo:** Plasmo is in maintenance mode as of 2025 — little active development, larger bundle size (Parcel vs Vite), unreliable HMR for background scripts. WXT is the clear successor with active development.
- **vs CRXJS:** CRXJS posted a public notice that the repository would be archived if no new maintainer was established by June 2025. As of March 2026 the project status is uncertain. Do not take a new dependency on it.
- **vs raw MV3:** Service worker lifecycle in MV3 is significantly more complex than MV2 background pages. WXT provides correct lifecycle abstractions, structured entry points, and dev-mode reload without manual chrome://extensions refreshing.

WXT 0.20.x is pre-1.0 by semver but the API is stable. Pin to `~0.20.20` and do not auto-upgrade until 1.0 is tagged.

**Confidence:** MEDIUM-HIGH — WXT 0.20.18 confirmed from npm (published ~19 days before research date). CRXJS archival risk confirmed from github.com/crxjs/chrome-extension-tools discussions. Plasmo maintenance concerns from multiple independent sources.

---

### EA Web App DOM Interaction — Architecture

This is the highest-risk technical area. The EA FC 26 Web App is an Angular SPA. Existing community tools (EasyFUT, fut-trade-enhancer, shortfuts) reveal two distinct automation patterns:

#### Pattern A: Main World Service Injection (preferred)

The EA Web App exposes its internal Angular service tree on `window` globals (e.g., `services.Search`, `services.Item`, `services.Auction`, `repositories.Item`). Community scripts access these directly to construct search queries (`UTSearchCriteriaDTO`, `UTAuctionSearchCriteriaDTO`) and submit trades without simulating user clicks.

**Why this is better than DOM clicking:** Angular SPAs re-render DOM elements frequently; CSS selectors for buttons will break on every EA Web App update. Service-level calls are more stable because they target the JavaScript API layer, not the rendered HTML.

**How to implement from a Chrome extension:** Content scripts run in an isolated world and cannot access `window.services`. Use `chrome.scripting.executeScript` with `world: "MAIN"` to inject a script into the page's JavaScript context. The injected script reads `window.services` and passes data back to the content script via `window.postMessage` or `CustomEvent`.

```
Content script (isolated world)
  → chrome.scripting.executeScript({ world: "MAIN" })
    → Injected script reads window.services / window.repositories
    → Dispatches CustomEvent with data
  ← Content script receives event, relays to service worker via chrome.runtime.sendMessage
Service worker
  → Calls localhost:8000 backend for portfolio / prices
  → Sends commands back to content script
Content script
  → Dispatches commands to injected main-world script
    → Injected script calls window.services.Auction.placeBid() etc.
```

**MV3 manifest requirement:** The injected main-world script file must be listed in `web_accessible_resources`. WXT handles this automatically when you use `injectScript()`.

#### Pattern B: Simulated DOM Clicks (fallback)

Click `querySelector('[data-icon="transfer"]')`, fill input fields, click confirm buttons. This is how shortfuts implements keyboard shortcuts.

Use this only as a fallback for flows where service-level access is unclear (e.g., confirming a buy-now dialog that has no direct service call). The selectors will require maintenance after EA Web App updates.

#### URL match pattern for EA Web App

```
*://www.ea.com/ea-sports-fc/ultimate-team/web-app/*
```

The WXT content script `matches` field should use this exact pattern.

**Confidence:** MEDIUM — Main-world service access pattern confirmed by examining FSU script source code on GreasyFork (which uses this approach for FUT automation). The specific service/repository names (`services.Auction`, `repositories.Item`) are confirmed in that source. The exact current method names for buy-now and relist in FC26 are not confirmed — they must be verified by inspecting `window.services` in browser devtools on the live web app before implementation.

---

### Chrome Extension — Service Worker Lifecycle

The MV3 service worker terminates after 30 seconds of inactivity. This is critical for the automated buy/list/relist cycle which may run for minutes.

**Solution:** Use an alarm to keep the service worker alive during active automation runs.

```typescript
// In service worker: register a 1-minute repeating alarm when automation starts
chrome.alarms.create('keepAlive', { periodInMinutes: 0.4 });
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === 'keepAlive') {
    // Receiving this event resets the 30-second idle timer
  }
});
```

`chrome.alarms` API calls are Chrome extension API calls, which reset the 30-second idle timer. A 24-second (0.4 minute) alarm gives a comfortable margin below the 30-second cutoff.

**Alternative:** Maintain an open `chrome.runtime.connect` port from the content script to the service worker. An active port connection extends service worker lifetime for the duration of the connection. This is the approach WXT's content script channel uses internally.

Clear the keepAlive alarm when automation is stopped to avoid draining resources when idle.

**Confidence:** HIGH — Service worker 30-second idle timer confirmed from Chrome for Developers official docs. Alarm-based keepalive pattern confirmed as valid approach in multiple community sources.

---

### Extension-to-Backend Communication

The extension calls `http://localhost:8000` (the FastAPI backend). This is a cross-origin request from the extension's context.

**Background service worker (no CORS issue):** Extension service workers are not subject to CORS restrictions when the extension declares host permissions. Add `"http://localhost:8000/*"` to `host_permissions` in manifest.json. WXT handles this via the `wxt.config.ts` `manifest` option.

**Content scripts (isolated world) cannot call localhost directly.** Cross-origin fetch from content scripts is blocked even with host permissions. The content script must relay requests to the service worker via `chrome.runtime.sendMessage`, which then makes the fetch call.

```
Content script → chrome.runtime.sendMessage({ type: "GET_PORTFOLIO" })
Service worker → fetch("http://localhost:8000/portfolio")
              ← response
Service worker → chrome.tabs.sendMessage(tabId, { type: "PORTFOLIO_RESULT", data })
Content script ← receives data, updates UI overlay
```

**Confidence:** HIGH — This cross-origin restriction is official Chrome extension behavior documented at developer.chrome.com. The service-worker-as-proxy pattern is the standard solution.

---

### Supporting Libraries — Chrome Extension

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| @wxt-dev/module-react | latest | React integration for WXT | Add for popup and options page UI only; do not load React in the content script |
| zod | ^3.23.0 | Runtime schema validation | Validate backend API responses in the service worker before passing to content scripts; prevents crashes from API shape changes |

**Do not add a state management library** (Zustand, Jotai, etc.) to the extension. The extension has no complex shared client state — the service worker fetches from the backend on demand, and the content script's UI overlay reads from service worker messages. `chrome.storage.local` is sufficient for persisting any automation state (current target list, progress) across service worker restarts.

---

### Web Dashboard — React + Vite + Recharts + TanStack Query

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| React | 19.x | UI framework | Same version as extension; no context-switching; large ecosystem |
| Vite | 6.x | Build tool | Standard React build tool in 2025 (Create React App is deprecated); fast HMR; matches WXT's underlying bundler |
| Recharts | 3.8.x | Charts | SVG-based, React-native component API; easiest for line/bar/area charts showing profit history and score trends; no Canvas complexity |
| TanStack Query | 5.95.x | Server state management | Handles polling the FastAPI backend every N seconds for live recommendations; built-in caching, stale-while-revalidate, loading/error states with minimal boilerplate |
| Tailwind CSS | 4.x | Styling | Utility-first; zero custom CSS needed for an internal analytics tool; Vite plugin integration is mature |

**Do not use Chart.js directly.** Chart.js is canvas-based and requires manual React wrapper setup; Recharts is drop-in React components and integrates naturally with component state.

**Do not use Redux/Zustand for server state.** TanStack Query handles all server-side state (fetching, caching, refetching). Only add Zustand if local client-only state becomes complex.

**Confidence:** MEDIUM-HIGH — Recharts 3.8.0 and @tanstack/react-query 5.95.0 confirmed from npm. React 19 + Vite 6 confirmed as 2025 standard from multiple sources.

---

## Complete Dependency Summary

### Python (add to requirements.txt)

```
# API server
fastapi>=0.135.0
uvicorn[standard]>=0.34.0

# Scheduler
APScheduler>=3.11.0,<4.0

# Database
sqlalchemy[asyncio]>=2.0.48
aiosqlite>=0.22.1
alembic>=1.18.0
```

Existing dependencies (httpx, pydantic, click, rich) are unchanged.

### TypeScript — Chrome Extension (extension/package.json)

```json
{
  "devDependencies": {
    "wxt": "~0.20.20",
    "@wxt-dev/module-react": "*",
    "typescript": "^5.0.0",
    "@types/chrome": "^0.1.38"
  },
  "dependencies": {
    "react": "^19.0.0",
    "react-dom": "^19.0.0",
    "zod": "^3.23.0"
  }
}
```

### TypeScript — Web Dashboard (dashboard/package.json)

```json
{
  "devDependencies": {
    "vite": "^6.0.0",
    "@vitejs/plugin-react": "^4.0.0",
    "typescript": "^5.0.0",
    "tailwindcss": "^4.0.0",
    "@tailwindcss/vite": "^4.0.0"
  },
  "dependencies": {
    "react": "^19.0.0",
    "react-dom": "^19.0.0",
    "recharts": "^3.8.0",
    "@tanstack/react-query": "^5.95.0"
  }
}
```

---

## Alternatives Considered

| Category | Recommended | Alternative | Why Not |
|----------|-------------|-------------|---------|
| Scheduler | APScheduler 3.11 | APScheduler 4.x | Pre-release, breaking API rewrite, no migration path for job stores yet |
| Scheduler | APScheduler 3.11 | Celery + Redis | Massive overkill for a single-process personal tool; adds Redis infrastructure dependency |
| Scheduler | APScheduler 3.11 | FastAPI BackgroundTasks | No persistence, no cron triggers, no retry; not suitable for 24/7 recurring jobs |
| Database | SQLite + SQLAlchemy | SQLModel | SQLModel is a thin SQLAlchemy wrapper by FastAPI's author; fine choice but adds an abstraction layer with less documentation — SQLAlchemy 2.0 directly is better understood |
| Migrations | Alembic | Manual `create_all()` | No history, no rollback, no PostgreSQL migration path |
| Extension framework | WXT | Plasmo | Community reports of maintenance lag; larger bundle size; Parcel bundler vs Vite |
| Extension framework | WXT | CRXJS | Archival risk — maintainer-wanted notice posted; uncertain future |
| Extension framework | WXT | Raw MV3 | MV3 service worker lifecycle is complex; WXT handles it correctly |
| EA automation | Main-world service injection | DOM click simulation | CSS selectors break on every web app update; service calls target the stable JavaScript API layer |
| Extension state | chrome.storage.local | Zustand/Jotai | Extension has no complex client state graph; chrome.storage survives service worker restarts which in-memory stores do not |
| Charts | Recharts | Chart.js | Canvas-based, needs manual React wrapper; no native React component API |
| Charts | Recharts | Victory | Less active community in 2025; heavier API for simple line/bar charts |
| Server state | TanStack Query | SWR | TanStack Query v5 has superior TypeScript support, devtools, and mutation API; both are valid but TanStack has more community momentum |

---

## What NOT to Use

| Avoid | Why | Use Instead |
|-------|-----|-------------|
| Plasmo | Under-maintained in 2025; Parcel bundler 2–3x slower than Vite; ~800KB bundles vs ~400KB for WXT | WXT |
| CRXJS @crxjs/vite-plugin | Posted public archival notice if no new maintainer by June 2025; status uncertain March 2026 | WXT |
| APScheduler 4.x | No stable release; breaking API rewrite from 3.x; no migration for job stores | APScheduler 3.11 |
| Direct DOM click simulation as primary automation | EA Web App re-renders DOM frequently; selectors break on updates | Main-world service injection via `chrome.scripting.executeScript({ world: "MAIN" })` |
| `fetch()` from content script to localhost | Blocked by CORS restrictions even with host permissions in MV3 | Relay via `chrome.runtime.sendMessage` to service worker, which does the fetch |
| Storing automation state in service worker memory globals | Service worker terminates after 30s idle; all state is lost | `chrome.storage.local` for persistence across restarts |
| `chrome.action.setIcon` / DOM mutations during relist loop without keepalive | Service worker will die mid-loop | `chrome.alarms` keepalive during active automation |

---

## Monorepo Structure Recommendation

Keep the Python backend and TypeScript frontends in the same repo with separate directories:

```
op-seller/
├── src/                  — existing Python CLI + scoring engine
├── api/                  — FastAPI server (imports from src/)
├── extension/            — WXT Chrome extension
│   ├── entrypoints/
│   │   ├── popup/        — React UI showing recommended sell list
│   │   ├── content.ts    — vanilla TS content script, injected on EA Web App
│   │   └── background.ts — MV3 service worker, calls localhost:8000
│   ├── lib/
│   │   └── ea-services.ts — main-world injection script for EA service access
│   └── wxt.config.ts
├── dashboard/            — React + Vite web dashboard
├── requirements.txt      — Python deps (updated)
└── alembic/              — database migrations
```

The content script is kept as vanilla TypeScript (no React) because loading a UI framework into a content script that runs on every EA Web App page load is unnecessary and slow. React is only loaded in the popup (which opens on user click).

---

## Version Compatibility

| Package | Compatible With | Notes |
|---------|-----------------|-------|
| WXT ~0.20.20 | TypeScript ^5.0.0 | WXT ships its own Vite internally; do not add Vite as a separate dep |
| React 19.x | @wxt-dev/module-react (any) | React 19 concurrent features not needed; React 18 also acceptable if 19 causes issues |
| @types/chrome ^0.1.38 | TypeScript ^5.0.0 | Use DefinitelyTyped version; `chrome-types` from Google is also valid but less stable versioning |
| APScheduler 3.11 | FastAPI 0.135 / Python 3.12 | No known incompatibilities; tested combination |
| SQLAlchemy 2.0.48 | aiosqlite 0.22.1 | Use `sqlite+aiosqlite://` driver string; `sqlite+aiosqlite+file://` for WAL mode |

---

## Sources

- [FastAPI Release Notes](https://fastapi.tiangolo.com/release-notes/) — version 0.135.2 confirmed
- [APScheduler 3.x Docs](https://apscheduler.readthedocs.io/en/3.x/) — version 3.11.2.post1
- [APScheduler Migration Guide](https://apscheduler.readthedocs.io/en/master/migration.html) — 4.x instability confirmed
- [SQLAlchemy PyPI](https://pypi.org/project/SQLAlchemy/) — version 2.0.48
- [aiosqlite PyPI](https://pypi.org/project/aiosqlite/) — version 0.22.1
- [Alembic Docs](https://alembic.sqlalchemy.org/en/latest/front.html) — version 1.18.4
- [WXT Framework](https://wxt.dev/) — version 0.20.18/0.20.20, MV3 native, actively maintained
- [WXT Content Scripts Docs](https://wxt.dev/guide/essentials/content-scripts.html) — matches patterns, main-world injection, `injectScript()`
- [WXT vs Plasmo vs CRXJS comparison (redreamality.com)](https://redreamality.com/blog/the-2025-state-of-browser-extension-frameworks-a-comparative-analysis-of-plasmo-wxt-and-crxjs/) — MEDIUM confidence, community analysis
- [CRXJS archival discussion](https://github.com/crxjs/chrome-extension-tools/discussions/872) — confirms maintenance uncertainty
- [Chrome MV3 Service Worker Lifecycle (official)](https://developer.chrome.com/docs/extensions/develop/concepts/service-workers/lifecycle) — 30-second idle timer confirmed
- [Longer Extension Service Worker Lifetimes (Chrome blog)](https://developer.chrome.com/blog/longer-esw-lifetimes) — Chrome 110+ behavior confirmed
- [Cross-Origin Requests in Chrome Extensions (official)](https://developer.chrome.com/docs/extensions/develop/concepts/network-requests) — content script CORS restriction confirmed
- [FSU EAFC FUT Web Enhancer source code (GreasyFork)](https://greasyfork.org/en/scripts/431044-fsu-eafc-fut-web-%E5%A2%9E%E5%BC%BA%E5%99%A8/code) — main-world service injection pattern with `window.services`, `window.repositories` confirmed in real FUT automation script
- [@types/chrome npm](https://www.npmjs.com/package/@types/chrome) — version 0.1.38, last updated March 2026
- [TanStack Query npm](https://www.npmjs.com/package/@tanstack/react-query) — version 5.95.0
- [Recharts npm](https://www.npmjs.com/package/recharts) — version 3.8.0
- [FastAPI + Async SQLAlchemy 2.0 Setup](https://medium.com/@tclaitken/setting-up-a-fastapi-app-with-async-sqlalchemy-2-0-pydantic-v2-e6c540be4308) — session patterns
- [Alembic + SQLite batch mode](https://blog.greeden.me/en/2025/08/12/no-fail-guide-getting-started-with-database-migrations-fastapi-x-sqlalchemy-x-alembic/) — render_as_batch requirement
