# Phase 8: DOM Automation Layer - Research

**Researched:** 2026-03-30
**Domain:** Chrome Extension DOM automation, MV3 content script patterns, EA Web App interaction
**Confidence:** HIGH (architecture is well-understood from prior phases; LOW only for raw EA selectors which require live DevTools)

## Summary

Phase 8 builds the automation engine that drives the full buy/list/relist cycle on the EA Web App. The architecture is extension-driven: the content script owns the entire automation state machine, navigates between EA pages, clicks buttons and fills forms, and reports outcomes to the backend in real-time. All backend calls still route through the service worker per the established CORS constraint.

The codebase is in an excellent state for this phase. The content script, service worker, selectors.ts, trade observer, message protocol, overlay panel, and backend `GET /portfolio/actions-needed` endpoint are all complete and can be reused directly. The primary new work is: (1) a DOM exploration task to document automation selectors, (2) the automation state machine in the content script, (3) new message types for automation start/stop/status, (4) new storage items for automation state, (5) two new backend endpoints (daily cap, fresh price lookup), and (6) status/activity-log UI in the overlay panel.

The largest open area is EA Web App DOM selectors — these are LOW confidence from training data and must be discovered via live DevTools inspection. The CONTEXT.md and STATE.md both flag this explicitly. The plan must open with a DOM exploration task.

**Primary recommendation:** Structure the phase as waves: (Wave 0) DOM exploration + new backend endpoints, (Wave 1) automation state machine + message protocol, (Wave 2) buy/list/relist execution logic, (Wave 3) status UI in overlay. Keep automation code in a separate module (`automation.ts`) rather than extending ea-webapp.content.ts directly.

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**D-01:** Action-queue driven model with extension-driven execution. Extension loads portfolio + actions-needed from backend, then drives the cycle locally. Reports outcomes to backend via POST /trade-records/direct in real-time per event.

**D-02:** Full cycle: buy all portfolio players → list each immediately after buying (from same page) → poll transfer list for sold/expired → relist-all for expired → rebuy+list for sold → clear sold cards. Continuous loop until stopped.

**D-03:** Relist uses EA's "Relist All" button for batch relisting of all expired cards. Single click, no per-card interaction.

**D-04:** Auto-navigate between EA Web App pages. Extension clicks sidebar nav items to reach Transfer Market search, transfer list, etc. Requires nav selectors in selectors.ts.

**D-05:** Search by player name + card rarity. No other filters needed initially.

**D-06:** Price discovery via binary search: start at target buy price as max BIN, step up if no results found, step down if found. Buy the cheapest available card.

**D-07:** Buy cheapest card available — even if target is 50k and card is at 49k, buy for 49k.

**D-08:** Price guard tolerance: 5% above target buy price. If cheapest available exceeds target * 1.05, skip this player.

**D-09:** To refresh search results, change min BIN/BID value (EA caches results without a value change).

**D-10:** 3 buy-attempt retries per player before skipping to next. A "retry" = the buy was attempted but sniped (card disappeared during purchase). Price discovery searches do NOT count as retries.

**D-11:** After 3 failed buy attempts, skip to the next player. Skipped players retried on next full cycle.

**D-12:** After buying, list immediately from the same page. Set OP sell price from the locked price for this player.

**D-13:** Prices lock on purchase: fresh buy/sell prices fetched from backend before each buy attempt, then locked for that player until they sell. After sell → unlock → next rebuy gets fresh prices.

**D-14:** When a sold player is detected, rebuy the same player. Fresh prices fetched from backend for the rebuy.

**D-15:** Always rebuy same player — no re-evaluation of whether the player is still "worth it."

**D-16:** Separate "Start Automation" button — appears after portfolio is confirmed. Confirming portfolio does NOT auto-start automation.

**D-17:** Stop finishes the current action gracefully, then halts. No mid-action abandonment.

**D-18:** Resume after stop: fresh scan of transfer list DOM, detect current state, determine next actions.

**D-19:** Cold start (fresh load): call GET /portfolio/actions-needed from backend first, then verify against actual transfer list DOM scan.

**D-20:** Status panel shows: current action, last event, running/stopped/error state badge, running profit counter.

**D-21:** Collapsible activity log below status summary — scrollable list of all actions taken this session with timestamps.

**D-22:** CAPTCHA detection via action failure heuristic — if buy/list action fails unexpectedly, treat as potential CAPTCHA. Stop and alert user.

**D-23:** DOM mismatch = immediate stop + alert (AUTO-07). Show the specific selector/element that failed. No silent continuation.

**D-24:** Daily cap on searches + buys only. Tracked in backend DB. Cap hit → relist-only mode.

**D-25:** Default daily cap: 500 search+buy transactions. Configurable by user.

**D-26:** Direct DOM clicks via element.click() + dispatchEvent for most interactions. No EA internal API (window.services).

**D-27:** Price input fields: simulate keystrokes digit-by-digit with small delays between each.

**D-28:** Random uniform jitter 800-2500ms between all DOM interactions.

**D-29:** DOM exploration task FIRST — manually inspect EA Web App with DevTools and document all needed selectors in selectors.ts.

**D-30:** Extension-driven model: reports outcomes to backend via POST /trade-records/direct (existing endpoint).

**D-31:** Fresh prices fetched from backend before each buy attempt.

**D-32:** Daily cap tracked in backend DB — new endpoint needed for GET/POST daily cap counter.

**D-33:** GET /portfolio/actions-needed (existing) used for cold start / resume.

**D-34:** Leftover players: relist only. Do NOT buy new leftovers — only manage existing positions passively.

**D-35:** Out of coins: skip to relist-only mode. Resume buying when sale generates coins.

**D-36:** Transfer list full: stop buying until space opens.

**D-37:** Multiple cards same player: filter by rarity + OVR rating to disambiguate.

**D-38:** EA session expiry: detect login page redirect or session error DOM elements. Stop automation. User must manually restart after logging back in.

**D-39:** Transfer list pagination: auto-paginate and scan ALL pages.

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

### Deferred Ideas (OUT OF SCOPE)

None — discussion stayed within phase scope.
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| AUTO-01 | Extension searches transfer market for target player and executes Buy Now when BIN is at or below expected buy price | Binary search price discovery pattern (D-06/D-07); direct DOM clicks (D-26); price input keystrokes (D-27) |
| AUTO-02 | Extension skips player if current BIN exceeds backend buy price (price guard) | 5% tolerance guard (D-08); skip-to-next logic (D-11) |
| AUTO-03 | Extension auto-lists purchased cards at the locked OP price from the portfolio | Post-buy list from same page (D-12); locked sell_price from PortfolioSlot (D-13) |
| AUTO-04 | Extension auto-relists expired cards at the same locked OP price they were originally listed at | "Relist All" button DOM click (D-03); locked price does not change |
| AUTO-05 | All DOM interactions use human-like delays with randomized jitter (800-2500ms) | Random uniform jitter helper (D-28); digit-by-digit price input (D-27) |
| AUTO-06 | Extension detects CAPTCHA and stops automation immediately, alerting the user | Action failure heuristic (D-22); immediate stop + alert pattern |
| AUTO-07 | Extension fails loudly on DOM mismatch rather than silently continuing | requireElement() helper pattern; selector name in error message (D-23) |
| AUTO-08 | All selectors centralized in selectors.ts | Already complete; Phase 8 adds automation selectors to existing file |
| UI-02 | User can confirm the portfolio list to start the automated buy/list/relist cycle | "Start Automation" button in confirmed panel state (D-16); AUTOMATION_START message |
| UI-04 | Start/stop automation toggle in overlay panel | Toggle wired to AUTOMATION_START/AUTOMATION_STOP messages; enabledItem gate |
| UI-05 | Status display showing current action, last event, and running/stopped/error state | Status panel in overlay (D-20); activity log (D-21); new AUTOMATION_STATUS message type |
</phase_requirements>

---

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| WXT | 0.20.20 (installed) | Extension build framework | Already in project; proven across phases 6-7 |
| vitest | 4.1.2 (installed) | Unit test runner | Already in project; jsdom environment configured |
| TypeScript | 6.0.2 (installed) | Type safety | Already in project; discriminated unions, assertNever |
| chrome.storage.local (via WXT) | MV3 | State persistence across service worker termination | Already used for enabledItem, portfolioItem, reportedOutcomesItem |
| MutationObserver + polling | Web API | SPA navigation detection, DOM change observation | Already used in content script |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| fakeBrowser (wxt/testing) | WXT 0.20.20 | Mock chrome.* APIs in unit tests | All extension unit tests |
| jsdom | 29.0.1 (installed) | DOM environment in tests | Unit tests for DOM manipulation functions |
| FastAPI + SQLAlchemy | (existing) | Two new backend endpoints | Daily cap counter, fresh price lookup |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Separate automation.ts module | Extending ea-webapp.content.ts directly | Separate module keeps content script manageable — automation is large enough to warrant its own file |
| chrome.runtime.sendMessage for automation commands | Direct state in content script | All backend calls must route through service worker; automation state can live in content script but backend I/O goes through SW |

**Installation:** No new packages needed. All required libraries are already installed.

---

## Architecture Patterns

### Recommended Project Structure
```
extension/
├── src/
│   ├── selectors.ts          # Extended with automation selectors (Wave 0)
│   ├── automation.ts         # NEW: automation state machine + cycle executor
│   ├── messages.ts           # Extended with AUTOMATION_* message types
│   ├── storage.ts            # Extended with automation state items
│   └── overlay/
│       └── panel.ts          # Extended with Start/Stop button + Status panel + activity log
├── entrypoints/
│   ├── ea-webapp.content.ts  # Extended to wire automation module
│   └── background.ts         # Extended with daily cap + fresh price handlers
```

### Pattern 1: Automation State Machine in Content Script
**What:** A finite state machine (FSM) manages the automation cycle. States: IDLE, BUYING, LISTING, SCANNING_TRANSFER_LIST, RELISTING, STOPPED, ERROR. Each state knows the next action and transitions only after DOM confirms success or timeout.
**When to use:** Whenever the cycle needs to be pauseable, resumeable, and debuggable. Prevents race conditions where multiple cycle iterations overlap.
**Example:**
```typescript
// In automation.ts
type AutomationState = 'IDLE' | 'BUYING' | 'LISTING' | 'SCANNING' | 'RELISTING' | 'STOPPED' | 'ERROR';

interface AutomationContext {
  state: AutomationState;
  currentPlayer: ActionNeeded | null;
  buyRetries: number;
  lastEvent: string;
  isRunning: boolean;
}
```

### Pattern 2: requireElement — Loud DOM Failure (AUTO-07)
**What:** A helper function that queries a selector and throws a descriptive error (including the selector name) if the element is missing. Used for every DOM interaction point.
**When to use:** Any DOM querySelector call where the element is expected to exist. Never silently continue if an expected element is absent.
**Example:**
```typescript
// In automation.ts
function requireElement(selector: string, root: Document | Element = document): Element {
  const el = root.querySelector(selector);
  if (!el) {
    throw new AutomationError(`DOM mismatch: element not found for selector "${selector}"`);
  }
  return el;
}

class AutomationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'AutomationError';
  }
}
```

### Pattern 3: Jitter Sleep
**What:** A sleep helper with randomized uniform jitter in the 800-2500ms range. Every single DOM interaction calls jitter() before proceeding. No two consecutive action intervals are identical.
**When to use:** Between every click, every keystroke group, every navigation. Non-negotiable for human-pacing.
**Example:**
```typescript
// In automation.ts
function jitter(minMs = 800, maxMs = 2500): Promise<void> {
  const delay = Math.floor(Math.random() * (maxMs - minMs + 1)) + minMs;
  return new Promise(resolve => setTimeout(resolve, delay));
}
```

### Pattern 4: Digit-by-Digit Price Input (D-27)
**What:** Price input fields are cleared, then each digit is typed with a small delay between each keystroke. More human-like than programmatic value setting. Uses dispatchEvent with KeyboardEvent.
**When to use:** Any price input (BIN search filter, listing price, start price). Never set `.value` directly without dispatching input events.
**Example:**
```typescript
async function typePrice(input: HTMLInputElement, price: number): Promise<void> {
  // Clear first
  input.value = '';
  input.dispatchEvent(new Event('input', { bubbles: true }));
  await jitter(100, 300);

  // Type each digit with small delay
  for (const char of String(price)) {
    input.value += char;
    input.dispatchEvent(new Event('input', { bubbles: true }));
    await jitter(50, 150);
  }
  input.dispatchEvent(new Event('change', { bubbles: true }));
}
```

### Pattern 5: Transfer List Pagination (D-39)
**What:** After initial scan, check for a "next page" button and click through all pages to collect all listed/expired/sold cards. `readTransferList()` from trade-observer.ts is called on each page.
**When to use:** During the scanning phase of the automation cycle — after buys, to detect expired cards for relist and sold cards for rebuy.

### Pattern 6: Message Types for Automation Commands
**What:** New discriminated union variants in ExtensionMessage for automation control and status reporting. All commands flow content script → service worker (for backend relay) or content script handles directly.
**Recommended new message types:**
```typescript
// In messages.ts — new variants to add:
| { type: 'AUTOMATION_START' }
| { type: 'AUTOMATION_START_RESULT'; success: boolean; error?: string }
| { type: 'AUTOMATION_STOP' }
| { type: 'AUTOMATION_STOP_RESULT'; success: boolean }
| { type: 'AUTOMATION_STATUS_REQUEST' }
| { type: 'AUTOMATION_STATUS_RESULT'; state: AutomationStatusData }
| { type: 'AUTOMATION_STATUS_UPDATE'; state: AutomationStatusData }  // push from content script to SW (for badge?)
| { type: 'DAILY_CAP_REQUEST' }
| { type: 'DAILY_CAP_RESULT'; count: number; cap: number; capped: boolean; error?: string }
| { type: 'DAILY_CAP_INCREMENT' }
| { type: 'DAILY_CAP_INCREMENT_RESULT'; success: boolean; capped: boolean }
| { type: 'FRESH_PRICE_REQUEST'; ea_id: number }
| { type: 'FRESH_PRICE_RESULT'; ea_id: number; buy_price: number; sell_price: number; error?: string }
```

### Pattern 7: New Storage Items for Automation State
**What:** Automation runtime state stored in chrome.storage.local to survive service worker termination. The automation loop itself runs in the content script (which lives as long as the tab is open), but status data needs to be accessible to the overlay panel and potentially the service worker.
**Recommended new storage items in storage.ts:**
```typescript
export type AutomationStatus = {
  isRunning: boolean;
  currentAction: string | null;   // e.g., "Buying: Mbappé (searching...)"
  lastEvent: string | null;       // e.g., "Listed Salah at 85,000"
  sessionProfit: number;
  errorMessage: string | null;
};

export const automationStatusItem = storage.defineItem<AutomationStatus | null>(
  'local:automationStatus',
  { fallback: null }
);

export type ActivityLogEntry = {
  timestamp: string;  // ISO
  message: string;
};

export const activityLogItem = storage.defineItem<ActivityLogEntry[]>(
  'local:activityLog',
  { fallback: [] }
);
```

### Pattern 8: Cold Start and Resume (D-18/D-19)
**What:** On cold start, fetch GET /portfolio/actions-needed, then cross-reference with a DOM scan of the transfer list. The union of both sources gives the full picture of what needs doing.
**When to use:** When automation starts for the first time (no prior state), and when it resumes after a stop.
**Example flow:**
1. Call `ACTIONS_NEEDED_REQUEST` → get backend view
2. Call `readTransferList()` → get DOM view
3. Merge: items detected in DOM as `listed` override backend `BUY` actions (already bought); items `expired` in DOM override `RELIST` actions (need relist).

### Pattern 9: CAPTCHA Detection Heuristic (D-22)
**What:** After clicking a buy or list button, the automation waits for an expected DOM change (e.g., confirmation modal, success message, or page navigation). If the expected change does not occur within a timeout, or if a CAPTCHA-like pattern appears (unexpected modal, redirect), treat as CAPTCHA — stop immediately and alert.
**Implementation approach:** After each critical action, poll for expected selector with a 5-second timeout. If timeout triggers without the expected element appearing, raise AutomationError with CAPTCHA flag.

### Pattern 10: Daily Cap Tracking (D-24/D-25)
**What:** The backend tracks a daily transaction count (searches + buys) in a new `daily_cap` table or column. Two new backend endpoints: GET /api/v1/automation/daily-cap (get current count/cap/capped state) and POST /api/v1/automation/daily-cap/increment (add 1 to counter). The counter resets at midnight UTC.
**When cap is hit:** Extension transitions to "relist-only" mode — continues relisting expired cards but skips all search/buy actions.

### Pattern 11: Fresh Price Lookup on Rebuy (D-13/D-14/D-31)
**What:** Before each buy attempt (initial or rebuy after sale), fetch fresh prices from backend. New endpoint: GET /api/v1/portfolio/player-price/{ea_id} returns current buy_price and sell_price from the portfolio slot.
**Why locked prices:** After a successful buy, the sell_price is locked (not refreshed) until that player sells. This is the existing PortfolioSlot.sell_price.

### Anti-Patterns to Avoid
- **Silent querySelector failure:** Never do `const el = document.querySelector(sel); el.click()` without a null check. Use `requireElement()` always.
- **Fixed sleep values:** Never use `await sleep(1000)` — always `await jitter()`. Fixed delays are obvious to bot detection.
- **Programmatic value setting without events:** `input.value = price` alone does not trigger React/Angular change handlers. Must dispatch `input` and `change` events.
- **window.services access:** EA internal API — not used per D-26. Changes without notice and risks faster detection.
- **Parallel buying:** EA rate-limits transfer market; parallel purchases trigger soft bans. Always sequential.
- **Calling backend directly from content script:** Violates the established Chrome CORS constraint. All fetch calls go through service worker messages.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Transfer list DOM reading | Custom reader | `readTransferList()` from trade-observer.ts | Already tested, handles all status cases including time-remaining strings |
| Player name matching | Custom fuzzy match | Existing `endsWith + rating + position` pattern from ea-webapp.content.ts | Already accounts for DOM short names ("Lo Celso") vs full portfolio names |
| Storage persistence | Custom localStorage | `storage.defineItem<T>()` from wxt/utils/storage | Survives service worker termination; type-safe |
| Service worker backend proxy | Direct fetch in content script | Existing message protocol (chrome.runtime.sendMessage) | CORS constraint; already wired in background.ts |
| Actions-needed lookup | Custom endpoint | Existing `GET /api/v1/portfolio/actions-needed` | Already returns BUY/LIST/RELIST/WAIT per player with buy_price, sell_price |
| Trade outcome reporting | New endpoint | Existing `POST /api/v1/trade-records/direct` | Already validates portfolio membership; already wired in background.ts |

**Key insight:** The codebase already has ~70% of the infrastructure needed. The automation engine builds on top of working primitives — don't duplicate them.

---

## Common Pitfalls

### Pitfall 1: EA Results Caching Without Value Change
**What goes wrong:** Searching for a player a second time returns identical cached results even if the market changed.
**Why it happens:** EA's SPA caches search results until a filter value actually changes.
**How to avoid:** Per D-09, change the min BIN/BID value between refreshes (e.g., alternate between 200 and 201). This forces EA to re-query.
**Warning signs:** Always getting exactly the same players/prices on repeat searches.

### Pitfall 2: DOM Selectors Change with EA Updates
**What goes wrong:** EA pushes a Web App update; class names change; automation silently fails or throws.
**Why it happens:** EA's SPA uses generated/minified class names that are not guaranteed stable.
**How to avoid:** All selectors in selectors.ts. The DOM exploration task (Wave 0) must capture real current selectors from a live DevTools session. When a selector breaks, update selectors.ts in one place.
**Warning signs:** `requireElement()` throwing for selectors that were previously working.

### Pitfall 3: Bought-But-Not-Listed State
**What goes wrong:** Automation buys a card then gets stopped (user stops, CAPTCHA, error) before listing. The card sits in the unassigned pile, locking coins.
**Why it happens:** Stop can interrupt between buy and list steps.
**How to avoid:** Per D-17, stop finishes the current action gracefully — complete the in-progress buy+list pair before halting. The cold-start scan (D-19) also detects unassigned cards and lists them before starting new buys.
**Warning signs:** Unassigned pile growing over time; bought_but_not_listed counter non-zero.

### Pitfall 4: Transfer List Pagination Miss
**What goes wrong:** Automation only sees page 1 of the transfer list; expired cards on page 2+ are not relisted; sold cards on page 2+ trigger no rebuy.
**Why it happens:** `readTransferList()` only reads the currently rendered DOM. EA paginates at some threshold.
**How to avoid:** After reading page 1, check for a "next page" selector. If present, click it, wait for DOM update, then read again. Accumulate all items across pages.
**Warning signs:** Known portfolio players not showing up in transfer list scans despite being listed; inconsistent relist behavior.

### Pitfall 5: MV3 Service Worker Termination During Automation
**What goes wrong:** Service worker is terminated mid-cycle; automation state is lost; automation appears to stop for no reason.
**Why it happens:** MV3 service workers are terminated after ~30 seconds of inactivity. Backend calls from the content script go through the service worker, which may terminate.
**How to avoid:** Automation state (isRunning, currentPlayer, retryCount) lives in the content script (tab stays open → content script stays alive). Service worker is only used for backend relay. Persist status to `automationStatusItem` after each action so resume can reconstruct state.
**Warning signs:** Automation stopping silently after ~30 seconds of no user interaction.

### Pitfall 6: Snipe Race Condition on Buy
**What goes wrong:** The card appears in search results at the right price, automation clicks Buy Now, but EA reports "card unavailable" — another user sniped it first.
**Why it happens:** High-demand cards sell in milliseconds. The search result is stale by click time.
**How to avoid:** Per D-10, count this as a retry (up to 3). Re-search immediately after a snipe — do not count the failed attempt against price discovery steps. The retry count tracks buy-attempt failures only.
**Warning signs:** Frequent "card unavailable" errors on high-demand players.

### Pitfall 7: dispatchEvent Without bubbles:true
**What goes wrong:** Input events dispatched to a field do not trigger React/Angular change handlers; price values appear set but EA's own UI state doesn't update.
**Why it happens:** EA Web App likely uses event delegation at a parent level; non-bubbling events don't reach the listener.
**How to avoid:** Always pass `{ bubbles: true }` when constructing Event/KeyboardEvent objects for dispatch.
**Warning signs:** Price field shows the typed value visually, but search/list form does not acknowledge it (search returns unfiltered results, price doesn't stick on listing).

### Pitfall 8: Leftover Players Bought Again
**What goes wrong:** Automation buys new copies of leftover players (not in confirmed portfolio), inflating spend and creating untracked positions.
**Why it happens:** actions-needed endpoint returns RELIST for leftovers; if automation logic misreads this as BUY it will search and buy.
**How to avoid:** Per D-34, check `is_leftover` flag on each action. For leftovers, only RELIST/LIST actions are valid — skip any BUY attempt for a leftover player.
**Warning signs:** Portfolio slot count exceeds confirmed portfolio size; unrecognized players on transfer list.

---

## Code Examples

### requireElement Pattern (AUTO-07)
```typescript
// In automation.ts
class AutomationError extends Error {
  isCaptcha: boolean;
  constructor(message: string, opts: { isCaptcha?: boolean } = {}) {
    super(message);
    this.name = 'AutomationError';
    this.isCaptcha = opts.isCaptcha ?? false;
  }
}

function requireElement<T extends Element = Element>(
  selector: string,
  root: Document | Element = document,
): T {
  const el = root.querySelector<T>(selector);
  if (!el) {
    throw new AutomationError(
      `DOM mismatch: element not found for selector "${selector}". ` +
      `This may indicate an EA Web App update changed the DOM structure.`,
    );
  }
  return el;
}
```

### Jitter Sleep
```typescript
// In automation.ts
function jitter(minMs = 800, maxMs = 2500): Promise<void> {
  const delay = Math.floor(Math.random() * (maxMs - minMs + 1)) + minMs;
  return new Promise(resolve => setTimeout(resolve, delay));
}
```

### Automation State Machine Entry Point
```typescript
// In automation.ts — called from ea-webapp.content.ts on AUTOMATION_START message
export async function runAutomationCycle(
  ctx: ContentScriptContext,
  onStatusUpdate: (status: AutomationStatus) => void,
): Promise<void> {
  // 1. Cold start: fetch actions-needed from backend
  const actionsResponse = await chrome.runtime.sendMessage({ type: 'ACTIONS_NEEDED_REQUEST' });
  // 2. Scan transfer list DOM for current state
  const domItems = readTransferList(document);
  // 3. Merge backend + DOM to build work queue
  // 4. Execute actions in order: LIST first, RELIST, BUY, WAIT
  // 5. Report each outcome via TRADE_REPORT message
}
```

### Binary Search Price Discovery (D-06)
```typescript
// Pseudocode for price discovery in automation.ts
async function findCheapestCard(
  playerName: string,
  rarity: string,
  targetBuyPrice: number,
): Promise<{ price: number; element: Element } | null> {
  const STEP_PCT = 0.10; // 10% step increments for binary search
  let maxBin = targetBuyPrice;

  // Step up if no results found
  for (let attempt = 0; attempt < 10; attempt++) {
    const results = await searchMarket(playerName, rarity, maxBin);
    if (results.length > 0) {
      // Step down to find cheapest tier
      const cheapest = results.sort((a, b) => a.price - b.price)[0];
      // Price guard: skip if cheapest > target * 1.05
      if (cheapest.price > targetBuyPrice * 1.05) return null;
      return cheapest;
    }
    maxBin = Math.round(maxBin * (1 + STEP_PCT));
  }
  return null; // not found
}
```

### New Backend Endpoint: Daily Cap
```python
# In src/server/api/automation.py (new file)
@router.get("/automation/daily-cap")
async def get_daily_cap(request: Request):
    """Return current daily transaction count and cap state."""
    # Query DailyCapRecord for today's date
    # Return: { count: int, cap: int, capped: bool, reset_at: str }

@router.post("/automation/daily-cap/increment")
async def increment_daily_cap(request: Request):
    """Increment daily transaction counter by 1. Returns new count and capped state."""
    # Upsert DailyCapRecord for today
    # Return: { count: int, cap: int, capped: bool }
```

### New Backend Endpoint: Fresh Player Price
```python
# In src/server/api/portfolio.py (extend existing router)
@router.get("/portfolio/player-price/{ea_id}")
async def get_player_price(ea_id: int, request: Request):
    """Return current buy_price and sell_price for a portfolio slot."""
    # Query PortfolioSlot by ea_id
    # Return: { ea_id: int, buy_price: int, sell_price: int }
    # 404 if not in portfolio
```

---

## Environment Availability

Step 2.6: SKIPPED — Phase 8 is purely extension TypeScript and Python backend changes. No new external tools, runtimes, or services are required beyond those already in use (Node.js for WXT, Python for the backend).

---

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | vitest 4.1.2 |
| Config file | `extension/vitest.config.ts` |
| Quick run command | `cd extension && npx vitest run` |
| Full suite command | `cd extension && npx vitest run` |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|--------------|
| AUTO-07 | `requireElement()` throws with selector name on missing element | unit | `cd extension && npx vitest run tests/automation.test.ts` | ❌ Wave 0 |
| AUTO-05 | `jitter()` returns delay in 800-2500ms range | unit | `cd extension && npx vitest run tests/automation.test.ts` | ❌ Wave 0 |
| AUTO-01 | Price guard skips player when BIN > target * 1.05 | unit | `cd extension && npx vitest run tests/automation.test.ts` | ❌ Wave 0 |
| AUTO-04 | Relist-only mode when leftover flag is set | unit | `cd extension && npx vitest run tests/automation.test.ts` | ❌ Wave 0 |
| UI-04/UI-05 | Start/Stop button appears in confirmed panel state; status panel renders running/stopped/error badge | unit | `cd extension && npx vitest run tests/overlay.test.ts` | ✅ (file exists, new tests needed) |
| AUTO-06/AUTO-07 | CAPTCHA heuristic stop + alert; DOM mismatch stop + alert | unit | `cd extension && npx vitest run tests/automation.test.ts` | ❌ Wave 0 |
| D-32 | Daily cap backend endpoint increments and resets | integration | `pytest tests/integration/ -x -k "daily_cap"` | ❌ Wave 0 |
| D-31 | Fresh price endpoint returns 404 for non-portfolio players | integration | `pytest tests/integration/ -x -k "player_price"` | ❌ Wave 0 |

**Note:** AUTO-02, AUTO-03 verified implicitly through price guard unit test and integration test of the full buy→list→report cycle. AUTO-08 is already complete (selectors.ts). UI-02 (confirm → start button) is behavioral and verified by the overlay unit test.

### Sampling Rate
- **Per task commit:** `cd extension && npx vitest run`
- **Per wave merge:** `cd extension && npx vitest run` (full suite)
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `extension/tests/automation.test.ts` — covers AUTO-01, AUTO-05, AUTO-06, AUTO-07, D-34 (leftover guard), jitter range, requireElement, price guard, CAPTCHA heuristic
- [ ] `extension/src/automation.ts` — the automation module itself (stub needed before tests can import)
- [ ] `src/server/api/automation.py` — new backend file for daily cap endpoints
- [ ] New PortfolioSlot price endpoint in `src/server/api/portfolio.py`

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| window.services (EA internal API) | Direct DOM clicks + dispatchEvent | Phase 8 decision (D-26) | No dependency on undocumented API; more stable |
| Per-card relist (click each card) | Relist All button (D-03) | Phase 8 decision | Single click relists all expired; simpler automation |
| Polling action queue from backend | Extension-driven local queue + backend reporting | Phase 8 decision (D-01) | Reduces backend round-trips; extension controls timing |

**Deprecated/outdated:**
- `lastActionItem` + `maybePoll()` pattern in background.ts: The alarm-based polling was a Phase 6 placeholder for Phase 8 automation. Phase 8 replaces this with direct automation control from the content script. `maybePoll()` can be kept for backward compatibility but the automation system does not use it for cycle control.

---

## Open Questions

1. **EA Transfer List Pagination Selector**
   - What we know: EA renders paginated transfer list; pagination UI exists
   - What's unclear: Exact selector for the "next page" button or page count indicator
   - Recommendation: Document during DOM exploration task (Wave 0, Task 1)

2. **EA Search Page Selectors (search form, results list, Buy Now button)**
   - What we know: EA Transfer Market search has name input, rarity dropdown, BIN min/max inputs, Buy Now button
   - What's unclear: All specific class/aria selectors for FC26 Web App
   - Recommendation: Document during DOM exploration task (Wave 0, Task 1) — this is the main selector blocker per STATE.md

3. **EA Post-Buy Listing UI (list from same page)**
   - What we know: D-12 says card can be listed from the same page after buy without navigating to unassigned pile
   - What's unclear: Exact DOM flow — does a modal appear? Does the card automatically move to an inline listing form?
   - Recommendation: Document during DOM exploration task — requires purchasing a cheap card to observe the post-buy DOM state

4. **EA Session Expiry DOM Pattern**
   - What we know: EA redirects to login page or shows session error on expiry (D-38)
   - What's unclear: Whether it's a full page redirect or in-SPA modal; specific selector to detect it
   - Recommendation: Document during DOM exploration task. As fallback: detect by absence of expected page content after a navigation attempt.

5. **Daily Cap Reset Time**
   - What we know: EA's cap resets at some cadence (assumed daily); D-25 sets default at 500
   - What's unclear: Whether EA resets at midnight UTC or local time; whether the cap is a hard block or a soft-ban trigger
   - Recommendation: Implement midnight UTC reset in backend. Monitor empirically once automation runs.

6. **dispatchEvent Compatibility with EA's Framework**
   - What we know: EA Web App is a SPA — likely React or custom framework; input events must be dispatched correctly (D-27)
   - What's unclear: Whether EA uses synthetic event system that ignores native `dispatchEvent`, or needs additional event types (e.g., InputEvent with inputType, or React's native event simulation)
   - Recommendation: During DOM exploration task, test digit-by-digit input dispatch on a real field and verify the value is accepted by EA's form logic.

---

## Project Constraints (from CLAUDE.md)

- **Data source**: fut.gg API only — no FUTBIN, no EA API direct access for data
- **Tech stack**: Python backend (keep existing scoring), TypeScript for Chrome extension
- **Storage**: SQLite/PostgreSQL for backend, chrome.storage.local for extension
- **No EA internal API (window.services)**: Direct DOM clicks only (D-26 confirms this)
- **All backend calls route through service worker** — content scripts never call backend directly (Chrome CORS constraint)
- **Rate limiting**: Respect EA rate limits; no parallel buying; human-paced timing
- **Python conventions**: snake_case functions, PascalCase classes, UPPER_CASE constants, absolute imports from repo root
- **TypeScript conventions**: discriminated union messages, assertNever exhaustiveness, all selectors in selectors.ts
- **Testing**: No mocks of real bugs — tests must reflect real behavior; no weakening assertions
- **GSD workflow**: All changes through GSD commands; direct repo edits require explicit user bypass

---

## Sources

### Primary (HIGH confidence)
- Existing codebase — extension/src/selectors.ts, trade-observer.ts, messages.ts, storage.ts, overlay/panel.ts, entrypoints/background.ts, entrypoints/ea-webapp.content.ts (direct read, full context)
- .planning/phases/08-dom-automation-layer/08-CONTEXT.md — all locked decisions (D-01 through D-39)
- .planning/REQUIREMENTS.md — AUTO-01 through AUTO-08, UI-02, UI-04, UI-05 acceptance criteria
- .planning/STATE.md — accumulated decisions from prior phases, known blockers

### Secondary (MEDIUM confidence)
- Chrome MV3 content script lifecycle patterns — established in Phase 6 research and proven across phases 6-7 implementation
- WXT content script context (ctx) API — proven patterns from existing ea-webapp.content.ts
- FastAPI SQLAlchemy endpoint patterns — established from prior backend phases (5, 9, 9.1, 10)

### Tertiary (LOW confidence)
- EA Web App DOM selectors for FC26 automation (search form, buy button, listing form, sidebar nav, relist-all button) — REQUIRES live DevTools verification; no existing source; this is the primary blocker per STATE.md
- EA daily transaction cap behavior — undocumented; 500/day default is a conservative estimate per STATE.md

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all libraries already installed and proven across prior phases
- Architecture patterns: HIGH — all patterns derive from existing codebase conventions and CONTEXT.md locked decisions
- EA DOM selectors: LOW — must be discovered via live DevTools inspection; no reliable source exists
- Backend endpoints: HIGH — patterns are identical to existing portfolio and trade-record endpoints
- Pitfalls: HIGH — derived from existing code, CONTEXT.md specifics, and general Chrome extension DOM automation knowledge

**Research date:** 2026-03-30
**Valid until:** 2026-04-30 (EA Web App selectors may change with any EA patch — re-verify before each automation update)
