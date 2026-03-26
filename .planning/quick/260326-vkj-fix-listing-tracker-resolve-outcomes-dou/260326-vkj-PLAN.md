---
phase: quick
plan: 260326-vkj
type: execute
wave: 1
depends_on: []
files_modified:
  - src/server/listing_tracker.py
  - tests/test_listing_tracker.py
autonomous: true
requirements: []
must_haves:
  truths:
    - "resolve_outcomes only counts completed sales that occurred AFTER the previous resolution for that player"
    - "First-ever resolution for a player counts all available sales (bootstrap case)"
    - "Consecutive resolution batches with the same completedAuctions do NOT double-count sales"
  artifacts:
    - path: "src/server/listing_tracker.py"
      provides: "Timestamp-filtered outcome resolution"
      contains: "last_resolved_at"
    - path: "tests/test_listing_tracker.py"
      provides: "Double-counting regression test"
      contains: "test_resolve_outcomes_no_double_counting"
  key_links:
    - from: "resolve_outcomes"
      to: "ListingObservation.resolved_at"
      via: "MAX(resolved_at) query to determine cutoff"
      pattern: "last_resolved_at"
---

<objective>
Fix double-counting bug in resolve_outcomes() where the same completedAuctions are re-counted across consecutive resolution batches, inflating sold rates (e.g., Guirassy 60.6% vs FUTBIN 36.6%).

Purpose: Correct sell/expire ratio accuracy — the core metric driving OP sell recommendations.
Output: Fixed resolve_outcomes() with timestamp filtering and a regression test proving multi-batch correctness.
</objective>

<execution_context>
@$HOME/.claude/get-shit-done/workflows/execute-plan.md
@$HOME/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@src/server/listing_tracker.py
@tests/test_listing_tracker.py
@src/server/models_db.py (ListingObservation schema — has resolved_at column)
@src/models.py (SaleRecord — has sold_at field)
</context>

<interfaces>
<!-- Key types the executor needs -->

From src/models.py:
```python
class SaleRecord(BaseModel):
    resource_id: int
    sold_at: datetime
    sold_price: int
```

From src/server/models_db.py:
```python
class ListingObservation(Base):
    __tablename__ = "listing_observations"
    id: Mapped[int]
    fingerprint: Mapped[str]  # unique, indexed
    ea_id: Mapped[int]  # indexed
    buy_now_price: Mapped[int]
    market_price_at_obs: Mapped[int]
    first_seen_at: Mapped[datetime]  # indexed
    last_seen_at: Mapped[datetime]
    expected_expiry_at: Mapped[datetime | None]
    scan_count: Mapped[int]
    outcome: Mapped[str | None]  # "sold"|"expired"|None
    resolved_at: Mapped[datetime | None]
```
</interfaces>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: Add regression test for double-counting bug</name>
  <files>tests/test_listing_tracker.py</files>
  <behavior>
    - test_resolve_outcomes_no_double_counting: Simulates two consecutive resolution batches with the SAME completedAuctions list.
      - Batch 1: 5 listings at 160k disappear. completedAuctions has 10 sales at 160k. All 5 marked sold (correct).
      - Batch 2: 3 NEW listings at 160k disappear. completedAuctions STILL has same 10 sales at 160k. Only sales with sold_at AFTER batch 1's resolved_at should count. If no new sales occurred, 0 should be sold and 3 should be expired.
    - test_resolve_outcomes_first_resolution_counts_all: First-ever resolution for a player (no prior resolved_at) counts all available completedAuctions — bootstrap correctness.
  </behavior>
  <action>
    Add two new test functions to tests/test_listing_tracker.py:

    1. `test_resolve_outcomes_no_double_counting`:
       - Record 5 listings at 160k with trade_ids 7001-7005, remaining_seconds=-60 (expired)
       - Create 10 SaleRecord entries at 160k with sold_at = now (these represent the sliding window)
       - Call resolve_outcomes with empty current_fingerprints — expect 5 sold, 0 expired
       - Record 3 NEW listings at 160k with trade_ids 7006-7008, remaining_seconds=-60
       - Call resolve_outcomes AGAIN with the SAME 10 SaleRecord list (simulating the sliding window not changing)
       - Assert: 0 sold, 3 expired (not 3 sold) — because no NEW sales occurred after the first resolution

    2. `test_resolve_outcomes_first_resolution_counts_all`:
       - Record 2 listings at 100k with trade_ids 8001-8002, remaining_seconds=-60
       - Create 5 SaleRecord entries at 100k with sold_at = now
       - Call resolve_outcomes — expect 2 sold, 0 expired (all sales available for first resolution)

    Use the existing _make_live_auction and _make_sale helpers. Use the existing db fixture.
  </action>
  <verify>
    <automated>cd C:/Users/maftu/Projects/op-seller && python -m pytest tests/test_listing_tracker.py::test_resolve_outcomes_no_double_counting tests/test_listing_tracker.py::test_resolve_outcomes_first_resolution_counts_all -x -v 2>&1 | tail -20</automated>
  </verify>
  <done>
    - test_resolve_outcomes_no_double_counting FAILS (proving the bug exists before fix)
    - test_resolve_outcomes_first_resolution_counts_all PASSES (bootstrap case already works)
  </done>
</task>

<task type="auto">
  <name>Task 2: Fix resolve_outcomes to filter completed_sales by timestamp</name>
  <files>src/server/listing_tracker.py</files>
  <action>
    Modify the `resolve_outcomes()` function in src/server/listing_tracker.py to filter out stale completedAuctions:

    1. Before the "Group by price" block (line ~223), query the most recent `resolved_at` for this ea_id from previously resolved ListingObservation rows:
       ```python
       last_resolved_stmt = select(
           sqlalchemy.func.max(ListingObservation.resolved_at)
       ).where(
           ListingObservation.ea_id == ea_id,
           ListingObservation.resolved_at.isnot(None),
       )
       last_resolved_result = await session.execute(last_resolved_stmt)
       last_resolved_at = last_resolved_result.scalar()
       ```
       Add `func` import: `from sqlalchemy import select, func` (replace existing import line).

    2. Filter completed_sales to only include sales AFTER last_resolved_at:
       ```python
       if last_resolved_at is not None:
           completed_sales = [
               sale for sale in completed_sales
               if sale.sold_at > last_resolved_at
           ]
       ```
       If last_resolved_at is None (first resolution), keep all sales — correct bootstrap behavior.

    3. The rest of the function (grouping by price, proportional matching) stays exactly the same.

    4. Add a debug log after filtering:
       ```python
       logger.debug(
           f"resolve_outcomes: ea_id={ea_id} last_resolved_at={last_resolved_at} "
           f"sales_after_filter={len(completed_sales)} disappeared={len(disappeared)}"
       )
       ```
  </action>
  <verify>
    <automated>cd C:/Users/maftu/Projects/op-seller && python -m pytest tests/test_listing_tracker.py -x -v 2>&1 | tail -30</automated>
  </verify>
  <done>
    - All existing tests pass (no regressions)
    - test_resolve_outcomes_no_double_counting now PASSES (bug fixed)
    - test_resolve_outcomes_first_resolution_counts_all still PASSES
  </done>
</task>

</tasks>

<verification>
Run full listing_tracker test suite:
```bash
python -m pytest tests/test_listing_tracker.py -v
```
All tests pass including the new double-counting regression test.
</verification>

<success_criteria>
- resolve_outcomes() filters completedAuctions by sold_at > last_resolved_at for the player
- First-ever resolution counts all sales (bootstrap)
- Consecutive batches with same completedAuctions do NOT inflate sold counts
- All existing tests pass without modification
</success_criteria>

<output>
After completion, create `.planning/quick/260326-vkj-fix-listing-tracker-resolve-outcomes-dou/260326-vkj-SUMMARY.md`
</output>
