---
phase: 08-dom-automation-layer
plan: "01"
subsystem: backend-api, extension-selectors
tags: [automation, daily-cap, price-guard, endpoints, selectors, dom-inspection]
dependency_graph:
  requires: []
  provides:
    - DailyTransactionCount ORM model
    - GET /api/v1/automation/daily-cap
    - POST /api/v1/automation/daily-cap/increment
    - GET /api/v1/portfolio/player-price/{ea_id}
    - Verified automation selectors in extension/src/selectors.ts
  affects:
    - src/server/main.py (router registered)
    - src/server/db.py (table auto-creation)
    - extension/src/selectors.ts (automation selectors added)
tech_stack:
  added: []
  patterns:
    - FastAPI APIRouter with prefix /api/v1
    - SQLAlchemy text() upsert for cross-dialect conflict resolution
    - _read_session_factory helper for read/write session routing
    - EA Web App PointerEvent+MouseEvent sequence for DOM automation (eaClick)
key_files:
  created:
    - src/server/api/automation.py
  modified:
    - src/server/models_db.py
    - src/server/main.py
    - src/server/db.py
    - extension/src/selectors.ts
decisions:
  - "Raw SQL text() upsert used for daily-cap increment — SQLAlchemy ORM on_conflict_do_update is dialect-specific; text() works for both PostgreSQL and SQLite"
  - "DailyTransactionCount uses unique=True on date column — one row per UTC calendar day"
  - "_DEFAULT_CAP = 500 per D-24/D-25 conservative initial threshold"
  - "EA Web App ignores .click() — full PointerEvent+MouseEvent sequence required (pointerdown, mousedown, pointerup, mouseup, click) verified live"
  - "Dialog button ordering is inconsistent across EA dialogs — always match by text or .primary class, never by :first-child/:last-child position"
  - "Quick list panel native 'List for Transfer' button is btn-standard.primary, NOT call-to-action (which is FC Enhancer)"
  - "Filter dropdowns indexed 0-8 in DOM order; Quality=0, Rarity=2, Position=3"
metrics:
  duration_seconds: 300
  completed_date: "2026-03-30"
  tasks_completed: 2
  tasks_total: 2
  files_created: 1
  files_modified: 4
---

# Phase 8 Plan 01: DOM Automation Layer — Foundation Summary

**One-liner:** Verified EA Web App FC26 automation selectors via live DevTools (bought, listed, cleared, relisted a real player) plus backend automation endpoints for daily cap enforcement and fresh price guard.

## Status

COMPLETE. Both tasks executed and committed.

## Tasks Executed

### Task 1: DOM Exploration — Map automation selectors via live DevTools (COMPLETE)

**Commit:** f18e3d8

All automation selectors discovered and verified by performing a live buy/list/relist cycle on the EA Web App (bought Gottfrid Rapp for 200 coins, listed him, cleared sold items, relisted all expired items).

Key discoveries:

- **eaClick pattern required:** EA Web App ignores `element.click()`. Full event sequence needed: `pointerdown > mousedown > pointerup > mouseup > click` using PointerEvent and MouseEvent with `{bubbles: true, cancelable: true}`. Documented in selectors.ts header comment.
- **Dialog ordering inconsistent:** Buy confirm dialog has `[Ok (primary), Cancel]` — Ok is first. Re-list All dialog has `[Cancel, Yes (primary)]` — Yes is last. Always match by `.primary` class or text content.
- **Player autocomplete:** `.playerResultsList` contains `<button>` children. Click matching button after typing in `.ut-player-search-control input`.
- **Quick list panel:** Native "List for Transfer" button has class `btn-standard primary` (NOT `call-to-action` — that is FC Enhancer). Match by text to be safe.
- **Filter dropdowns:** `.inline-list-select.ut-search-filter-control` elements, indexed 0–8 by DOM order (Quality=0, EvolutionStatus=1, Rarity=2, Position=3, ChemStyle=4, Country=5, League=6, Club=7, PlayStyles=8). Open by clicking `.ut-search-filter-control--row`, select by clicking matching `<li>`.

Selectors added:

| Constant | Selector | Purpose |
|---|---|---|
| NAV_SIDEBAR | .ut-tab-bar-view | Sidebar nav container |
| NAV_TRANSFERS | .ut-tab-bar-item.icon-transfer | Transfers hub nav button |
| TILE_SEARCH_MARKET | .ut-tile-transfer-market | "Search the Transfer Market" tile |
| TILE_TRANSFER_LIST | .ut-tile-transfer-list | "Transfer List" tile |
| SEARCH_PLAYER_NAME_INPUT | .ut-player-search-control input | Player name text input |
| SEARCH_PLAYER_SUGGESTIONS | .playerResultsList | Autocomplete suggestions |
| SEARCH_FILTER_DROPDOWN | .inline-list-select.ut-search-filter-control | Filter dropdowns (indexed 0-8) |
| SEARCH_SUBMIT_BUTTON | .button-container > button.btn-standard.primary | Native Search button |
| SEARCH_PRICE_INPUT | .price-filter input.ut-number-input-control | Min/Max price inputs |
| SEARCH_RESULTS_LIST | .paginated-item-list.ut-pinned-list | Search results container |
| BUY_NOW_BUTTON | button.buyButton | Buy Now in detail panel |
| EA_DIALOG | .ea-dialog-view | EA native dialog |
| EA_DIALOG_PRIMARY_BUTTON | .ea-dialog-view .ut-st-button-group button.btn-standard.primary | Primary dialog button |
| TL_CLEAR_SOLD | .ut-transfer-list-view .section-header-btn | Clear Sold button |
| TL_RELIST_ALL_CLASS | btn-standard section-header-btn mini primary | Re-list All (match by section) |
| QUICK_LIST_PANEL | .ut-quick-list-panel-view | Quick list panel container |
| QUICK_LIST_PRICE_INPUTS | .ut-quick-list-panel-view input.ut-number-input-control | [0]=Start, [1]=BIN price |
| LIST_ON_MARKET_ACCORDION | button.accordian | List on Transfer Market toggle |
| SESSION_LOGIN_VIEW | .ut-login-view | Session expired indicator |

### Task 2: Backend endpoints — daily cap tracking and fresh price lookup (COMPLETE)

**Commit:** 8acfdbd

Created `src/server/api/automation.py` with three endpoints:

- `GET /api/v1/automation/daily-cap` — returns `{count, cap, capped, date}` for today UTC. Returns defaults (count=0, cap=500, capped=false) if no row exists.
- `POST /api/v1/automation/daily-cap/increment` — upserts today's row using `INSERT ... ON CONFLICT (date) DO UPDATE SET count = count + 1`. Returns same shape as GET.
- `GET /api/v1/portfolio/player-price/{ea_id}` — returns `{ea_id, buy_price, sell_price}` from `portfolio_slots`. Returns 404 if player not in portfolio.

Added `DailyTransactionCount` ORM model to `models_db.py` with `date` (unique), `count`, and `cap` columns. Registered `automation.router` in `main.py`. Added model to `create_engine_and_tables` import in `db.py` for auto-creation on startup.

**Verification:** `python -c "from src.server.api.automation import router; print(len(router.routes), 'routes')"` → `3 routes`.

## Deviations from Plan

### Auto-applied

**1. [Rule 3 - Blocking] Raw SQL text() upsert instead of ORM dialect-specific upsert**
- **Found during:** Task 2 implementation
- **Issue:** SQLAlchemy's `insert().on_conflict_do_update()` is dialect-specific (postgresql_on_conflict vs sqlite_on_conflict). The project targets both PostgreSQL production and SQLite tests.
- **Fix:** Used `text("INSERT ... ON CONFLICT (date) DO UPDATE SET count = count + 1")` which works across both dialects.
- **Files modified:** src/server/api/automation.py
- **Commit:** 8acfdbd

## Known Stubs

None. All endpoints and selectors are fully wired from live verification.

## Self-Check

- [x] `extension/src/selectors.ts` has automation selectors — commit f18e3d8
- [x] `src/server/api/automation.py` exists (3 routes)
- [x] `DailyTransactionCount` in `src/server/models_db.py`
- [x] `automation.router` registered in `src/server/main.py`
- [x] Commit 8acfdbd exists (backend endpoints)
- [x] Commit f18e3d8 exists (selectors)
- [x] Extension tests pass — 57/57 (5 test files)
- [x] Python import test passes: `from src.server.api.automation import router` → 3 routes

## Self-Check: PASSED
