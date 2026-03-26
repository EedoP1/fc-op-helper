# Phase 6: Extension Architecture Foundation - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-03-27
**Phase:** 06-extension-architecture-foundation
**Areas discussed:** Polling & keepalive, State persistence, Message protocol design, Content script lifecycle

---

## Polling & Keepalive

| Option | Description | Selected |
|--------|-------------|----------|
| Alarm + setTimeout combo | chrome.alarms fires every 1 min as keepalive; setTimeout(30s) handles actual polling | |
| Just use 1-minute polling | Simplify to 60s intervals via chrome.alarms only | ✓ |
| Persistent connection | Keep a long-lived fetch/SSE connection to backend | |

**User's choice:** Just use 1-minute polling
**Notes:** User preferred simplicity over 30s responsiveness.

### Wake Behavior

| Option | Description | Selected |
|--------|-------------|----------|
| Poll immediately on wake | Fetch pending action right away after termination | ✓ |
| Wait for next alarm | Let the alarm cycle handle everything | |

**User's choice:** Poll immediately on wake

### Polling Gate

| Option | Description | Selected |
|--------|-------------|----------|
| Yes — toggle in storage | chrome.storage.local stores 'enabled' flag | ✓ |
| Always poll when installed | No gate, always polls | |

**User's choice:** Yes — toggle in storage

---

## State Persistence

### What to Persist

| Option | Description | Selected |
|--------|-------------|----------|
| Enabled/disabled flag | Polling gate toggle | |
| Last fetched action | Cache pending action | |
| Backend URL | Configurable setting | |
| Portfolio snapshot | Cache portfolio list | |

**User's choice:** "You decide" — deferred to Claude's discretion
**Notes:** User did not want to specify individual persistence items.

### Backend URL

| Option | Description | Selected |
|--------|-------------|----------|
| Hardcoded localhost | v1.1 is localhost-only | ✓ |
| Configurable in storage | Store URL in chrome.storage.local | |

**User's choice:** Hardcoded localhost

---

## Message Protocol Design

### Message Shape

| Option | Description | Selected |
|--------|-------------|----------|
| Discriminated unions | TypeScript discriminated unions with compile-time safety | ✓ |
| Generic envelope | { type: string, payload: unknown } with runtime validation | |
| You decide | Claude picks best approach | |

**User's choice:** Discriminated unions

### Message Types for Phase 6

| Option | Description | Selected |
|--------|-------------|----------|
| Minimal — PING/PONG only | Phase 6 just proves channel works, stubs for future | ✓ |
| Full set with placeholder payloads | All expected types defined now | |
| Just the type system | Base type and pattern only | |

**User's choice:** Minimal — PING/PONG only

---

## Content Script Lifecycle

### SPA Navigation Detection

| Option | Description | Selected |
|--------|-------------|----------|
| MutationObserver on root | Watch top-level DOM node for child changes | ✓ |
| URL polling | setInterval checking location.href | |
| You decide | Claude picks based on EA Web App research | |

**User's choice:** MutationObserver on root

### Injection Scope

| Option | Description | Selected |
|--------|-------------|----------|
| All EA Web App pages | Inject everywhere, lightweight listener | ✓ |
| Transfer market only | Only inject on transfer market URLs | |
| You decide | Claude picks based on URL patterns | |

**User's choice:** All EA Web App pages

### Disconnection Recovery

| Option | Description | Selected |
|--------|-------------|----------|
| Auto-reconnect with retry | Detect disconnection, retry every few seconds | ✓ |
| Alert user to refresh | Show visible warning | |
| You decide | Claude picks recovery strategy | |

**User's choice:** Auto-reconnect with retry

---

## Claude's Discretion

- What to persist in chrome.storage.local beyond the enabled flag
- WXT project structure and file organization
- TypeScript configuration and build setup
- Test approach for service worker and content script
- Error handling patterns within the message channel

## Deferred Ideas

None — discussion stayed within phase scope.
