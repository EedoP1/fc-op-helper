# Phase 8: DOM Automation Layer - Context

**Gathered:** 2026-03-30
**Status:** Ready for planning

<domain>
## Phase Boundary

Extension autonomously executes the full buy/list/relist cycle on the EA Web App with price guard, human-paced timing, CAPTCHA detection, and user controls for start/stop and status. Extension-driven automation with backend reporting — the extension drives the cycle locally using portfolio data, reports outcomes to the backend in real-time. Includes DOM exploration task to map all automation selectors before writing code.

</domain>

<decisions>
## Implementation Decisions

### Automation Cycle Flow
- **D-01:** Action-queue driven model with extension-driven execution. Extension loads portfolio + actions-needed from backend, then drives the cycle locally. Reports outcomes to backend via POST /trade-records/direct in real-time per event.
- **D-02:** Full cycle: buy all portfolio players → list each immediately after buying (from same page, no navigation needed) → poll transfer list for sold/expired → relist-all for expired → rebuy+list for sold → clear sold cards. Continuous loop until stopped.
- **D-03:** Relist uses EA's "Relist All" button for batch relisting of all expired cards. Single click, no per-card interaction.
- **D-04:** Auto-navigate between EA Web App pages. Extension clicks sidebar nav items to reach Transfer Market search, transfer list, etc. Requires nav selectors in selectors.ts.

### Buy Search Strategy
- **D-05:** Search by player name + card rarity. No other filters needed initially.
- **D-06:** Price discovery via binary search: start at target buy price as max BIN, step up if no results found, step down if found (to find cheapest tier). Buy the cheapest available card.
- **D-07:** Buy cheapest card available — even if target is 50k and card is at 49k, buy for 49k.
- **D-08:** Price guard tolerance: 5% above target buy price. If cheapest available exceeds target * 1.05, skip this player.
- **D-09:** To refresh search results, change min BIN/BID value (EA caches results without a value change).

### Buy Retry & Failure Handling
- **D-10:** 3 buy-attempt retries per player before skipping to next. A "retry" = the buy was attempted but sniped (card disappeared during purchase). Price discovery searches (stepping up/down to find the player) do NOT count as retries.
- **D-11:** After 3 failed buy attempts, skip to the next player in the portfolio. Skipped players will be retried on the next full cycle.

### Post-Buy Listing
- **D-12:** After buying, list immediately from the same page (no navigation to unassigned pile needed). Set OP sell price from the locked price for this player.
- **D-13:** Prices lock on purchase: fresh buy/sell prices fetched from backend before each buy attempt, then locked for that player until they sell. After sell → unlock → next rebuy gets fresh prices.

### Rebuy After Sale
- **D-14:** When a sold player is detected, rebuy the same player (portfolio composition does not change during automation — only on regenerate). Fresh prices fetched from backend for the rebuy.
- **D-15:** Always rebuy same player — no re-evaluation of whether the player is still "worth it." Portfolio is locked until user regenerates.

### Start/Stop & Confirmation UX
- **D-16:** Separate "Start Automation" button — appears after portfolio is confirmed. Confirming the portfolio does NOT auto-start automation. Two distinct actions.
- **D-17:** Stop finishes the current action gracefully (e.g., completes an in-progress buy+list), then halts. No mid-action abandonment to avoid bought-but-not-listed state.
- **D-18:** Resume after stop: fresh scan of transfer list DOM to detect current state (listed, expired, sold), then determine next actions. Handles state changes that occurred while stopped.
- **D-19:** Cold start (fresh load): call GET /portfolio/actions-needed from backend first, then verify against actual transfer list DOM scan. Both sources combined give the full picture.

### Status Display
- **D-20:** Status panel shows: current action ("Buying: Mbapp\u00e9 (searching...)"), last event ("Listed Salah at 85k"), running/stopped/error state badge, and running profit counter.
- **D-21:** Collapsible activity log below the status summary — scrollable list of all actions taken this session with timestamps.

### Safety & Error Handling
- **D-22:** CAPTCHA detection via action failure heuristic — if a buy/list action fails unexpectedly (button click doesn't produce expected DOM result), treat as potential CAPTCHA. Stop automation immediately and alert user.
- **D-23:** DOM mismatch = immediate stop + alert (AUTO-07). Show the specific selector/element that failed. No silent continuation.
- **D-24:** Daily cap on searches + buys only (not lists/relists). Tracked in backend DB. When cap hits, automation degrades to relist-only mode — continues relisting expired cards but stops buying/searching until cap resets at midnight.
- **D-25:** Default daily cap: 500 search+buy transactions. Configurable by user.

### DOM Interaction Strategy
- **D-26:** Direct DOM clicks via element.click() + dispatchEvent for most interactions. No EA internal API (window.services) usage.
- **D-27:** Price input fields: simulate keystrokes digit-by-digit with small delays between each. Clear field first, then type each character. More human-like than programmatic value setting.
- **D-28:** Random uniform jitter 800-2500ms between all DOM interactions. No two consecutive intervals identical (AUTO-05).
- **D-29:** DOM exploration task FIRST — manually inspect EA Web App with DevTools and document all needed selectors in selectors.ts before writing any automation code. Aligns with STATE.md blocker about LOW confidence selectors.

### Backend API Contract
- **D-30:** Extension-driven model: extension drives the cycle locally, reports outcomes to backend in real-time via POST /trade-records/direct (existing endpoint).
- **D-31:** Fresh prices fetched from backend before each buy attempt (new or rebuy after sale). Backend provides current buy/sell prices. Prices lock on successful purchase until that player sells.
- **D-32:** Daily cap tracked in backend DB — new endpoint needed for GET/POST daily cap counter. Backend enforces the cap.
- **D-33:** GET /portfolio/actions-needed (existing) used for cold start / resume to get the full picture of what each player needs.

### Edge Cases: Leftovers, Coins, Transfer List Space
- **D-34:** Leftover players (not in confirmed portfolio but tracked): relist only. If leftovers are already listed and expire, relist them. Do NOT buy new leftovers — only manage existing positions passively.
- **D-35:** Out of coins: skip to relist-only mode. Stop buying, continue relisting expired cards and monitoring for sales. When a sale generates coins, resume buying. Natural coin recovery cycle.
- **D-36:** Transfer list full (EA caps active listings): stop buying until space opens. Only relist expired cards and wait for sales to free slots. Avoids locking coins in unassigned pile with no way to list.

### Edge Cases: Card Matching, Session, Pagination
- **D-37:** Multiple cards same player: search already filters by rarity (D-05). If two cards of the same rarity exist, filter by overall rating to disambiguate. Portfolio has the exact OVR rating for matching.
- **D-38:** EA session expiry: detect login page redirect or session error DOM elements. Stop automation immediately and alert user to re-login. No auto-resume — user must manually restart automation after logging back in (fresh transfer list scan on resume per D-18).
- **D-39:** Transfer list pagination: auto-paginate and scan ALL pages. Click through transfer list pages to find every listed/expired/sold card. Thorough scan required to catch all expired cards for relist-all and all sold cards for rebuy.

### Claude's Discretion
- Exact selectors (discovered during DOM exploration task)
- How to clear sold cards from the transfer list (DOM interaction specifics)
- Navigation sidebar selector structure
- Specific error messages and alert formatting
- Activity log entry format and styling
- How the price discovery binary search steps (increment size)
- New message types needed in messages.ts for automation commands
- Whether to extend the existing content script or add separate automation module
- Backend endpoint design for daily cap (request/response shapes)
- Backend endpoint design for fresh price lookup on rebuy

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Requirements
- `.planning/REQUIREMENTS.md` — AUTO-01 through AUTO-08, UI-02, UI-04, UI-05 acceptance criteria for this phase

### Prior Phase Context
- `.planning/phases/05-backend-infrastructure/05-CONTEXT.md` — Action queue design (D-01 through D-04), trade lifecycle tracking, profit summary
- `.planning/phases/06-extension-architecture-foundation/06-CONTEXT.md` — Service worker architecture, alarm polling, message protocol, storage patterns, content script lifecycle
- `.planning/phases/07-portfolio-management/07-CONTEXT.md` — Overlay panel layout (D-01-D-03), portfolio generation flow (D-04-D-06), data persistence (D-10-D-12)
- `.planning/phases/07.1-trade-reporting/07.1-CONTEXT.md` — DOM observation scope, selector centralization (D-05), trade reporting behavior, action queue integration
- `.planning/phases/07.2-portfolio-dashboard-trade-tracking/07.2-CONTEXT.md` — Dashboard layout, tab bar, profit visibility, data freshness patterns

### Existing Extension Code
- `extension/src/selectors.ts` — Centralized selector map (AUTO-08). Phase 8 extends this with automation selectors (search, buy, list, relist-all, navigation)
- `extension/src/messages.ts` — Discriminated union message types. Includes ActionsNeededData type already defined
- `extension/src/storage.ts` — PendingAction type, enabledItem gate, portfolioItem, reportedOutcomesItem
- `extension/src/trade-observer.ts` — readTransferList() and isTransferListPage() — reusable for transfer list scanning during automation
- `extension/entrypoints/ea-webapp.content.ts` — Content script with trade observer, overlay panel injection, SPA navigation handling
- `extension/entrypoints/background.ts` — Service worker with portfolio/trade handlers, handleActionsNeeded() already implemented
- `extension/src/overlay/panel.ts` — Overlay panel with three states, tab bar from Phase 07.2

### Existing Backend Code
- `src/server/api/actions.py` — Action queue endpoints, _derive_next_action()
- `src/server/api/portfolio.py` — Portfolio endpoints including actions-needed
- `src/server/api/trade_records.py` — POST /trade-records/direct endpoint for outcome reporting

### State Blockers
- `.planning/STATE.md` — "EA Web App DOM internals are LOW confidence. Selectors must be verified by live DevTools inspection before automation code."
- `.planning/STATE.md` — "EA daily transaction cap threshold unpublished — set automation conservatively at 500/day initially."

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `readTransferList()` in trade-observer.ts — pure DOM reader, reusable for detecting sold/expired cards during automation polling
- `isTransferListPage()` — gate for activating transfer list scanning
- `handleActionsNeeded()` in background.ts — already fetches GET /portfolio/actions-needed and returns typed ActionsNeededData
- `enabledItem` storage gate — already wired for polling toggle, can be reused for automation on/off state
- `lastActionItem` + PendingAction type — existing action storage pattern
- `reportedOutcomesItem` — dedup pattern reusable for automation event tracking
- `mapToPortfolioPlayer()` in background.ts — maps backend JSON to typed PortfolioPlayer

### Established Patterns
- All backend calls route through service worker (content script never calls backend directly)
- Discriminated union messages for type-safe communication
- chrome.storage.local for state that survives service worker termination
- MutationObserver + polling for DOM detection
- Batch reporting via POST /trade-records/batch

### Integration Points
- New automation selectors added to selectors.ts (search page, buy button, list price input, relist-all, sidebar nav)
- New automation module in content script (or extension of existing ea-webapp.content.ts)
- New message types in messages.ts for automation start/stop/status
- New storage items for automation state (running, daily cap counter, activity log)
- New backend endpoint for daily cap tracking
- New backend endpoint for fresh price lookup on rebuy
- Status display + activity log added to overlay panel

</code_context>

<specifics>
## Specific Ideas

- After buying a card, you can list it from the same page without navigating to the unassigned pile — streamlines the buy→list flow
- To refresh EA search results, you must change a filter value (e.g., min BIN/BID) — EA caches results without a value change
- Price discovery is a binary search: start at target, step up if empty, step down if found to find cheapest tier
- "Relist All" button relists at the same price cards were originally listed at — aligns perfectly with locked OP price decision
- Cap applies to searches+buys only — when hit, automation degrades to relist-only mode rather than stopping entirely
- DOM player names are short ("Lo Celso") vs portfolio full names ("Giovani Lo Celso") — endsWith matching from Phase 07.1 trade observer handles this

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 08-dom-automation-layer*
*Context gathered: 2026-03-30*
