---
phase: quick-260418-ddp
plan: 01
subsystem: scanner-discovery
tags: [scanner, discovery, futgg-client, config]
requires: []
provides:
  - "SCANNER_MAX_PRICE=0 sentinel meaning 'no upper bound' on scanner discovery"
  - "FutGGClient.discover_players: max_price<=0 emits URL without price__lte"
  - "MockClient.discover_players: same 'no upper bound' semantics for test parity"
affects:
  - src/config.py
  - src/futgg_client.py
  - src/server/scanner_discovery.py
  - tests/test_futgg_client.py
  - tests/mock_client.py
tech_stack:
  added: []
  patterns:
    - "Sentinel-value semantics (0 = no cap) over None to keep existing signatures"
key_files:
  created: []
  modified:
    - src/config.py
    - src/futgg_client.py
    - src/server/scanner_discovery.py
    - tests/test_futgg_client.py
    - tests/mock_client.py
decisions:
  - "D-01 kept SCANNER_MAX_PRICE in config as 0 sentinel (semantic clarity, minimal churn)"
  - "D-02 dropped budget*0.10 auto-fill in discover_players (the root cause of the silent 500k cap)"
  - "D-03 MockClient mirrors real-client sentinel semantics to prevent future test surprises"
metrics:
  duration_minutes: 3
  completed_date: "2026-04-18"
  tasks: 2
  commits: 2
  files_modified: 5
requirements_completed:
  - QUICK-260418-DDP-01
---

# Quick 260418-ddp: Remove scanner max-price cap — discover all price tiers Summary

Removed the hidden `price__lte=500000` cap from fut.gg scanner discovery so release-day TOTS/TOTY/Icon promo cards (700k–2M+) enter `PlayerRecord` on release day instead of lagging 24h–8d.

## Truths Achieved

All truths from `must_haves.truths` verified:

- [x] Discovery no longer caps player search at 500,000 coins — release-day high-priced promos enter `PlayerRecord` immediately. (Verified by unit test: `max_price=0` URL lacks `price__lte`; scanner_discovery call sites pass `SCANNER_MAX_PRICE` which is now `0`.)
- [x] `FutGGClient.discover_players(budget=…, max_price=0)` builds URL WITHOUT `price__lte` and does NOT auto-fill from `budget`. (Verified by `test_discover_players_no_max_price_omits_price_lte` and `test_discover_players_no_budget_autofill_when_max_price_zero`.)
- [x] `FutGGClient.discover_players(max_price > 0)` still emits `price__lte=<max_price>`. (Verified by `test_discover_players_with_max_price_includes_price_lte`.)
- [x] Both `run_bootstrap` and `run_discovery` call `discover_players` with `max_price=SCANNER_MAX_PRICE` which is now `0` (no cap). (Static read of `src/server/scanner_discovery.py` lines 47–51 and 178–182.)
- [x] `SCANNER_MIN_PRICE = 11_000` unchanged on both call sites. (Static read of `src/config.py` and scanner_discovery call sites.)
- [x] `run_discovery` "mark cold" branch (lines 244–254) untouched — semantics preserved. (No edits to that block; `test_cold_player_not_rediscovered_stays_cold` passes.)

## Artifacts Modified

| File                                | Commit      | Change                                                                 |
| ----------------------------------- | ----------- | ---------------------------------------------------------------------- |
| `src/futgg_client.py`               | `53a6954b`  | Removed `max_price = int(budget * 0.10)` auto-fill; updated docstring  |
| `tests/test_futgg_client.py`        | `53a6954b`  | Added 3 URL-construction tests (RED → GREEN)                            |
| `src/config.py`                     | `b10a135c`  | `SCANNER_MAX_PRICE = 0` (from `500_000`) with sentinel comment         |
| `src/server/scanner_discovery.py`   | `b10a135c`  | Both call sites now pass `budget=0` + explanatory inline comments       |
| `tests/mock_client.py`              | `b10a135c`  | `MockClient.discover_players` honors `max_price<=0` as "no upper bound" |

## Commits

- `53a6954b` — fix(quick-260418-ddp): drop budget*0.10 auto-fill in discover_players (Task 1)
- `b10a135c` — fix(quick-260418-ddp): remove 500k price cap from discovery (SCANNER_MAX_PRICE=0) (Task 2)

## Test Delta

**New tests (Task 1, tests/test_futgg_client.py):**
- `test_discover_players_no_max_price_omits_price_lte` — PASS
- `test_discover_players_with_max_price_includes_price_lte` — PASS
- `test_discover_players_no_budget_autofill_when_max_price_zero` — PASS (was RED on baseline with `price__lte=50000`, now GREEN)

**Targeted change-surface suite (plan verification step 2):**
`python -m pytest tests/test_futgg_client.py tests/test_scanner_discovery.py -v` → **14 passed, 0 failed** (11 in test_futgg_client.py, 3 in test_scanner_discovery.py).

**Full non-algo, non-integration suite (plan verification step 1):**
`python -m pytest --ignore=tests/algo --ignore=tests/integration -q` → **167 passed, 14 failed, 1 skipped**.

The 14 failures are the identical baseline failures documented in `.planning/quick/260418-c65-fix-scanner-data-loss-bugs-playwright-ch/deferred-items.md`:
- `test_cli.py`: `test_portfolio_display`, `test_portfolio_csv_export`
- `test_optimizer.py`: 8 tests (`test_fills_budget`, `test_no_duplicates`, `test_prefers_cheaper_when_budget_tight`, `test_backfill_uses_remaining_budget`, `test_ranks_by_score`, `test_min_profit_filter`, `test_exclude_card_types`, `test_upgrade_swaps_weakest`)
- `test_portfolio_swap.py`: `test_swap_returns_replacements`
- `test_portfolio_swap_preview.py`: `test_swap_preview_returns_multiple_when_slots_available`
- `test_scanner.py`: `test_scan_player_writes_score`, `test_scan_player_sets_scorer_version`

**Delta: 0 new failures.** Pre-existing failures remain out of scope per user constraint and GSD scope-boundary rule.

## Static Audit (plan verification steps 3 & 4)

```
grep -rn "SCANNER_MAX_PRICE\|\b500_000\b\|\b500000\b" src/
```
Result (src/ only):
- `src/config.py:35: SCANNER_MAX_PRICE = 0`
- `src/server/scanner_discovery.py:20,50,181` (import + 2 call sites)

No other `500_000` / `500000` occurrences in `src/` that semantically mean "scanner discovery upper bound." Audit clean.

## Out-of-scope items explicitly untouched

- `SCAN_INTERVAL_SECONDS` (unchanged, 300)
- `SCAN_CONCURRENCY` (unchanged, 10)
- `SCAN_DISPATCH_BATCH_SIZE` / `SCAN_DISPATCH_INTERVAL` (unchanged)
- Thread-pool / `get_batch_market_data` concurrency (unchanged)
- `src/algo/**`, `tests/algo/**` (not touched)
- `run_discovery` "mark cold" branch semantics (untouched — `test_cold_player_not_rediscovered_stays_cold` confirms)

## Deviations from Plan

None — plan executed exactly as written. RED/GREEN progression for Task 1 matched the plan's predictions precisely (Test 3 failed on baseline with `price__lte=50000` as forecast in the plan's "expect" note).

## Follow-up

None specific to this plan:
- Throughput tuning (if needed after user's next scanner run) is separate scope per D-05.
- Live-discovery smoke validation deferred to the user's own scanner run post-merge per D-05.
- The 14 baseline failures in `deferred-items.md` remain a standing follow-up (CLI/optimizer/scorer-v2 contract drift — likely from the quick-260417-sp2 field repurposing).

## Self-Check: PASSED

**Files verified to exist:**
- `src/config.py` — FOUND (SCANNER_MAX_PRICE = 0 at line 35)
- `src/futgg_client.py` — FOUND (auto-fill removed)
- `src/server/scanner_discovery.py` — FOUND (call sites updated)
- `tests/test_futgg_client.py` — FOUND (3 new tests appended)
- `tests/mock_client.py` — FOUND (MockClient updated)

**Commits verified on HEAD:**
- `53a6954b` — FOUND (Task 1)
- `b10a135c` — FOUND (Task 2)
