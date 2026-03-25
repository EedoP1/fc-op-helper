---
phase: 03-cli-as-api-client
plan: 01
subsystem: cli
tags: [cli, api-client, httpx, rich, refactor]
dependency_graph:
  requires: []
  provides: [cli-api-client]
  affects: [src/main.py, tests/test_cli.py]
tech_stack:
  added: [httpx (direct usage in CLI)]
  patterns: [thin-api-client, click-mutual-exclusion, asyncio-run, rich-panel]
key_files:
  created:
    - tests/test_cli.py
  modified:
    - src/main.py
decisions:
  - "DEFAULT_SERVER_URL hardcoded to localhost:8000 per D-01; overridable via --url"
  - "--budget and --player are validated as mutually exclusive in Python, not via click.option(is_eager=True), for clearer error messages"
  - "display_results() drops sell_price, net_profit, op_sales columns — those are not in portfolio API response; expected_profit replaces net_profit in summary"
  - "export_csv() columns align with API response: Rank, Player, Rating, Position, Buy, Margin, Expected Profit, OP Ratio, Efficiency"
metrics:
  duration: 103s
  completed_date: "2026-03-25"
  tasks_completed: 1
  files_changed: 2
---

# Phase 03 Plan 01: CLI Rewrite as API Client Summary

Rewrote `src/main.py` from a direct fut.gg scoring pipeline into a thin httpx API client that queries `/api/v1/portfolio` and `/api/v1/players/{ea_id}`, then displays results using the existing Rich table and CSV export.

## What Was Built

**src/main.py** — Completely rewritten. All old scoring imports (`FutGGClient`, `score_player`, `optimize_portfolio`, `MarketDataClient`) removed. New CLI has:

- `--budget` mode: GET `/api/v1/portfolio?budget=N` → map response → `display_results()` + `export_csv()`
- `--player` mode: GET `/api/v1/players/{ea_id}` → Rich panel + score breakdown table + trend line
- `--url` option: configurable server URL, default `http://localhost:8000`
- Mutual exclusion: exactly one of `--budget` or `--player` must be provided
- Error handling: `ConnectError` prints readable message with server URL and exits 1; 4xx/5xx responses print status code with text snippet and exit 1

**tests/test_cli.py** — 8 tests using `click.testing.CliRunner` and `unittest.mock.patch` on `httpx.AsyncClient`:

1. `test_portfolio_display` — portfolio renders table with player name
2. `test_portfolio_csv_export` — CSV file created with correct headers and data
3. `test_player_detail_display` — player panel shows name, club, trend
4. `test_server_unreachable_exits_1` — `ConnectError` → exit 1 with URL
5. `test_budget_and_player_mutually_exclusive` — both flags → exit 1
6. `test_neither_budget_nor_player_exits_1` — no flags → exit 1
7. `test_api_returns_500_graceful` — 500 → exit 1, no traceback
8. `test_player_not_found_returns_404_gracefully` — 404 → exit 1, readable message

## Tasks

| # | Name | Status | Commit |
|---|------|--------|--------|
| 1 | Rewrite src/main.py as API client with tests | Done | 2682ff2 |

## Verification Results

```
pytest tests/test_cli.py -x -v → 8 passed in 0.24s
grep "futgg_client|score_player|optimize_portfolio" src/main.py → 0 matches
grep "api/v1/portfolio|api/v1/players" src/main.py → 2 matches
grep "sys.exit(1)" src/main.py → 7 matches
```

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

None — all display paths wired to API response fields.

## Self-Check: PASSED

- src/main.py exists and contains all required patterns
- tests/test_cli.py exists with 8 test functions
- Commit 2682ff2 exists: `feat(03-01): rewrite main.py as API client with tests`
