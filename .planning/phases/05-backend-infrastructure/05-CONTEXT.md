# Phase 5: Backend Infrastructure - Context

**Gathered:** 2026-03-26
**Status:** Ready for planning

<domain>
## Phase Boundary

Backend is ready to serve the Chrome extension — action queue, trade recording, and profit summary are live and integrated with existing data. No frontend/extension work in this phase.

</domain>

<decisions>
## Implementation Decisions

### Action Queue Design
- **D-01:** Actions are generated on-demand when the extension polls `GET /api/v1/actions/pending` — no background job or pre-built queue. Backend inspects portfolio state and trade records to determine the next action (buy a player not yet bought, list a purchased player, relist an expired listing).
- **D-02:** Single global queue returning one action at a time. Extension processes actions sequentially — no parallel actions.
- **D-03:** Three action types: BUY, LIST, RELIST. Each action includes ea_id, player name, target price, and action-specific data.
- **D-04:** Actions claimed as IN_PROGRESS are auto-reset to PENDING after 5 minutes (stale timeout). This handles extension crashes or tab closures.

### Trade Lifecycle Tracking
- **D-05:** Record outcomes only — bought, listed, sold, expired. Failed attempts (e.g., outbid, price guard skip) are not persisted. Extension reports completion via `POST /api/v1/actions/{id}/complete`.
- **D-06:** Single `trade_records` table with columns: ea_id, action_type (buy/list/relist), price, outcome, timestamp. One row per lifecycle event, not one row per full cycle. Profit is computed by joining buy + sell events for the same ea_id.

### Player Swap Mechanics
- **D-07:** When a player is removed, re-run `optimize_portfolio()` with the freed budget added back and the remaining portfolio players locked in. Return replacement player(s) in the response.
- **D-08:** Removing a player cancels any pending or in-progress actions for that ea_id. Completed trades are preserved for profit tracking.

### Profit Summary
- **D-09:** `GET /api/v1/profit/summary` returns both totals (coins spent, coins earned, net profit, trade count) and per-player breakdown (ea_id, name, total spent, total earned, net, trade count).
- **D-10:** All-time aggregation only for v1.1. No daily/weekly windows — can extend later.
- **D-11:** Realized profit only — profit from completed sell cycles. No unrealized/estimated profit requiring live price lookups.

### Claude's Discretion
- DB table schema details (column types, indexes) — follow existing patterns in models_db.py
- API response format details beyond what's specified above
- Error response structure and HTTP status codes
- Whether to use a separate router file or extend existing ones

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Requirements
- `.planning/REQUIREMENTS.md` — BACK-01 through BACK-06 acceptance criteria for this phase

### Existing Code
- `src/server/models_db.py` — Existing DB models (PlayerRecord, PlayerScore, etc.) — follow same ORM patterns
- `src/server/api/portfolio.py` — Existing portfolio endpoint — action queue builds on this data
- `src/server/main.py` — FastAPI app lifespan, router registration, session_factory setup
- `src/server/db.py` — Database engine and session factory creation
- `src/optimizer.py` — optimize_portfolio() function reused for player swap (D-07)

### Architecture
- `.planning/codebase/ARCHITECTURE.md` — Layer structure, data flow, patterns

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `optimize_portfolio()` in `src/optimizer.py` — reuse directly for player swap (D-07)
- `_build_scored_entry()` in `src/server/api/portfolio.py` — pattern for building response dicts from DB rows
- `_PlayerProxy` in portfolio.py — pattern for satisfying optimizer's interface requirements

### Established Patterns
- SQLAlchemy 2.0 async ORM with mapped_column() and Mapped[] type hints (models_db.py)
- FastAPI APIRouter with `/api/v1` prefix (all existing routers)
- Session factory via `request.app.state.session_factory` (portfolio.py, players.py)
- Index definitions via `__table_args__` tuple (models_db.py)

### Integration Points
- New routers registered in `src/server/main.py` lifespan/app setup
- New DB models added to `src/server/models_db.py` (same Base)
- CORS middleware added to FastAPI app in `src/server/main.py`
- Player swap endpoint needs access to existing portfolio query logic

</code_context>

<specifics>
## Specific Ideas

No specific requirements — open to standard approaches.

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 05-backend-infrastructure*
*Context gathered: 2026-03-26*
