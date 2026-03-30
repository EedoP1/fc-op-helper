# Phase 8: DOM Automation Layer - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-03-30
**Phase:** 08-dom-automation-layer
**Areas discussed:** Automation cycle flow, Start/stop & confirmation UX, Safety & error handling, DOM interaction strategy, Backend-Extension API contract

---

## Automation Cycle Flow

### Loop Driver

| Option | Description | Selected |
|--------|-------------|----------|
| Action-queue driven | Extension reads one pending action at a time from backend queue. Executes, reports, fetches next. Backend controls sequence. | |
| Batch-list driven | Extension fetches full actions-needed list and works through it locally. Extension decides ordering. | |
| Hybrid | Fetch actions-needed for full picture (show in UI), execute one-at-a-time via action queue. | |

**User's choice:** Action-queue driven, with note: "for relisting players you can do relist-all so that should be taken into account"

### Navigation

| Option | Description | Selected |
|--------|-------------|----------|
| Auto-navigate | Extension programmatically navigates to correct EA Web App page for each action type. | ✓ |
| Wait for user | Extension only acts when user is already on the right page. Shows prompts. | |
| You decide | Claude picks during implementation. | |

**User's choice:** Auto-navigate

### Buy Search

| Option | Description | Selected |
|--------|-------------|----------|
| Name search + BIN filter | Type player name, set max BIN to target buy price. | |
| Full filter setup | Set name, position, quality, and price range for precision. | |
| You decide | Claude determines during DOM exploration. | |

**User's choice:** Name search + card rarity. Buy cheapest available (even below target). Handle "not found" (price increased) and "sniped" (other user bought) scenarios.

### Post-Buy Flow

| Option | Description | Selected |
|--------|-------------|----------|
| Immediate auto-list | After buying, navigate to unassigned pile, find card, list it. | |
| Separate actions | BUY and LIST are separate action queue items. | |
| You decide | Claude determines. | |

**User's choice:** Immediate list, but no navigation needed — you can list from the same page you bought the card.

### Buy Retry Limit

| Option | Description | Selected |
|--------|-------------|----------|
| 3 retries then skip | | ✓ |
| 5 retries then skip | | |
| Unlimited until stopped | | |
| You decide | | |

**User's choice:** 3 retries. But clarified: "you first need to find how much he is on the market. Search with increasing max BIN until you find the right price. If cheap enough, try to buy. Only actual buy attempts (sniped) count as retries."

### Price Discovery

| Option | Description | Selected |
|--------|-------------|----------|
| Start at target, step up | Set max BIN to target first, increase if no results. | ✓ |
| Start high, narrow down | Set max BIN well above target, browse lowest. | |
| You decide | | |

**User's choice:** Start at target, step up if not found, step down if found but possibly cheaper option exists.

### Wait Cycle

| Option | Description | Selected |
|--------|-------------|----------|
| Poll transfer list periodically | Check for expired/sold cards periodically. | ✓ |
| Backend drives next action | Extension polls GET /actions/pending. | |
| You decide | | |

**User's choice:** Poll transfer list. Expired → relist all. Sold → buy and list again. Clear sold cards.

---

## Start/Stop & Confirmation UX

### Start Trigger

| Option | Description | Selected |
|--------|-------------|----------|
| Confirm starts automation | Existing Confirm button becomes automation trigger. | |
| Separate Start button | New "Start Automation" button after confirming portfolio. | ✓ |
| You decide | | |

**User's choice:** Separate Start button

### Stop Behavior

| Option | Description | Selected |
|--------|-------------|----------|
| Finish current action, then stop | Complete in-progress action gracefully. | ✓ |
| Immediate halt | Stop right away even mid-action. | |
| You decide | | |

**User's choice:** Finish current action, then stop

### Resume Behavior

| Option | Description | Selected |
|--------|-------------|----------|
| Continue where left off | Resume from next unfinished action. | |
| Fresh scan of transfer list | Scan DOM to detect current state, then decide. | ✓ |
| You decide | | |

**User's choice:** Fresh scan of transfer list

### Status Display

| Option | Description | Selected |
|--------|-------------|----------|
| Current action + last event | Compact summary always visible. | |
| Full activity log | Scrollable log of all actions. | |
| Both | Summary at top + collapsible log below. | |

**User's choice:** Current action + last event + collapsible activity log + profit counter

---

## Safety & Error Handling

### CAPTCHA Detection

| Option | Description | Selected |
|--------|-------------|----------|
| DOM marker detection | Watch for known CAPTCHA container elements. | |
| Action failure heuristic | If action fails unexpectedly, assume CAPTCHA. | ✓ |
| You decide | | |

**User's choice:** Action failure heuristic

### Error Response

| Option | Description | Selected |
|--------|-------------|----------|
| Stop + alert user | Fail loudly, show specific error, wait for user. | ✓ |
| Retry once, then stop | Wait and retry, then stop if still failing. | |
| You decide | | |

**User's choice:** Stop + alert user

### Daily Cap

| Option | Description | Selected |
|--------|-------------|----------|
| Yes, configurable cap | Default 500/day, counter resets at midnight. | |
| No cap | User manages risk. | |
| You decide | | |

**User's choice:** Cap searches + buys only (not lists). When cap hits, degrade to relist-only mode — continue relisting expired but stop buying until reset.

### Price Guard

| Option | Description | Selected |
|--------|-------------|----------|
| Strictly at or below target | No tolerance. | |
| Small tolerance (5%) | Allow buying slightly above target. | ✓ |
| You decide | | |

**User's choice:** Small tolerance (5%)

---

## DOM Interaction Strategy

### DOM Method

| Option | Description | Selected |
|--------|-------------|----------|
| Direct DOM clicks + dispatchEvent | Use element.click() and dispatchEvent. | ✓ |
| EA internal services | Call window.services APIs directly. | |
| Mouse event simulation | Full MouseEvent objects with coordinates. | |
| You decide | | |

**User's choice:** Direct DOM clicks + dispatchEvent

### Timing

| Option | Description | Selected |
|--------|-------------|----------|
| Random uniform 800-2500ms | Simple random delay. | ✓ |
| Gaussian around 1500ms | Normal distribution centered at 1500ms. | |
| You decide | | |

**User's choice:** Random uniform 800-2500ms

### Selector Discovery

| Option | Description | Selected |
|--------|-------------|----------|
| DOM exploration task first | Manual DevTools inspection before coding. | ✓ |
| Discover as we go | Add selectors incrementally during development. | |
| You decide | | |

**User's choice:** DOM exploration task first

### Price Input Method

| Option | Description | Selected |
|--------|-------------|----------|
| Set value + trigger event | Programmatic value setting. | |
| Simulate keystrokes | Type each digit with delays. | ✓ |
| You decide | | |

**User's choice:** Simulate keystrokes

---

## Backend-Extension API Contract

### API Model

| Option | Description | Selected |
|--------|-------------|----------|
| Extension-driven with reporting | Extension drives cycle locally, reports to backend. | ✓ |
| Backend-driven queue | Extension asks backend what to do next. | |
| Hybrid | Load actions-needed once, work locally, sync periodically. | |

**User's choice:** Extension-driven with reporting. Note: "make sure we talk about how the client knows what he needs to do now when just loaded"

### Cold Start

| Option | Description | Selected |
|--------|-------------|----------|
| Backend status endpoint | Call GET /portfolio/actions-needed. | |
| Transfer list scan + portfolio | Scan DOM to detect current state. | |
| Both | Backend first, DOM verify. | ✓ |

**User's choice:** Both: backend first, DOM verify

### Reporting

| Option | Description | Selected |
|--------|-------------|----------|
| Real-time per-event | Report each event immediately via POST /trade-records/direct. | ✓ |
| Batch at end of cycle | Queue locally, send batch after cycle. | |
| You decide | | |

**User's choice:** Real-time per-event

### Daily Cap Storage

| Option | Description | Selected |
|--------|-------------|----------|
| Extension storage | Track in chrome.storage.local. | |
| Backend DB | Backend tracks, new endpoint. | ✓ |
| You decide | | |

**User's choice:** Backend DB

### Price Source

| Option | Description | Selected |
|--------|-------------|----------|
| Local portfolio | Use confirmed portfolio prices. | |
| Fresh from backend each cycle | Call backend for fresh prices. | |
| You decide | | |

**User's choice:** Fresh from backend for new bought players (buy + sell price). At moment of purchase, price locks for that specific player until he sells.

### Rebuy Price Strategy

| Option | Description | Selected |
|--------|-------------|----------|
| Fresh prices from backend | Call backend for fresh buy/sell before rebuy. | ✓ |
| Reuse last confirmed prices | Same prices from last cycle. | |
| You decide | | |

**User's choice:** Fresh prices from backend

### Rebuy Evaluation

| Option | Description | Selected |
|--------|-------------|----------|
| Always rebuy same player | Portfolio composition doesn't change during automation. | ✓ |
| Re-evaluate worthiness | Check if player still scores well enough. | |
| You decide | | |

**User's choice:** Always rebuy same player

---

## Edge Cases: Leftovers, Coins, Transfer List Space

### Leftover Players

| Option | Description | Selected |
|--------|-------------|----------|
| Ignore leftovers | Only handle confirmed portfolio players. | |
| Relist leftovers only | Don't buy, but relist if already listed and expired. | ✓ |
| Full cycle for leftovers | Treat same as portfolio players. | |

**User's choice:** Relist leftovers only

### Out of Coins

| Option | Description | Selected |
|--------|-------------|----------|
| Skip to relist-only mode | Stop buying, continue relisting. Resume when sales generate coins. | ✓ |
| Stop automation entirely | Halt everything and alert user. | |
| You decide | | |

**User's choice:** Skip to relist-only mode

### Transfer List Full

| Option | Description | Selected |
|--------|-------------|----------|
| Buy but don't list | Continue buying, hold off listing until space. | |
| Stop buying until space opens | Only relist expired, wait for sales to free slots. | ✓ |
| You decide | | |

**User's choice:** Stop buying until space opens

### Multiple Cards Same Player

| Option | Description | Selected |
|--------|-------------|----------|
| Match by rarity from portfolio | Search filters by rarity. Only matching cards appear. | |
| Match by rating + position | Use OVR + position to disambiguate. | |
| You decide | | |

**User's choice:** Already filter by rarity, but if two cards of same rarity exist, filter by overall rating to disambiguate.

### EA Session Expiry

| Option | Description | Selected |
|--------|-------------|----------|
| Detect and stop + alert | Watch for login redirect, stop, alert user. | ✓ |
| Auto-detect and pause | Pause silently, auto-resume after re-login. | |
| You decide | | |

**User's choice:** Detect and stop + alert

### Transfer List Pagination

| Option | Description | Selected |
|--------|-------------|----------|
| Scan visible page only | Only process current page. | |
| Auto-paginate and scan all | Click through all pages to scan every card. | ✓ |
| You decide | | |

**User's choice:** Auto-paginate and scan all

---

## Claude's Discretion

- Exact selectors (discovered during DOM exploration)
- Navigation sidebar selector structure
- How to clear sold cards from transfer list
- Error message formatting and alert styling
- Activity log entry format
- Price discovery binary search step size
- New message types for automation
- Module structure (extend content script vs separate module)
- Backend endpoint shapes for daily cap and fresh price lookup

## Deferred Ideas

None — discussion stayed within phase scope.
