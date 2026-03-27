# Phase 7: Portfolio Management - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-03-27
**Phase:** 07-portfolio-management
**Areas discussed:** Overlay panel layout, Portfolio generation flow, Player swap interaction, Data persistence & sync

---

## Overlay Panel Layout

### Panel Position

| Option | Description | Selected |
|--------|-------------|----------|
| Right sidebar | Fixed panel on right edge, ~300-350px wide. Doesn't overlap main content. | ✓ |
| Bottom bar | Horizontal bar pinned to bottom. Compact player list in scrollable row. | |
| Floating overlay | Draggable floating panel that can be repositioned. | |

**User's choice:** Right sidebar
**Notes:** None

### Panel Visibility

| Option | Description | Selected |
|--------|-------------|----------|
| Collapsible with toggle | Small tab/button on right edge to expand/collapse. Slides in/out. | ✓ |
| Always visible | Panel always shown once portfolio exists. | |
| You decide | Claude picks best approach. | |

**User's choice:** Collapsible with toggle
**Notes:** None

### Player Row Info Density

| Option | Description | Selected |
|--------|-------------|----------|
| Compact: name + price + margin | Player name, buy price, OP sell price, margin %. | |
| Detailed: add rating, position, profit | Above plus OVR rating, position, expected profit, OP ratio. | ✓ |
| You decide | Claude picks right density. | |

**User's choice:** Detailed info per player row
**Notes:** None

### Panel Styling

| Option | Description | Selected |
|--------|-------------|----------|
| Match EA Web App dark theme | Dark background, similar fonts/colors to EA Web App. Feels native. | ✓ |
| Distinct branded style | Own color scheme, clearly distinguishable as third-party. | |
| You decide | Claude picks for readability. | |

**User's choice:** Match EA Web App dark theme
**Notes:** None

---

## Portfolio Generation Flow

### Budget Input Method

| Option | Description | Selected |
|--------|-------------|----------|
| Text field in panel | Simple number input at top of overlay. Type budget, hit Generate. | ✓ |
| Preset budget buttons | Quick-select buttons (100k, 250k, 500k, 1M) plus custom input. | |
| You decide | Claude picks input method. | |

**User's choice:** Text field in panel
**Notes:** None

### Regeneration Behavior

| Option | Description | Selected |
|--------|-------------|----------|
| Replace entirely | New generation replaces old portfolio completely. Clean slate. | ✓ |
| Confirm before replacing | Warning prompt before regenerating. | |
| You decide | Claude picks best UX. | |

**User's choice:** Replace entirely
**Notes:** None

### Generate + Seed vs Two-Step

| Option | Description | Selected |
|--------|-------------|----------|
| Generate + seed slots | Single endpoint creates portfolio_slots rows immediately. | |
| Separate: generate then confirm | First endpoint returns preview, second confirms and seeds slots. | ✓ |
| You decide | Claude picks API design. | |

**User's choice:** Separate generate then confirm
**Notes:** User wants to review portfolio before committing to DB.

---

## Player Swap Interaction

### Replacement Handling

| Option | Description | Selected |
|--------|-------------|----------|
| Auto-accept replacements | Backend returns replacement(s), automatically added to portfolio. | ✓ |
| Show suggestions, user picks | Backend returns 3-5 candidates. User selects which to add. | |
| You decide | Claude picks swap UX. | |

**User's choice:** Auto-accept (via free-text response)
**Notes:** User described ideal flow: "user gets portfolio -> user swaps and auto gets different players -> user is happy and confirms portfolio." This confirmed auto-accept with the two-step generate/confirm flow.

### Remove Confirmation

| Option | Description | Selected |
|--------|-------------|----------|
| Instant remove | Click X, immediately removed and replacement appears. | ✓ |
| Confirm before remove | Small 'Are you sure?' prompt before removing. | |
| You decide | Claude picks based on flow. | |

**User's choice:** Instant remove
**Notes:** Since portfolio isn't committed pre-confirm, no risk of losing committed work.

---

## Data Persistence & Sync

### Draft Portfolio Storage

| Option | Description | Selected |
|--------|-------------|----------|
| In-memory in content script | Draft is local state. Nothing persisted until Confirm. Closing tab loses draft. | ✓ |
| chrome.storage.local | Draft cached in extension storage. Survives tab close and browser restart. | |
| You decide | Claude picks based on flow. | |

**User's choice:** In-memory in content script
**Notes:** None

### Confirmed Portfolio Loading

| Option | Description | Selected |
|--------|-------------|----------|
| Fetch from backend on load | Content script asks service worker to fetch from backend on page load. | ✓ |
| Cache in chrome.storage + sync | Store in chrome.storage for instant display, background sync. | |
| You decide | Claude picks persistence approach. | |

**User's choice:** Fetch from backend on load
**Notes:** Backend DB is single source of truth.

### Overlay State Views

| Option | Description | Selected |
|--------|-------------|----------|
| Yes — show different views | Empty state (budget input), Draft state (swap/confirm), Confirmed state (regenerate). | ✓ |
| You decide | Claude designs states. | |

**User's choice:** Yes — show different views
**Notes:** Three distinct states: empty, draft, confirmed.

---

## Claude's Discretion

- Panel width and toggle button styling
- Loading/spinner states during API calls
- Player row sort order within panel
- Error handling for API failures
- New message types for messages.ts
- Budget summary display in panel header
- API endpoint design details

## Deferred Ideas

None — discussion stayed within phase scope.
