---
status: awaiting_human_verify
trigger: "GET /api/v1/portfolio?budget=2000000 takes >30 seconds and times out"
created: 2026-03-27T13:15:00Z
updated: 2026-03-27T13:30:00Z
---

## Current Focus

hypothesis: CONFIRMED — _get_volatile_ea_ids() was the bottleneck
test: Fix applied, profiled with real 2.3GB database
expecting: Endpoint responds in <10s
next_action: Human verification against running server

## Symptoms

expected: Portfolio endpoint returns within 10 seconds
actual: httpx.ReadTimeout after 30 seconds when CLI calls GET /api/v1/portfolio
errors: httpx.ReadTimeout at src/main.py:44
reproduction: `python -m src.main --budget 2000000` (server must be running)
started: Likely worsened after volatility filter was added (commit 3ea5753, 8d4b8ea)

## Eliminated

- hypothesis: optimize_portfolio() is slow
  evidence: Pure Python on 1500 entries; negligible CPU time
  timestamp: 2026-03-27T13:16:00Z

- hypothesis: Window function approach is faster
  evidence: Tested FIRST_VALUE/ROW_NUMBER approach — took 76s for all ea_ids (worse than original)
  timestamp: 2026-03-27T13:22:00Z

## Evidence

- timestamp: 2026-03-27T13:10:00Z
  checked: Database size and table row counts
  found: snapshot_price_points has ~20M rows, market_snapshots ~141k rows, players 1768, player_scores 104k
  implication: Volatility queries scan massive tables

- timestamp: 2026-03-27T13:11:00Z
  checked: Database indexes on snapshot_price_points
  found: Only index is ix_snapshot_price_points_snapshot_id (on snapshot_id). No index on recorded_at.
  implication: Query 1 filter on recorded_at >= cutoff must scan all rows per snapshot_id

- timestamp: 2026-03-27T13:12:00Z
  checked: Query 1 timing with 100 ea_ids (before fix)
  found: Takes 1.35s for 100 ea_ids without covering index
  implication: Query 1 alone would take 20s+ for all 1768 players

- timestamp: 2026-03-27T13:13:00Z
  checked: Query 2/3 pattern
  found: Generates N tuple_() OR conditions where N = number of boundaries (~1500)
  implication: OR clauses with 1500+ conditions are catastrophic for SQLite

- timestamp: 2026-03-27T13:25:00Z
  checked: After adding covering index (snapshot_id, recorded_at, lowest_bin)
  found: Boundaries query for 200 ea_ids: 0.54s (was ~2.7s). Uses COVERING INDEX scan.
  implication: Index eliminates recorded_at scan entirely

- timestamp: 2026-03-27T13:27:00Z
  checked: Combined query approach (boundaries + correlated subqueries, batched)
  found: All 1768 ea_ids processed in 4.64s total (9 batches of 200)
  implication: Well within 10s budget, no OR clauses needed

- timestamp: 2026-03-27T13:28:00Z
  checked: PlayerScore subquery with new covering index
  found: 0.07s (was 0.70s) with ix_player_scores_viable_ea_scored
  implication: 10x improvement for initial candidate query

- timestamp: 2026-03-27T13:29:00Z
  checked: Unit tests (15 portfolio tests)
  found: All 15 pass, including volatility filter tests
  implication: Behavioral equivalence maintained

## Resolution

root_cause: _get_volatile_ea_ids() performed 3 queries on 20M-row snapshot_price_points table. (1) No covering index forced full sequential scan per snapshot_id to filter by recorded_at. (2) Queries 2 and 3 generated 1500+ OR(tuple_() == tuple_()) conditions which SQLite cannot optimize. Combined wall-clock time exceeded 30s timeout.
fix: (1) Added covering index ix_spp_snapshot_recorded_bin(snapshot_id, recorded_at, lowest_bin) for efficient range scans. (2) Rewrote _get_volatile_ea_ids to use batched raw SQL with correlated subqueries instead of 3 separate ORM queries with massive OR clauses. (3) Added ix_player_scores_viable_ea_scored(is_viable, ea_id, scored_at) covering index for the initial candidate query.
verification: All 15 portfolio tests pass. Profiled with real 2.3GB database: volatility check 4.64s (was 30s+), PlayerScore query 0.07s (was 0.70s). Estimated total endpoint time ~5s.
files_changed: [src/server/api/portfolio.py, src/server/models_db.py]
