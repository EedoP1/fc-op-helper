# Algo Popup Page — Design Spec

**Date**: 2026-04-10
**Status**: Draft

## Problem

The algo credentials form and master status live inside the overlay panel, which is injected into the EA web app page. When the session expires and the page dies, the overlay dies with it — you can't configure credentials or see recovery status.

## Solution

Add a Chrome extension popup page (click the extension icon in the toolbar) with two sections: credentials configuration and master recovery status. Remove the credentials section from the overlay panel's algo tab to avoid duplication.

## Popup Layout

350px wide popup with dark theme matching the overlay panel.

### Section 1: EA Credentials

- Email input field
- Password input field (type="password", masked)
- Save button + status indicator ("Credentials saved" / "Not configured")
- Reads/writes `algoCredentialsItem` from `chrome.storage.local`
- Same logic as the current overlay credentials form

### Section 2: Master Status

Reads `algoMasterStateItem` from storage, displays current state:

| State | Display |
|-------|---------|
| IDLE | "Algo inactive" — gray |
| SPAWNING | "Starting..." — yellow |
| MONITORING | "Session active" — green, shows last health check time |
| RECOVERING | "Recovering session..." — yellow |
| WAITING_FOR_LOGIN | "Please log in manually" — orange warning |
| ERROR | Error message — red |

Auto-refreshes every 5 seconds via `setInterval` reading storage.

## File Changes

### New Files

| File | Purpose |
|------|---------|
| `extension/entrypoints/popup.html` | Minimal HTML shell for the popup |
| `extension/entrypoints/popup/main.ts` | Popup logic: credentials form + master status display |

### Modified Files

| File | Changes |
|------|---------|
| `extension/src/overlay/panel.ts` | Remove the credentials section from `renderAlgoTab` (the block added in Task 8, ~120 lines from `credsSection` through `parent.appendChild(credsSection)`) |

### WXT Configuration

No changes to `wxt.config.ts` needed — WXT auto-discovers `entrypoints/popup.html` and registers it as `default_popup` in the manifest.

## Styling

- Dark theme: `#1a1a2e` background, `#fff` text
- Input fields: `#2a2a3e` background, `#444` border
- Same color conventions as overlay (`#2ecc71` green, `#e74c3c` red, `#f39c12` yellow/orange)
- Inline styles (no CSS files), matching the overlay pattern
