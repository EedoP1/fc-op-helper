---
phase: quick
plan: 260330-nuv
subsystem: portfolio-api, scanner
tags: [filter, icons, portfolio, scanner, sql]
dependency_graph:
  requires: []
  provides: [base-icon-exclusion-sql, base-icon-exclusion-scanner]
  affects: [portfolio-endpoints, scanner-bootstrap, scanner-discovery]
tech_stack:
  added: []
  patterns: [SQL-WHERE-filter, list-comprehension-filter]
key_files:
  modified:
    - src/server/api/portfolio.py
    - src/server/scanner.py
decisions:
  - SQL-level filter in _fetch_latest_viable_scores covers all portfolio endpoints simultaneously
  - Python-level filter in get_portfolio removed as redundant after SQL fix
  - Scanner uses rarityName field (API source) instead of card_type (DB field) to filter pre-insert
metrics:
  duration: ~5 min
  completed: "2026-03-30T14:15:19Z"
  tasks_completed: 2
  files_modified: 2
---

# Quick Task 260330-nuv: Add filter to ignore base icon players — Summary

## One-liner

SQL-level `card_type != 'Icon'` filter in portfolio query plus pre-insert `rarityName != 'Icon'` filter in scanner bootstrap/discovery to exclude unprofitable base icons.

## What Was Done

### Task 1: SQL-level Icon filter in portfolio query

Added `AND pr.card_type != 'Icon'` to the WHERE clause of `_fetch_latest_viable_scores()` in `src/server/api/portfolio.py`. This single change filters base icons at the DB level for all portfolio endpoints simultaneously: GET /portfolio, POST /portfolio/generate, POST /portfolio/swap-preview, POST /portfolio/rebalance, and DELETE /portfolio/{ea_id}.

The previously existing Python-level filter in `get_portfolio` (a list comprehension + logging block that checked `r.card_type != "Icon"`) was removed as it became redundant.

### Task 2: Scanner bootstrap and discovery Icon filter

Added a list comprehension filter in both `run_bootstrap()` and `run_discovery()` in `src/server/scanner.py`. Each filter runs immediately after the `discover_players()` call and its elapsed-time log, before the player list is used for DB upserts or building `discovered_ids`. Base icons (players where `rarityName == "Icon"`) are skipped entirely — never upserted into the DB and never queued for API scoring calls.

Both filters log the count of icons skipped when any are found.

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

None.

## Verification

- `card_type != 'Icon'` confirmed present in `_fetch_latest_viable_scores` SQL query
- Python-level Icon filter confirmed removed from `get_portfolio`
- `rarityName` and `Icon` filters confirmed present in both `run_bootstrap` and `run_discovery`
- Non-base icon variants (e.g., "Icon Hero") are unaffected — filter is exact match on `"Icon"` only

## Commits

- `11f29b4`: feat(quick-260330-nuv): add SQL-level Icon filter to portfolio query
- `599c9b9`: feat(quick-260330-nuv): skip base icons during scanner bootstrap and discovery

## Self-Check: PASSED

- src/server/api/portfolio.py: modified (SQL filter added, Python filter removed)
- src/server/scanner.py: modified (bootstrap and discovery filters added)
- Commits 11f29b4 and 599c9b9 both exist in git log
