# Master Tab Mode Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user choose (via a popup dropdown) whether the master tab's session-recovery drives Algo trading or OP selling. One mode runs at a time; the master always handles the EA session.

**Architecture:** The algo master becomes mode-agnostic. Background handlers for `ALGO_START` and `AUTOMATION_START` both set a `mode` field on `AlgoMasterState` and call a shared start helper. On session recovery, the master dispatches `op-seller-algo-start` or `op-seller-automation-start` depending on the stored mode; the existing isolated-world event listeners relay the right command to the main-world engines.

**Tech Stack:** TypeScript, WXT, Vitest + fakeBrowser, Chrome MV3 (service worker + content scripts).

**Spec reference:** `docs/superpowers/specs/2026-04-18-master-tab-mode-selection-design.md`

**Files touched:**
- Modify `extension/src/storage.ts` — add `mode` to `AlgoMasterState`, export `TradeMode` type
- Modify `extension/src/algo-master.ts` — accept mode param, dispatch mode-appropriate recovery event
- Modify `extension/entrypoints/background.ts` — add `handleAutomationStart`/`handleAutomationStop`; update `handleAlgoStart`/`handleAlgoStop` to set mode
- Modify `extension/entrypoints/ea-webapp-main.content.ts` — auto-resume branches on mode
- Modify `extension/entrypoints/ea-webapp.content.ts` — panel events send `AUTOMATION_START`/`STOP` messages (route through master) instead of direct bridge
- Modify `extension/entrypoints/popup/index.html` — add mode dropdown, rename button
- Modify `extension/entrypoints/popup/main.ts` — mode dropdown logic, resolution order, persistence, dispatch right message
- Modify `extension/tests/background.test.ts` — add tests for mode propagation

---

## Task 1: Extend `AlgoMasterState` with `mode` field

**Files:**
- Modify: `extension/src/storage.ts:135-156`

- [ ] **Step 1: Write the failing test**

Create `extension/tests/storage.test.ts` if it doesn't exist; otherwise append. Check existence first with `ls extension/tests/storage.test.ts`.

```ts
import { describe, it, expect, beforeEach } from 'vitest';
import { fakeBrowser } from 'wxt/testing';
import { algoMasterStateItem } from '../src/storage';

describe('algoMasterStateItem', () => {
  beforeEach(() => {
    fakeBrowser.reset();
  });

  it('includes mode field defaulting to "algo"', async () => {
    const state = await algoMasterStateItem.getValue();
    expect(state.mode).toBe('algo');
  });

  it('persists mode changes', async () => {
    const state = await algoMasterStateItem.getValue();
    await algoMasterStateItem.setValue({ ...state, mode: 'op-selling' });
    const reloaded = await algoMasterStateItem.getValue();
    expect(reloaded.mode).toBe('op-selling');
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd extension && npm test -- storage.test.ts`
Expected: FAIL — `state.mode` is `undefined` (field not yet added).

- [ ] **Step 3: Add `TradeMode` type and extend `AlgoMasterState`**

In `extension/src/storage.ts`, modify the section around line 135-156:

```ts
/** Which automation mode the master tab is driving. */
export type TradeMode = 'algo' | 'op-selling';

/**
 * Master state machine for session management.
 * Persisted so the background service worker can resume after MV3 restarts.
 */
export type AlgoMasterStatus = 'IDLE' | 'SPAWNING' | 'MONITORING' | 'RECOVERING' | 'WAITING_FOR_LOGIN' | 'ERROR';

export type AlgoMasterState = {
  status: AlgoMasterStatus;
  tabId: number | null;
  recoveryAttempts: number;
  lastHealthCheck: string | null;
  errorMessage: string | null;
  mode: TradeMode;
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
      mode: 'algo',
    },
  },
);
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd extension && npm test -- storage.test.ts`
Expected: PASS (both cases).

- [ ] **Step 5: Run full test suite to catch migration issues**

Run: `cd extension && npm test`
Expected: PASS across all files. If any test creates an `AlgoMasterState` literal without `mode`, fix by adding `mode: 'algo'`.

- [ ] **Step 6: Typecheck**

Run: `cd extension && npm run compile`
Expected: no type errors. If any code constructs an `AlgoMasterState` without `mode`, add `mode: 'algo'` to satisfy the compiler.

- [ ] **Step 7: Commit**

```bash
git add extension/src/storage.ts extension/tests/storage.test.ts
git commit -m "feat(extension): add mode field to AlgoMasterState for algo/op-selling selection"
```

---

## Task 2: Mode-aware master — accept mode, dispatch right event on recovery

**Files:**
- Modify: `extension/src/algo-master.ts:93-96, 563-568`

- [ ] **Step 1: Write the failing test**

Append to `extension/tests/storage.test.ts` or create `extension/tests/algo-master.test.ts`:

```ts
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { fakeBrowser } from 'wxt/testing';
import { algoMasterStateItem } from '../src/storage';

describe('startAlgoMaster mode', () => {
  beforeEach(() => {
    fakeBrowser.reset();
    vi.restoreAllMocks();
  });

  it('persists the mode passed in when starting', async () => {
    vi.resetModules();
    const mod = await import('../src/algo-master');
    // Stub chrome.tabs/chrome.scripting to avoid real spawn work
    vi.spyOn(chrome.tabs, 'query').mockResolvedValue([] as any);
    vi.spyOn(chrome.tabs, 'create').mockResolvedValue({ id: 1, status: 'complete' } as any);
    vi.spyOn(chrome.tabs, 'get').mockResolvedValue({ id: 1, url: 'https://www.ea.com/ea-sports-fc/ultimate-team/web-app/', status: 'complete' } as any);
    vi.spyOn(chrome.tabs, 'sendMessage').mockResolvedValue({ type: 'PONG' });

    await mod.startAlgoMaster('op-selling');
    const state = await algoMasterStateItem.getValue();
    expect(state.mode).toBe('op-selling');
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd extension && npm test -- algo-master`
Expected: FAIL — `startAlgoMaster` currently takes no arguments.

- [ ] **Step 3: Update `startAlgoMaster` signature to accept mode**

In `extension/src/algo-master.ts`, replace the function at lines 89-96:

```ts
/**
 * Start the master — called when user starts algo trading or OP selling.
 * Finds or creates the EA tab, waits for worker ready, then signals the worker
 * to run the loop corresponding to `mode`.
 */
export async function startAlgoMaster(mode: TradeMode): Promise<void> {
  await transition('SPAWNING', { recoveryAttempts: 0, errorMessage: null, mode });
  await spawnWorker();
}
```

Also extend the import at line 13 to include `TradeMode`:

```ts
import { algoMasterStateItem, algoCredentialsItem, type AlgoMasterState, type AlgoMasterStatus, type TradeMode } from './storage';
```

- [ ] **Step 4: Update recovery dispatch to use mode**

In `extension/src/algo-master.ts`, replace the block at lines 563-568 (inside `waitForWorkerAndStart`):

```ts
  // Session confirmed alive — start the configured loop
  const mode = currentState.mode ?? 'algo';
  const eventName = mode === 'op-selling' ? 'op-seller-automation-start' : 'op-seller-algo-start';
  console.log(`[algo-master] Session alive — dispatching ${eventName}`);
  await chrome.scripting.executeScript({
    target: { tabId },
    func: (event: string) => document.dispatchEvent(new CustomEvent(event)),
    args: [eventName],
  });
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd extension && npm test -- algo-master`
Expected: PASS.

- [ ] **Step 6: Typecheck**

Run: `cd extension && npm run compile`
Expected: no type errors. The signature change will cause `startAlgoMaster()` callers in `background.ts` to fail — that's OK, we fix them in Task 3/4. Note the compile errors but don't fix them yet.

- [ ] **Step 7: Commit**

```bash
git add extension/src/algo-master.ts extension/tests/algo-master.test.ts
git commit -m "feat(extension): startAlgoMaster accepts mode, recovery dispatches mode-specific event"
```

---

## Task 3: Background — `handleAlgoStart`/`Stop` pass mode; register `AUTOMATION_START`/`STOP` handlers

**Files:**
- Modify: `extension/entrypoints/background.ts:90-95, 126-127, 468-499`
- Modify: `extension/tests/background.test.ts`

- [ ] **Step 1: Write the failing tests**

Append to `extension/tests/background.test.ts`:

```ts
import { algoMasterStateItem } from '../src/storage';

describe('mode selection via message handlers', () => {
  beforeEach(async () => {
    fakeBrowser.reset();
    vi.restoreAllMocks();
    // Stub backend + master-side chrome calls so handlers can run
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ budget: 100000, cash: 100000 }),
    }) as any;
    vi.spyOn(chrome.tabs, 'query').mockResolvedValue([] as any);
    vi.spyOn(chrome.tabs, 'create').mockResolvedValue({ id: 1, status: 'complete' } as any);
    vi.spyOn(chrome.tabs, 'get').mockResolvedValue({ id: 1, url: 'https://www.ea.com/ea-sports-fc/ultimate-team/web-app/', status: 'complete' } as any);
    vi.spyOn(chrome.tabs, 'sendMessage').mockResolvedValue({ type: 'PONG' });
  });

  it('ALGO_START sets mode to "algo" on master state', async () => {
    await runBackground();
    const response = await new Promise(resolve => {
      chrome.runtime.sendMessage({ type: 'ALGO_START', budget: 100000 }, resolve);
    });
    // Allow startAlgoMaster to run
    await new Promise(r => setTimeout(r, 50));
    const state = await algoMasterStateItem.getValue();
    expect(state.mode).toBe('algo');
  });

  it('AUTOMATION_START sets mode to "op-selling" on master state', async () => {
    await runBackground();
    const response = await new Promise(resolve => {
      chrome.runtime.sendMessage({ type: 'AUTOMATION_START' }, resolve);
    });
    await new Promise(r => setTimeout(r, 50));
    const state = await algoMasterStateItem.getValue();
    expect(state.mode).toBe('op-selling');
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd extension && npm test -- background.test`
Expected: FAIL — `ALGO_START` currently calls `startAlgoMaster()` without mode; `AUTOMATION_START` currently isn't handled.

- [ ] **Step 3: Update `handleAlgoStart` to pass mode**

In `extension/entrypoints/background.ts`, replace `handleAlgoStart` (around lines 468-485):

```ts
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
    // Start the master in algo mode
    startAlgoMaster('algo').catch(err => console.error('[background] Master start failed:', err));
    return { type: 'ALGO_START_RESULT', success: true, budget: data.budget, cash: data.cash };
  } catch (e) {
    return { type: 'ALGO_START_RESULT', success: false, error: String(e) };
  }
}
```

- [ ] **Step 4: Add `handleAutomationStart` / `handleAutomationStop`**

In `extension/entrypoints/background.ts`, add these handlers after `handleAlgoStop` (around line 499):

```ts
async function handleAutomationStart(): Promise<ExtensionMessage> {
  try {
    // OP selling does not require a backend activation call — just start the master
    startAlgoMaster('op-selling').catch(err => console.error('[background] Master start failed:', err));
    return { type: 'AUTOMATION_START_RESULT', success: true };
  } catch (e) {
    return { type: 'AUTOMATION_START_RESULT', success: false, error: String(e) };
  }
}

async function handleAutomationStop(): Promise<ExtensionMessage> {
  try {
    await stopAlgoMaster();
    return { type: 'AUTOMATION_STOP_RESULT', success: true };
  } catch (e) {
    return { type: 'AUTOMATION_STOP_RESULT', success: false };
  }
}
```

- [ ] **Step 5: Register `AUTOMATION_START` / `AUTOMATION_STOP` in the message switch**

In `extension/entrypoints/background.ts`, inside the `chrome.runtime.onMessage.addListener` switch statement (around line 52-140), find the `case 'AUTOMATION_START':` / `case 'AUTOMATION_STOP':` entries (currently at lines 126-127 returning `false`) and move/replace them with handled cases. Locate the block:

```ts
        case 'AUTOMATION_STATUS_REQUEST':
        case 'AUTOMATION_START':
        case 'AUTOMATION_STOP':
        case 'AUTOMATION_START_RESULT':
        case 'AUTOMATION_STOP_RESULT':
        case 'AUTOMATION_STATUS_RESULT':
```

Replace with:

```ts
        case 'AUTOMATION_START':
          handleAutomationStart().then(sendResponse);
          return true;
        case 'AUTOMATION_STOP':
          handleAutomationStop().then(sendResponse);
          return true;
        case 'AUTOMATION_STATUS_REQUEST':
        case 'AUTOMATION_START_RESULT':
        case 'AUTOMATION_STOP_RESULT':
        case 'AUTOMATION_STATUS_RESULT':
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd extension && npm test -- background.test`
Expected: PASS (both new tests + existing tests still green).

- [ ] **Step 7: Typecheck**

Run: `cd extension && npm run compile`
Expected: no type errors.

- [ ] **Step 8: Commit**

```bash
git add extension/entrypoints/background.ts extension/tests/background.test.ts
git commit -m "feat(extension): background handlers set mode on master; add AUTOMATION_START/STOP handlers"
```

---

## Task 4: Auto-resume in main content branches on mode

**Files:**
- Modify: `extension/entrypoints/ea-webapp-main.content.ts:286-300`

Note: This block has no test harness — the main-world content script depends on EA globals and cannot run under vitest/jsdom. Verify manually after build.

- [ ] **Step 1: Update the auto-resume block to branch on mode**

In `extension/entrypoints/ea-webapp-main.content.ts`, replace the block at lines 286-300:

```ts
    // Auto-start the configured mode if master is MONITORING or SPAWNING (handles page refresh).
    // After a refresh, the content script re-injects but the engine is fresh/stopped.
    // The master still tracks the active mode in state — read it and restart the matching engine.
    bridgedStorageGet<{ status: string; mode?: string }>('algoMasterState').then(state => {
      if (!state || (state.status !== 'MONITORING' && state.status !== 'SPAWNING')) return;
      const mode = state.mode ?? 'algo';
      if (mode === 'op-selling') {
        console.log('[OP Seller Main] Master is active (op-selling), auto-starting OP selling engine');
        automationEngine.start().then(result => {
          if (result.success) {
            runAutomationLoop(automationEngine, bridgedSendMessage)
              .catch(err => automationEngine.setError(
                err instanceof Error ? err.message : String(err),
              ));
          }
        });
      } else {
        console.log('[OP Seller Main] Master is active (algo), auto-starting algo engine');
        algoEngine.start().then(result => {
          if (result.success) {
            runAlgoAutomationLoop(algoEngine, bridgedSendMessage)
              .catch(err => algoEngine.setError(
                err instanceof Error ? err.message : String(err),
              ));
          }
        });
      }
    }).catch(() => {
      // Bridge not ready yet — master health check will catch it
    });
```

- [ ] **Step 2: Typecheck**

Run: `cd extension && npm run compile`
Expected: no type errors.

- [ ] **Step 3: Build to verify WXT compiles the content script**

Run: `cd extension && npm run build`
Expected: build completes successfully, writes to `extension/.output/chrome-mv3/`.

- [ ] **Step 4: Commit**

```bash
git add extension/entrypoints/ea-webapp-main.content.ts
git commit -m "feat(extension): auto-resume main content script branches on master mode"
```

---

## Task 5: Route panel's OP selling start/stop through the master

Per spec non-goal: "Preserving the old 'OP selling without a master tab' path." The overlay panel currently dispatches `op-seller-automation-start` events that bypass the master. Re-route the isolated-world handlers to send `AUTOMATION_START` messages so the master always manages the session. The same `op-seller-automation-start` custom event is still used by the master to tell the worker to run — but only the master dispatches it now.

**Files:**
- Modify: `extension/entrypoints/ea-webapp.content.ts:188-205`

Note: Without a direct unit test for this handler, rely on typecheck + manual E2E.

- [ ] **Step 1: Split panel-origin events from master-origin events**

The problem: today the isolated world listens for `op-seller-automation-start` (dispatched by both panel and — after Task 2 — the master). If both emit the same event, the isolated world can't tell them apart.

Fix: introduce a dedicated panel-origin event. Rename the panel's events to `op-seller-automation-start-panel` / `op-seller-automation-stop-panel`. The master keeps using `op-seller-automation-start`/`op-seller-automation-stop` (these remain the "worker should start the OP loop now" events handled by the existing isolated-world listener that calls `sendAutomationCommand('start')`).

In `extension/entrypoints/ea-webapp.content.ts`, locate the `op-seller-automation-start`/`stop` listeners (lines 188-205):

```ts
    document.addEventListener('op-seller-automation-start', async () => {
      // Relay start command to the main world engine via the postMessage bridge.
      // The main world handles AutomationEngine.start() + runAutomationLoop().
      try {
        await sendAutomationCommand('start');
      } catch (err) {
        console.error('[OP Seller CS] Failed to start automation via bridge:', err);
      }
    });

    document.addEventListener('op-seller-automation-stop', async () => {
      // Relay stop command to the main world engine via the postMessage bridge.
      try {
        await sendAutomationCommand('stop');
      } catch (err) {
        console.error('[OP Seller CS] Failed to stop automation via bridge:', err);
      }
    });
```

These listeners handle master-dispatched events — leave them as-is. They should continue to relay to the main-world engine directly (fast-path after master confirms session alive).

Add new listeners below them for panel-origin events that route through the master:

```ts
    // Panel-origin start/stop: route through the background master so the session-recovery
    // tab is always spawned and maintained (spec non-goal: OP selling without a master).
    document.addEventListener('op-seller-automation-start-panel', async () => {
      try {
        await chrome.runtime.sendMessage({ type: 'AUTOMATION_START' } satisfies ExtensionMessage);
      } catch (err) {
        console.error('[OP Seller CS] Failed to send AUTOMATION_START:', err);
      }
    });

    document.addEventListener('op-seller-automation-stop-panel', async () => {
      try {
        await chrome.runtime.sendMessage({ type: 'AUTOMATION_STOP' } satisfies ExtensionMessage);
      } catch (err) {
        console.error('[OP Seller CS] Failed to send AUTOMATION_STOP:', err);
      }
    });
```

- [ ] **Step 2: Point the overlay panel at the new event names**

Find where the panel dispatches `op-seller-automation-start` and `op-seller-automation-stop`. Locate with:

```bash
cd extension && grep -rn "op-seller-automation-start\|op-seller-automation-stop" src/overlay/ entrypoints/popup/ entrypoints/*.ts
```

In any file inside `src/overlay/` that dispatches these events (likely `src/overlay/panel.ts`), rename the dispatched events:
- `'op-seller-automation-start'` → `'op-seller-automation-start-panel'`
- `'op-seller-automation-stop'` → `'op-seller-automation-stop-panel'`

Do NOT rename the event names used by the master (in `algo-master.ts`) or the listener for master events in `ea-webapp.content.ts`. Only panel code changes.

- [ ] **Step 3: Typecheck**

Run: `cd extension && npm run compile`
Expected: no type errors.

- [ ] **Step 4: Run full test suite**

Run: `cd extension && npm test`
Expected: PASS. If any test imports the old panel event names, update them.

- [ ] **Step 5: Commit**

```bash
git add extension/entrypoints/ea-webapp.content.ts extension/src/overlay/
git commit -m "feat(extension): route panel-origin OP selling start/stop through master"
```

---

## Task 6: Popup HTML — add mode dropdown, rename button

**Files:**
- Modify: `extension/entrypoints/popup/index.html`

- [ ] **Step 1: Add mode dropdown and restructure section**

Replace the `<h2>OP Seller - Algo</h2>` line and the "Algo Trading" section block with:

```html
  <h2>OP Seller</h2>

  <div class="section">
    <div class="section-title">Mode</div>
    <select id="mode-select" style="background:#2a2a3e;color:#fff;border:1px solid #444;padding:7px 10px;width:100%;border-radius:4px;font-size:13px;outline:none;">
      <option value="algo">Algo Trading</option>
      <option value="op-selling">OP Selling</option>
    </select>
  </div>

  <div class="section">
    <div class="section-title">EA Login (auto-recovery)</div>
    <input type="email" id="email" placeholder="EA Email">
    <input type="password" id="password" placeholder="EA Password">
    <div class="btn-row">
      <button id="save-btn">Save</button>
      <span class="status-text" id="creds-status"></span>
    </div>
  </div>

  <div class="section">
    <div class="section-title" id="control-title">Control</div>
    <input type="number" id="budget" placeholder="Budget (coins)">
    <div class="btn-row">
      <button id="start-btn" class="start">Start</button>
      <button id="stop-btn" class="stop">Stop</button>
    </div>
    <div class="status-text" id="algo-status" style="margin-top: 8px;"></div>
  </div>

  <div class="section">
    <div class="section-title">Session Status</div>
    <div class="master-status">
      <div class="status-dot" id="status-dot"></div>
      <span id="status-label"></span>
    </div>
    <div class="status-detail" id="status-detail"></div>
  </div>
```

Note: The existing `<div class="section">` for "EA Login" already exists before the Algo Trading section — don't duplicate it. The snippet above shows the full new order (Mode → EA Login → Control → Session Status). Remove the original `<h2>OP Seller - Algo</h2>` and the original "Algo Trading" section and replace with this block.

- [ ] **Step 2: Manual visual check**

Run: `cd extension && npm run dev` (if not already running) and open the popup in the browser — confirm dropdown renders above the EA Login section, single "Start" / "Stop" buttons are present, budget input still visible.

- [ ] **Step 3: Commit**

```bash
git add extension/entrypoints/popup/index.html
git commit -m "feat(extension): add mode dropdown to popup, unify start/stop labels"
```

---

## Task 7: Popup main.ts — mode dropdown logic, persistence, dispatch right message

**Files:**
- Modify: `extension/entrypoints/popup/main.ts`

- [ ] **Step 1: Add mode types and storage key**

At the top of `extension/entrypoints/popup/main.ts`, below existing type definitions (after line 22 block), add:

```ts
type TradeMode = 'algo' | 'op-selling';

const SELECTED_MODE_KEY = 'selectedMode';
```

Extend `AlgoMasterState` type (around line 16-22) to include `mode`:

```ts
type AlgoMasterState = {
  status: 'IDLE' | 'SPAWNING' | 'MONITORING' | 'RECOVERING' | 'WAITING_FOR_LOGIN' | 'ERROR';
  tabId: number | null;
  recoveryAttempts: number;
  lastHealthCheck: string | null;
  errorMessage: string | null;
  mode?: TradeMode;
};
```

- [ ] **Step 2: Add mode-select DOM reference**

In the `DOM Elements` section (around line 24-36), add:

```ts
const modeSelect = document.getElementById('mode-select') as HTMLSelectElement;
const budgetInput = document.getElementById('budget') as HTMLInputElement;
const controlTitle = document.getElementById('control-title') as HTMLDivElement;
```

(The existing `budgetInput` reference should already be present; keep it.)

- [ ] **Step 3: Implement mode resolution + persistence**

Add a new section after the existing "Credentials" block and before "Master Status":

```ts
// ── Mode Selection ──────────────────────────────────────────────────────────

async function resolveInitialMode(): Promise<TradeMode> {
  // Priority order: (1) running mode from master state, (2) stored selectedMode, (3) default 'algo'
  const stored = await chrome.storage.local.get([MASTER_KEY, SELECTED_MODE_KEY]);
  const master = (stored[MASTER_KEY] as AlgoMasterState | undefined) ?? null;
  const isRunning = master && master.status !== 'IDLE';
  if (isRunning && master.mode) return master.mode;
  const selected = stored[SELECTED_MODE_KEY] as TradeMode | undefined;
  return selected ?? 'algo';
}

function applyModeUI(mode: TradeMode, isRunning: boolean): void {
  modeSelect.value = mode;
  modeSelect.disabled = isRunning;
  // Budget input is only relevant for algo mode
  budgetInput.style.display = mode === 'algo' ? '' : 'none';
  controlTitle.textContent = mode === 'algo' ? 'Algo Trading' : 'OP Selling';
  startBtn.textContent = 'Start';
  stopBtn.textContent = 'Stop';
}

async function onModeChange(): Promise<void> {
  const mode = modeSelect.value as TradeMode;
  await chrome.storage.local.set({ [SELECTED_MODE_KEY]: mode });
  applyModeUI(mode, false);
}

modeSelect.addEventListener('change', onModeChange);
```

- [ ] **Step 4: Rewrite Start / Stop handlers to dispatch based on mode**

Replace the existing `startBtn.addEventListener('click', ...)` and `stopBtn.addEventListener('click', ...)` blocks (lines 133-186) with:

```ts
startBtn.addEventListener('click', async () => {
  const mode = modeSelect.value as TradeMode;

  startBtn.disabled = true;
  startBtn.textContent = 'Starting...';
  algoStatus.textContent = '';

  try {
    let res: any;
    if (mode === 'algo') {
      const budget = parseInt(budgetInput.value, 10);
      if (!budget || budget <= 0) {
        algoStatus.textContent = 'Enter a valid budget';
        algoStatus.style.color = '#e74c3c';
        startBtn.disabled = false;
        startBtn.textContent = 'Start';
        return;
      }
      res = await chrome.runtime.sendMessage({ type: 'ALGO_START', budget });
      if (res?.type === 'ALGO_START_RESULT' && res.success) {
        algoStatus.textContent = `Algo started with ${budget.toLocaleString()} budget`;
        algoStatus.style.color = '#2ecc71';
        budgetInput.value = '';
      } else {
        algoStatus.textContent = `Failed: ${res?.error || 'Unknown error'}`;
        algoStatus.style.color = '#e74c3c';
      }
    } else {
      res = await chrome.runtime.sendMessage({ type: 'AUTOMATION_START' });
      if (res?.type === 'AUTOMATION_START_RESULT' && res.success) {
        algoStatus.textContent = 'OP selling started';
        algoStatus.style.color = '#2ecc71';
      } else {
        algoStatus.textContent = `Failed: ${res?.error || 'Unknown error'}`;
        algoStatus.style.color = '#e74c3c';
      }
    }
  } catch (err) {
    algoStatus.textContent = `Error: ${err instanceof Error ? err.message : String(err)}`;
    algoStatus.style.color = '#e74c3c';
  }

  startBtn.disabled = false;
  startBtn.textContent = 'Start';
  refreshStatus();
});

stopBtn.addEventListener('click', async () => {
  stopBtn.disabled = true;
  stopBtn.textContent = 'Stopping...';

  try {
    // Read current running mode from master state to decide which stop message to send
    const stored = await chrome.storage.local.get(MASTER_KEY);
    const master = (stored[MASTER_KEY] as AlgoMasterState | undefined) ?? null;
    const runningMode: TradeMode = master?.mode ?? 'algo';
    const stopType = runningMode === 'op-selling' ? 'AUTOMATION_STOP' : 'ALGO_STOP';
    const res: any = await chrome.runtime.sendMessage({ type: stopType });
    const resultType = runningMode === 'op-selling' ? 'AUTOMATION_STOP_RESULT' : 'ALGO_STOP_RESULT';
    if (res?.type === resultType && res.success) {
      algoStatus.textContent = `${runningMode === 'op-selling' ? 'OP selling' : 'Algo'} stopped`;
      algoStatus.style.color = '#888';
    } else {
      algoStatus.textContent = `Failed: ${res?.error || 'Unknown error'}`;
      algoStatus.style.color = '#e74c3c';
    }
  } catch (err) {
    algoStatus.textContent = `Error: ${err instanceof Error ? err.message : String(err)}`;
    algoStatus.style.color = '#e74c3c';
  }

  stopBtn.disabled = false;
  stopBtn.textContent = 'Stop';
  refreshStatus();
});
```

- [ ] **Step 5: Update `refreshStatus` to also reconcile dropdown lock state**

Modify `refreshStatus` (around line 98-129). Replace the function with:

```ts
async function refreshStatus(): Promise<void> {
  const stored = await chrome.storage.local.get(MASTER_KEY);
  const state = (stored[MASTER_KEY] as AlgoMasterState | undefined) ?? null;

  const isRunning = !!state && state.status !== 'IDLE';
  const currentMode: TradeMode = (state?.mode as TradeMode | undefined) ?? (modeSelect.value as TradeMode);
  applyModeUI(currentMode, isRunning);

  if (!state) {
    statusDot.style.background = '#888';
    statusLabel.textContent = 'Idle';
    statusDetail.textContent = '';
    return;
  }

  const display = STATUS_DISPLAY[state.status] ?? STATUS_DISPLAY.IDLE;
  statusDot.style.background = display.color;
  const modeLabel = currentMode === 'op-selling' ? 'OP Selling' : 'Algo';
  statusLabel.textContent = state.status === 'MONITORING' ? `Running: ${modeLabel}` : display.label;

  if (state.status === 'MONITORING' && state.lastHealthCheck) {
    const ago = Math.round((Date.now() - new Date(state.lastHealthCheck).getTime()) / 60_000);
    statusDetail.textContent = `Last health check: ${ago}m ago`;
  } else if (state.status === 'ERROR' && state.errorMessage) {
    statusDetail.textContent = state.errorMessage;
    statusDetail.style.color = '#e74c3c';
  } else if (state.status === 'WAITING_FOR_LOGIN' && state.errorMessage) {
    statusDetail.textContent = state.errorMessage;
    statusDetail.style.color = '#e67e22';
  } else if (state.status === 'RECOVERING') {
    statusDetail.textContent = `Attempt ${state.recoveryAttempts} of 3`;
  } else {
    statusDetail.textContent = '';
    statusDetail.style.color = '#888';
  }
}
```

Also update `STATUS_DISPLAY` (around line 89-96) to use neutral labels (the mode-specific label is added at the call site above):

```ts
const STATUS_DISPLAY: Record<string, { label: string; color: string; detail?: string }> = {
  IDLE: { label: 'Idle', color: '#888' },
  SPAWNING: { label: 'Starting...', color: '#f39c12' },
  MONITORING: { label: 'Session active', color: '#2ecc71' },
  RECOVERING: { label: 'Recovering session...', color: '#f39c12' },
  WAITING_FOR_LOGIN: { label: 'Please log in manually', color: '#e67e22' },
  ERROR: { label: 'Error', color: '#e74c3c' },
};
```

- [ ] **Step 6: Wire initialization — resolve mode on popup open**

Replace the init block at the bottom (lines 188-192):

```ts
// ── Init ────────────────────────────────────────────────────────────────────

loadCredentials();
resolveInitialMode().then(mode => {
  modeSelect.value = mode;
  // applyModeUI will be called by refreshStatus with isRunning state
  refreshStatus();
});
setInterval(refreshStatus, 5_000);
```

- [ ] **Step 7: Typecheck**

Run: `cd extension && npm run compile`
Expected: no type errors.

- [ ] **Step 8: Build**

Run: `cd extension && npm run build`
Expected: build completes successfully.

- [ ] **Step 9: Commit**

```bash
git add extension/entrypoints/popup/main.ts
git commit -m "feat(extension): popup mode dropdown with persistence and mode-aware start/stop"
```

---

## Task 8: Full build + run full test suite

- [ ] **Step 1: Run full vitest suite**

Run: `cd extension && npm test`
Expected: all tests PASS. If any tests break because they construct `AlgoMasterState` literals missing `mode`, add `mode: 'algo'`.

- [ ] **Step 2: Typecheck**

Run: `cd extension && npm run compile`
Expected: no errors.

- [ ] **Step 3: Production build**

Run: `cd extension && npm run build`
Expected: build writes `extension/.output/chrome-mv3/` successfully.

- [ ] **Step 4: Commit any cleanup**

If the previous steps surfaced leftover issues requiring code changes:

```bash
git add -A
git commit -m "chore(extension): fix type literals after AlgoMasterState mode addition"
```

Otherwise skip this step.

---

## Task 9: Manual E2E verification (per spec Testing section)

These cannot be automated — run them in Chrome with the built extension loaded.

- [ ] **Step 1: Load the built extension in Chrome**

Open `chrome://extensions`, enable Developer mode, click "Load unpacked", select `extension/.output/chrome-mv3/`. If it was previously loaded, click the reload icon.

- [ ] **Step 2: E2E-1 — Algo mode end-to-end**

1. Open the extension popup.
2. Confirm mode dropdown defaults to "Algo Trading".
3. Enter EA credentials if not already saved.
4. Enter a budget and click Start.
5. Confirm the EA web app tab opens (master spawns worker) and algo loop begins.
6. Confirm dropdown is disabled while running.

Expected: Status reads "Running: Algo". Master state in storage shows `mode: "algo"`.

- [ ] **Step 3: E2E-2 — Algo session recovery**

1. While algo is running, log out of EA (or close the EA tab).
2. Wait up to 5 minutes for health check to fire (or use "Inspect" > Service Worker > trigger the alarm manually if known).

Expected: Master detects session death, reopens tab, re-logs in using saved credentials, algo loop resumes.

- [ ] **Step 4: E2E-3 — Stop algo, switch to OP selling**

1. Click Stop in the popup.
2. Confirm dropdown re-enables.
3. Change dropdown to "OP Selling".
4. Confirm budget input hides (OP selling doesn't take budget).
5. Click Start.

Expected: EA web app tab reopens/comes to front, OP selling engine starts, status reads "Running: OP Selling", master state `mode: "op-selling"`.

- [ ] **Step 5: E2E-4 — OP selling session recovery**

1. Log out / close EA tab while OP selling is running.
2. Wait for recovery.

Expected: Master re-opens EA tab, re-logs in, OP selling resumes (not algo).

- [ ] **Step 6: E2E-5 — Dropdown lock during run**

Verify: dropdown is `disabled` while either mode is running; re-enabled after Stop. Reload the popup mid-run — dropdown should re-show the running mode and be disabled.

- [ ] **Step 7: E2E-6 — Persistence**

Start OP selling. Close the popup. Reopen. Dropdown should still show "OP Selling" and be disabled.

Stop. Close popup. Reopen. Dropdown should still show "OP Selling" (persisted via `selectedMode`).

- [ ] **Step 8: Final commit (empty if no fixes needed)**

Only if manual testing surfaced fixes:

```bash
git add -A
git commit -m "fix(extension): address issues found in E2E verification"
```

Otherwise, this task is complete without a commit.
