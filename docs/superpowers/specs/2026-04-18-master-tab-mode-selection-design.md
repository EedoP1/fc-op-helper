# Master Tab Mode Selection — Design

**Date:** 2026-04-18
**Scope:** Chrome extension only (`extension/`)
**Status:** Approved for implementation planning

## Problem

The "master tab" feature — a Chrome extension tab that keeps the EA Sports FC web app session alive, handles auto-login on session death, and manages a worker tab that runs automation — is currently hardwired to algo trading mode only.

OP selling mode (`AUTOMATION_START`) runs a separate automation loop in the EA web app tab but has no session recovery. When the EA session dies, OP selling stops and the user has to manually re-login.

The user wants the master tab's session-recovery capability to be usable for OP selling too, with a UI control to choose which mode the master drives.

## Goal

Let the user pick Algo or OP Selling from a dropdown in the popup. Whichever mode they pick, the master tab spawns a worker, keeps the session alive, and auto-restarts the chosen automation loop on recovery.

## Non-goals

- Running algo and OP selling simultaneously. One mode at a time.
- Separate credentials per mode. One shared EA login covers both.
- Preserving the old "OP selling without a master tab" path. After this change, OP selling always uses the master.
- UI polish beyond the new dropdown. Existing popup styling is kept.
- Per-mode settings panels. If algo/OP selling diverge on config later, that's a future change.
- Any change to the Python backend, scoring pipeline, or non-extension code.

## Architecture

The algo master becomes a mode-agnostic session manager. Minimal renaming — the state item and file stay named `algoMaster*`, with an added `mode` field.

### Current flow (algo only)

```
popup → ALGO_START → background.handleAlgoStart()
  → startAlgoMaster()
  → spawns EA web app tab (worker)
  → worker runs runAlgoAutomationLoop in main world
  → on session death: master recovers, worker restarts algo loop
```

### New flow (mode-aware)

```
popup → ALGO_START or AUTOMATION_START {mode is implicit from message type}
  → background.handleAlgoStart() / handleAutomationStart()
  → sets algoMasterState.mode = "algo" | "op-selling"
  → startMaster() (shared helper)
  → spawns EA web app tab (worker)
  → worker reads algoMasterState.mode → runs runAlgoAutomationLoop OR runAutomationLoop
  → on session death: master recovers, worker restarts whichever loop matches stored mode
```

### Touch points

- `extension/src/algo-master.ts` — `startAlgoMaster`/`stopAlgoMaster` become mode-agnostic; no branching on mode inside the master itself (it just manages the tab + recovery)
- `extension/entrypoints/background.ts` — `handleAlgoStart` and `handleAutomationStart` both set the mode in state and delegate to a shared `startMaster()` helper; corresponding `*Stop` handlers delegate to a shared `stopMaster()`
- `extension/entrypoints/ea-webapp-main.content.ts` — auto-resume block (current lines ~286–300) branches on `algoMasterState.mode` and sends `algo-start` or `start` to the in-tab command router; existing command cases (`case 'start'`, `case 'algo-start'`) stay untouched
- `extension/entrypoints/popup/main.ts` — adds mode dropdown, persists selection, sends the right start/stop message

## State

### `algoMasterState` — extended

Add one field:

```ts
{
  // existing fields unchanged
  mode: "algo" | "op-selling",
}
```

### `chrome.storage.local` — new key

```ts
selectedMode: "algo" | "op-selling"   // popup's last-selected dropdown value
```

### Migration

- If `algoMasterState` exists without a `mode` field on first load after upgrade, default to `"algo"`. Preserves behavior for anyone mid-algo-run during the upgrade.
- If `selectedMode` isn't set, popup defaults the dropdown to `"algo"`.

## Messages

**No new message types. No renames.** Existing names stay so any external caller (if any) is unaffected.

- `ALGO_START` — sets `mode: "algo"` in master state, calls shared `startMaster()`
- `ALGO_STOP` — calls shared `stopMaster()`
- `AUTOMATION_START` — sets `mode: "op-selling"` in master state, calls shared `startMaster()`
- `AUTOMATION_STOP` — calls shared `stopMaster()`

The popup sends `ALGO_START` or `AUTOMATION_START` based on the dropdown. For stop, the popup sends the stop message that matches the currently running mode (read from `algoMasterState.mode` when the popup renders).

## UI

Popup layout (top to bottom):

1. **Mode dropdown:** `Algo` / `OP Selling`
2. **Credentials form** — unchanged, one shared set for both modes
3. **Start / Stop button** — single button that toggles label based on running state
4. **Status readout:** `Idle` / `Running: Algo` / `Running: OP Selling`

Behavior:

- Dropdown is **disabled** while any mode is running. Re-enabled when stopped.
- Dropdown value persists in `chrome.storage.local.selectedMode`. On popup open, the resolution order is: (1) if a mode is currently running, show `algoMasterState.mode`; (2) else use `selectedMode` from storage; (3) else default to `"algo"`.
- On popup open, popup queries background for current run state so it can render the correct status and lock the dropdown.
- `Start` click: reads dropdown, sends `ALGO_START` or `AUTOMATION_START`.
- `Stop` click: popup tracks what it started; sends the matching stop message.
- Mid-session switch is explicit: user must Stop, change dropdown, then Start again. No auto-switch.

## Error handling

No new error paths introduced. Existing error handling in algo-master (session recovery retries, worker respawn, credential failure logging) applies to both modes identically.

If `algoMasterState.mode` is somehow unset when the worker resumes (shouldn't happen after migration, but defensive), worker defaults to `"algo"` and logs a warning.

## Testing

- Unit-ish: verify background handlers set `mode` correctly on each start message
- Manual E2E:
  1. Select `Algo`, Start → confirm worker runs algo loop, master tab present
  2. Kill EA session (close tab / log out) → confirm auto-recovery restarts algo
  3. Stop, switch to `OP Selling`, Start → confirm worker runs OP selling loop, master tab present
  4. Kill EA session again → confirm auto-recovery restarts OP selling
  5. Confirm dropdown disabled during both runs, re-enabled after Stop
  6. Reload popup mid-run → confirm dropdown shows running mode, is locked

## Risk / blast radius

Extension-only. Python backend untouched. Touches four files in `extension/`. Reversible via git revert.

The main behavior change visible to existing users: OP selling now always spawns a master tab. Anyone who relied on OP selling running in a tab they manually opened will see the extra tab. Acceptable per user confirmation.
