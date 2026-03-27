# Phase 7: Portfolio Management - Context

**Gathered:** 2026-03-27
**Status:** Ready for planning

<domain>
## Phase Boundary

User can generate an OP sell portfolio from the extension overlay, view it in a right sidebar panel on the EA Web App, swap out players with auto-replacements, and confirm to seed portfolio_slots in the DB. No automation (Phase 8), no profit tracking UI (v2).

</domain>

<decisions>
## Implementation Decisions

### Overlay Panel Layout
- **D-01:** Right sidebar, fixed position, ~300-350px wide. Collapsible via a toggle tab on the right edge — slides in/out.
- **D-02:** Styled to match the EA Web App dark theme (dark background, similar fonts/colors). Should feel native, not jarring.
- **D-03:** Each player row shows detailed info: name, OVR rating, position, buy price, OP sell price, margin %, expected profit, OP ratio. Taller rows are acceptable — list is scrollable.

### Portfolio Generation Flow
- **D-04:** Budget input is a text field at the top of the overlay panel. User types budget and hits Generate.
- **D-05:** Two-step flow: first endpoint returns portfolio preview (no DB seeding), second endpoint confirms and seeds portfolio_slots. User reviews before committing.
- **D-06:** Regeneration replaces the previous portfolio entirely. No append, no confirmation warning — clean slate each time.

### Player Swap Interaction
- **D-07:** Full flow is: Generate (preview) → swap players freely → Confirm to lock in. Swaps happen in the preview/draft phase before confirmation.
- **D-08:** Auto-accept replacements — when a player is removed, backend returns replacement(s) that are automatically added to the draft. No selection step.
- **D-09:** Instant remove — click X on a player row, immediately removed and replacement appears. No "are you sure?" prompt. Since the portfolio isn't committed yet (pre-confirm), there's no risk.

### Data Persistence & Sync
- **D-10:** Draft portfolio (pre-confirm) lives in-memory in the content script only. Closing the tab loses the draft. Nothing persisted until the user hits Confirm.
- **D-11:** Confirmed portfolio fetched from backend on EA Web App page load. Content script asks service worker, service worker calls backend. Backend DB is the single source of truth.
- **D-12:** Three distinct overlay states: (1) Empty — budget input + Generate button, (2) Draft — player list with swap/remove + Confirm button, (3) Confirmed — player list with Regenerate option.

### Claude's Discretion
- Panel width and exact toggle button styling
- Loading/spinner states during generate and swap API calls
- How player rows are sorted within the panel (by efficiency, by price, etc.)
- Error handling for API failures (network issues, empty portfolio)
- New message types to add to messages.ts for portfolio communication
- Whether to add a budget summary (used/remaining) to the panel header
- API endpoint design details (request/response shapes beyond what's specified)

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Requirements
- `.planning/REQUIREMENTS.md` — PORT-01, UI-01, UI-03 acceptance criteria for this phase

### Prior Phase Context
- `.planning/phases/05-backend-infrastructure/05-CONTEXT.md` — Backend API decisions: action queue, trade tracking, player swap mechanics (D-07, D-08)
- `.planning/phases/06-extension-architecture-foundation/06-CONTEXT.md` — Extension architecture: message protocol (D-05, D-06), storage patterns (D-04), content script lifecycle (D-07, D-08, D-09)

### Existing Backend Code
- `src/server/api/portfolio.py` — GET /portfolio (returns optimized list), DELETE /portfolio/{ea_id} (swap + replacements)
- `src/server/models_db.py` — PortfolioSlot, PlayerScore, PlayerRecord models
- `src/optimizer.py` — optimize_portfolio() reused for generation and swap

### Existing Extension Code
- `extension/src/messages.ts` — Discriminated union message types (PING/PONG, extend with portfolio types)
- `extension/src/storage.ts` — WXT storage pattern with typed items (enabledItem, lastActionItem)
- `extension/entrypoints/background.ts` — Service worker with alarm polling
- `extension/entrypoints/ea-webapp.content.ts` — Content script with MutationObserver

### Architecture
- `.planning/codebase/ARCHITECTURE.md` — Overall system architecture and patterns

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `GET /portfolio?budget=N` endpoint — returns full scored player list, can serve as the "preview" step
- `DELETE /portfolio/{ea_id}?budget=N` — handles swap with auto-replacement, returns replacement list
- `_build_scored_entry()` in portfolio.py — builds response dicts from DB rows, already includes all fields needed for detailed player rows
- `extension/src/storage.ts` — WXT typed storage pattern to extend for confirmed portfolio state
- `extension/src/messages.ts` — discriminated union pattern to extend with PORTFOLIO_REQUEST, PORTFOLIO_RESPONSE, etc.

### Established Patterns
- FastAPI routers with `/api/v1` prefix
- WXT framework for extension (entrypoints/, src/)
- Discriminated union messages between service worker and content script
- chrome.storage.local for state that survives service worker termination
- Content script injects on all EA Web App pages via broad match pattern

### Integration Points
- New `POST /api/v1/portfolio/generate` endpoint — accepts budget, returns preview (no DB seeding)
- New `POST /api/v1/portfolio/confirm` endpoint — seeds portfolio_slots from a confirmed list
- Content script injects overlay DOM into EA Web App pages
- Service worker proxies portfolio API calls between content script and backend
- New message types in messages.ts for portfolio generate/confirm/swap commands

</code_context>

<specifics>
## Specific Ideas

- User described the ideal flow as: "user gets portfolio -> user swaps and auto gets different players -> user is happy and confirms portfolio"
- The two-step generate/confirm pattern means the existing `GET /portfolio` can serve as the preview step, and a new confirm endpoint seeds slots

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 07-portfolio-management*
*Context gathered: 2026-03-27*
