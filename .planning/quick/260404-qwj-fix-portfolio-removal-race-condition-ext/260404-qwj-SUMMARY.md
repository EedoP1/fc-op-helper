---
phase: quick
plan: 260404-qwj
subsystem: portfolio-management
tags: [race-condition, idempotent, extension, portfolio-generation]
dependency_graph:
  requires: []
  provides: [idempotent-portfolio-removal, banned-ea-ids-filter]
  affects: [portfolio-draft-ui, generate-endpoint]
tech_stack:
  added: []
  patterns: [idempotent-regenerate, banned-set-accumulation, queued-regeneration]
key_files:
  created: []
  modified:
    - src/server/api/_helpers.py
    - src/server/api/portfolio_read.py
    - tests/test_portfolio_swap_preview.py
    - extension/src/messages.ts
    - extension/entrypoints/background.ts
    - extension/src/overlay/panel.ts
    - extension/tests/overlay.test.ts
    - extension/tests/background.test.ts
decisions:
  - Idempotent regenerate replaces partial swap — budget + banned_ea_ids always produces same result
  - regenerateQueued flag queues rapid clicks without racing instead of blocking them
  - swap-preview endpoint kept as dead code — no breaking changes for other consumers
metrics:
  duration: ~15 min
  completed: 2026-04-04
  tasks_completed: 2
  files_changed: 8
---

# Quick 260404-qwj: Fix Portfolio Removal Race Condition — Summary

**One-liner:** Replaced partial swap-preview flow with idempotent regenerate-with-banned-ids, eliminating the race condition where rapid X-clicks caused portfolio to exceed TARGET_PLAYER_COUNT.

## What Was Built

### Task 1: Server — banned_ea_ids on generate endpoint (commit 26a4916)

- `GenerateRequest` in `_helpers.py` gains `banned_ea_ids: list[int] = []` optional field
- `generate_portfolio` in `portfolio_read.py` filters banned IDs before passing scored list to optimizer
- 2 new tests in `test_portfolio_swap_preview.py`:
  - `test_generate_with_banned_ea_ids`: verifies banned player absent from result
  - `test_generate_banned_ea_ids_empty_default`: verifies backward compatibility (no field = no change)
- All 10 tests pass

### Task 2: Extension — X button sends PORTFOLIO_GENERATE with banned set (commit e063cd0)

- `messages.ts`: `PORTFOLIO_GENERATE` type gains optional `banned_ea_ids?: number[]` field
- `background.ts`: `handlePortfolioGenerate` accepts and forwards `banned_ea_ids` in POST body
- `panel.ts`: `removeBtn` click handler replaced entirely:
  - Adds removed player to `removedEaIds` set and splices from `draftPlayers` immediately (instant UI)
  - If regenerate in flight: sets `regenerateQueued = true` (queued, not raced)
  - Fires `PORTFOLIO_GENERATE` with `{ budget: draftBudget, banned_ea_ids: [...removedEaIds] }`
  - On response: replaces `draftPlayers` entirely with `res.data` from server
  - On completion: if `regenerateQueued`, fires another regenerate (handles rapid clicks)
  - Added `regenerateQueued` variable alongside `swapInFlight`; both reset on empty state transition
- `overlay.test.ts`: X-button test updated to assert `PORTFOLIO_GENERATE` + `banned_ea_ids` instead of `PORTFOLIO_SWAP`
- `background.test.ts`: new test verifies `banned_ea_ids` appear in POST body sent to server
- All 62 extension tests pass

## Decisions Made

1. **Idempotent regenerate over partial swap**: Same `budget + banned_ea_ids` always returns the same portfolio. No server state needed. Eliminates the race condition entirely.
2. **Queue, don't block rapid clicks**: `regenerateQueued` flag means a second removal during an in-flight request re-fires with the full accumulated banned set when the first request completes — consistent final state without races.
3. **swap-preview endpoint kept**: Left as dead code. No other consumers known but removing it would be an unnecessary breaking change. Can be cleaned up in a future pass.

## Deviations from Plan

None — plan executed exactly as written.

## Verification

- Server: `python -m pytest tests/test_portfolio_swap_preview.py` → 10 passed
- Extension: `npm test -- --run` → 62 passed (6 test files)

## Self-Check: PASSED

All files exist and commits verified.
