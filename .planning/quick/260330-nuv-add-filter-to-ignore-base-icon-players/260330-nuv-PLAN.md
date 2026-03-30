---
phase: quick
plan: 260330-nuv
type: execute
wave: 1
depends_on: []
files_modified:
  - src/server/api/portfolio.py
  - src/server/scanner.py
autonomous: true
must_haves:
  truths:
    - "Base icon players (card_type == 'Icon') never appear in portfolio recommendations"
    - "Base icon players are skipped during scanner bootstrap and discovery (no wasted API calls)"
    - "Non-base icon variants like 'Icon Hero' still appear in results"
  artifacts:
    - path: "src/server/api/portfolio.py"
      provides: "SQL-level Icon filter in _fetch_latest_viable_scores"
      contains: "card_type != 'Icon'"
    - path: "src/server/scanner.py"
      provides: "Pre-insert Icon filter in bootstrap and discovery"
      contains: "rarityName"
  key_links:
    - from: "src/server/api/portfolio.py"
      to: "_fetch_latest_viable_scores SQL query"
      via: "WHERE clause filter"
      pattern: "pr\\.card_type != 'Icon'"
---

<objective>
Filter out base icon players (card_type == "Icon") from portfolio selection and scanner discovery.

Purpose: Base icons are not profitable for OP selling but currently consume scoring API calls and pollute portfolio recommendations. Only base rarity icons should be excluded -- other icon variants like "Icon Hero" must be kept.

Output: SQL-level filter in portfolio query, redundant Python filter removed, scanner skips base icons during bootstrap/discovery.
</objective>

<execution_context>
@C:/Users/maftu/.claude/get-shit-done/workflows/execute-plan.md
@C:/Users/maftu/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@src/server/api/portfolio.py
@src/server/scanner.py
</context>

<tasks>

<task type="auto">
  <name>Task 1: Add SQL-level Icon filter to portfolio query and remove redundant Python filter</name>
  <files>src/server/api/portfolio.py</files>
  <action>
1. In `_fetch_latest_viable_scores()` (line ~111-135), add `AND pr.card_type != 'Icon'` to the WHERE clause after `AND pr.is_active = TRUE` (line 134). This filters base icons at the SQL level for ALL portfolio endpoints at once.

2. Remove the Python-level base icon filter block at lines ~202-207 (the `rows = [(s, r) for s, r in rows if r.card_type != "Icon"]` block with the logging). This is now redundant since the SQL query already excludes them. The filter currently only exists in `generate_portfolio` but the SQL fix covers all endpoints.
  </action>
  <verify>
    <automated>cd C:/Users/maftu/Projects/op-seller && python -c "from src.server.api.portfolio import _fetch_latest_viable_scores; import inspect; src = inspect.getsource(_fetch_latest_viable_scores); assert \"card_type != 'Icon'\" in src, 'SQL filter missing'; print('SQL filter present')"</automated>
  </verify>
  <done>SQL query in _fetch_latest_viable_scores excludes card_type='Icon'. Python-level filter block removed from generate_portfolio.</done>
</task>

<task type="auto">
  <name>Task 2: Skip base icons during scanner bootstrap and discovery</name>
  <files>src/server/scanner.py</files>
  <action>
1. In `run_bootstrap()` (~line 165), add a list comprehension filter on the `players` list BEFORE building `values_list`. Filter out players where `p.get("rarityName", "") == "Icon"`. Log how many were filtered:
   ```python
   before = len(players)
   players = [p for p in players if p.get("rarityName", "") != "Icon"]
   if before - len(players):
       logger.info(f"Bootstrap: filtered {before - len(players)} base icons")
   ```

2. In `run_discovery()` (~line 270), add the same filter on the `players` list BEFORE building `discovered_ids`. This ensures base icons are neither upserted nor counted in the discovery set:
   ```python
   before = len(players)
   players = [p for p in players if p.get("rarityName", "") != "Icon"]
   if before - len(players):
       logger.info(f"Discovery: filtered {before - len(players)} base icons")
   ```

Place both filters immediately after the `discover_players()` call and its elapsed-time log (bootstrap) or info log (discovery), before any other processing.
  </action>
  <verify>
    <automated>cd C:/Users/maftu/Projects/op-seller && python -c "import inspect; from src.server.scanner import ScannerService; src = inspect.getsource(ScannerService.run_bootstrap); assert 'rarityName' in src and 'Icon' in src, 'Bootstrap filter missing'; src2 = inspect.getsource(ScannerService.run_discovery); assert 'rarityName' in src2 and 'Icon' in src2, 'Discovery filter missing'; print('Both scanner filters present')"</automated>
  </verify>
  <done>Both run_bootstrap() and run_discovery() skip players with rarityName=="Icon" before DB upsert, saving API calls on base icons.</done>
</task>

</tasks>

<verification>
- Grep confirms `card_type != 'Icon'` in portfolio.py SQL query
- Grep confirms `rarityName.*Icon` filter in both scanner bootstrap and discovery methods
- No Python-level icon filter remains in generate_portfolio
- "Icon Hero" and other icon variants are NOT filtered (exact match on "Icon" only)
</verification>

<success_criteria>
Base icon players excluded at SQL level from all portfolio endpoints. Scanner skips base icons during bootstrap and discovery. Non-base icon variants unaffected.
</success_criteria>

<output>
After completion, create `.planning/quick/260330-nuv-add-filter-to-ignore-base-icon-players/260330-nuv-SUMMARY.md`
</output>
