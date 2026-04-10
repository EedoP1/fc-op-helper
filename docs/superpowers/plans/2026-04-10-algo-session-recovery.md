# Algo Session Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a master-worker session recovery system so the algo trading loop automatically detects dead EA sessions, reopens the web app, logs in, and resumes trading.

**Architecture:** The background service worker (master) monitors the EA web app tab (worker) via tab events, worker failure reports, and periodic health checks. On session death, the master navigates/creates the EA tab, auto-fills login credentials via `chrome.scripting.executeScript`, waits for the content script to re-inject, then restarts the algo loop.

**Tech Stack:** Chrome Extension MV3, WXT framework, TypeScript, `chrome.tabs`, `chrome.scripting`, `chrome.alarms`, `chrome.storage.local`

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `extension/src/messages.ts` | Modify | Add 3 new message types to `ExtensionMessage` union |
| `extension/src/storage.ts` | Modify | Add `algoCredentialsItem` and `algoMasterStateItem` storage definitions |
| `extension/wxt.config.ts` | Modify | Add `scripting` permission |
| `extension/src/algo-master.ts` | Create | Master state machine: health monitoring, recovery flow, login injection |
| `extension/src/algo-automation-loop.ts` | Modify | Add consecutive failure tracking, send `ALGO_SESSION_DEAD` |
| `extension/entrypoints/ea-webapp-main.content.ts` | Modify | Handle `ALGO_HEALTH_CHECK` command — session test + relist maintenance |
| `extension/src/ea-bridge.ts` | Modify | Add `health-check` to `AutomationCommand` union |
| `extension/entrypoints/ea-webapp.content.ts` | Modify | Route new message types + relay health check to main world |
| `extension/entrypoints/background.ts` | Modify | Wire up algo-master, register tab/alarm listeners, route new messages |
| `extension/src/overlay/panel.ts` | Modify | Add credentials form to Algo tab |

---

### Task 1: Add New Message Types and Storage Definitions

**Files:**
- Modify: `extension/src/messages.ts:107-160`
- Modify: `extension/src/storage.ts:102-116`
- Modify: `extension/wxt.config.ts:1-14`

- [ ] **Step 1: Add new message types to `ExtensionMessage` union**

In `extension/src/messages.ts`, add these 3 new variants to the `ExtensionMessage` union, before the closing semicolon at line 160:

```typescript
  // Algo session recovery (master ↔ worker)
  | { type: 'ALGO_SESSION_DEAD' }
  | { type: 'ALGO_HEALTH_CHECK' }
  | { type: 'ALGO_HEALTH_CHECK_RESULT'; healthy: boolean; relisted_algo: number; relisted_other: number }
```

- [ ] **Step 2: Add storage definitions**

In `extension/src/storage.ts`, add after the `activityLogItem` definition (after line 115):

```typescript
/**
 * EA login credentials for auto-recovery.
 * Stored locally in the browser — never sent to the backend server.
 */
export type AlgoCredentials = {
  email: string;
  password: string;
};

export const algoCredentialsItem = storage.defineItem<AlgoCredentials | null>(
  'local:algoCredentials',
  { fallback: null },
);

/**
 * Master state machine for algo session management.
 * Persisted so the background service worker can resume after MV3 restarts.
 */
export type AlgoMasterStatus = 'IDLE' | 'SPAWNING' | 'MONITORING' | 'RECOVERING' | 'WAITING_FOR_LOGIN' | 'ERROR';

export type AlgoMasterState = {
  status: AlgoMasterStatus;
  tabId: number | null;
  recoveryAttempts: number;
  lastHealthCheck: string | null;
  errorMessage: string | null;
};

export const algoMasterStateItem = storage.defineItem<AlgoMasterState>(
  'local:algoMasterState',
  {
    fallback: {
      status: 'IDLE',
      tabId: null,
      recoveryAttempts: 0,
      lastHealthCheck: null,
      errorMessage: null,
    },
  },
);
```

- [ ] **Step 3: Add `scripting` permission to manifest**

In `extension/wxt.config.ts`, add `'scripting'` to the permissions array:

```typescript
permissions: ['alarms', 'storage', 'tabs', 'scripting'],
```

- [ ] **Step 4: Verify TypeScript compiles**

Run: `cd extension && npx tsc --noEmit`
Expected: No new errors (existing errors may be present, but no new ones from our changes)

- [ ] **Step 5: Commit**

```
git add extension/src/messages.ts extension/src/storage.ts extension/wxt.config.ts
git commit -m "feat(ext): add session recovery message types and storage definitions"
```

---

### Task 2: Add Consecutive Failure Tracking to Algo Loop

**Files:**
- Modify: `extension/src/algo-automation-loop.ts:28-233`

- [ ] **Step 1: Add failure tracking and session-dead reporting**

In `extension/src/algo-automation-loop.ts`, add a failure counter and threshold constant at the top of `runAlgoAutomationLoop`, right after the `stopped` lambda (after line 33):

```typescript
  let consecutiveFailures = 0;
  const FAILURE_THRESHOLD = 3;
```

- [ ] **Step 2: Track failures in BUY phase**

In the BUY branch (around line 112), after `executeAlgoBuyCycle` returns, add failure tracking. Replace the block from line 114 to line 131:

```typescript
          if (result.outcome === 'bought') {
            consecutiveFailures = 0;
            totalBought += result.quantity;
            lastPrice = result.buyPrice;
            lastItemId = result.itemId;
            await engine.setLastEvent(
              `Bought ${algoSignal.player_name} for ${result.buyPrice.toLocaleString()}`,
            );
          } else if (result.outcome === 'skipped') {
            // Skipped is not a failure — item not found, price guard, etc.
            consecutiveFailures = 0;
            await engine.setLastEvent(
              `Skipped ${algoSignal.player_name}: ${result.reason}`,
            );
            break;
          } else {
            consecutiveFailures++;
            await engine.setLastEvent(
              `Error buying ${algoSignal.player_name}: ${result.reason}`,
            );
            if (consecutiveFailures >= FAILURE_THRESHOLD) {
              await engine.log(`${consecutiveFailures} consecutive failures — reporting session dead`);
              try {
                await sendMessage({ type: 'ALGO_SESSION_DEAD' } satisfies ExtensionMessage);
              } catch { /* background worker may be dead too */ }
              await engine.setError('Session expired — recovery in progress');
              return;
            }
            break;
          }
```

- [ ] **Step 3: Track failures in SELL phase**

In the SELL branch (around line 184), after `executeAlgoSellCycle` returns, add the same pattern. Replace the block from line 184 to line 200:

```typescript
          if (result.outcome === 'listed') {
            consecutiveFailures = 0;
            totalListed += result.quantity;
            lastPrice = result.sellPrice;
            await engine.setLastEvent(
              `Listed ${algoSignal.player_name} for ${result.sellPrice.toLocaleString()}`,
            );
          } else if (result.outcome === 'skipped') {
            consecutiveFailures = 0;
            await engine.setLastEvent(
              `Skipped sell ${algoSignal.player_name}: ${result.reason}`,
            );
            break;
          } else {
            consecutiveFailures++;
            await engine.setLastEvent(
              `Error selling ${algoSignal.player_name}: ${result.reason}`,
            );
            if (consecutiveFailures >= FAILURE_THRESHOLD) {
              await engine.log(`${consecutiveFailures} consecutive failures — reporting session dead`);
              try {
                await sendMessage({ type: 'ALGO_SESSION_DEAD' } satisfies ExtensionMessage);
              } catch { /* background worker may be dead too */ }
              await engine.setError('Session expired — recovery in progress');
              return;
            }
            break;
          }
```

- [ ] **Step 4: Add import for ExtensionMessage type**

At the top of `algo-automation-loop.ts`, the import already includes `ExtensionMessage` on line 20. Verify it's there — if not, add it.

- [ ] **Step 5: Verify TypeScript compiles**

Run: `cd extension && npx tsc --noEmit`
Expected: No new errors

- [ ] **Step 6: Commit**

```
git add extension/src/algo-automation-loop.ts
git commit -m "feat(ext): add consecutive failure tracking to algo loop for session recovery"
```

---

### Task 3: Add Health Check Command to Bridge

**Files:**
- Modify: `extension/src/ea-bridge.ts:44-49,264-265`

- [ ] **Step 1: Add `health-check` to the AutomationCommand union**

In `extension/src/ea-bridge.ts`, update the `AutomationCommand` interface's `command` field (line 49) to include the new command:

```typescript
  command: 'start' | 'stop' | 'getStatus' | 'algo-start' | 'algo-stop' | 'algo-getStatus' | 'algo-health-check';
```

- [ ] **Step 2: Update sendAutomationCommand's type signature**

In `extension/src/ea-bridge.ts`, update the `sendAutomationCommand` function signature (line 265) to match:

```typescript
export function sendAutomationCommand(
  command: 'start' | 'stop' | 'getStatus' | 'algo-start' | 'algo-stop' | 'algo-getStatus' | 'algo-health-check',
): Promise<any> {
```

- [ ] **Step 3: Commit**

```
git add extension/src/ea-bridge.ts
git commit -m "feat(ext): add algo-health-check to bridge command union"
```

---

### Task 4: Handle Health Check in Main World Content Script

**Files:**
- Modify: `extension/entrypoints/ea-webapp-main.content.ts:58-102`

The main world content script is where EA globals live. The health check needs to:
1. Call `getCoins()` to test if the session is alive
2. If alive, check for expired algo positions and relist them (price-adjusted)
3. Then call `relistAll()` for non-algo expired items
4. Return the health status

- [ ] **Step 1: Add imports for EA services**

At the top of `extension/entrypoints/ea-webapp-main.content.ts`, add imports after line 25:

```typescript
import {
  getCoins,
  getTransferList,
  refreshAuctions,
  searchMarket,
  buildCriteria,
  listItem,
  relistAll,
  roundToNearestStep,
  getBeforeStepValue,
  MAX_PRICE,
} from '../src/ea-services';
import { jitter } from '../src/automation';
```

- [ ] **Step 2: Add the health check handler function**

Add this function before the `defineContentScript` call (before line 29):

```typescript
const RATE_LIMIT_ERROR_CODE = 460;
const EA_PAGE_SIZE = 20;

/**
 * Discover current lowest BIN for a player via transfer market search.
 * Same narrowing algorithm used by algo-sell-cycle and algo-transfer-list-sweep.
 */
async function discoverLowestBinForRelist(
  ea_id: number,
  fallbackPrice: number,
): Promise<number> {
  const MAX_NARROW_STEPS = 6;
  let currentMax = MAX_PRICE;
  let lastCheapest = fallbackPrice;

  for (let step = 0; step < MAX_NARROW_STEPS; step++) {
    const criteria = buildCriteria(ea_id, currentMax);
    if (step > 0) await jitter(1000, 2000);
    const result = await searchMarket(criteria);

    if (!result.success) {
      if (result.error === RATE_LIMIT_ERROR_CODE) {
        await jitter(4000, 8000);
        step--;
        continue;
      }
      return lastCheapest;
    }

    if (result.items.length === 0) return lastCheapest;

    let lowestBin = Infinity;
    for (const item of result.items) {
      const bin = item.getAuctionData().buyNowPrice;
      if (bin < lowestBin) lowestBin = bin;
    }
    lastCheapest = lowestBin;

    if (result.items.length < EA_PAGE_SIZE) return lowestBin;

    if (currentMax === lowestBin) {
      const below = getBeforeStepValue(lowestBin);
      if (below <= 0) return lowestBin;
      currentMax = below;
    } else {
      currentMax = lowestBin;
    }
  }

  return lastCheapest;
}

/**
 * Run the health check + maintenance routine.
 *
 * 1. Test session via getCoins()
 * 2. If alive, relist expired algo positions at current market price
 * 3. Then relistAll() for non-algo expired items
 *
 * @param sendMessage  Bridged sendMessage for backend communication
 * @returns Health check result
 */
async function runHealthCheck(
  sendMessage: (msg: any) => Promise<any>,
): Promise<{ healthy: boolean; relisted_algo: number; relisted_other: number }> {
  // Step 1: Session test
  try {
    getCoins();
  } catch {
    return { healthy: false, relisted_algo: 0, relisted_other: 0 };
  }

  let relistedAlgo = 0;
  let relistedOther = 0;

  // Step 2: Get transfer list and find expired items
  const { groups, success } = await getTransferList();
  if (!success || groups.expired.length === 0) {
    return { healthy: true, relisted_algo: 0, relisted_other: 0 };
  }

  // Step 3: Get algo positions from backend to identify which expired items are algo
  let algoEaIds = new Set<number>();
  try {
    const statusRes = await sendMessage({ type: 'ALGO_STATUS_REQUEST' });
    if (statusRes?.type === 'ALGO_STATUS_RESULT' && statusRes.data) {
      for (const pos of statusRes.data.positions) {
        algoEaIds.add(pos.ea_id);
      }
    }
  } catch {
    // Can't reach backend — skip algo-specific relist, just do relist-all
    const relistResult = await relistAll();
    if (relistResult.success) {
      relistedOther = groups.expired.length;
    }
    return { healthy: true, relisted_algo: 0, relisted_other: relistedOther };
  }

  // Step 4: Relist expired algo positions with price adjustment
  const algoExpired = groups.expired.filter(item => algoEaIds.has(item.definitionId));
  const nonAlgoExpired = groups.expired.filter(item => !algoEaIds.has(item.definitionId));

  // Group algo items by ea_id for efficient price discovery (one search per player)
  const algoByEaId = new Map<number, typeof algoExpired>();
  for (const item of algoExpired) {
    const list = algoByEaId.get(item.definitionId) ?? [];
    list.push(item);
    algoByEaId.set(item.definitionId, list);
  }

  for (const [ea_id, items] of algoByEaId) {
    // Discover current lowest BIN
    const fallback = items[0].getAuctionData().buyNowPrice || 10000;
    await jitter(1000, 2000);
    const lowestBin = await discoverLowestBinForRelist(ea_id, fallback);
    const listBin = roundToNearestStep(getBeforeStepValue(lowestBin));
    const listStart = roundToNearestStep(getBeforeStepValue(listBin));

    for (const expItem of items) {
      await jitter(1000, 2000);
      const listResult = await listItem(expItem, listStart, listBin);
      if (listResult.success) {
        relistedAlgo++;
      } else {
        console.warn(`[health-check] Relist failed for algo defId=${expItem.definitionId} (error ${listResult.error})`);
      }
    }

    // Report relist to backend
    if (relistedAlgo > 0) {
      try {
        await sendMessage({
          type: 'ALGO_POSITION_RELIST',
          ea_id,
          price: listBin,
          quantity: items.length,
        });
      } catch {
        console.warn(`[health-check] ALGO_POSITION_RELIST report failed for ea_id=${ea_id}`);
      }
    }
  }

  // Step 5: Relist all non-algo expired items at their previous prices
  if (nonAlgoExpired.length > 0) {
    await jitter(1000, 2000);
    const relistResult = await relistAll();
    if (relistResult.success) {
      relistedOther = nonAlgoExpired.length;
    }
  }

  return { healthy: true, relisted_algo: relistedAlgo, relisted_other: relistedOther };
}
```

- [ ] **Step 3: Add the health check command handler to the switch statement**

In `extension/entrypoints/ea-webapp-main.content.ts`, inside the `switch (command.command)` block (around line 60-102), add a new case after `algo-getStatus`:

```typescript
          case 'algo-health-check': {
            const healthResult = await runHealthCheck(bridgedSendMessage);
            response.result = healthResult;
            break;
          }
```

- [ ] **Step 4: Verify TypeScript compiles**

Run: `cd extension && npx tsc --noEmit`
Expected: No new errors

- [ ] **Step 5: Commit**

```
git add extension/entrypoints/ea-webapp-main.content.ts
git commit -m "feat(ext): add health check handler with session test and relist maintenance"
```

---

### Task 5: Route New Messages in Isolated World Content Script

**Files:**
- Modify: `extension/entrypoints/ea-webapp.content.ts:128-143`

- [ ] **Step 1: Add new message types to the exhaustive switch**

In `extension/entrypoints/ea-webapp.content.ts`, find the block handling algo messages (lines 128-143). Add the 3 new types. Replace lines 128-143:

```typescript
        case 'ALGO_START':
        case 'ALGO_STOP':
        case 'ALGO_STATUS_REQUEST':
        case 'ALGO_SIGNAL_REQUEST':
        case 'ALGO_SIGNAL_COMPLETE':
        case 'ALGO_SESSION_DEAD':
          return false;
        case 'ALGO_START_RESULT':
        case 'ALGO_STOP_RESULT':
        case 'ALGO_STATUS_RESULT':
        case 'ALGO_SIGNAL_RESULT':
        case 'ALGO_SIGNAL_COMPLETE_RESULT':
        case 'ALGO_POSITION_SOLD':
        case 'ALGO_POSITION_SOLD_RESULT':
        case 'ALGO_POSITION_RELIST':
        case 'ALGO_POSITION_RELIST_RESULT':
        case 'ALGO_HEALTH_CHECK':
        case 'ALGO_HEALTH_CHECK_RESULT':
          return false;
```

- [ ] **Step 2: Add health check relay handler**

The `ALGO_HEALTH_CHECK` message arrives from the background worker via `chrome.tabs.sendMessage` → isolated world. It needs to be relayed to the main world via the automation command bridge. Update the `ALGO_HEALTH_CHECK` case to actually handle it instead of just returning false.

Replace the `case 'ALGO_HEALTH_CHECK':` line in the switch with:

```typescript
        case 'ALGO_HEALTH_CHECK':
          // Relay to main world via bridge, return result to background worker
          sendAutomationCommand('algo-health-check')
            .then(result => sendResponse({ type: 'ALGO_HEALTH_CHECK_RESULT', ...result } satisfies ExtensionMessage))
            .catch(() => sendResponse({ type: 'ALGO_HEALTH_CHECK_RESULT', healthy: false, relisted_algo: 0, relisted_other: 0 } satisfies ExtensionMessage));
          return true; // async response
```

- [ ] **Step 3: Verify TypeScript compiles**

Run: `cd extension && npx tsc --noEmit`
Expected: No new errors

- [ ] **Step 4: Commit**

```
git add extension/entrypoints/ea-webapp.content.ts
git commit -m "feat(ext): route session recovery messages and relay health check to main world"
```

---

### Task 6: Create the Algo Master State Machine

**Files:**
- Create: `extension/src/algo-master.ts`

This is the core of the feature — the master state machine that monitors the worker tab and performs recovery.

- [ ] **Step 1: Create `extension/src/algo-master.ts`**

```typescript
/**
 * Algo Master — background service worker state machine for session management.
 *
 * Monitors the EA web app tab (worker), detects session death via 3 methods
 * (tab events, worker failure reports, periodic health checks), and recovers
 * automatically including credential-based auto-login.
 *
 * State machine:
 *   IDLE → SPAWNING → MONITORING → RECOVERING → SPAWNING
 *                         ↑                         |
 *                         └─────────────────────────┘
 */
import { algoMasterStateItem, algoCredentialsItem, type AlgoMasterState, type AlgoMasterStatus } from './storage';

// ── Constants ────────────────────────────────────────────────────────────────

const EA_WEBAPP_URL = 'https://www.ea.com/ea-sports-fc/ultimate-team/web-app/';
const EA_WEBAPP_PATTERN = 'https://www.ea.com/ea-sports-fc/ultimate-team/web-app/*';
const EA_DOMAIN_PATTERN = 'https://www.ea.com/*';
const HEALTH_CHECK_ALARM = 'algo-health-check';
const HEALTH_CHECK_INTERVAL_MINUTES = 5;
const PING_INTERVAL_MS = 2_000;
const PING_TIMEOUT_MS = 30_000;
const PAGE_LOAD_TIMEOUT_MS = 30_000;
const LOGIN_TIMEOUT_MS = 15_000;
const MAX_RECOVERY_ATTEMPTS = 3;
const RETRY_AFTER_ERROR_MS = 5 * 60_000; // 5 minutes
const HEALTH_CHECK_RETRY_MS = 10_000;

// ── State ────────────────────────────────────────────────────────────────────

let currentState: AlgoMasterState = {
  status: 'IDLE',
  tabId: null,
  recoveryAttempts: 0,
  lastHealthCheck: null,
  errorMessage: null,
};

// ── State Persistence ────────────────────────────────────────────────────────

async function loadState(): Promise<void> {
  currentState = await algoMasterStateItem.getValue();
}

async function saveState(): Promise<void> {
  await algoMasterStateItem.setValue(currentState);
}

async function transition(status: AlgoMasterStatus, extra?: Partial<AlgoMasterState>): Promise<void> {
  currentState.status = status;
  if (extra) Object.assign(currentState, extra);
  await saveState();
  console.log(`[algo-master] → ${status}`, extra ?? '');
}

// ── Public API ───────────────────────────────────────────────────────────────

/**
 * Initialize the master on service worker wake.
 * Reads persisted state and re-registers listeners if algo is active.
 */
export async function initAlgoMaster(): Promise<void> {
  await loadState();

  // Register tab event listeners (always, so we catch events even after worker restart)
  chrome.tabs.onRemoved.addListener(onTabRemoved);
  chrome.tabs.onUpdated.addListener(onTabUpdated);

  // Register health check alarm listener
  chrome.alarms.onAlarm.addListener(onAlarm);

  // If we were active before worker restart, resume
  if (currentState.status === 'MONITORING') {
    ensureHealthCheckAlarm();
  } else if (currentState.status === 'SPAWNING' || currentState.status === 'RECOVERING') {
    // Worker restarted mid-recovery — retry
    await startRecovery();
  }
}

/**
 * Start the algo master — called when user starts algo trading.
 * Finds or creates the EA tab, waits for worker ready, then signals algo-start.
 */
export async function startAlgoMaster(): Promise<void> {
  await transition('SPAWNING', { recoveryAttempts: 0, errorMessage: null });
  await spawnWorker();
}

/**
 * Stop the algo master — called when user stops algo trading.
 * Clears alarms, resets state. Does NOT close the EA tab.
 */
export async function stopAlgoMaster(): Promise<void> {
  await chrome.alarms.clear(HEALTH_CHECK_ALARM);
  await transition('IDLE', { tabId: null, recoveryAttempts: 0, errorMessage: null });
}

/**
 * Handle ALGO_SESSION_DEAD message from the worker (Method 2).
 * Worker detected 3 consecutive EA failures.
 */
export async function onSessionDead(): Promise<void> {
  if (currentState.status !== 'MONITORING') return;
  console.log('[algo-master] Worker reported session dead — starting recovery');
  await startRecovery();
}

/** Get current master state for status display. */
export function getMasterState(): AlgoMasterState {
  return { ...currentState };
}

// ── Tab Event Handlers (Method 1) ────────────────────────────────────────────

function onTabRemoved(tabId: number): void {
  if (tabId !== currentState.tabId) return;
  if (currentState.status === 'IDLE') return;
  console.log('[algo-master] EA tab closed — starting recovery');
  currentState.tabId = null;
  startRecovery();
}

function onTabUpdated(tabId: number, changeInfo: chrome.tabs.TabChangeInfo): void {
  if (tabId !== currentState.tabId) return;
  if (currentState.status !== 'MONITORING') return;
  if (!changeInfo.url) return;

  // URL drifted away from the web app — session redirected
  if (!changeInfo.url.includes('/ultimate-team/web-app/')) {
    console.log(`[algo-master] EA tab navigated away: ${changeInfo.url} — starting recovery`);
    startRecovery();
  }
}

// ── Health Check Alarm (Method 3) ────────────────────────────────────────────

function ensureHealthCheckAlarm(): void {
  chrome.alarms.get(HEALTH_CHECK_ALARM).then(alarm => {
    if (!alarm) {
      chrome.alarms.create(HEALTH_CHECK_ALARM, { periodInMinutes: HEALTH_CHECK_INTERVAL_MINUTES });
    }
  });
}

async function onAlarm(alarm: chrome.alarms.Alarm): Promise<void> {
  if (alarm.name !== HEALTH_CHECK_ALARM) return;
  if (currentState.status !== 'MONITORING') return;
  if (currentState.tabId == null) return;

  console.log('[algo-master] Running health check');

  const healthy = await performHealthCheck(currentState.tabId);
  if (healthy) {
    await transition('MONITORING', { lastHealthCheck: new Date().toISOString() });
    return;
  }

  // Retry once after 10s before declaring dead (false positive protection)
  console.log('[algo-master] Health check failed — retrying in 10s');
  await new Promise(r => setTimeout(r, HEALTH_CHECK_RETRY_MS));

  // Re-check state — user may have stopped algo during the wait
  await loadState();
  if (currentState.status !== 'MONITORING') return;

  const healthyRetry = await performHealthCheck(currentState.tabId!);
  if (healthyRetry) {
    await transition('MONITORING', { lastHealthCheck: new Date().toISOString() });
    return;
  }

  console.log('[algo-master] Health check failed twice — starting recovery');
  await startRecovery();
}

async function performHealthCheck(tabId: number): Promise<boolean> {
  try {
    const response = await chrome.tabs.sendMessage(tabId, { type: 'ALGO_HEALTH_CHECK' });
    if (response?.type === 'ALGO_HEALTH_CHECK_RESULT') {
      if (response.relisted_algo > 0 || response.relisted_other > 0) {
        console.log(`[algo-master] Health check OK — relisted ${response.relisted_algo} algo, ${response.relisted_other} other`);
      }
      return response.healthy === true;
    }
    return false;
  } catch {
    // Content script not responding — tab dead or navigated away
    return false;
  }
}

// ── Recovery Flow ────────────────────────────────────────────────────────────

async function startRecovery(): Promise<void> {
  await chrome.alarms.clear(HEALTH_CHECK_ALARM);

  currentState.recoveryAttempts++;
  if (currentState.recoveryAttempts > MAX_RECOVERY_ATTEMPTS) {
    await transition('ERROR', {
      errorMessage: `Recovery failed ${MAX_RECOVERY_ATTEMPTS} times — please log in manually`,
    });
    // Schedule a retry after 5 minutes
    setTimeout(() => {
      loadState().then(() => {
        if (currentState.status === 'ERROR') {
          currentState.recoveryAttempts = 0;
          startRecovery();
        }
      });
    }, RETRY_AFTER_ERROR_MS);
    return;
  }

  await transition('RECOVERING');
  await spawnWorker();
}

async function spawnWorker(): Promise<void> {
  // Step 1: Find or create the EA tab
  const tabId = await findOrCreateEaTab();
  await transition(currentState.status, { tabId });

  // Step 2: Wait for page to finish loading
  const loaded = await waitForPageLoad(tabId);
  if (!loaded) {
    console.warn('[algo-master] Page load timed out — retrying');
    await startRecovery();
    return;
  }

  // Step 3: Check what page we're on
  const tab = await chrome.tabs.get(tabId);
  const url = tab.url ?? '';

  if (url.includes('/ultimate-team/web-app/')) {
    // Session cookies valid — skip login, wait for content script
    console.log('[algo-master] Web app loaded — waiting for content script');
    await waitForWorkerAndStart(tabId);
    return;
  }

  // We're on a login page (or somewhere else)
  console.log(`[algo-master] Not on web app (${url}) — attempting login`);
  await attemptLogin(tabId);
}

async function findOrCreateEaTab(): Promise<number> {
  // Try to find an existing EA tab
  const tabs = await chrome.tabs.query({ url: EA_DOMAIN_PATTERN });
  if (tabs.length > 0 && tabs[0].id != null) {
    const tabId = tabs[0].id;
    // Navigate to the web app URL (may already be there)
    await chrome.tabs.update(tabId, { url: EA_WEBAPP_URL, active: true });
    return tabId;
  }

  // No EA tab found — create one
  const newTab = await chrome.tabs.create({ url: EA_WEBAPP_URL, active: true });
  return newTab.id!;
}

function waitForPageLoad(tabId: number): Promise<boolean> {
  return new Promise(resolve => {
    const timeout = setTimeout(() => {
      chrome.tabs.onUpdated.removeListener(listener);
      resolve(false);
    }, PAGE_LOAD_TIMEOUT_MS);

    function listener(updatedTabId: number, changeInfo: chrome.tabs.TabChangeInfo) {
      if (updatedTabId !== tabId) return;
      if (changeInfo.status !== 'complete') return;
      chrome.tabs.onUpdated.removeListener(listener);
      clearTimeout(timeout);
      resolve(true);
    }

    // Check if already loaded
    chrome.tabs.get(tabId).then(tab => {
      if (tab.status === 'complete') {
        chrome.tabs.onUpdated.removeListener(listener);
        clearTimeout(timeout);
        resolve(true);
      } else {
        chrome.tabs.onUpdated.addListener(listener);
      }
    }).catch(() => {
      clearTimeout(timeout);
      resolve(false);
    });
  });
}

async function attemptLogin(tabId: number): Promise<void> {
  const credentials = await algoCredentialsItem.getValue();

  if (!credentials) {
    // No credentials configured — wait for manual login
    await transition('WAITING_FOR_LOGIN', {
      errorMessage: 'Session expired — please log in manually',
    });
    pollForWebApp(tabId);
    return;
  }

  // Inject login script via chrome.scripting.executeScript
  try {
    await chrome.scripting.executeScript({
      target: { tabId },
      func: fillLoginForm,
      args: [credentials.email, credentials.password],
    });
  } catch (err) {
    console.error('[algo-master] Login script injection failed:', err);
    await transition('WAITING_FOR_LOGIN', {
      errorMessage: 'Login injection failed — please log in manually',
    });
    pollForWebApp(tabId);
    return;
  }

  // Wait for navigation back to web app
  const navigatedToWebApp = await waitForUrlMatch(tabId, '/ultimate-team/web-app/', LOGIN_TIMEOUT_MS);

  if (!navigatedToWebApp) {
    console.warn('[algo-master] Login did not redirect to web app — may have failed');
    // Check if we're still on login page
    const tab = await chrome.tabs.get(tabId);
    if (tab.url?.includes('/ultimate-team/web-app/')) {
      // Actually made it
      await waitForWorkerAndStart(tabId);
      return;
    }
    // Login failed — retry recovery
    await startRecovery();
    return;
  }

  // Wait for page to fully load after login redirect
  await waitForPageLoad(tabId);
  await waitForWorkerAndStart(tabId);
}

/**
 * Login form filler — injected into the login page via chrome.scripting.executeScript.
 * Finds email/password fields and submits. Runs in the target page's context.
 */
function fillLoginForm(email: string, password: string): void {
  // Try common selectors for EA's login form
  const emailInput = document.querySelector<HTMLInputElement>(
    'input[type="email"], input[name="email"], input[id="email"], input[autocomplete="email"]'
  );
  const passwordInput = document.querySelector<HTMLInputElement>(
    'input[type="password"], input[name="password"], input[id="password"]'
  );

  if (!emailInput || !passwordInput) {
    console.error('[algo-master] Login form inputs not found');
    return;
  }

  // Set values using native input setter to trigger React/framework change handlers
  const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
    HTMLInputElement.prototype, 'value'
  )?.set;

  if (nativeInputValueSetter) {
    nativeInputValueSetter.call(emailInput, email);
    emailInput.dispatchEvent(new Event('input', { bubbles: true }));
    emailInput.dispatchEvent(new Event('change', { bubbles: true }));

    nativeInputValueSetter.call(passwordInput, password);
    passwordInput.dispatchEvent(new Event('input', { bubbles: true }));
    passwordInput.dispatchEvent(new Event('change', { bubbles: true }));
  } else {
    emailInput.value = email;
    passwordInput.value = password;
  }

  // Find and click the submit button
  const submitBtn = document.querySelector<HTMLButtonElement>(
    'button[type="submit"], input[type="submit"], button[class*="login"], button[class*="submit"]'
  );
  if (submitBtn) {
    setTimeout(() => submitBtn.click(), 500);
  } else {
    // Try submitting the form directly
    const form = emailInput.closest('form');
    if (form) {
      setTimeout(() => form.submit(), 500);
    }
  }
}

function waitForUrlMatch(tabId: number, urlFragment: string, timeoutMs: number): Promise<boolean> {
  return new Promise(resolve => {
    const timeout = setTimeout(() => {
      chrome.tabs.onUpdated.removeListener(listener);
      resolve(false);
    }, timeoutMs);

    function listener(updatedTabId: number, changeInfo: chrome.tabs.TabChangeInfo) {
      if (updatedTabId !== tabId) return;
      if (!changeInfo.url) return;
      if (changeInfo.url.includes(urlFragment)) {
        chrome.tabs.onUpdated.removeListener(listener);
        clearTimeout(timeout);
        resolve(true);
      }
    }

    chrome.tabs.onUpdated.addListener(listener);
  });
}

/**
 * Poll for the web app URL when waiting for manual login.
 * Checks the tab URL every 5 seconds until it matches the web app pattern.
 */
function pollForWebApp(tabId: number): void {
  const intervalId = setInterval(async () => {
    // Re-check state — user may have stopped algo
    await loadState();
    if (currentState.status !== 'WAITING_FOR_LOGIN') {
      clearInterval(intervalId);
      return;
    }

    try {
      const tab = await chrome.tabs.get(tabId);
      if (tab.url?.includes('/ultimate-team/web-app/')) {
        clearInterval(intervalId);
        console.log('[algo-master] Manual login detected — resuming');
        await waitForPageLoad(tabId);
        await waitForWorkerAndStart(tabId);
      }
    } catch {
      // Tab gone — start recovery
      clearInterval(intervalId);
      await startRecovery();
    }
  }, 5_000);
}

/**
 * Wait for the content script to respond to PING, then start the algo loop.
 */
async function waitForWorkerAndStart(tabId: number): Promise<void> {
  const startTime = Date.now();

  while (Date.now() - startTime < PING_TIMEOUT_MS) {
    try {
      const response = await chrome.tabs.sendMessage(tabId, { type: 'PING' });
      if (response?.type === 'PONG') {
        console.log('[algo-master] Worker is alive — starting algo');

        // Small delay for main world script to fully initialize
        await new Promise(r => setTimeout(r, 2_000));

        // Dispatch the algo-start custom event that ea-webapp.content.ts listens for.
        // The isolated world content script hears this and relays to main world via bridge.
        await chrome.scripting.executeScript({
          target: { tabId },
          func: () => document.dispatchEvent(new CustomEvent('op-seller-algo-start')),
        });

        await transition('MONITORING', {
          tabId,
          recoveryAttempts: 0,
          errorMessage: null,
          lastHealthCheck: new Date().toISOString(),
        });
        ensureHealthCheckAlarm();
        return;
      }
    } catch {
      // Content script not ready yet
    }
    await new Promise(r => setTimeout(r, PING_INTERVAL_MS));
  }

  console.warn('[algo-master] Worker did not respond to PING in time');
  await startRecovery();
}
```

- [ ] **Step 2: Verify TypeScript compiles**

Run: `cd extension && npx tsc --noEmit`
Expected: No new errors

- [ ] **Step 3: Commit**

```
git add extension/src/algo-master.ts
git commit -m "feat(ext): create algo master state machine with health monitoring and recovery"
```

---

### Task 7: Wire Algo Master into Background Service Worker

**Files:**
- Modify: `extension/entrypoints/background.ts:1-125`

- [ ] **Step 1: Import and initialize algo master**

At the top of `extension/entrypoints/background.ts`, add the import after line 14:

```typescript
import { initAlgoMaster, startAlgoMaster, stopAlgoMaster, onSessionDead } from '../src/algo-master';
```

- [ ] **Step 2: Initialize master on worker wake**

Inside the `main()` function of `defineBackground`, after the alarm registration block (after line 37), add:

```typescript
    // Initialize algo master state machine (re-registers listeners, resumes if active)
    initAlgoMaster();
```

- [ ] **Step 3: Route ALGO_SESSION_DEAD message**

In the `chrome.runtime.onMessage.addListener` switch block, add a case for the new message type. Add after the `case 'ALGO_POSITION_RELIST':` handler (after line 98):

```typescript
        case 'ALGO_SESSION_DEAD':
          onSessionDead().then(() => sendResponse({ type: 'ALGO_SESSION_DEAD_ACK' }));
          return true;
        case 'ALGO_HEALTH_CHECK':
        case 'ALGO_HEALTH_CHECK_RESULT':
          return false; // These go directly to content script via chrome.tabs.sendMessage
```

- [ ] **Step 4: Wire algo start/stop through master**

In the existing `handleAlgoStart` function (around line 433), add master startup after the backend call succeeds. Replace the function:

```typescript
async function handleAlgoStart(budget: number): Promise<ExtensionMessage> {
  try {
    const res = await fetch(`${BACKEND_URL}/api/v1/algo/start`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ budget }),
    });
    if (!res.ok) {
      return { type: 'ALGO_START_RESULT', success: false, error: `Backend ${res.status}` };
    }
    const data = await res.json();
    // Start the master to monitor the worker tab
    startAlgoMaster().catch(err => console.error('[background] Master start failed:', err));
    return { type: 'ALGO_START_RESULT', success: true, budget: data.budget, cash: data.cash };
  } catch (e) {
    return { type: 'ALGO_START_RESULT', success: false, error: String(e) };
  }
}
```

Replace the `handleAlgoStop` function:

```typescript
async function handleAlgoStop(): Promise<ExtensionMessage> {
  try {
    const res = await fetch(`${BACKEND_URL}/api/v1/algo/stop`, { method: 'POST' });
    if (!res.ok) {
      return { type: 'ALGO_STOP_RESULT', success: false, error: `Backend ${res.status}` };
    }
    // Stop the master (clears alarms, resets state)
    await stopAlgoMaster();
    return { type: 'ALGO_STOP_RESULT', success: true };
  } catch (e) {
    return { type: 'ALGO_STOP_RESULT', success: false, error: String(e) };
  }
}
```

- [ ] **Step 5: Add `ALGO_SESSION_DEAD` to the result-type pass-through list**

In the switch block, find the group of `ALGO_*_RESULT` types that return false (around line 100-107). Add the new types:

```typescript
        case 'ALGO_START_RESULT':
        case 'ALGO_STOP_RESULT':
        case 'ALGO_STATUS_RESULT':
        case 'ALGO_SIGNAL_RESULT':
        case 'ALGO_SIGNAL_COMPLETE_RESULT':
        case 'ALGO_POSITION_SOLD_RESULT':
        case 'ALGO_POSITION_RELIST_RESULT':
          return false;
```

Note: `ALGO_SESSION_DEAD` is handled above. `ALGO_HEALTH_CHECK` and `ALGO_HEALTH_CHECK_RESULT` are also handled above.

- [ ] **Step 6: Verify TypeScript compiles**

Run: `cd extension && npx tsc --noEmit`
Expected: No new errors

- [ ] **Step 7: Commit**

```
git add extension/entrypoints/background.ts
git commit -m "feat(ext): wire algo master into background service worker"
```

---

### Task 8: Add Credentials Form to Overlay Panel

**Files:**
- Modify: `extension/src/overlay/panel.ts:1494-1518`

- [ ] **Step 1: Import the credentials storage item**

At the top of `extension/src/overlay/panel.ts`, find the import from `'../storage'` and add `algoCredentialsItem`:

```typescript
import { portfolioItem, reportedOutcomesItem, automationStatusItem, algoCredentialsItem } from '../src/storage';
```

Note: Check the exact import path — in `panel.ts` the relative path from `overlay/` to `storage.ts` is `../storage`.

- [ ] **Step 2: Add credentials section to `renderAlgoTab`**

In `extension/src/overlay/panel.ts`, inside the `renderAlgoTab` function, after the budget input block (after line 1518 where `parent.appendChild(budgetInput)`), add the credentials section:

```typescript
    // ── Credentials section ──────────────────────────────────────────────
    const credsSection = document.createElement('div');
    Object.assign(credsSection.style, {
      background: '#1e1e2e',
      padding: '10px',
      borderRadius: '4px',
      marginBottom: '12px',
      border: '1px solid #333',
    });

    const credsTitle = document.createElement('div');
    credsTitle.textContent = 'EA Login (auto-recovery)';
    Object.assign(credsTitle.style, {
      fontSize: '11px',
      color: '#888',
      marginBottom: '8px',
      textTransform: 'uppercase',
      letterSpacing: '0.5px',
    });
    credsSection.appendChild(credsTitle);

    const emailInput = document.createElement('input');
    emailInput.type = 'email';
    emailInput.placeholder = 'EA Email';
    Object.assign(emailInput.style, {
      background: '#2a2a3e',
      color: '#fff',
      border: '1px solid #444',
      padding: '6px 10px',
      width: '100%',
      borderRadius: '4px',
      boxSizing: 'border-box',
      fontSize: '13px',
      marginBottom: '6px',
    });
    credsSection.appendChild(emailInput);

    const passwordInput = document.createElement('input');
    passwordInput.type = 'password';
    passwordInput.placeholder = 'EA Password';
    Object.assign(passwordInput.style, {
      background: '#2a2a3e',
      color: '#fff',
      border: '1px solid #444',
      padding: '6px 10px',
      width: '100%',
      borderRadius: '4px',
      boxSizing: 'border-box',
      fontSize: '13px',
      marginBottom: '6px',
    });
    credsSection.appendChild(passwordInput);

    const credsBtnRow = document.createElement('div');
    Object.assign(credsBtnRow.style, { display: 'flex', gap: '8px', alignItems: 'center' });

    const saveCredsBtn = document.createElement('button');
    saveCredsBtn.textContent = 'Save';
    Object.assign(saveCredsBtn.style, {
      background: '#3498db',
      color: '#fff',
      border: 'none',
      padding: '5px 14px',
      borderRadius: '4px',
      cursor: 'pointer',
      fontSize: '12px',
    });

    const credsStatus = document.createElement('span');
    Object.assign(credsStatus.style, { fontSize: '11px', color: '#888' });

    credsBtnRow.appendChild(saveCredsBtn);
    credsBtnRow.appendChild(credsStatus);
    credsSection.appendChild(credsBtnRow);

    // Load existing credentials status
    algoCredentialsItem.getValue().then(creds => {
      if (creds) {
        emailInput.value = creds.email;
        // Don't show password — just indicate it's saved
        passwordInput.placeholder = '••••••••';
        credsStatus.textContent = 'Credentials saved';
        credsStatus.style.color = '#2ecc71';
      } else {
        credsStatus.textContent = 'Not configured';
        credsStatus.style.color = '#e74c3c';
      }
    });

    saveCredsBtn.addEventListener('click', async () => {
      const email = emailInput.value.trim();
      const password = passwordInput.value;

      if (!email) {
        credsStatus.textContent = 'Email required';
        credsStatus.style.color = '#e74c3c';
        return;
      }

      // If password is empty but we already have creds, keep the old password
      if (!password) {
        const existing = await algoCredentialsItem.getValue();
        if (existing) {
          await algoCredentialsItem.setValue({ email, password: existing.password });
          credsStatus.textContent = 'Email updated';
          credsStatus.style.color = '#2ecc71';
          return;
        }
        credsStatus.textContent = 'Password required';
        credsStatus.style.color = '#e74c3c';
        return;
      }

      await algoCredentialsItem.setValue({ email, password });
      passwordInput.value = '';
      passwordInput.placeholder = '••••••••';
      credsStatus.textContent = 'Credentials saved';
      credsStatus.style.color = '#2ecc71';
    });

    parent.appendChild(credsSection);
```

- [ ] **Step 3: Verify TypeScript compiles**

Run: `cd extension && npx tsc --noEmit`
Expected: No new errors

- [ ] **Step 4: Commit**

```
git add extension/src/overlay/panel.ts
git commit -m "feat(ext): add EA login credentials form to algo overlay panel"
```

---

### Task 9: Handle `assertNever` Exhaustiveness for New Message Types

**Files:**
- Modify: `extension/entrypoints/ea-webapp.content.ts`
- Modify: `extension/entrypoints/background.ts`

The `assertNever` helper in the content script's switch will produce compile errors for any new message variants not handled. We need to make sure all three new types are covered in every switch that uses `assertNever`.

- [ ] **Step 1: Verify ea-webapp.content.ts handles all new types**

This was done in Task 5. Verify the switch block in `ea-webapp.content.ts` includes:
- `case 'ALGO_SESSION_DEAD':` → returns false
- `case 'ALGO_HEALTH_CHECK':` → relays to main world, returns true
- `case 'ALGO_HEALTH_CHECK_RESULT':` → returns false

- [ ] **Step 2: Verify background.ts handles all new types**

Check that the background.ts switch block includes:
- `case 'ALGO_SESSION_DEAD':` → calls `onSessionDead()`
- `case 'ALGO_HEALTH_CHECK':` → returns false
- `case 'ALGO_HEALTH_CHECK_RESULT':` → returns false

- [ ] **Step 3: Run full type check**

Run: `cd extension && npx tsc --noEmit`
Expected: Zero errors. If `assertNever` errors appear, add the missing case to the relevant switch.

- [ ] **Step 4: Commit (if any fixes needed)**

```
git add -A
git commit -m "fix(ext): handle all new message types in exhaustive switch blocks"
```

---

### Task 10: Integration Test — Manual Verification

**Files:** None (manual testing)

- [ ] **Step 1: Build the extension**

Run: `cd extension && npm run build`
Expected: Build succeeds with no errors

- [ ] **Step 2: Load extension in Chrome**

Load the unpacked extension from `extension/.output/chrome-mv3/` in `chrome://extensions` (developer mode).

- [ ] **Step 3: Verify credentials form**

1. Open EA web app
2. Open the OP Seller overlay panel → Algo tab
3. Verify the credentials section appears below the budget input
4. Enter email and password, click Save
5. Verify "Credentials saved" indicator shows green
6. Switch tabs and switch back — verify credentials persist (email shows, password placeholder shows dots)

- [ ] **Step 4: Verify algo start triggers master**

1. Enter a budget and click "Start Algo"
2. Open Chrome DevTools → background.js console
3. Verify `[algo-master] → SPAWNING` and then `[algo-master] → MONITORING` log messages
4. Verify health check alarm is registered in `chrome://extensions` → alarms section

- [ ] **Step 5: Verify health check fires**

1. Wait 5 minutes (or temporarily reduce `HEALTH_CHECK_INTERVAL_MINUTES` to 1 for testing)
2. Verify `[algo-master] Running health check` appears in background console
3. Verify `Health check OK` response

- [ ] **Step 6: Verify recovery on tab close**

1. Close the EA web app tab
2. Verify `[algo-master] EA tab closed — starting recovery` in background console
3. Verify a new EA tab opens and navigates to the web app
4. If session cookies valid: verify `[algo-master] Worker is alive — starting algo`
5. If login page: verify credentials are filled and form is submitted

- [ ] **Step 7: Commit final state**

```
git add -A
git commit -m "feat(ext): complete algo session recovery with master-worker architecture"
```

---

## Dependency Graph

```
Task 1 (messages, storage, manifest)
  ├── Task 2 (algo loop failure tracking) — needs message types
  ├── Task 3 (bridge command) — needs message types
  ├── Task 5 (content script routing) — needs message types
  ├── Task 6 (algo master) — needs storage types
  └── Task 8 (credentials UI) — needs storage types
Task 3 (bridge command)
  └── Task 4 (main world health check) — needs bridge command
Task 4 + Task 5 (content script routing)
  └── Task 7 (background wiring) — needs all message routing in place
Task 6 (algo master)
  └── Task 7 (background wiring) — imports algo master
Task 7 (background wiring)
  └── Task 9 (assertNever check) — final type verification
Task 9
  └── Task 10 (integration test) — manual verification
```

Tasks 2, 3, 5, 6, and 8 can run in parallel after Task 1 completes.
