---
phase: quick
plan: 260326-ufn
type: execute
wave: 1
depends_on: []
files_modified:
  - src/server/scanner.py
  - src/server/models_db.py
  - tests/test_scanner.py
autonomous: true
requirements: [FUTBIN-FIX]
must_haves:
  truths:
    - "Duplicate completed auctions are never inserted into snapshot_sales"
    - "Player names are populated from fut.gg API data during discovery and scans"
    - "PlayerScore rows always have scorer_version='v2' set"
  artifacts:
    - path: "src/server/scanner.py"
      provides: "Deduplicated snapshot_sales insertion, player name population, scorer_version tagging"
    - path: "src/server/models_db.py"
      provides: "Unique constraint on snapshot_sales(snapshot_id, sold_at, sold_price)"
    - path: "tests/test_scanner.py"
      provides: "Tests for deduplication and name population"
  key_links:
    - from: "src/server/scanner.py"
      to: "src/server/models_db.py"
      via: "SnapshotSale unique constraint prevents duplicates"
      pattern: "UniqueConstraint.*sold_at.*sold_price"
---

<objective>
Fix three data quality issues found by comparing server DB against FUTBIN after 12 hours of operation:
1. Deduplicate snapshot_sales — same completed auction appears 3-5x across scan cycles
2. Populate player names from fut.gg API data instead of storing ea_id as name
3. Set scorer_version='v2' on all PlayerScore rows

Purpose: Correct data integrity issues that inflate OP ratio calculations and make player data unusable without names.
Output: Patched scanner.py, models_db.py, and tests proving deduplication works.

Note: Finding 2 (scorer margin inflation from v1 scorer) is NOT addressed here — the v2 scorer already uses listing observations instead of completedAuctions. The issue was that scorer_version was None, causing uncertainty about which scorer ran. Setting scorer_version='v2' explicitly confirms v2 is active. The v2 scorer itself is correct — it scores from ListingObservation data, not from snapshot_sales.
</objective>

<execution_context>
@.claude/get-shit-done/workflows/execute-plan.md
@.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@src/server/scanner.py
@src/server/models_db.py
@src/futgg_client.py
@tests/test_scanner.py
</context>

<tasks>

<task type="auto">
  <name>Task 1: Deduplicate snapshot_sales and add unique constraint</name>
  <files>src/server/models_db.py, src/server/scanner.py</files>
  <action>
**models_db.py — Add unique constraint to SnapshotSale:**

Add a `__table_args__` tuple to the `SnapshotSale` class with a `UniqueConstraint` on `(snapshot_id, sold_at, sold_price)`. This prevents the same sale (identified by timestamp + price) from being inserted twice within a single snapshot. Import `UniqueConstraint` from sqlalchemy.

```python
from sqlalchemy import ..., UniqueConstraint

class SnapshotSale(Base):
    ...
    __table_args__ = (
        UniqueConstraint("snapshot_id", "sold_at", "sold_price", name="uq_snapshot_sale"),
    )
```

**scanner.py — Deduplicate sales before insertion in scan_player():**

In `scan_player()`, around line 365, before the `for sale in market_data.sales:` loop, deduplicate sales using a set of `(sold_at, sold_price)` tuples. The fut.gg API returns 100 most recent completedAuctions, and between 5-minute scan cycles many of these overlap. Only insert sales not already seen in this snapshot.

Replace the direct insertion loop (lines 365-370) with:

```python
seen_sales = set()
for sale in market_data.sales:
    key = (sale.sold_at, sale.sold_price)
    if key in seen_sales:
        continue
    seen_sales.add(key)
    session.add(SnapshotSale(
        snapshot_id=snapshot.id,
        sold_at=sale.sold_at,
        sold_price=sale.sold_price,
    ))
```

Note: The deduplication within a single snapshot's sales list handles the in-memory case. The UniqueConstraint is a DB-level safety net. Cross-snapshot duplication is acceptable — each snapshot is a point-in-time capture, and the cleanup job handles retention.
  </action>
  <verify>
    <automated>cd C:/Users/maftu/Projects/op-seller && python -m pytest tests/test_scanner.py -x -q 2>&1 | tail -5</automated>
  </verify>
  <done>SnapshotSale model has UniqueConstraint on (snapshot_id, sold_at, sold_price). Scanner deduplicates sales before insertion. Existing tests still pass.</done>
</task>

<task type="auto">
  <name>Task 2: Populate player names from API and set scorer_version</name>
  <files>src/server/scanner.py, tests/test_scanner.py</files>
  <action>
**scanner.py — Extract player name in scan_player() and update PlayerRecord:**

In `scan_player()`, after market_data is fetched (around line 279), the `market_data.player` object already has the correct name (built by `_build_player()` which uses `commonName` or `firstName + lastName`). When updating the PlayerRecord (around line 380-384), also update the name:

```python
record = await session.get(PlayerRecord, ea_id)
if record is not None:
    record.last_scanned_at = now
    if market_data is not None:
        record.listing_count = market_data.listing_count
        # Populate player name from API data
        if market_data.player and market_data.player.name:
            record.name = market_data.player.name
```

**scanner.py — Extract player name in run_bootstrap():**

In `run_bootstrap()`, the `player_data` dict from `discover_players()` contains the raw fut.gg API response which has `commonName`, `firstName`, `lastName` fields. Update the values_list construction (around line 100-116) to extract the name:

```python
values_list = [
    dict(
        ea_id=p["ea_id"],
        name=p.get("commonName") or f"{p.get('firstName', '')} {p.get('lastName', '')}".strip() or str(p["ea_id"]),
        ...  # rest unchanged
    )
    for p in players
]
```

Apply the same name extraction in `run_discovery()` (around line 215) — update the `sqlite_insert` values to use the same name logic instead of `name=str(ea_id)`.

**scanner.py — Set scorer_version='v2' on PlayerScore rows:**

In `scan_player()`, when creating the PlayerScore object (around line 318), add `scorer_version="v2"` to the v2_result branch. Also add `scorer_version=None` explicitly to the else branch (non-viable fallback) for clarity.

**tests/test_scanner.py — Add tests for deduplication and name population:**

Add two new test functions:

1. `test_scan_player_deduplicates_snapshot_sales` — Create a mock market_data with duplicate sales (same sold_at + sold_price appearing twice in the sales list). Run scan_player. Query SnapshotSale rows and assert only unique sales were inserted.

2. `test_scan_player_populates_name` — Create a mock market_data where `player.name = "Klostermann"`. Run scan_player. Query PlayerRecord and assert `record.name == "Klostermann"` (not the ea_id string).

3. `test_scan_player_sets_scorer_version` — After scan_player completes with a viable v2 result, query PlayerScore and assert `scorer_version == "v2"`.

Follow existing test patterns: use the `scanner` fixture, mock `_client.get_player_market_data`, seed a PlayerRecord, call `await svc.scan_player(ea_id)`, then query the DB to verify.
  </action>
  <verify>
    <automated>cd C:/Users/maftu/Projects/op-seller && python -m pytest tests/test_scanner.py -x -q 2>&1 | tail -10</automated>
  </verify>
  <done>Player names populated from fut.gg commonName/firstName/lastName during bootstrap, discovery, and scans. PlayerScore rows have scorer_version="v2". Three new tests pass proving deduplication, name population, and scorer_version tagging work correctly.</done>
</task>

</tasks>

<verification>
```bash
# All scanner tests pass
python -m pytest tests/test_scanner.py -x -v

# Full test suite passes (no regressions)
python -m pytest tests/ -x -q
```
</verification>

<success_criteria>
- SnapshotSale has UniqueConstraint preventing duplicate insertions
- Scanner deduplicates sales in-memory before DB insertion
- PlayerRecord.name populated with real player names from fut.gg API
- PlayerScore.scorer_version set to "v2" for all v2-scored rows
- Three new tests cover deduplication, name population, and scorer_version
- Full test suite passes with no regressions
</success_criteria>

<output>
After completion, create `.planning/quick/260326-ufn-fix-all-futbin-findings-deduplicate-snap/260326-ufn-SUMMARY.md`
</output>
