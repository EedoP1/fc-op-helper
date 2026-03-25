# Technology Stack

**Project:** FC26 OP Sell Platform — Backend + API + Chrome Extension + Web Dashboard
**Researched:** 2026-03-25
**Scope:** New components only. Existing stack (Python 3.12, httpx, pydantic, click, rich) is not re-evaluated.

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

### Chrome Extension — WXT + TypeScript + React

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| WXT | 0.20.x | Extension framework | Vite-based, Manifest V3 native, hot reload for service workers, ~43% smaller bundles than Plasmo; actively maintained unlike Plasmo which has community-reported maintenance concerns |
| TypeScript | 5.x | Extension language | Type safety when interacting with DOM elements on EA Web App; `chrome-types` package provides auto-complete for all `chrome.*` APIs |
| React | 19.x | Popup/options UI | Familiar; WXT has first-class `@wxt-dev/module-react` support; consistent with dashboard stack |

**Do not use Plasmo.** Despite strong initial DX, Plasmo is reported as under-maintained in 2025. WXT has taken clear community momentum and produces smaller, faster bundles.

**Do not use raw Manifest V3 without a framework.** Service worker architecture in MV3 is significantly more complex than MV2 background pages; WXT abstracts the lifecycle correctly.

WXT 0.20.x is a release candidate for v1.0 — the API is stable but semver pre-1.0. Pin to `~0.20.x` and do not auto-upgrade to avoid breaking changes before 1.0.

**Confidence:** MEDIUM-HIGH — WXT 0.20.20 confirmed from npm. Plasmo maintenance concerns from multiple community sources (DevKit.best, redreamality.com comparisons). MV3 requirement confirmed from Chrome for Developers.

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
    "typescript": "^5.0.0"
  },
  "dependencies": {
    "react": "^19.0.0",
    "react-dom": "^19.0.0"
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
| Extension framework | WXT | Raw MV3 | MV3 service worker lifecycle is complex; WXT handles it correctly |
| Charts | Recharts | Chart.js | Canvas-based, needs manual React wrapper; no native React component API |
| Charts | Recharts | Victory | Less active community in 2025; heavier API for simple line/bar charts |
| Server state | TanStack Query | SWR | TanStack Query v5 has superior TypeScript support, devtools, and mutation API; both are valid but TanStack has more community momentum |

---

## Monorepo Structure Recommendation

Keep the Python backend and TypeScript frontends in the same repo with separate directories:

```
op-seller/
├── src/                  — existing Python CLI + scoring engine
├── api/                  — new FastAPI server (imports from src/)
├── extension/            — WXT Chrome extension
├── dashboard/            — React + Vite web dashboard
├── requirements.txt      — Python deps (updated)
└── alembic/              — database migrations
```

This avoids a multi-repo setup for what is still a personal tool. The Python `api/` directory imports the existing `src/` modules directly — no packaging step needed.

---

## Sources

- [FastAPI Release Notes](https://fastapi.tiangolo.com/release-notes/) — version 0.135.2 confirmed
- [APScheduler 3.x Docs](https://apscheduler.readthedocs.io/en/3.x/) — version 3.11.2.post1
- [APScheduler Migration Guide](https://apscheduler.readthedocs.io/en/master/migration.html) — 4.x instability confirmed
- [SQLAlchemy PyPI](https://pypi.org/project/SQLAlchemy/) — version 2.0.48
- [aiosqlite PyPI](https://pypi.org/project/aiosqlite/) — version 0.22.1
- [Alembic Docs](https://alembic.sqlalchemy.org/en/latest/front.html) — version 1.18.4
- [WXT Framework](https://wxt.dev/) — version 0.20.20, MV3 native
- [2025 Extension Framework Comparison (redreamality.com)](https://redreamality.com/blog/the-2025-state-of-browser-extension-frameworks-a-comparative-analysis-of-plasmo-wxt-and-crxjs/) — WXT vs Plasmo analysis
- [Chrome Extensions MV3](https://developer.chrome.com/docs/extensions/develop/migrate/what-is-mv3) — MV3 requirement
- [TanStack Query npm](https://www.npmjs.com/package/@tanstack/react-query) — version 5.95.0
- [Recharts npm](https://www.npmjs.com/package/recharts) — version 3.8.0
- [Best React Chart Libraries 2025 (LogRocket)](https://blog.logrocket.com/best-react-chart-libraries-2025/) — Recharts recommendation
- [FastAPI + Async SQLAlchemy 2.0 Setup (Medium)](https://medium.com/@tclaitken/setting-up-a-fastapi-app-with-async-sqlalchemy-2-0-pydantic-v2-e6c540be4308) — session patterns
- [Alembic + SQLite batch mode (greeden.me)](https://blog.greeden.me/en/2025/08/12/no-fail-guide-getting-started-with-database-migrations-fastapi-x-sqlalchemy-x-alembic/) — render_as_batch requirement
