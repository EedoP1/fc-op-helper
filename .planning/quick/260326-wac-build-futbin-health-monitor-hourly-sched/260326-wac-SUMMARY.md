---
phase: quick
plan: 260326-wac
subsystem: health-monitoring
tags: [futbin, health-check, cli, audit]
dependency_graph:
  requires: [players-table, market-snapshots, listing-observations, snapshot-sales]
  provides: [futbin-client, health-check-cli, health-checks-table]
  affects: [players-table-futbin-id-column]
tech_stack:
  added: [beautifulsoup4]
  patterns: [sync-http-client, sqlite3-direct, rich-tables, click-cli]
key_files:
  created:
    - src/futbin_client.py
    - src/health_check.py
    - tests/test_health_check.py
  modified:
    - requirements.txt
decisions:
  - Used BeautifulSoup for HTML parsing instead of regex for reliability
  - Sync httpx client (not async) since this is a simple CLI tool
  - 1.5s rate limiting between FUTBIN requests
  - Health score weights: sell-through 40%, price accuracy 30%, listing count 15%, price range 15%
  - JSON fallback if HTML table is JS-rendered
metrics:
  duration: 226
  completed: "2026-03-26T21:26:26Z"
  tasks_completed: 1
  tasks_total: 1
  tests_added: 24
  tests_passing: 24
  files_created: 3
  files_modified: 1
---

# Quick Task 260326-wac: Build FUTBIN Health Monitor Summary

FUTBIN health monitor CLI that audits our DB data against FUTBIN reality using BeautifulSoup HTML parsing, weighted health scoring, and rich table output.

## What Was Built

### src/futbin_client.py — FUTBIN HTTP Client
- `FutbinClient` class with sync httpx, realistic User-Agent, 1.5s rate limiting
- `search_player(name)` — searches FUTBIN player page, extracts futbin_id from `/26/player/{id}/` links
- `fetch_sales_page(futbin_id, name)` — parses HTML sales table (Date, Listed for, Sold for, EA Tax, Net Price, Type)
- JSON fallback endpoint if HTML table is empty (JS-rendered)
- Helper functions: `_parse_sales_html`, `_parse_price`, `_parse_futbin_date` (multiple date formats)

### src/health_check.py — CLI Entry Point + Audit Logic
- Click CLI: `python -m src.health_check --count N --verbose`
- Direct sqlite3 connection to `D:/op-seller/op_seller.db`
- Auto-adds `futbin_id` column to players table on first run
- Creates `health_checks` table for result persistence
- 50/50 player selection: half with cached futbin_id, half without (gradually builds cache)
- Skips players with digit-only names (ea_id as string) or empty names
- Per-player health metrics: sell-through rate, price accuracy, listing count ratio, price range match
- Weighted health score (0-100): sell-through 40%, price accuracy 30%, listing count 15%, price range 15%
- Rich table output with color-coded scores (green >= 80, yellow 50-79, red < 50)
- Verbose mode shows detailed per-player breakdown
- Graceful handling: missing DB, no active players, FUTBIN search failures

### tests/test_health_check.py — 24 Unit Tests
- Search player: finds first result, handles not-found, handles HTTP errors
- Sales page: parses full table, handles empty page, handles HTTP errors
- Parsing helpers: price with commas/spaces/zero/empty, date formats, HTML table parsing
- Health score: perfect match (100), completely off (<20), no FUTBIN data, both empty (100), all expired, always in range

## Deviations from Plan

None - plan executed exactly as written.

## Known Stubs

None - all functionality is fully wired.

## Self-Check: PASSED

- [x] src/futbin_client.py exists
- [x] src/health_check.py exists
- [x] tests/test_health_check.py exists
- [x] Commit f45f78d exists
- [x] 24/24 tests passing
