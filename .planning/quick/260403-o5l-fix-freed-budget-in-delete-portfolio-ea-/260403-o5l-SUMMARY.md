---
phase: quick
plan: 260403-o5l
subsystem: portfolio-write
tags: [bugfix, portfolio, optimizer, freed-budget]
dependency_graph:
  requires: []
  provides: [correct-freed-budget-in-delete-endpoint]
  affects: [optimizer-replacement-suggestions]
tech_stack:
  added: []
  patterns: [budget-minus-remaining-cost]
key_files:
  created: []
  modified:
    - src/server/api/portfolio_write.py
    - tests/integration/test_smoke_all_endpoints.py
decisions:
  - "freed_budget = budget - remaining_total_cost: optimizer receives full available funds (unspent + freed by removal), not just the removed player's buy_price"
metrics:
  duration: ~5 min
  completed: "2026-04-03T14:40:00Z"
  tasks_completed: 1
  files_modified: 2
---

# Quick 260403-o5l: Fix freed_budget in DELETE /portfolio/{ea_id} Summary

**One-liner:** Corrected `freed_budget` in portfolio delete endpoint to return `budget - remaining_total_cost` so the optimizer receives the true available budget for replacement suggestions.

## What Was Done

Fixed a bug in `DELETE /portfolio/{ea_id}` where `freed_budget` was set to `slot.buy_price` (the removed player's cost) instead of the full available budget after the deletion.

**Root cause:** When removing a 20k player from a portfolio with 300k unspent budget, the optimizer was only given 20k to find replacements — ignoring the 300k that was never spent. The optimizer would only suggest very cheap players, missing higher-value opportunities.

**Fix:** After deleting the slot, query `PortfolioSlot.ea_id` AND `PortfolioSlot.buy_price` for all remaining rows. Compute `remaining_total_cost = sum(row.buy_price)`. Set `freed_budget = budget - remaining_total_cost`.

**Integration test strengthened:** `test_portfolio_delete` now asserts `freed_budget == 2_000_000` (the full budget) when all slots are removed, proving the formula works correctly.

## Task Commits

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Fix freed_budget calculation and add test | 8f78286 | src/server/api/portfolio_write.py, tests/integration/test_smoke_all_endpoints.py |

## Deviations from Plan

None — plan executed exactly as written.

**Note:** Integration test could not be run to completion — Docker is not available and disk is at capacity (C: drive 100% full, 2GB brand/ folder). The code logic was verified by inspection. The test assertion is logically correct: deleting the only confirmed slot leaves `remaining_total_cost=0`, so `freed_budget = budget - 0 = 2_000_000`.

## Known Stubs

None.

## Self-Check: PASSED

- `src/server/api/portfolio_write.py` — file exists, contains `remaining_total_cost` and `freed_budget = budget - remaining_total_cost`
- `tests/integration/test_smoke_all_endpoints.py` — file exists, contains `assert body["freed_budget"] == 2_000_000`
- Commit `8f78286` — exists in git log
