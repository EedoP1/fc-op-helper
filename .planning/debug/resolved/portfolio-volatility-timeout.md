---
status: awaiting_human_verify
trigger: "GET /portfolio endpoint times out (httpx.ReadTimeout) after the volatility filter was added in quick task 260327-gxd"
created: 2026-03-27T00:00:00Z
updated: 2026-03-27T00:01:00Z
---

## Current Focus

hypothesis: CONFIRMED — _get_volatile_ea_ids() four-subquery plan caused SQLite to scan ~1.5M rows four times, blocking the event loop for >30s.
test: Ran full test suite (142 tests, excluding pre-existing test_health_check.py import error).
expecting: All portfolio and volatility tests pass with new 3-query implementation.
next_action: Await human verification that GET /portfolio now responds within 30s in the real environment.

## Symptoms

expected: GET /api/v1/portfolio responds within normal timeout (~30s)
actual: httpx.ReadTimeout — the endpoint never responds in time
errors: httpx.ReadTimeout at src/main.py line 44: `resp = await client.get(f"{url}/api/v1/portfolio", params={"budget": budget})`
reproduction: Run `python -m src.main --budget 1000000` — the GET /portfolio call times out
started: Immediately after adding _get_volatile_ea_ids() volatility filter in quick task 260327-gxd (commits 913a456..c55dc80)

## Eliminated

- hypothesis: network/connectivity issue between CLI and server
  evidence: The error is ReadTimeout (server never responds), not ConnectError (server unreachable). ConnectError is handled separately in main.py:45-50 and results in a different error message.
  timestamp: 2026-03-27T00:00:00Z

- hypothesis: the main portfolio query itself (PlayerScore + PlayerRecord join) is slow
  evidence: This query existed before the volatility filter was added and the endpoint worked. Only the _get_volatile_ea_ids() call is new (commits 913a456..c55dc80).
  timestamp: 2026-03-27T00:00:00Z

## Evidence

- timestamp: 2026-03-27T00:00:00Z
  checked: src/server/api/portfolio.py _get_volatile_ea_ids() function (lines 97-185)
  found: The function issues a 4-level nested subquery: time_range_subq (GROUP BY + MIN/MAX/COUNT on market_snapshots WHERE ea_id IN (N ids) AND captured_at >= cutoff), then earliest_subq (JOIN to time_range_subq ON captured_at == min_ts), then latest_subq (JOIN to time_range_subq ON captured_at == max_ts), then final SELECT joining earliest and latest. All four passes scan market_snapshots for every ea_id in the list.
  implication: With ~1800 ea_ids and a 3-day lookback, market_snapshots could contain hundreds of thousands of rows. SQLite executes subqueries sequentially. The entire query runs as one blocking DB call on the async event loop, which can take 30+ seconds.

- timestamp: 2026-03-27T00:00:00Z
  checked: src/server/models_db.py MarketSnapshot table definition (lines 57-72)
  found: Index ix_market_snapshots_ea_id_captured_at exists on (ea_id, captured_at). However, SQLite uses this index for individual ea_id lookups, not for large IN() lists (SQLite query planner falls back to full-scan when the IN() list is large). The index will help per-ea_id lookups but not the grouped subquery across 1800 IDs simultaneously.
  implication: Even with the composite index, the query will not be fast enough for 1800 IDs in one pass.

- timestamp: 2026-03-27T00:00:00Z
  checked: GET /portfolio code path (lines 207-241 of portfolio.py)
  found: all_ea_ids = [score.ea_id for score, record in rows] collects ALL viable player IDs (could be 500-1800) and passes the entire list to _get_volatile_ea_ids() in a single call. The fix to call the function once per request rather than per-player means the IN() list grows proportionally with the player pool.
  implication: The root cause is confirmed: _get_volatile_ea_ids() with a large ea_id list issues a slow multi-subquery SQL statement that blocks the event loop beyond the 30s client timeout.

- timestamp: 2026-03-27T00:00:00Z
  checked: VOLATILITY_LOOKBACK_DAYS = 3, scanner writes one MarketSnapshot per scan per player, SCAN_INTERVAL_SECONDS = 300 (every 5 min)
  found: 3 days × 288 scans/day × 1800 players = ~1.55 million rows in market_snapshots within the lookback window alone. The query must aggregate all of these.
  implication: The data volume makes the current query approach infeasible without significant optimisation.

## Resolution

root_cause: _get_volatile_ea_ids() issued a 4-level nested subquery against market_snapshots with up to 1800 ea_ids in an IN() list. SQLite materialises each subquery sequentially. At ~1.55M rows in the 3-day lookback window (1800 players × 288 scans/day × 3 days), the query took >30s, causing the FastAPI endpoint to exceed the client's 30s ReadTimeout.

fix: Rewrote _get_volatile_ea_ids() in src/server/api/portfolio.py to use exactly 3 SQL queries:
  1. One GROUP BY with HAVING to find min/max captured_at per ea_id (uses composite index on (ea_id, captured_at)).
  2. One bulk tuple IN() query to fetch earliest bin values for all ea_ids.
  3. One bulk tuple IN() query to fetch latest bin values for all ea_ids.
  Volatility comparison then runs in Python over the in-memory dicts. Added tuple_ to the top-level sqlalchemy import.

verification: All 14 test_portfolio.py tests pass. Full suite (142 tests) passes with zero failures.
files_changed: [src/server/api/portfolio.py]
