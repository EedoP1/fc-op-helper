# Remove FUTBIN scraper, make fut.gg `market_snapshots` the sole backtest source

**Date:** 2026-04-17
**Status:** Approved (ready for planning)
**Scope:** Backtester data-source simplification on `feat/algo-trading-backtester`

## Goal

Delete the FUTBIN Playwright scraper and its supporting code paths from the algo backtester. Make the existing fut.gg live-scanner data (`market_snapshots` table) the sole price source. Do not touch the database schema — the `price_history` table and `PriceHistory` ORM class stay in place as dead storage.

## Motivation

The backtester currently has two data sources:

- `price_history` — FUTBIN daily prices, bulk-scraped via Playwright + Cloudflare-bypass stealth (`src/algo/scraper.py`)
- `market_snapshots` — fut.gg hourly data continuously written by the live scanner (`src/server/scanner.py`)

The FUTBIN path carries ongoing cost: Cloudflare retries, headed Chrome, a 3-page pool that fails mid-run with no resume, daily granularity that fights hourly strategies. The fut.gg scanner data is already richer (hourly vs daily), always fresh, and written automatically. Keeping both paths means a `--source` flag, a `futbin_to_ea` id-mapping dict threaded through every backtest signature, and two divergent player-name lookup branches.

## Non-goals

- No DB migration, no schema change, no data deletion. `price_history` rows stay; the table becomes unreferenced but intact.
- No new scraper against fut.gg's `/api/fut/player-prices/26/{eaId}/` history endpoint. The live scanner already writes fut.gg data.
- No removal of `playwright==1.58.0` from `requirements.txt` — `src/server/playwright_client.py` still uses it.
- No changes to strategies, `src/algo/models.py`, or server code.

## Changes

### Files deleted in full

| File | LOC | Reason |
|---|---|---|
| `src/algo/scraper.py` | 437 | FUTBIN Playwright scraper — the thing we're removing |
| `tests/algo/test_scraper.py` | 32 | Tests for `parse_futbin_price_data` / `extract_ea_id` — meaningless without the scraper |

### `src/algo/__main__.py`

- Remove the `scrape` subcommand branch (currently lines 11–13: `if cmd == "scrape": ...`).
- Remove the `"scrape   Fetch full price history from fut.gg"` line from the usage help (currently line 24).

Leaves `run` and `report` subcommands intact.

### `src/algo/engine.py`

- **Delete `load_price_data()`** (currently lines 475–541).
- **Delete the `--source` CLI flag** (currently lines 813–814); remove the `source` kwarg from `run_cli` (line 622) and its passthrough at the `asyncio.run` call site (line 823).
- **Collapse the loader branch**: currently `run_cli` does `if use_market_snapshots: load_market_snapshot_data(...) else: load_price_data(...)` (lines 646–653). After change, always call `load_market_snapshot_data(...)` — no branching, no `use_market_snapshots` local.
- **Remove `futbin_to_ea` plumbing entirely**:
    - Drop the kwarg from `run_backtest` (lines 29, 94).
    - Drop from `_worker_run_combos` (lines 253, 315) and its caller `run_sweep_parallel` (lines 350, 404).
    - Drop the local in `run_cli` (lines 634, 651) and all forwarding into `run_backtest` / `run_sweep_parallel` (lines 689, 696).
    - In the trade-log dict built at lines 95–104 (and the identical block at 316–327), drop the `"futbin_id": t.ea_id` field. Each trade row becomes `{"ea_id", "qty", "buy_price", "sell_price", "net_profit", "buy_time", "sell_time"}`.
    - This is a breaking change to `backtest_results.json` consumers — intentional. No shim.
- **Hourly param grid unconditional**: `run_sweep_parallel` currently takes `use_hourly_grid: bool = False` and selects `param_grid_hourly()` only when true (line 386). Remove the kwarg; always prefer `param_grid_hourly()` when the strategy exposes it, falling back to `param_grid()`.
- **Player-name lookup**: delete the FUTBIN branch (lines 712–720 — the JOIN on `price_history`). Keep the market_snapshots branch (lines 704–710).
- **Error message**: line 660 currently says `"No price data found. Run the scraper first: python -m src.algo.scraper"`. Change to `"No price data found. Is the scanner running?"`.
- **Port the `--days` filter** into `load_market_snapshot_data`. Preserve the existing Sunday-aligned cutoff logic from the deleted `load_price_data` — it matters for promo strategies that key off week boundaries. Shape: `days=0` means no filter; `days>0` computes `cutoff = utcnow - timedelta(days=days)`, rolls back to the previous Sunday, zeroes the time, and adds a `captured_at >= :cutoff` clause to the existing query.

### `tests/algo/test_integration.py`

The existing tests seed `price_history` and call `load_price_data`. Both are going away.

- Rewrite `seed_price_data` to INSERT into `market_snapshots` with `ea_id`, `captured_at`, `current_lowest_bin` columns (not `price_history` / `ea_id` / `timestamp` / `price`).
- Import `MarketSnapshot` from `src.server.models_db` in the `db` fixture so `Base.metadata.create_all` includes it.
- In `test_full_pipeline` (line 42) and `test_all_strategies_run` (line 85): replace `load_price_data(db, min_data_points=10)` with `load_market_snapshot_data(db, min_data_points=10)`. Note the return shape change: `load_market_snapshot_data` returns `(price_data, created_at_map)` — destructure accordingly or just take `price_data, _`.
- The synthetic sine-wave data pattern stays; only the destination table changes.

### `src/algo/models_db.py`

No changes. The `PriceHistory` class stays. `test_models_db.py` still imports it to assert the table exists. Removing the class while leaving the table would be worse than this small bit of vestigial code.

## Data flow after the change

```
Live scanner (src/server/scanner.py)
    → writes hourly BIN → market_snapshots table
                                ↓
CLI: python -m src.algo run --strategy promo_dip_buy [--days N] [--min-price] [--max-price]
    → engine.run_cli
        → load_market_snapshot_data(session, days, min_price, max_price)
            → SELECT DISTINCT ON (ea_id, date_trunc('hour', captured_at))
              + optional Sunday-aligned cutoff filter
            → returns (price_data, created_at_map)
        → run_sweep_parallel(classes, price_data, budget, created_at_map)
        → write backtest_results rows + backtest_results.json
```

No conditional, no `--source`, no `futbin_to_ea`.

## Testing

- `tests/algo/test_integration.py` — rewritten as described; covers full load → sweep → save pipeline on `market_snapshots`.
- `tests/algo/test_models_db.py` — unchanged; continues to verify `price_history` table exists (it still does).
- `tests/algo/test_scraper.py` — deleted.
- Other tests under `tests/algo/` — grepped for `load_price_data|futbin_to_ea|price_history|PriceHistory|--source|use_hourly_grid|use_market_snapshots|from src.algo.scraper` and confirmed **zero** matches in `test_engine.py`, `test_algo_api.py`, `test_algo_runner.py`, `test_algo_models.py`, `test_models.py`, `test_signal_parity.py`, `test_signal_parity_db.py`, `test_strategies.py`. All relevant test code is contained in the three files already addressed.
- Run full `pytest tests/algo/` after edits to catch indirect failures.

## Risks and mitigations

- **`backtest_results.json` consumers break** — any external script reading `futbin_id` from the trade log will fail. Accepted: the field was redundant on the `market_snapshots` path anyway (already equal to `ea_id`).
- **`--days` + Sunday alignment regression** — easy to get the cutoff off-by-one when porting. Mitigation: lift the existing logic (lines 487–491 of current `engine.py`) verbatim into `load_market_snapshot_data`; add a unit test asserting `captured_at >= :cutoff` rows are kept and earlier ones dropped.
- **Hidden `futbin_to_ea` usage elsewhere** — a global grep on the `src/algo/` tree before committing is required. Grep patterns: `futbin_to_ea`, `load_price_data`, `--source`, `price_history`, `from src.algo.scraper`.
- **Scanner-less environments** — running backtests in a fresh checkout where the scanner has never written to `market_snapshots` will produce an empty result. The new error message directs the user to the scanner. Intentional.

## Out of scope (explicit)

- Deleting the `price_history` table or its rows.
- Removing `PriceHistory` ORM class.
- Removing `playwright` from requirements.txt.
- Adding a new fut.gg history scraper.
- Any change to `src/algo/report.py`, strategies, or server code.
