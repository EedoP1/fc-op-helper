# GSD Debug Knowledge Base

Resolved debug sessions. Used by `gsd-debugger` to surface known-pattern hypotheses at the start of new investigations.

---

## portfolio-volatility-timeout — GET /portfolio times out after volatility filter added
- **Date:** 2026-03-27
- **Error patterns:** ReadTimeout, httpx.ReadTimeout, portfolio, timeout, volatility, _get_volatile_ea_ids, market_snapshots, subquery, nested subquery, slow query, event loop blocked
- **Root cause:** _get_volatile_ea_ids() issued a 4-level nested subquery against market_snapshots with up to 1800 ea_ids in an IN() list. SQLite materialises each subquery sequentially. At ~1.55M rows in the 3-day lookback window (1800 players x 288 scans/day x 3 days), the query took >30s, causing the FastAPI endpoint to exceed the client's 30s ReadTimeout.
- **Fix:** Rewrote _get_volatile_ea_ids() to use 3 focused queries: (1) GROUP BY with HAVING to find min/max captured_at per ea_id, (2) bulk tuple IN() to fetch earliest bin values, (3) bulk tuple IN() to fetch latest bin values. Price comparison runs in Python over in-memory dicts.
- **Files changed:** src/server/api/portfolio.py
---

