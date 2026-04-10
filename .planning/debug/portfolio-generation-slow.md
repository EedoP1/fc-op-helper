---
status: resolved
trigger: "portfolio-endpoint-timeout — clicking Generate in Chrome extension sometimes takes over a minute"
created: 2026-03-30T00:00:00Z
updated: 2026-03-30T00:01:00Z
---

## Current Focus

hypothesis: CONFIRMED — subquery+nested-loop pattern on player_scores causes 33s cold-cache scan
test: rewrite 5 query sites to use ROW_NUMBER() window function via raw SQL helper
expecting: cold-cache time drops from 33s to ~4s (8x improvement)
next_action: apply fix to src/server/api/portfolio.py and models_db.py

## Symptoms

expected: Portfolio generation should return quickly (seconds)
actual: Sometimes takes over a minute to return when clicking Generate in the extension
errors: No errors — it eventually completes, just very slow
reproduction: Click "Generate" in the Chrome extension
started: Unclear if it ever worked fast

## Eliminated

- hypothesis: volatility query (_get_volatile_ea_ids) is the bottleneck
  evidence: volatility filter not present in current codebase; main query is the culprit
  timestamp: 2026-03-30T00:01:00Z

- hypothesis: optimizer Python computation is slow
  evidence: pure Python on ~1676 entries with 200-iteration cap; negligible
  timestamp: 2026-03-30T00:01:00Z

## Evidence

- timestamp: 2026-03-30T00:00:30Z
  checked: player_scores table row count and index sizes
  found: 591K rows, avg 333 rows per player (1,779 players), indexes 81MB, shared_buffers 128MB
  implication: working set (96MB table + 81MB indexes) exceeds buffer pool; cold-cache I/O is unavoidable

- timestamp: 2026-03-30T00:00:45Z
  checked: EXPLAIN ANALYZE on the current portfolio query
  found: 79s execution — Parallel Seq Scan scans 591K rows, then 1,774 nested index lookups at 44ms each
  implication: nested loop with 1,774 iterations × 44ms/iter = ~78s. This is the bottleneck.

- timestamp: 2026-03-30T00:01:00Z
  checked: ROW_NUMBER() OVER (PARTITION BY ea_id ORDER BY scored_at DESC) rewrite
  found: 4.3s on cold cache vs 33s for current subquery (measured from Python timing, not EXPLAIN)
  implication: 8x improvement by replacing nested-loop with single-pass window scan

- timestamp: 2026-03-30T00:01:10Z
  checked: all occurrences of slow pattern in portfolio.py
  found: lines 118, 235, 436, 765, 893 all use the same latest_subq nested loop pattern
  implication: fix must be applied to all 5 sites; a shared helper function is the right approach

## Resolution

root_cause: GET /portfolio, POST /portfolio/generate, and 3 other endpoints in portfolio.py use a subquery pattern (JOIN (SELECT ea_id, MAX(scored_at) GROUP BY ea_id) latest ...) that PostgreSQL executes as a nested loop with one random index scan per player. With 591K rows across 1,779 players and PostgreSQL shared_buffers=128MB (smaller than the 177MB total table+index size), cold cache forces 1,774 random I/O operations at ~44ms each = 79s measured execution time.
fix: Replaced the ORM subquery+nested-loop pattern in _fetch_latest_viable_scores() with ROW_NUMBER() OVER (PARTITION BY ea_id ORDER BY scored_at DESC) via raw SQL. This does a single forward scan using the ix_player_scores_viable_ea_scored index with incremental sort, yielding 4-7s (measured) vs 33-79s (measured) on cold cache. Applied to 5 query sites via shared helper function.
verification: Before: 33-79s (EXPLAIN ANALYZE shows 79s, Python timing shows 33s on cold cache). After: 4-7s measured on real DB. Tests: 20 pass (same as before), 3 pre-existing failures unchanged (test fixtures missing expected_profit_per_hour), 2 previously-failing tests now pass (volatile player tests for removed code).
files_changed: [src/server/api/portfolio.py]
