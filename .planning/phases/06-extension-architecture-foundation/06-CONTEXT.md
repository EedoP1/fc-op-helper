# Phase 6: Extension Architecture Foundation - Context

**Gathered:** 2026-03-27
**Status:** Ready for planning

<domain>
## Phase Boundary

Chrome extension scaffolding is proven — WXT project with MV3 service worker that communicates with the backend via polling, survives termination, and relays typed commands to the content script via discriminated union messages. No DOM automation (Phase 7), no UI overlay (Phase 8).

</domain>

<decisions>
## Implementation Decisions

### Polling & Keepalive
- **D-01:** Use chrome.alarms with 1-minute interval for polling. No setTimeout workaround — simplicity over 30s responsiveness.
- **D-02:** On service worker wake (after termination), poll the backend immediately before resuming the 1-minute alarm cycle.
- **D-03:** Polling gated by an `enabled` flag in chrome.storage.local. Alarm fires but polling is skipped when disabled. Phase 8 UI will wire the toggle; the gate mechanism exists from Phase 6.

### State Persistence
- **D-04:** Backend URL hardcoded to `http://localhost:8000`. No configurable setting — v1.1 is localhost-only per project constraints.

### Message Protocol
- **D-05:** Service worker <-> content script messages use TypeScript discriminated unions (`{ type: 'PING' } | { type: 'PONG' } | ...`). Compile-time exhaustive switch safety.
- **D-06:** Phase 6 defines only PING/PONG message types to prove the channel works. Future phases (7, 8) add EXECUTE_ACTION, ACTION_RESULT, STATUS_UPDATE types as needed.

### Content Script Lifecycle
- **D-07:** Content script injects on ALL EA Web App pages (broad match pattern). It's lightweight — just listens for messages. Phase 7/8 decide which pages to act on.
- **D-08:** MutationObserver on root DOM node detects SPA navigation. Re-initialize listeners when main content area swaps.
- **D-09:** Content script auto-reconnects to service worker on disconnection (e.g., extension update) with retry loop. No user action required — logs status to console.

### Claude's Discretion
- What exactly persists to chrome.storage.local beyond the enabled flag (last action cache, portfolio snapshot, etc.) — choose based on WXT best practices and what makes the system robust across worker termination
- WXT project structure and file organization
- TypeScript configuration and build setup
- Test approach for service worker and content script
- Error handling patterns within the message channel

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Requirements
- `.planning/REQUIREMENTS.md` — ARCH-01 through ARCH-04 acceptance criteria for this phase

### Prior Phase Context
- `.planning/phases/05-backend-infrastructure/05-CONTEXT.md` — Backend API decisions (action queue, trade tracking) that the extension consumes

### Existing Backend Code
- `src/server/api/actions.py` — GET /pending, POST /complete endpoints the service worker will poll
- `src/server/api/portfolio.py` — Portfolio endpoint the extension may cache
- `src/server/main.py` — CORS middleware configuration for chrome-extension:// origins

### Architecture
- `.planning/codebase/ARCHITECTURE.md` — Overall system architecture and patterns

### Project Decisions
- `.planning/PROJECT.md` — v1.1 constraints: localhost-only, TypeScript for extension, WXT framework

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- No extension code exists yet — this is a greenfield TypeScript project within the existing Python repo

### Established Patterns
- Backend uses FastAPI with `/api/v1` prefix — extension must target these routes
- CORS already configured for `chrome-extension://` origins in `src/server/main.py`
- Action queue returns one action at a time (GET /api/v1/actions/pending)
- Action completion via POST /api/v1/actions/{id}/complete

### Integration Points
- Service worker -> `http://localhost:8000/api/v1/actions/pending` (1-min polling)
- Service worker -> `http://localhost:8000/api/v1/actions/{id}/complete` (action results)
- Service worker <-> content script via chrome.runtime messaging (discriminated unions)
- Content script injects on `https://www.ea.com/ea-sports-fc/ultimate-team/web-app/*`

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

*Phase: 06-extension-architecture-foundation*
*Context gathered: 2026-03-27*
