---
phase: quick
plan: 260330-ocv
subsystem: scorer
tags: [scoring, filter, ea-price-cap, futgg-api]
dependency_graph:
  requires: []
  provides: [max_price_range on PlayerMarketData, scorer_v2 EA BIN cap filter]
  affects: [src/models.py, src/futgg_client.py, src/server/scorer_v2.py, src/server/scanner.py]
tech_stack:
  added: []
  patterns: [Optional field with None default for soft guard, is not None check for zero-safe filtering]
key_files:
  created: []
  modified:
    - src/models.py
    - src/futgg_client.py
    - src/server/scorer_v2.py
    - src/server/scanner.py
decisions:
  - Use `is not None` check (not truthiness) so max_price_range=0 would not silently disable the filter
  - Field defaults to None to preserve backward compatibility — no filtering when API data absent
metrics:
  duration: ~5 min
  completed: 2026-03-30
  tasks: 2
  files: 4
---

# Quick 260330-ocv: Filter Players Whose Sell Price Exceeds EA Max BIN Range

**One-liner:** Adds EA max BIN cap filter to scorer_v2 that skips margin tiers producing unlistable sell prices, extracted from fut.gg's `priceRange.maxPrice` field.

## What Was Done

scorer_v2 was picking margin tiers that produce sell prices above EA's transfer market BIN ceiling — prices that cannot physically be listed. The fix threads `priceRange.maxPrice` from the fut.gg prices API through the model, client, and scorer to skip those impossible margins.

## Tasks Completed

| Task | Description | Commit |
|------|-------------|--------|
| 1 | Add `max_price_range` to `PlayerMarketData` and extract from both async and sync fut.gg client methods | ca28220 |
| 2 | Add `max_price_range` param to `score_player_v2` with BIN cap skip logic; wire from `scanner.py` | d8f9834 |

## Changes

### src/models.py
- Added `max_price_range: Optional[int] = None` field to `PlayerMarketData` after `futgg_url`

### src/futgg_client.py
- `get_player_market_data` (async): extracts `prices.get("priceRange", {}).get("maxPrice")` and passes to `PlayerMarketData`
- `get_player_market_data_sync`: same extraction and pass-through for thread-pool path

### src/server/scorer_v2.py
- Added `max_price_range: int | None = None` parameter with full docstring
- Added skip guard in margin loop: `if max_price_range is not None and sell_price > max_price_range: continue`
- Guard placed after `sell_price` is computed, before EA tax and profit calculations

### src/server/scanner.py
- `score_player_v2` call now passes `max_price_range=market_data.max_price_range`

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

None.

## Self-Check: PASSED

- src/models.py: max_price_range field present, Optional[int] with None default
- src/futgg_client.py: both async and sync paths extract priceRange.maxPrice
- src/server/scorer_v2.py: max_price_range parameter present, skip guard in margin loop
- src/server/scanner.py: passes market_data.max_price_range to score_player_v2
- Commits ca28220 and d8f9834 verified in git log
