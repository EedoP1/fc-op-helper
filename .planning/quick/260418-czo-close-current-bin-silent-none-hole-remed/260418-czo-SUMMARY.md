---
phase: quick-260418-czo
plan: 01
subsystem: scanner / data-pipeline
tags: [silent-data-loss, futgg-client, scanner, market-snapshot, remediation]
requires:
  - quick-260418-c65 (PricesFetchError + Playwright poll + cold-mark reset)
provides:
  - market_data_shell_on_no_current_bin
  - snapshot_write_gated_by_current_lowest_bin_gt_zero
  - one_off_rescan_stuck_players_script
affects:
  - src/futgg_client.py
  - src/server/scanner.py
  - tools/rescan_stuck_players.py (new)
tech_stack_added: []
patterns:
  - "Shell over None: return a partial model so downstream can still do its housekeeping"
  - "Downstream gate at the write site when upstream widens its return contract"
key_files_created:
  - tools/__init__.py
  - tools/rescan_stuck_players.py
  - .planning/quick/260418-czo-close-current-bin-silent-none-hole-remed/260418-czo-SUMMARY.md
key_files_modified:
  - src/futgg_client.py
  - src/server/scanner.py
  - tests/test_futgg_client.py
  - tests/test_scanner.py
decisions:
  - "Shell over None when current_bin is falsy: cards with no liveAuctions AND no currentPrice still yield a PlayerMarketData (current_lowest_bin=0) so downstream can populate PlayerRecord.created_at / last_scanned_at — the previous silent None stranded ~118 active cards with created_at=NULL, invisible to promo_dip_buy's Friday-batch detection."
  - "Snapshot gate at current_lowest_bin > 0: zero-BIN shell rows don't pollute market_snapshots — a MarketSnapshot is meant to be evidence of an observed live BIN, not a zero placeholder."
  - "Remediation script is NOT scheduled, NOT a migration — one-off `python -m tools.rescan_stuck_players --yes` to unstick pre-czo cards. Reads DATABASE_URL from src.config so it picks up the live Postgres URL automatically."
  - "Docstring now documents four result paths instead of three: full outage (None), defn-only failure (None), prices-only failure (raise PricesFetchError), defn+prices OK (shell if no current_bin, full if yes)."
metrics:
  duration_minutes: 12
  completed: "2026-04-18"
  tasks: 3
  new_tests: 3
  renamed_tests: 2
  commits: 5
---

# Quick 260418-czo: Close current_bin Silent-None Hole + Remediate Stuck Cards Summary

Close the last scanner silent-data-loss hole: both futgg_client paths now
return a `PlayerMarketData` shell (instead of silent None) when
`_extract_current_bin` returns None/0, and scanner guards its MarketSnapshot
write on `current_lowest_bin > 0`. This preserves PlayerRecord.created_at /
last_scanned_at updates for momentarily-untradeable cards, closing the hole
that stranded ~118 active rows with created_at=NULL (invisible to
promo_dip_buy). Ships a one-off remediation script (NOT executed here) to
requeue those stranded cards for immediate rescan.

## Files Changed

**Modified (2 src + 2 tests):**

- `src/futgg_client.py` — `get_player_market_data` and
  `get_player_market_data_sync` now construct a `PlayerMarketData` shell
  (`current_lowest_bin=0`, empty arrays, preserved player / futgg_url /
  max_price_range / created_at) instead of returning None when
  `_extract_current_bin` is falsy. Hoisted the `max_price_range` +
  `created_at` parsing above the no-bin branch so both paths share it.
  Docstring updated from three-way to four-way semantics.
- `src/server/scanner.py` — MarketSnapshot insert guarded behind
  `market_data is not None and market_data.current_lowest_bin > 0`.
  PlayerRecord update block (name, futgg_url, created_at, last_scanned_at,
  listing_count, listings_per_hour) untouched — still runs on the shell,
  which is the whole point.
- `tests/test_futgg_client.py` — renamed two tests
  (`..._returns_none` → `..._returns_shell`) and added two new tests
  (`test_get_player_market_data_returns_shell_when_no_current_bin` +
  sync variant) covering the shell shape including preserved createdAt.
  6 tests → 8 tests, all passing.
- `tests/test_scanner.py` — added `test_no_snapshot_when_current_lowest_bin_zero`
  asserting zero MarketSnapshot rows AND populated
  `last_scanned_at` + `created_at` on PlayerRecord for the shell case.

**Created (1 script + 1 package marker):**

- `tools/__init__.py` — empty package marker so
  `python -m tools.rescan_stuck_players` works.
- `tools/rescan_stuck_players.py` — one-off async script:
  - `count_stuck(session_factory)` — COUNT(*) of `is_active AND
    created_at IS NULL AND last_scanned_at IS NOT NULL`.
  - `requeue_stuck(session_factory)` — UPDATE those rows
    `SET next_scan_at = utcnow()`.
  - `main(yes)` — prompts before committing unless `--yes` is passed,
    prints the affected row count.
  - Reads `DATABASE_URL` from `src.config` so it automatically picks up
    the live Postgres URL on the user's box.

## Tests

Before: 6 tests in `test_futgg_client.py`, 0 directly on the shell-path in
`test_scanner.py` (existing `test_no_snapshot_on_none_market_data` covers
the full-outage None path, not the shell path).

After:

- `test_futgg_client.py`: 8 tests, all passing.
  - Updated `test_defn_ok_prices_ok_no_bin_returns_shell` (was `_returns_none`).
  - Updated `test_sync_defn_ok_prices_ok_no_bin_returns_shell` (was `_returns_none`).
  - New `test_get_player_market_data_returns_shell_when_no_current_bin` (async, createdAt).
  - New `test_get_player_market_data_sync_returns_shell_when_no_current_bin` (sync, createdAt).
  - Unchanged and passing: `test_defn_ok_prices_none_raises`,
    `test_both_none_returns_none`, `test_sync_defn_ok_prices_none_raises`,
    `test_sync_both_none_returns_none`.
- `test_scanner.py`: +1 test.
  - New `test_no_snapshot_when_current_lowest_bin_zero`.
  - Unchanged and passing: `test_snapshot_created_on_scan`,
    `test_no_snapshot_on_none_market_data`.

`python -m pytest tests/test_futgg_client.py tests/test_scanner.py`:

- Passing: 21 (all czo + c65 tests).
- Failing: 2 — `test_scan_player_writes_score` and
  `test_scan_player_sets_scorer_version`. Both are items #13 and #14 of the
  pre-existing baseline failures documented in
  `.planning/quick/260418-c65-fix-scanner-data-loss-bugs-playwright-ch/deferred-items.md`
  (v2 scorer contract drift, unrelated to this fix). **Out of scope per
  plan constraints.**

Smoke on adjacent target files (no regressions):
`python -m pytest tests/test_futgg_client.py tests/test_playwright_client.py tests/test_scanner_discovery.py` —
**14 passed**.

## Commits

| # | Hash       | Message |
|---|------------|---------|
| 1 | `345fa2df` | test(czo): add failing tests for market_data shell on no current_bin |
| 2 | `48da7ed9` | fix(czo): preserve market_data shell when current_bin is None |
| 3 | `bb27ea56` | test(czo): add failing scanner test for zero-BIN shell snapshot skip |
| 4 | `e1199c7e` | fix(czo): skip MarketSnapshot write when current_lowest_bin is 0 |
| 5 | `99a02e30` | feat(czo): add tools/rescan_stuck_players.py remediation script |

Two RED/GREEN pairs (Tasks 1 and 2) + one feat commit (Task 3) = 5 commits,
matching the plan's `<success_criteria>`.

## Deviations from Plan

**Worktree baseline setup (Rule 3 — blocking):** This worktree
(`worktree-agent-a7a70020`) branched off `feat/algo-trading-backtester` at
`368493ae`, which is **pre-c65**. The PLAN.md was written assuming the c65
fixes were baseline (e.g. references line 156 for `if not current_bin`,
`PricesFetchError` semantics). Before implementing, merged `main` into the
worktree to bring in c65's scanner + Playwright + cold-mark fixes plus the
new `tests/test_futgg_client.py`, `tests/test_playwright_client.py`,
`tests/test_scanner_discovery.py` files. Merge was a fast-forward equivalent
(no conflicts on files this plan touches). After the merge the baseline
matched the plan's interface contract exactly.

No other deviations. Rules 1 / 2 / 4 not triggered. No authentication gates.

### Auto-fix attempts

None — implementation matched the plan's `<action>` blocks exactly.

## Remediation Script Status

**NOT executed by the agent.** Per plan constraint, the user runs it
manually against the live DB after merge:

```bash
python -m tools.rescan_stuck_players          # prompts first
# or
python -m tools.rescan_stuck_players --yes    # no prompt
```

The script does two reads (`count_stuck`) followed by a confirmation prompt
and one UPDATE (`requeue_stuck`). Expected affected-row count at time of
user-run is ~118 (the figure cited in the PLAN's objective — the population
of pre-czo cards with created_at=NULL + last_scanned_at set).

Import + syntax checks passed:

```bash
$ python -c "import ast; ast.parse(open('tools/rescan_stuck_players.py').read())"
$ python -c "from tools.rescan_stuck_players import main, count_stuck, requeue_stuck"
imports ok
```

## Known Stubs

None. All code paths write real values from real data; no placeholder /
mock / TODO returns were introduced.

## Self-Check

- `src/futgg_client.py` — async and sync both return a `PlayerMarketData`
  shell with `current_lowest_bin=0` when `_extract_current_bin` returns
  None/0. **Verified by pytest.**
- `src/server/scanner.py` — MarketSnapshot write guarded by
  `market_data.current_lowest_bin > 0`. **Verified by pytest and `grep -n
  'current_lowest_bin > 0' src/server/scanner.py` showing two matches (line
  321: v3 scoring guard, line 385: new snapshot guard).**
- `tools/rescan_stuck_players.py` — exists, imports cleanly, NOT executed.
  **Verified by import check.**
- No changes to `src/algo/**` or `tests/algo/**`. **Verified by `git diff
  --stat`.**
- `_extract_current_bin` and `overview.averageBin` fallback unchanged.
  **Verified by diff — only `get_player_market_data{,_sync}` were edited.**
- All 4 new/renamed tests pass; the 4 unchanged tests still pass; scanner
  adds 1 new passing test.

## Self-Check: PASSED
