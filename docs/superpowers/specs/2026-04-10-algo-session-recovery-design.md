# Algo Session Recovery — Design Spec

**Date**: 2026-04-10
**Status**: Draft

## Problem

The algo trading automation loop polls for signals every 30-60 seconds. Buy/sell signals are infrequent. The EA FC web app session times out after a few hours of inactivity. When a signal arrives, EA API calls fail because the session is dead. There is no mechanism to detect this or recover automatically.

## Solution

Introduce a **master-worker architecture** where the background service worker (master) manages the EA web app tab (worker). The master monitors session health, detects failures, and performs full recovery including automated login.

## Architecture

### Master-Worker Model

```
Master (background service worker)
  ├── Spawns worker tab (EA web app)
  ├── Monitors health (3 detection methods)
  ├── Performs recovery (navigate + auto-login)
  └── Restarts algo loop after recovery

Worker (main world content script)
  ├── Runs algo automation loop
  ├── Responds to health checks + maintenance
  └── Reports consecutive failures upward to master
```

### Master State Machine

```
IDLE → SPAWNING → MONITORING → RECOVERING → SPAWNING
                      ↑                         |
                      └─────────────────────────┘
```

- **IDLE**: Algo not active, master does nothing
- **SPAWNING**: Opening/navigating the EA tab, waiting for content script to inject and PONG
- **MONITORING**: Worker is alive, master watches health via 3 methods
- **RECOVERING**: Session death detected, master navigating to web app + auto-login

Master state persists in `chrome.storage.local` so it survives MV3 service worker restarts.

## Health Detection (3 Methods)

### Method 1: Tab Events (instant)

- `chrome.tabs.onRemoved` — tab was closed
- `chrome.tabs.onUpdated` — URL changed away from `*/web-app/*` (redirect to login page)
- These fire immediately, no polling needed. Trigger recovery instantly.

### Method 2: Worker Reports Failure (during signal execution)

- Algo loop tracks consecutive EA call failures (same pattern as OP sell loop's `consecutiveFailures >= 3`)
- After 3 consecutive failures, sends `ALGO_SESSION_DEAD` message to background worker via `chrome.runtime.sendMessage`
- Master transitions from MONITORING → RECOVERING

### Method 3: Periodic Health Check + Maintenance (during idle)

- Master runs a `chrome.alarms` alarm every 5 minutes while in MONITORING state
- Sends `ALGO_HEALTH_CHECK` message to the EA tab via `chrome.tabs.sendMessage` → isolated world content script relays to main world via the existing postMessage bridge
- Worker (main world) receives it and:
  1. Calls `getCoins()` as a lightweight EA session test
  2. If healthy, checks transfer list for expired items and relists:
     - **Algo positions**: Price-adjusted relist — discover current lowest BIN, adjust price, relist individually (same logic as `algo-sell-cycle.ts` `discoverLowestBin()`)
     - **Everything else**: Call `relistExpiredAuctions()` — relists all at previous prices, one API call
     - **Order**: Algo position relists first (price-adjusted), then relist-all for the rest (prevents relist-all from relisting algo positions at stale prices)
  3. Responds `{ healthy: true/false, relisted_algo: number, relisted_other: number }`
- If no response (content script dead) or `healthy: false` → recovery
- **False positive protection**: A single failed health check retries once after 10 seconds before triggering recovery. Tab events and 3-consecutive-failures are definitive — no retry needed.

## Recovery Flow

### Step 1: Find or Create Tab

- Query `chrome.tabs.query` for any tab matching `https://www.ea.com/*`
- If found → reuse it, navigate to `https://www.ea.com/ea-sports-fc/ultimate-team/web-app/`
- If not found → `chrome.tabs.create` with the web app URL

### Step 2: Wait for Page Load

- Listen on `chrome.tabs.onUpdated` for `status === 'complete'`
- Timeout after 30 seconds → retry step 1

### Step 3: Detect Login Page vs Web App

After page loads, check the tab URL:
- If it matches `*/web-app/*` → session cookies were still valid, skip to step 5
- If it's a login/auth page → proceed to step 4
- If no credentials are configured AND page loaded into web app → continue normally (no login needed)

### Step 4: Auto-Login

- Read credentials from `chrome.storage.local` (`algoCredentials` key)
- If no credentials configured → set master state to `WAITING_FOR_LOGIN`, log "Session expired — please log in manually." Master polls with PING until user logs in manually, then continues to step 5.
- If credentials available:
  1. Use `chrome.scripting.executeScript` to inject a login script into the tab (content scripts aren't matched on the login page URL)
  2. The injected script finds the email and password input fields, fills them, and submits the form
  3. Wait for navigation back to the web app URL via `chrome.tabs.onUpdated`
  4. If login fails (stays on login page after 15 seconds) → set master state to ERROR, log it, retry after 5 minutes
- **Login page selectors**: Will need to be determined by inspecting the actual EA login page. The injected script should find inputs by type (`email`, `password`) or common selectors and fill them.

### Step 5: Wait for Worker Ready

- Content scripts auto-inject when the web app URL loads (WXT `matches` pattern)
- Master polls with PING every 2 seconds via `chrome.tabs.sendMessage`
- Once PONG received → send `algo-start` command to the main world content script
- Algo loop resumes from fresh state (portfolio/positions reconstructed from DB by `algo_runner.py`)

### Retry Policy

If the full recovery flow fails 3 times in a row, master goes to ERROR state and stops retrying. Logs the issue — user needs to intervene manually.

## Credentials Storage & Configuration

### Storage

`chrome.storage.local` via WXT's `storage.defineItem`:

```typescript
interface AlgoCredentials {
  email: string;
  password: string;
}

export const algoCredentialsItem = storage.defineItem<AlgoCredentials | null>(
  'local:algoCredentials',
  { fallback: null },
);
```

### Security Model

- Credentials never leave the browser — not sent to backend server, not logged, not transmitted over network
- `chrome.storage.local` is scoped to the extension ID — only this extension can read it
- The injected login script reads from storage, fills the form, and discards — no intermediate persistence
- For users/clients: "Your EA credentials are stored locally on your device and are never transmitted to our servers"

### Configuration UI

Add a credentials section to the overlay panel's Algo tab:
- Email input field
- Password input field (type="password", masked)
- Save button
- Status indicator: "Credentials saved" / "Not configured"
- Positioned above the Start/Stop Algo buttons

## Message Types

New messages added to `ExtensionMessage` union:

```typescript
// Worker → Master: session is dead (3 consecutive failures)
{ type: 'ALGO_SESSION_DEAD' }

// Master → Worker (via chrome.tabs.sendMessage): health check request
{ type: 'ALGO_HEALTH_CHECK' }

// Worker → Master: health check response
{ type: 'ALGO_HEALTH_CHECK_RESULT', healthy: boolean, relisted_algo: number, relisted_other: number }
```

## Master State Storage

```typescript
interface AlgoMasterState {
  status: 'IDLE' | 'SPAWNING' | 'MONITORING' | 'RECOVERING' | 'WAITING_FOR_LOGIN' | 'ERROR';
  tabId: number | null;        // tracked EA tab
  recoveryAttempts: number;    // consecutive recovery failures (reset on success)
  lastHealthCheck: string | null;  // ISO timestamp
  errorMessage: string | null;
}

export const algoMasterStateItem = storage.defineItem<AlgoMasterState>(
  'local:algoMasterState',
  { fallback: { status: 'IDLE', tabId: null, recoveryAttempts: 0, lastHealthCheck: null, errorMessage: null } },
);
```

## File Changes

### Modified Files

| File | Changes |
|------|---------|
| `extension/wxt.config.ts` | Add `scripting` permission |
| `extension/entrypoints/background.ts` | Wire up master (import `algo-master.ts`), register tab event listeners and alarm handler, route new message types |
| `extension/src/algo-automation-loop.ts` | Add consecutive failure tracking, send `ALGO_SESSION_DEAD` on 3 failures |
| `extension/entrypoints/ea-webapp-main.content.ts` | Handle `ALGO_HEALTH_CHECK` — call `getCoins()`, relist expired algo positions (price-adjusted) + relist-all for others, respond with health status |
| `extension/src/messages.ts` | Add `ALGO_SESSION_DEAD`, `ALGO_HEALTH_CHECK`, `ALGO_HEALTH_CHECK_RESULT` message types |
| `extension/src/storage.ts` | Add `algoCredentialsItem` and `algoMasterStateItem` storage definitions |
| `extension/src/overlay/panel.ts` | Add credentials form to Algo tab (email, password, save button, status indicator) |
| `extension/entrypoints/ea-webapp.content.ts` | Route new message types in exhaustive switch |

### New Files

| File | Purpose |
|------|---------|
| `extension/src/algo-master.ts` | Master state machine: health check orchestration, recovery flow, tab management, login injection. Extracted to keep `background.ts` focused. |

### Unchanged

- Backend/server — no changes needed, all recovery is extension-side
- `extension/src/algo-buy-cycle.ts` — already returns error outcomes cleanly
- `extension/src/algo-sell-cycle.ts` — already returns error outcomes cleanly
- `extension/src/algo-transfer-list-sweep.ts` — unchanged

## Edge Cases

- **MV3 worker restart**: Master state persisted in storage. On wake, master reads state and resumes from where it was (e.g., if MONITORING, re-register alarm; if RECOVERING, continue recovery flow).
- **User closes and reopens EA tab manually**: `onRemoved` fires, master detects tab gone, respawns it.
- **Multiple EA tabs open**: Master tracks one `tabId`. `chrome.tabs.query` picks the first match. Other tabs are ignored.
- **Login page changes**: If EA changes their login form selectors, the injected script fails. Master retries 3 times then goes to ERROR. User logs in manually — master detects web app URL and resumes.
- **Algo stopped during recovery**: If user stops algo while master is recovering, master transitions to IDLE and aborts recovery.
- **No credentials + login page**: Master sets `WAITING_FOR_LOGIN`, logs message. Polls until user logs in manually. Resumes normally once web app loads.
- **No credentials + cookies valid**: Page loads directly into web app. Master skips login entirely, proceeds to step 5.
