---
phase: quick
plan: 260326-wac
type: execute
wave: 1
depends_on: []
files_modified:
  - src/health_check.py
  - src/futbin_client.py
  - tests/test_health_check.py
autonomous: true
requirements: []
must_haves:
  truths:
    - "Running `python -m src.health_check` picks 10 random players from DB and fetches FUTBIN data"
    - "Each player shows a side-by-side comparison of our DB metrics vs FUTBIN metrics"
    - "Overall health score summarizes data accuracy across all checked players"
    - "Resolved futbin_ids are cached in the players table for reuse"
  artifacts:
    - path: "src/futbin_client.py"
      provides: "FUTBIN HTTP client with search and sales page parsing"
    - path: "src/health_check.py"
      provides: "CLI entry point with full audit logic and rich output"
    - path: "tests/test_health_check.py"
      provides: "Unit tests for parsing and comparison logic"
  key_links:
    - from: "src/health_check.py"
      to: "D:/op-seller/op_seller.db"
      via: "sqlite3 direct connection"
      pattern: "sqlite3\\.connect"
    - from: "src/health_check.py"
      to: "src/futbin_client.py"
      via: "import and call fetch functions"
      pattern: "from src\\.futbin_client import"
---

<objective>
Build a FUTBIN health monitor CLI that picks 10 random scored players from the DB,
fetches their FUTBIN sales/listing data via httpx, and compares against our DB data
to produce a full audit report.

Purpose: Validate that our scanner's market data (prices, sell-through rates, listing
counts) accurately reflects reality as shown on FUTBIN.

Output: `src/futbin_client.py` (FUTBIN data fetcher), `src/health_check.py` (CLI + audit),
`tests/test_health_check.py` (parsing tests).
</objective>

<execution_context>
@$HOME/.claude/get-shit-done/workflows/execute-plan.md
@$HOME/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@src/server/models_db.py
@src/config.py
@src/server/db.py

DB path: D:/op-seller/op_seller.db (use sqlite3 directly for this CLI script — no async needed)
DATABASE_URL from config is async format; for sqlite3 use: "D:/op-seller/op_seller.db"

Key DB tables for comparison:
- `players` — ea_id, name (player metadata). Add `futbin_id` column (INTEGER, nullable).
- `snapshot_sales` — sold_at, sold_price (joined via market_snapshots.ea_id)
- `listing_observations` — buy_now_price, market_price_at_obs, outcome ("sold"|"expired"|None), first_seen_at, last_seen_at
- `market_snapshots` — current_lowest_bin, listing_count, captured_at

FUTBIN sales page URL: https://www.futbin.com/26/sales/{futbin_id}/{name}?platform=ps
FUTBIN search URL: https://www.futbin.com/26/players?search={name}
FUTBIN table columns: Date, Listed for, Sold for, EA Tax, Net Price, Type
  - Sold listings: "Sold for" > 0
  - Expired listings: "Sold for" = 0
</context>

<tasks>

<task type="auto">
  <name>Task 1: Build FUTBIN client and health check CLI</name>
  <files>src/futbin_client.py, src/health_check.py, tests/test_health_check.py</files>
  <action>

**src/futbin_client.py** — FUTBIN HTTP client:

1. Create `FutbinClient` class using httpx (sync, not async — this is a simple CLI tool).
   - Set User-Agent to a realistic browser string.
   - Add 1.5s delay between requests (FUTBIN rate limiting).
   - `search_player(name: str) -> int | None` — GET `https://www.futbin.com/26/players?search={name}`,
     parse HTML to find the first player result link, extract futbin_id from the URL pattern
     `/26/player/{futbin_id}/`. Return futbin_id or None if not found.
   - `fetch_sales_page(futbin_id: int, name: str) -> list[dict]` — GET the sales page URL,
     attempt to parse the HTML table. Each row dict: `{"date": datetime, "listed_for": int, "sold_for": int, "type": str}`.
     If the table is JS-rendered and not in the HTML, try fetching `https://www.futbin.com/26/playerPrices?player={futbin_id}&platform=ps`
     as a JSON fallback. If both fail, return empty list and log a warning.
   - Use `re` and basic string parsing (no BeautifulSoup — not in requirements.txt). If HTML parsing
     is too fragile, `pip install beautifulsoup4` and add to requirements.txt.

**src/health_check.py** — CLI entry point and audit logic:

1. Use `click` for CLI: `python -m src.health_check` with optional `--count N` (default 10) and `--verbose`.
2. Use `sqlite3` directly (not async SQLAlchemy) for DB access at `D:/op-seller/op_seller.db`.
3. On startup, ensure `players` table has a `futbin_id` column — run `ALTER TABLE players ADD COLUMN futbin_id INTEGER` wrapped in try/except (ignore if already exists).
4. Player selection: query `SELECT ea_id, name, futbin_id FROM players WHERE is_active = 1 ORDER BY RANDOM() LIMIT {count}`. Prefer players that already have `futbin_id` cached (50%) and players without (50%) to gradually build mapping. Use: `SELECT ... WHERE futbin_id IS NOT NULL ORDER BY RANDOM() LIMIT {count//2} UNION ALL SELECT ... WHERE futbin_id IS NULL ORDER BY RANDOM() LIMIT {count - count//2}`.
5. For each player:
   a. If no `futbin_id` cached, call `FutbinClient.search_player(name)`. Cache result: `UPDATE players SET futbin_id = ? WHERE ea_id = ?`.
   b. Fetch FUTBIN sales page data.
   c. Query our DB for comparison data (last 48 hours):
      - `listing_observations`: count sold, count expired, avg buy_now_price WHERE ea_id=? AND first_seen_at > now-48h
      - `snapshot_sales`: sold prices via JOIN on market_snapshots WHERE ea_id=? AND sold_at > now-48h
      - `market_snapshots`: latest current_lowest_bin, listing_count
   d. Compute per-player health metrics:
      - **Sell-through rate delta**: our (sold / (sold + expired)) vs FUTBIN (sold_for > 0 / total). Flag if delta > 10%.
      - **Price accuracy**: median of our snapshot_sales prices vs median FUTBIN sold prices. Flag if delta > 5%.
      - **Listing count ratio**: our listing_count vs FUTBIN total listings in same window.
      - **Price range match**: our min/max listing prices vs FUTBIN min/max listed_for.
      - **Per-player health score**: 0-100 based on weighted average of metrics (sell-through 40%, price accuracy 30%, count 15%, range 15%). 100 = perfect match.
6. Compute overall health score: average of all per-player scores.
7. Rich output:
   - Use `rich.table.Table` for per-player results: columns = Player Name, EA ID, FUTBIN ID, Sell-Through (Ours/FUTBIN), Price Accuracy, Listing Count, Health Score.
   - Color-code health scores: green >= 80, yellow 50-79, red < 50.
   - Print overall summary with total health score.
   - If `--verbose`, print detailed per-player breakdown.
8. Store results in `health_checks` table (create if not exists):
   - `id INTEGER PRIMARY KEY AUTOINCREMENT, ea_id INTEGER, checked_at TEXT, our_sell_rate REAL, futbin_sell_rate REAL, our_median_price INTEGER, futbin_median_price INTEGER, health_score REAL`.
   - INSERT one row per player checked.
9. Add `__main__` block: `if __name__ == "__main__": main()` (for `python -m src.health_check` invocation via `__main__.py` or direct run).

**tests/test_health_check.py** — Unit tests:

1. Test `FutbinClient.search_player` HTML parsing with a mock HTML snippet containing a player link.
2. Test `FutbinClient.fetch_sales_page` HTML parsing with a mock table HTML.
3. Test health score computation: given known our-data and futbin-data dicts, verify score calculation.
4. Test edge cases: player not found on FUTBIN (returns None), empty sales table, all expired.
5. Use `unittest.mock.patch` to mock httpx calls — no real network requests in tests.

  </action>
  <verify>
    <automated>cd C:/Users/maftu/Projects/op-seller && python -m pytest tests/test_health_check.py -x -v</automated>
  </verify>
  <done>
    - `python -m src.health_check --count 2` runs successfully, fetches FUTBIN data for 2 players, prints rich table with health scores
    - Tests pass covering HTML parsing, score computation, and edge cases
    - futbin_id cached in players table after first lookup
    - Results stored in health_checks table
  </done>
</task>

</tasks>

<verification>
1. `python -m pytest tests/test_health_check.py -x -v` — all parsing and scoring tests pass
2. `python -m src.health_check --count 2` — runs end-to-end, prints audit table (manual spot-check)
3. `sqlite3 D:/op-seller/op_seller.db "SELECT COUNT(*) FROM health_checks"` — rows inserted after run
</verification>

<success_criteria>
- Health check CLI runs without errors for 2+ players
- FUTBIN data is fetched and parsed (or gracefully fails with clear message if JS-rendered)
- Per-player and overall health scores are computed and displayed
- futbin_id caching works (second run for same player skips search)
- Results persisted in health_checks table
</success_criteria>

<output>
After completion, create `.planning/quick/260326-wac-build-futbin-health-monitor-hourly-sched/260326-wac-SUMMARY.md`
</output>
