---
phase: quick
plan: 260327-hus
type: execute
wave: 1
depends_on: []
files_modified:
  - src/server/api/portfolio.py
  - tests/test_portfolio.py
autonomous: true
requirements: []
must_haves:
  truths:
    - "Volatility filter detects mid-window price spikes, not just earliest-vs-latest"
    - "Volatility filter uses SnapshotPricePoint.lowest_bin (hourly fut.gg history) instead of MarketSnapshot.current_lowest_bin (local scan snapshots)"
    - "Players with (max_bin - min_bin) / min_bin > 30% over 3 days are excluded from portfolio"
    - "All existing unit and integration volatility tests pass after update"
  artifacts:
    - path: "src/server/api/portfolio.py"
      provides: "Rewritten _get_volatile_ea_ids using SnapshotPricePoint JOIN MarketSnapshot"
      contains: "SnapshotPricePoint"
    - path: "tests/test_portfolio.py"
      provides: "Updated volatility tests seeding SnapshotPricePoint rows"
      contains: "SnapshotPricePoint"
  key_links:
    - from: "src/server/api/portfolio.py"
      to: "SnapshotPricePoint table"
      via: "JOIN to MarketSnapshot for ea_id"
      pattern: "SnapshotPricePoint.*MarketSnapshot"
---

<objective>
Rewrite `_get_volatile_ea_ids()` in `src/server/api/portfolio.py` to use `SnapshotPricePoint` (fut.gg hourly price history) instead of `MarketSnapshot.current_lowest_bin` (local scan snapshots). The new approach compares MIN vs MAX `lowest_bin` over the 3-day window, which catches mid-window spikes that the old earliest-vs-latest approach missed.

Purpose: The current implementation only compares the earliest and latest `MarketSnapshot.current_lowest_bin` in the lookback window. A player whose price spiked mid-window then returned to normal would not be detected. Using `SnapshotPricePoint` with MIN/MAX aggregation over hourly data gives true volatility detection with full 3-day coverage.

Output: Updated `_get_volatile_ea_ids()` function and matching test updates.
</objective>

<execution_context>
@$HOME/.claude/get-shit-done/workflows/execute-plan.md
@$HOME/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@src/server/api/portfolio.py
@src/server/models_db.py
@tests/test_portfolio.py
@src/config.py

<interfaces>
From src/server/models_db.py:
```python
class MarketSnapshot(Base):
    __tablename__ = "market_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ea_id: Mapped[int] = mapped_column(Integer, index=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime)
    current_lowest_bin: Mapped[int] = mapped_column(Integer)
    listing_count: Mapped[int] = mapped_column(Integer)
    live_auction_prices: Mapped[str] = mapped_column(Text)

class SnapshotPricePoint(Base):
    __tablename__ = "snapshot_price_points"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("market_snapshots.id", ondelete="CASCADE"), index=True
    )
    recorded_at: Mapped[datetime] = mapped_column(DateTime)
    lowest_bin: Mapped[int] = mapped_column(Integer)
```

JOIN path: `SnapshotPricePoint.snapshot_id -> MarketSnapshot.id -> MarketSnapshot.ea_id`
</interfaces>
</context>

<tasks>

<task type="auto">
  <name>Task 1: Rewrite _get_volatile_ea_ids to use SnapshotPricePoint</name>
  <files>src/server/api/portfolio.py</files>
  <action>
Rewrite `_get_volatile_ea_ids()` (line 97) to query `SnapshotPricePoint` instead of `MarketSnapshot`. The new implementation:

1. Add `SnapshotPricePoint` to the import from `src.server.models_db` (line 16).

2. Replace the entire function body with a single query approach:
   - JOIN `SnapshotPricePoint` to `MarketSnapshot` on `SnapshotPricePoint.snapshot_id == MarketSnapshot.id`
   - Filter WHERE `MarketSnapshot.ea_id.in_(ea_ids)` AND `SnapshotPricePoint.recorded_at >= cutoff`
   - GROUP BY `MarketSnapshot.ea_id`
   - SELECT `MarketSnapshot.ea_id`, `func.min(SnapshotPricePoint.lowest_bin).label("min_bin")`, `func.max(SnapshotPricePoint.lowest_bin).label("max_bin")`
   - HAVING `func.count(SnapshotPricePoint.id) >= 2` (need at least 2 data points)
   - No need for DISTINCT — MIN/MAX aggregation naturally ignores duplicates from multiple snapshot scans storing the same hourly history row

3. In Python, iterate results: if `min_bin > 0` and `(max_bin - min_bin) / min_bin > threshold`, add ea_id to volatile set.

4. This replaces the old 3-query approach (range query + earliest bin query + latest bin query) with a single efficient query. The key semantic change: old code compared earliest-vs-latest timestamps (directional), new code compares global min-vs-max prices (captures mid-window spikes).

5. Update the docstring to reflect: "compares MIN vs MAX lowest_bin from SnapshotPricePoint (fut.gg hourly price history) over the lookback window" and remove references to MarketSnapshot.current_lowest_bin and the 3-query approach.

6. Keep `MarketSnapshot` in the import line — it is still used by other code (the JOIN itself, and the `_seed_snapshots` helper in tests references it). Only the function body and docstring change.
  </action>
  <verify>
    <automated>cd C:/Users/maftu/Projects/op-seller && python -m pytest tests/test_portfolio.py -x -q 2>&1 | head -30</automated>
  </verify>
  <done>_get_volatile_ea_ids queries SnapshotPricePoint with MIN/MAX aggregation via JOIN to MarketSnapshot for ea_id. Old 3-query MarketSnapshot.current_lowest_bin approach is fully replaced.</done>
</task>

<task type="auto">
  <name>Task 2: Update volatility tests to seed SnapshotPricePoint rows</name>
  <files>tests/test_portfolio.py</files>
  <action>
The existing tests seed `MarketSnapshot` rows via `_seed_snapshots()` helper and the `volatility_integration_app` fixture. These must be updated to also seed `SnapshotPricePoint` rows, since the new `_get_volatile_ea_ids` reads from that table.

1. Add `SnapshotPricePoint` to the import from `src.server.models_db` (line 12).

2. Rewrite `_seed_snapshots()` helper (line 201):
   - Keep seeding `MarketSnapshot` rows as before (they provide the ea_id via JOIN).
   - After adding each `MarketSnapshot`, also add a corresponding `SnapshotPricePoint` row:
     ```python
     snapshot = MarketSnapshot(ea_id=ea_id, captured_at=captured_at, current_lowest_bin=bin_price, listing_count=10, live_auction_prices="[]")
     session.add(snapshot)
     await session.flush()  # get snapshot.id
     session.add(SnapshotPricePoint(snapshot_id=snapshot.id, recorded_at=captured_at, lowest_bin=bin_price))
     ```
   - Use `recorded_at=captured_at` and `lowest_bin=bin_price` so the SnapshotPricePoint mirrors the MarketSnapshot data (same test semantics).

3. Update `volatility_integration_app` fixture (line 296): same pattern — after adding each `MarketSnapshot` for the volatile (4001) and stable (4002) players, flush to get the id, then add a `SnapshotPricePoint` with matching `recorded_at` and `lowest_bin`.

4. Add one new unit test `test_mid_window_spike_detected` to validate the key improvement:
   - Seed ea_id=3010 with 3 SnapshotPricePoint rows: day -2 at 10000, day -1 at 16000 (spike), day 0 at 10500 (returned to normal).
   - Old approach (earliest-vs-latest) would see +5% increase and NOT flag it.
   - New approach (min-vs-max) sees 10000 vs 16000 = +60% and flags it as volatile.
   - Assert `3010 in volatile`.

5. All 5 existing unit tests and 2 integration tests must still pass with updated seeding. The test semantics are unchanged — stable/volatile thresholds are the same — only the data source switches from MarketSnapshot.current_lowest_bin to SnapshotPricePoint.lowest_bin.
  </action>
  <verify>
    <automated>cd C:/Users/maftu/Projects/op-seller && python -m pytest tests/test_portfolio.py -x -v 2>&1 | tail -20</automated>
  </verify>
  <done>All volatility tests pass. Tests seed SnapshotPricePoint rows alongside MarketSnapshot rows. New mid-window spike test proves the improvement over the old approach.</done>
</task>

</tasks>

<verification>
Run the full test suite to confirm no regressions:
```bash
python -m pytest tests/test_portfolio.py -v
```
All tests pass including the new mid-window spike detection test.
</verification>

<success_criteria>
- `_get_volatile_ea_ids()` queries SnapshotPricePoint with MIN/MAX aggregation (not MarketSnapshot.current_lowest_bin)
- Mid-window price spikes are detected (new test proves this)
- All 5 existing unit tests + 2 integration tests + 1 new test pass
- No changes to the volatility threshold (30%) or lookback window (3 days) config
</success_criteria>

<output>
After completion, create `.planning/quick/260327-hus-fix-volatility-filter-to-use-snapshotpri/260327-hus-SUMMARY.md`
</output>
