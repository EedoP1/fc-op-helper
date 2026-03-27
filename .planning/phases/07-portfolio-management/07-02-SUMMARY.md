---
phase: 07-portfolio-management
plan: 02
subsystem: extension
tags: [chrome-extension, messages, storage, service-worker, typescript]
dependency_graph:
  requires: [07-01]
  provides: [portfolio-message-protocol, portfolio-storage, service-worker-portfolio-proxy]
  affects: [extension/src/messages.ts, extension/src/storage.ts, extension/entrypoints/background.ts, extension/entrypoints/ea-webapp.content.ts]
tech_stack:
  added: []
  patterns: [discriminated-union-exhaustive-switch, async-chrome-message-listener, storage-defineItem]
key_files:
  created: []
  modified:
    - extension/src/messages.ts
    - extension/src/storage.ts
    - extension/entrypoints/background.ts
    - extension/entrypoints/ea-webapp.content.ts
    - extension/tests/background.test.ts
    - extension/tests/content.test.ts
decisions:
  - "PORTFOLIO_* request types handled only in service worker, not content script — content script returns false for those types to maintain exhaustive switch without phantom handling"
  - "mapToPortfolioPlayer normalizes buy_price/price field name variants from different backend endpoints"
  - "portfolioItem.setValue called synchronously before sendResponse on confirm — ensures storage is updated before UI can issue PORTFOLIO_LOAD"
metrics:
  duration_minutes: 5
  completed_date: "2026-03-27"
  tasks_completed: 2
  files_modified: 6
---

# Phase 07 Plan 02: Portfolio Message Protocol Summary

**One-liner:** PORTFOLIO_* discriminated union message types with service worker backend proxy and exhaustive content script switch, enabling typed portfolio operations from overlay to backend.

## What Was Built

Extended the extension's typed message protocol with 8 new PORTFOLIO_* message variants and a service worker that proxies portfolio operations to the backend REST API.

### Task 1: Portfolio types in messages.ts and storage.ts

- Added `PortfolioPlayer` type (ea_id, name, rating, position, price, sell_price, margin_pct, expected_profit, op_ratio, efficiency) to storage.ts
- Added `ConfirmedPortfolio` type (players, budget, confirmed_at) to storage.ts
- Added `portfolioItem` storage item (`local:portfolio`) persisting `ConfirmedPortfolio | null`
- Extended `ExtensionMessage` union with 8 new variants: PORTFOLIO_GENERATE, PORTFOLIO_GENERATE_RESULT, PORTFOLIO_CONFIRM, PORTFOLIO_CONFIRM_RESULT, PORTFOLIO_SWAP, PORTFOLIO_SWAP_RESULT, PORTFOLIO_LOAD, PORTFOLIO_LOAD_RESULT
- Imported PortfolioPlayer and ConfirmedPortfolio from storage into messages.ts via type import

### Task 2: Service worker handlers and content script switch

- Added `chrome.runtime.onMessage` listener inside background.ts `main()` handling all 4 PORTFOLIO_* request types
- Implemented `handlePortfolioGenerate` (POST /api/v1/portfolio/generate)
- Implemented `handlePortfolioConfirm` (POST /api/v1/portfolio/confirm + portfolioItem.setValue)
- Implemented `handlePortfolioSwap` (POST /api/v1/portfolio/swap-preview)
- PORTFOLIO_LOAD reads from portfolioItem.getValue() directly (no backend call)
- Added `mapToPortfolioPlayer` helper normalizing buy_price/price field name variants
- Updated ea-webapp.content.ts switch with all 8 PORTFOLIO_* cases before assertNever default — TypeScript exhaustiveness check passes
- Added 4 portfolio handler tests to background.test.ts (generate proxies to backend, confirm stores to portfolioItem, load returns stored portfolio, generate handles fetch error)
- Added 2 content script tests verifying PORTFOLIO_GENERATE and PORTFOLIO_LOAD_RESULT return false from content script handler

## Verification

- TypeScript compiles cleanly: `npx tsc --noEmit` exits 0
- All 18 extension tests pass: 12 original + 6 new portfolio tests

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

None — all portfolio message handlers make real fetch calls or read real storage.

## Self-Check

### Files exist:
- extension/src/messages.ts — modified with PORTFOLIO_* types
- extension/src/storage.ts — modified with PortfolioPlayer, ConfirmedPortfolio, portfolioItem
- extension/entrypoints/background.ts — modified with portfolio handlers
- extension/entrypoints/ea-webapp.content.ts — modified with exhaustive switch
- extension/tests/background.test.ts — modified with portfolio handler tests
- extension/tests/content.test.ts — modified with portfolio content script tests

### Commits:
- 73b3511: feat(07-02): add PORTFOLIO_* message types and storage items
- 279f5a5: feat(07-02): service worker portfolio handlers and exhaustive content script switch
