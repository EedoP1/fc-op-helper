---
phase: 03-cli-as-api-client
verified: 2026-03-25T20:30:00Z
status: passed
score: 4/4 must-haves verified
re_verification: false
---

# Phase 03: CLI as API Client — Verification Report

**Phase Goal:** The CLI is a thin display layer that queries the running backend, so all scoring and portfolio logic executes on the server and the terminal just presents results
**Verified:** 2026-03-25T20:30:00Z
**Status:** passed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | `python -m src.main --budget 1000000` fetches portfolio from API, not fut.gg directly | VERIFIED | `src/main.py` uses `httpx.AsyncClient` to GET `/api/v1/portfolio`; zero references to `futgg_client`, `score_player`, `optimize_portfolio`, or `protocols` (grep count: 0) |
| 2 | `python -m src.main --player {id}` displays detailed score breakdown from API | VERIFIED | `run_player_detail()` GETs `/api/v1/players/{ea_id}`, renders Rich Panel + score table + trend line; test `test_player_detail_display` passes and asserts player name, "Player Detail", club, and "trending up" all appear |
| 3 | CLI with unreachable server prints clear error message and exits code 1 | VERIFIED | `httpx.ConnectError` caught in both `run_portfolio()` and `run_player_detail()`; prints `"Error: Cannot reach server at {url}. Start the backend with: uvicorn src.server.main:app"` and calls `sys.exit(1)`; test `test_server_unreachable_exits_1` passes |
| 4 | Portfolio table and CSV export produce correct columns mapped from API response | VERIFIED | `display_results()` renders columns: #, Player, OVR, Pos, Buy, Margin, ExpProf, OP%, Efficiency; `export_csv()` writes: Rank, Player, Rating, Position, Buy, Margin, Expected Profit, OP Ratio, Efficiency; tests `test_portfolio_csv_export` and `test_pipeline_csv_contains_all_players` pass |

**Score:** 4/4 truths verified

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `src/main.py` | CLI entry point that queries backend API | VERIFIED | 272 lines; contains `httpx.AsyncClient`, `DEFAULT_SERVER_URL`, `run_portfolio()`, `run_player_detail()`, `display_results()`, `export_csv()`; no old scoring imports |
| `tests/test_cli.py` | Tests for CLI API client behavior | VERIFIED | 228 lines; 8 test functions using `CliRunner` and `unittest.mock.patch`; all 8 pass |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `src/main.py` | `/api/v1/portfolio` | `httpx GET request` | WIRED | Line 44: `await client.get(f"{url}/api/v1/portfolio", params={"budget": budget})`; response mapped and passed to `display_results()` |
| `src/main.py` | `/api/v1/players/{ea_id}` | `httpx GET request` | WIRED | Line 93: `await client.get(f"{url}/api/v1/players/{ea_id}")`; response rendered in Rich panel + score table + trend |

### Data-Flow Trace (Level 4)

Data-flow tracing at Level 4 is not applicable here in the traditional sense: `src/main.py` is a pure API client — it renders data that flows in from HTTP responses, not from a local database or store. The flow is: API response JSON -> field mapping dict -> `display_results()` / Rich panel rendering.

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|--------------------|--------|
| `src/main.py` `display_results()` | `selected` (list of dicts) | `data["data"]` from portfolio API response | Yes — mapped from `resp.json()["data"]` at line 62–73; `budget_used` from `data["budget_used"]` at line 75 | FLOWING |
| `src/main.py` `run_player_detail()` | `score`, `trend`, player fields | `resp.json()` from players API | Yes — all fields read from `data` dict at lines 116–153; `score = data.get("current_score")` handles null gracefully | FLOWING |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| All 8 CLI unit tests pass | `python -m pytest tests/test_cli.py -x -v` | 8 passed in 0.27s | PASS |
| All 3 integration tests pass | `python -m pytest tests/test_integration.py -x -v` | 3 passed in 0.24s | PASS |
| No old scoring imports in main.py | `grep -c "futgg_client\|score_player\|optimize_portfolio" src/main.py` | 0 | PASS |
| Both API endpoints referenced | `grep -c "api/v1/portfolio\|api/v1/players" src/main.py` | 2 | PASS |
| sys.exit(1) present for error handling | `grep -c "sys.exit(1)" src/main.py` | 7 | PASS |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|----------|
| CLI-01 | 03-01-PLAN.md | CLI queries the server API instead of scoring directly | SATISFIED | `src/main.py` contains zero references to `futgg_client`, `score_player`, `optimize_portfolio`; all data comes from `httpx.AsyncClient` GET requests |
| CLI-02 | 03-01-PLAN.md | CLI accepts a budget and displays the optimized portfolio from the API | SATISFIED | `--budget` flag triggers `run_portfolio()` which GETs `/api/v1/portfolio?budget=N`, maps response, and calls `display_results()` + `export_csv()` |
| CLI-03 | 03-01-PLAN.md | CLI can show detailed score breakdown for a specific player | SATISFIED | `--player` flag triggers `run_player_detail()` which GETs `/api/v1/players/{ea_id}` and renders Rich panel with current_score breakdown (buy/sell/net profit/margin/op_sales/op_ratio/expected_profit/efficiency/sales_per_hour/scored_at) plus trend line |

No orphaned requirements: REQUIREMENTS.md maps CLI-01, CLI-02, CLI-03 all to Phase 3, and all three are claimed and verified in 03-01-PLAN.md.

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| — | — | — | — | No anti-patterns detected |

Scans performed:
- TODO/FIXME/HACK/PLACEHOLDER: 0 matches in `src/main.py`
- `return null / return [] / return {}`: 0 matches
- Empty handlers / pass-only bodies: 0 matches
- Hardcoded empty props: 0 matches

### Human Verification Required

None. All three success criteria are verifiable programmatically:

1. No fut.gg calls from CLI — confirmed by absence of old imports and confirmed by test mocks intercepting `httpx.AsyncClient` (no other HTTP client in `src/main.py`).
2. Player detail matches server-side calculation — the CLI is a display-only layer; it renders whatever the API returns without re-computing. The score breakdown fields are passed through verbatim from the JSON response, so correctness depends on the server (Phase 2), not the CLI.
3. Offline error message — confirmed by `test_server_unreachable_exits_1` test passing with explicit `httpx.ConnectError` injection.

### Gaps Summary

No gaps. All must-haves verified, all artifacts substantive and wired, all key links confirmed present in source, all tests pass.

**Note on Phase 2 dependency:** Phase 3 is marked Complete in ROADMAP.md but Phase 2 (Full API Surface) is listed as "In progress" with 0/2 plans complete. The Phase 3 CLI is correctly implemented as an API client, but the backend endpoints it calls (`/api/v1/portfolio`, `/api/v1/players/{ea_id}`) may not yet be fully implemented. This is a Phase 2 concern, not a Phase 3 defect — the CLI correctly delegates all computation to the server, which is Phase 3's stated goal.

---

_Verified: 2026-03-25T20:30:00Z_
_Verifier: Claude (gsd-verifier)_
