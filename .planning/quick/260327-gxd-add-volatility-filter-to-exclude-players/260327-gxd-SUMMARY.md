---
phase: quick
plan: 260327-gxd
subsystem: portfolio-api
tags: [volatility-filter, portfolio, market-data, scoring]
dependency_graph:
  requires: [src/server/models_db.MarketSnapshot, src/server/api/portfolio.py]
  provides: [_get_volatile_ea_ids, VOLATILITY_MAX_PRICE_INCREASE_PCT, VOLATILITY_LOOKBACK_DAYS]
  affects: [GET /portfolio, POST /portfolio/generate, POST /portfolio/swap-preview, DELETE /portfolio/{ea_id}]
tech_stack:
  added: []
  patterns: [SQLAlchemy aggregation subqueries with func.min/func.max on captured_at, session-scoped volatility check before session close]
key_files:
  created: []
  modified:
    - src/config.py
    - src/server/api/portfolio.py
    - tests/test_portfolio.py
decisions:
  - Volatility check is done inside the async session block (not after close) so the helper can issue its own DB queries
  - Used two aliased subqueries (earliest + latest) joined on ea_id for simple per-player price comparison
  - Players with fewer than 2 distinct snapshot timestamps in the lookback window are not flagged (insufficient data rule)
  - volatile set merged into excluded set for swap_preview and delete_portfolio_player to maintain single exclusion pass
metrics:
  duration: ~5 min
  completed_date: "2026-03-27"
  tasks: 2
  files_modified: 3
---

# Quick Task 260327-gxd: Add Volatility Filter to Portfolio Endpoints Summary

**One-liner:** MarketSnapshot-based price-spike detection using 3-day earliest/latest BIN comparison with >30% threshold, applied to all four portfolio query paths before optimizer runs.

## Tasks Completed

| # | Task | Commit | Files |
|---|------|--------|-------|
| 1 (RED) | Add failing volatility filter tests | 913a456 | tests/test_portfolio.py |
| 1 (GREEN) | Add volatility filter config and helper function | c8a45c5 | src/config.py, src/server/api/portfolio.py |
| 2 | Apply volatility filter to all portfolio endpoints | c55dc80 | src/server/api/portfolio.py, tests/test_portfolio.py |

## What Was Built

### Config constants (src/config.py)

```python
VOLATILITY_MAX_PRICE_INCREASE_PCT = 30  # players with >30% price increase are excluded
VOLATILITY_LOOKBACK_DAYS = 3            # how far back to check for the price spike
```

### Helper function (src/server/api/portfolio.py)

`_get_volatile_ea_ids(session, ea_ids)` — queries MarketSnapshot rows within the lookback window, groups by ea_id to find the earliest and latest BIN prices, and returns the set of ea_ids where `(latest - earliest) / earliest > 0.30`. Players with fewer than 2 distinct snapshot timestamps are skipped.

### Endpoint integration

Applied in all four portfolio query paths:
- **GET /portfolio** — filters rows inside session block before building scored_list
- **POST /portfolio/generate** — same pattern
- **POST /portfolio/swap-preview** — volatile ea_ids merged into `excluded` set
- **DELETE /portfolio/{ea_id}** — volatile ea_ids merged into `excluded` set for replacement candidates

Each path logs at INFO level how many candidates were removed.

## Tests Added

- `test_volatile_player_50pct_increase_is_flagged` — 50% spike returns ea_id in volatile set
- `test_stable_player_10pct_increase_not_flagged` — 10% increase not flagged
- `test_insufficient_data_not_flagged` — single snapshot, not flagged
- `test_price_decrease_not_flagged` — price drop not flagged
- `test_mixed_players_only_volatile_flagged` — only 3005 (50% spike) in volatile set from 3-player batch
- `test_get_portfolio_excludes_volatile_player` — integration: volatile ea_id absent from GET response
- `test_generate_portfolio_excludes_volatile_player` — integration: volatile ea_id absent from POST response

**Final test count:** 14 tests in test_portfolio.py (7 original + 7 new), all passing.

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

None.

## Self-Check: PASSED

- src/config.py VOLATILITY constants: FOUND
- src/server/api/portfolio.py _get_volatile_ea_ids: FOUND
- All 4 endpoint filter applications: FOUND (lines 235, 349, 494, 652)
- Commits: 913a456, c8a45c5, c55dc80 — all present in git log
