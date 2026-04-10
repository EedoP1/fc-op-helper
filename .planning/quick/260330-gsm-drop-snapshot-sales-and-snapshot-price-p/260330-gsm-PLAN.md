---
phase: quick
plan: 260330-gsm
type: execute
wave: 1
depends_on: []
files_modified:
  - src/server/models_db.py
  - src/server/db.py
  - src/server/scanner.py
  - tests/test_scanner.py
autonomous: true
requirements: []
must_haves:
  truths:
    - "SnapshotSale and SnapshotPricePoint classes no longer exist in codebase"
    - "No imports or references to removed models anywhere in src/ or tests/"
    - "Server starts without error (create_all succeeds without the removed models)"
    - "Existing tests pass (removed tests were for deleted functionality)"
  artifacts:
    - path: "src/server/models_db.py"
      provides: "ORM models without SnapshotSale/SnapshotPricePoint"
    - path: "src/server/db.py"
      provides: "create_engine_and_tables without removed model imports"
    - path: "tests/test_scanner.py"
      provides: "Scanner tests without snapshot_sales/price_points assertions"
  key_links:
    - from: "src/server/db.py"
      to: "src/server/models_db.py"
      via: "import in create_engine_and_tables"
      pattern: "from src.server.models_db import"
---

<objective>
Remove SnapshotSale and SnapshotPricePoint ORM models and all references. These tables hold 56M + 133M rows (22GB total) that are no longer written to after quick task 260330-g6d removed the insert logic. The ORM models, imports, test assertions, and comments referencing them must be cleaned up. The live DB tables will be dropped via a manual SQL command (provided in output).

Purpose: Eliminate dead ORM code and free 22GB of DB storage.
Output: Clean codebase with no references to removed models; SQL command for live DB cleanup.
</objective>

<execution_context>
@$HOME/.claude/get-shit-done/workflows/execute-plan.md
@$HOME/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/STATE.md
@src/server/models_db.py
@src/server/db.py
@src/server/scanner.py
@tests/test_scanner.py
</context>

<tasks>

<task type="auto">
  <name>Task 1: Remove SnapshotSale/SnapshotPricePoint ORM models and all references</name>
  <files>src/server/models_db.py, src/server/db.py, src/server/scanner.py, tests/test_scanner.py</files>
  <action>
1. **src/server/models_db.py** — Delete the `SnapshotSale` class (lines 77-92) and `SnapshotPricePoint` class (lines 94-108). Remove `ForeignKey` from the import line if no other model uses it (check: no other model uses ForeignKey after removal).

2. **src/server/db.py** line 60 — Remove `SnapshotSale, SnapshotPricePoint` from the import in `create_engine_and_tables()`. The line should become:
   ```python
   from src.server.models_db import PlayerRecord, PlayerScore, MarketSnapshot, ListingObservation, DailyListingSummary, TradeAction, TradeRecord, PortfolioSlot  # noqa: F401
   ```

3. **src/server/scanner.py** line 561 — Update the `run_cleanup` docstring. Remove the sentence "FK cascade on SnapshotSale and SnapshotPricePoint ensures child rows are deleted automatically." and the comment "# Delete old snapshots (cascades to sales + price points)" on line 572 — change to "# Delete old snapshots".

4. **tests/test_scanner.py** — Four changes:
   a. Line 11: Remove `SnapshotSale, SnapshotPricePoint` from the import.
   b. Delete `test_snapshot_sales_created` (lines 248-275) — tests SnapshotSale row creation which no longer happens.
   c. Delete `test_snapshot_price_points_created` (lines 278-307) — tests SnapshotPricePoint row creation which no longer happens.
   d. In `test_cleanup_deletes_old_snapshots` (lines 330-369): Remove the two `session.add(SnapshotSale(...))` calls (lines 345-347 and 356-358), remove the `sales = ...` query (line 365), and remove the `assert len(sales) == 1` assertion (line 369). The test should still verify snapshot cleanup works — just without checking cascade to child tables.
   e. Delete `test_scan_player_deduplicates_snapshot_sales` (lines 491-522) — tests dedup logic for a table that no longer receives inserts.
  </action>
  <verify>
    <automated>cd /c/Users/maftu/Projects/op-seller && python -c "from src.server.models_db import SnapshotSale" 2>&1 | grep -q "ImportError" && echo "PASS: SnapshotSale removed" || echo "FAIL: SnapshotSale still importable"</automated>
  </verify>
  <done>No SnapshotSale or SnapshotPricePoint classes, imports, or references exist in src/ or tests/. ForeignKey import removed from models_db.py if unused.</done>
</task>

<task type="auto">
  <name>Task 2: Verify tests pass and provide DROP TABLE SQL</name>
  <files></files>
  <action>
1. Run the scanner test suite to confirm all remaining tests pass:
   ```bash
   python -m pytest tests/test_scanner.py -x -v
   ```

2. Run a grep across the entire repo to confirm zero remaining references:
   ```bash
   grep -r "SnapshotSale\|SnapshotPricePoint\|snapshot_sales\|snapshot_price_points" src/ tests/ --include="*.py"
   ```
   This must return empty.

3. Output the DROP TABLE SQL for the user to run on the live Postgres DB:
   ```sql
   DROP TABLE IF EXISTS snapshot_sales CASCADE;
   DROP TABLE IF EXISTS snapshot_price_points CASCADE;
   ```
   This is a manual step the user runs via psql. The CASCADE handles any FK constraints.
  </action>
  <verify>
    <automated>cd /c/Users/maftu/Projects/op-seller && python -m pytest tests/test_scanner.py -x -v --timeout=60</automated>
  </verify>
  <done>All scanner tests pass. Grep confirms zero references to removed models. DROP TABLE SQL provided for live DB.</done>
</task>

</tasks>

<verification>
- `grep -r "SnapshotSale\|SnapshotPricePoint" src/ tests/ --include="*.py"` returns empty
- `python -m pytest tests/test_scanner.py -x -v` passes
- `python -c "from src.server.db import create_engine_and_tables"` succeeds without error
</verification>

<success_criteria>
- SnapshotSale and SnapshotPricePoint classes fully removed from ORM
- All imports and references cleaned from src/ and tests/
- Scanner tests pass without the removed test functions
- DROP TABLE SQL documented for live DB execution
</success_criteria>

<output>
After completion, create `.planning/quick/260330-gsm-drop-snapshot-sales-and-snapshot-price-p/260330-gsm-SUMMARY.md`
</output>
