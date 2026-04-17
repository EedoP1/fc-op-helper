# Remove FUTBIN Scraper Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Delete the FUTBIN Playwright scraper, make the fut.gg live-scanner data (`market_snapshots` table) the sole backtester price source, leave the DB schema and row data untouched.

**Architecture:** Strip the `--source` CLI flag, `load_price_data` function, `futbin_to_ea` id-mapping dict, and `use_hourly_grid` branching from `src/algo/engine.py`. Rewrite `load_market_snapshot_data` to be DB-agnostic (application-side hour bucketing instead of Postgres `DISTINCT ON`/`date_trunc`) so the SQLite-backed integration test can exercise it. Delete `src/algo/scraper.py` and its test. Rewrite `tests/algo/test_integration.py` to seed `market_snapshots` instead of `price_history`.

**Tech Stack:** Python 3.12, SQLAlchemy async, pytest + pytest-asyncio, aiosqlite (tests), asyncpg (prod).

**Spec:** `docs/superpowers/specs/2026-04-17-remove-futbin-scraper-design.md`

**Implementation note:** `load_market_snapshot_data` currently uses Postgres-only SQL (`DISTINCT ON`, `date_trunc`). The SQLite `:memory:` fixture used by `tests/algo/test_integration.py` cannot run that query. Rewriting the function to bucket rows in Python produces identical results and unblocks the test without adding Postgres infrastructure to the test suite. This is a behavior-preserving refactor, done as part of Task 1.

---

## File structure

- **Delete:**
    - `src/algo/scraper.py` (437 lines)
    - `tests/algo/test_scraper.py` (32 lines)
- **Modify:**
    - `src/algo/__main__.py` — drop `scrape` subcommand + help line
    - `src/algo/engine.py` — remove `load_price_data`, `--source`, `futbin_to_ea`, `use_hourly_grid`; add `days` + `now` params to `load_market_snapshot_data`; rewrite its query DB-agnostic
    - `tests/algo/test_integration.py` — seed `market_snapshots`, use `load_market_snapshot_data`
- **Untouched:**
    - `src/algo/models_db.py` (`PriceHistory` class stays — table stays)
    - `src/algo/report.py`, `src/algo/strategies/*`, `src/algo/models.py`, `src/algo/live.py`, `src/algo/backfill_created_at.py`
    - `tests/algo/test_models_db.py`, `test_engine.py`, `test_algo_api.py`, `test_algo_runner.py`, `test_algo_models.py`, `test_models.py`, `test_signal_parity.py`, `test_signal_parity_db.py`, `test_strategies.py`
    - `requirements.txt` (playwright stays — used by live scanner)

---

## Task 1: Add `--days` filter and make `load_market_snapshot_data` DB-agnostic

**Files:**
- Modify: `src/algo/engine.py:544-610` (the existing `load_market_snapshot_data` function)
- Test: `tests/algo/test_engine.py` (add new tests to this existing file)

- [ ] **Step 1: Write the failing tests**

Append to `tests/algo/test_engine.py`:

```python
@pytest.mark.asyncio
async def test_load_market_snapshot_data_hour_bucketing():
    """Multiple snapshots within the same hour collapse to the latest one."""
    from datetime import datetime, timedelta
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from sqlalchemy import text
    from src.server.db import Base
    from src.server.models_db import MarketSnapshot, PlayerRecord  # noqa: F401
    from src.algo.engine import load_market_snapshot_data

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    base = datetime(2026, 1, 1, 12, 0, 0)
    async with session_factory() as session:
        # Three snapshots in the same hour, increasing prices
        for i, price in enumerate([100, 200, 300]):
            await session.execute(
                text(
                    "INSERT INTO market_snapshots (ea_id, captured_at, current_lowest_bin, listing_count) "
                    "VALUES (:ea_id, :captured_at, :price, 1)"
                ),
                {"ea_id": 1, "captured_at": (base + timedelta(minutes=i * 10)).isoformat(), "price": price},
            )
        # Six snapshots in the next six hours to meet min_data_points=6
        for h in range(1, 7):
            await session.execute(
                text(
                    "INSERT INTO market_snapshots (ea_id, captured_at, current_lowest_bin, listing_count) "
                    "VALUES (:ea_id, :captured_at, :price, 1)"
                ),
                {"ea_id": 1, "captured_at": (base + timedelta(hours=h)).isoformat(), "price": 500 + h},
            )
        await session.commit()

    price_data, _ = await load_market_snapshot_data(session_factory, min_data_points=6)

    assert 1 in price_data, "ea_id 1 should be present"
    # First hour collapses to the last snapshot (price=300 at base+20min)
    first_ts, first_price = price_data[1][0]
    assert first_ts == base.replace(minute=0, second=0, microsecond=0)
    assert first_price == 300, f"Expected last snapshot in hour (300), got {first_price}"
    assert len(price_data[1]) == 7, "1 bucket for the first hour + 6 hourly rows after"

    await engine.dispose()


@pytest.mark.asyncio
async def test_load_market_snapshot_data_days_filter_sunday_aligned():
    """--days cutoff rolls back to the previous Sunday 00:00 UTC."""
    from datetime import datetime, timedelta
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from sqlalchemy import text
    from src.server.db import Base
    from src.server.models_db import MarketSnapshot, PlayerRecord  # noqa: F401
    from src.algo.engine import load_market_snapshot_data

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    # Reference "now" = Wednesday 2026-04-15 10:00:00 UTC
    now = datetime(2026, 4, 15, 10, 0, 0)
    # With days=5, naive cutoff = 2026-04-10 10:00:00 (a Friday).
    # Sunday-aligned cutoff rolls back to previous Sunday = 2026-04-05 00:00:00.
    # So anything at or after 2026-04-05 00:00 is kept; anything before is dropped.

    async with session_factory() as session:
        # Old row: 2026-04-04 23:59 — should be dropped
        await session.execute(
            text(
                "INSERT INTO market_snapshots (ea_id, captured_at, current_lowest_bin, listing_count) "
                "VALUES (:ea_id, :captured_at, :price, 1)"
            ),
            {"ea_id": 1, "captured_at": datetime(2026, 4, 4, 23, 59).isoformat(), "price": 100},
        )
        # Exactly on cutoff: 2026-04-05 00:00 — should be kept
        await session.execute(
            text(
                "INSERT INTO market_snapshots (ea_id, captured_at, current_lowest_bin, listing_count) "
                "VALUES (:ea_id, :captured_at, :price, 1)"
            ),
            {"ea_id": 1, "captured_at": datetime(2026, 4, 5, 0, 0).isoformat(), "price": 200},
        )
        # Pad with 5 more recent rows so ea_id 1 clears min_data_points=6
        for h in range(1, 6):
            await session.execute(
                text(
                    "INSERT INTO market_snapshots (ea_id, captured_at, current_lowest_bin, listing_count) "
                    "VALUES (:ea_id, :captured_at, :price, 1)"
                ),
                {"ea_id": 1, "captured_at": (datetime(2026, 4, 5, h, 0)).isoformat(), "price": 200 + h},
            )
        await session.commit()

    price_data, _ = await load_market_snapshot_data(
        session_factory, min_data_points=6, days=5, now=now,
    )

    assert 1 in price_data
    timestamps = [ts for ts, _ in price_data[1]]
    assert datetime(2026, 4, 4, 23, 0) not in timestamps, "Pre-cutoff row should be filtered out"
    assert datetime(2026, 4, 5, 0, 0) in timestamps, "Row at exact cutoff should be kept"

    await engine.dispose()


@pytest.mark.asyncio
async def test_load_market_snapshot_data_days_zero_means_no_filter():
    from datetime import datetime
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from sqlalchemy import text
    from src.server.db import Base
    from src.server.models_db import MarketSnapshot, PlayerRecord  # noqa: F401
    from src.algo.engine import load_market_snapshot_data

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        for h in range(6):
            await session.execute(
                text(
                    "INSERT INTO market_snapshots (ea_id, captured_at, current_lowest_bin, listing_count) "
                    "VALUES (:ea_id, :captured_at, :price, 1)"
                ),
                {"ea_id": 1, "captured_at": datetime(2020, 1, 1, h, 0).isoformat(), "price": 100 + h},
            )
        await session.commit()

    # days=0 → no filter; ancient data is returned
    price_data, _ = await load_market_snapshot_data(session_factory, min_data_points=6, days=0)
    assert 1 in price_data
    assert len(price_data[1]) == 6

    await engine.dispose()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/algo/test_engine.py::test_load_market_snapshot_data_hour_bucketing tests/algo/test_engine.py::test_load_market_snapshot_data_days_filter_sunday_aligned tests/algo/test_engine.py::test_load_market_snapshot_data_days_zero_means_no_filter -v`

Expected: FAIL — either `(sqlite3.OperationalError) near "ON"` from `DISTINCT ON`, or `TypeError: got unexpected keyword argument 'days'`/`'now'`.

- [ ] **Step 3: Rewrite `load_market_snapshot_data` to be DB-agnostic and accept `days` + `now`**

Replace the function body in `src/algo/engine.py` (currently lines 544–610). The replacement:

```python
async def load_market_snapshot_data(
    session_factory: async_sessionmaker[AsyncSession],
    min_price: int = 0,
    max_price: int = 0,
    min_data_points: int = 6,
    days: int = 0,
    now: dt | None = None,
) -> tuple[dict[int, list[tuple[dt, int]]], dict[int, dt]]:
    """Load hourly price data from market_snapshots.

    Aggregates to one price per player per hour (last snapshot in each hour).
    Hour bucketing is done in Python so the function works on any SQL backend.

    Args:
        days: If >0, only include snapshots within the last N days, with
              cutoff rolled back to the previous Sunday 00:00 UTC. Matches
              the week-aligned semantics previously in load_price_data.
        now: Reference "now" for cutoff calculation; defaults to utcnow().
             Exposed for deterministic tests.

    Returns:
        (price_data, created_at_map)
        price_data: {ea_id: [(timestamp, price), ...]} sorted by timestamp.
        created_at_map: {ea_id: created_at} from players table.
    """
    import datetime as _dt

    params: dict = {}
    where = ["current_lowest_bin > 0"]
    if min_price:
        where.append("current_lowest_bin >= :min_price")
        params["min_price"] = min_price
    if max_price:
        where.append("current_lowest_bin <= :max_price")
        params["max_price"] = max_price

    if days > 0:
        ref = now or dt.utcnow()
        cutoff = ref - _dt.timedelta(days=days)
        # Align cutoff to previous Sunday 00:00 UTC so strategies that key
        # off full week boundaries (e.g. Friday promos) get whole weeks.
        days_since_sunday = (cutoff.weekday() + 1) % 7  # Mon=0->1, Sun=6->0
        cutoff = cutoff - _dt.timedelta(days=days_since_sunday)
        cutoff = cutoff.replace(hour=0, minute=0, second=0, microsecond=0)
        where.append("captured_at >= :cutoff")
        params["cutoff"] = cutoff

    query = text(
        "SELECT ea_id, captured_at, current_lowest_bin "
        "FROM market_snapshots "
        f"WHERE {' AND '.join(where)} "
        "ORDER BY ea_id, captured_at"
    )

    async with session_factory() as session:
        result = await session.execute(query, params)
        rows = result.fetchall()

    logger.info(f"Loaded {len(rows)} raw snapshots from market_snapshots")

    # App-side hour bucketing + dedup: keep the latest snapshot per (ea_id, hour).
    # Replaces the Postgres-only DISTINCT ON / date_trunc query.
    buckets: dict[tuple[int, dt], tuple[dt, int]] = {}
    for ea_id, captured_at, price in rows:
        if isinstance(captured_at, str):
            captured_at = dt.fromisoformat(captured_at)
        hour_ts = captured_at.replace(minute=0, second=0, microsecond=0)
        key = (ea_id, hour_ts)
        prev = buckets.get(key)
        if prev is None or captured_at > prev[0]:
            buckets[key] = (captured_at, price)

    data: dict[int, list[tuple[dt, int]]] = defaultdict(list)
    for (ea_id, hour_ts), (_captured, price) in buckets.items():
        data[ea_id].append((hour_ts, price))

    for ea_id in data:
        data[ea_id].sort(key=lambda x: x[0])

    filtered = {
        eid: points for eid, points in data.items()
        if len(points) >= min_data_points
    }

    # Load created_at from players table
    created_at_map: dict[int, dt] = {}
    async with session_factory() as session:
        ca_result = await session.execute(
            text("SELECT ea_id, created_at FROM players WHERE created_at IS NOT NULL")
        )
        for ea_id, created_at in ca_result.fetchall():
            if isinstance(created_at, str):
                created_at = dt.fromisoformat(created_at)
            created_at_map[ea_id] = created_at

    logger.info(
        f"Filtered to {len(filtered)} players with >= {min_data_points} hourly points, "
        f"{len(created_at_map)} have created_at"
    )

    return filtered, created_at_map
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/algo/test_engine.py -v`

Expected: all three new tests PASS. Existing tests in the file (e.g. `test_run_and_save_results`) still PASS.

- [ ] **Step 5: Commit**

```bash
git add src/algo/engine.py tests/algo/test_engine.py
git commit -m "refactor(algo): make load_market_snapshot_data DB-agnostic, add --days filter"
```

---

## Task 2: Rewrite `test_integration.py` to seed `market_snapshots`

**Files:**
- Modify: `tests/algo/test_integration.py` (full rewrite)

- [ ] **Step 1: Replace `tests/algo/test_integration.py` with the new version**

Write the entire file:

```python
"""End-to-end test: seed market_snapshots -> run strategies -> check results."""
import pytest
from datetime import datetime, timedelta
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import text
from src.server.db import Base


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    # Ensure both algo + server tables are registered on the metadata
    from src.algo.models_db import BacktestResult  # noqa: F401
    from src.server.models_db import MarketSnapshot, PlayerRecord  # noqa: F401
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    yield session_factory
    await engine.dispose()


async def seed_price_data(session_factory, num_players=3, num_hours=200):
    """Insert synthetic hourly snapshots with a mean-reverting sine-wave pattern."""
    import math
    base = datetime(2026, 1, 1)
    async with session_factory() as session:
        for pid in range(1, num_players + 1):
            base_price = 10_000 * pid
            for h in range(num_hours):
                price = int(base_price + base_price * 0.15 * math.sin(h / 12 * math.pi))
                await session.execute(
                    text(
                        "INSERT INTO market_snapshots (ea_id, captured_at, current_lowest_bin, listing_count) "
                        "VALUES (:ea_id, :captured_at, :price, 1)"
                    ),
                    {
                        "ea_id": pid,
                        "captured_at": (base + timedelta(hours=h)).isoformat(),
                        "price": price,
                    },
                )
        await session.commit()


@pytest.mark.asyncio
async def test_full_pipeline(db):
    from src.algo.engine import load_market_snapshot_data, run_sweep, save_result
    from src.algo.strategies.mean_reversion import MeanReversionStrategy

    # Seed
    await seed_price_data(db, num_players=3, num_hours=200)

    # Load
    price_data, _ = await load_market_snapshot_data(db, min_data_points=10)
    assert len(price_data) == 3

    # Run sweep with a small grid
    class SmallGridMR(MeanReversionStrategy):
        name = "mean_reversion_test"
        def param_grid(self):
            return [
                {"window": 12, "threshold": 0.10, "position_pct": 0.02},
                {"window": 24, "threshold": 0.10, "position_pct": 0.02},
            ]

    results = run_sweep(SmallGridMR, price_data, budget=100_000)
    assert len(results) == 2

    # Save
    for r in results:
        await save_result(db, r)

    # Verify in DB
    async with db() as session:
        rows = await session.execute(text("SELECT COUNT(*) FROM backtest_results"))
        count = rows.scalar()
        assert count == 2

    # Verify results have expected fields
    for r in results:
        assert r["started_budget"] == 100_000
        assert isinstance(r["total_pnl"], int)
        assert 0.0 <= r["win_rate"] <= 1.0
        assert 0.0 <= r["max_drawdown"] <= 1.0


@pytest.mark.asyncio
async def test_all_strategies_run(db):
    """Every discovered strategy can complete a backtest without crashing."""
    from src.algo.engine import run_backtest, load_market_snapshot_data
    from src.algo.strategies import discover_strategies

    await seed_price_data(db, num_players=2, num_hours=100)
    price_data, _ = await load_market_snapshot_data(db, min_data_points=10)

    strategies = discover_strategies()
    assert len(strategies) >= 4, f"Expected 4+ strategies, found {list(strategies.keys())}"

    for name, cls in strategies.items():
        grid = cls({}).param_grid()
        strategy = cls(grid[0])
        result = run_backtest(strategy, price_data, budget=100_000)
        assert result["strategy_name"] == name
        assert result["started_budget"] == 100_000
```

- [ ] **Step 2: Run the integration tests**

Run: `python -m pytest tests/algo/test_integration.py -v`

Expected: both tests PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/algo/test_integration.py
git commit -m "test(algo): rewrite integration test to seed market_snapshots"
```

---

## Task 3: Strip `futbin_to_ea` plumbing from `engine.py`

**Files:**
- Modify: `src/algo/engine.py` (multiple locations)

- [ ] **Step 1: Remove `futbin_to_ea` from `run_backtest`**

In `src/algo/engine.py`, edit `run_backtest` (currently starts at line 25).

Replace:

```python
def run_backtest(
    strategy,
    price_data: dict[int, list[tuple[datetime, int]]],
    budget: int = 1_000_000,
    futbin_to_ea: dict[int, int] | None = None,
    created_at_map: dict[int, datetime] | None = None,
) -> dict:
```

With:

```python
def run_backtest(
    strategy,
    price_data: dict[int, list[tuple[datetime, int]]],
    budget: int = 1_000_000,
    created_at_map: dict[int, datetime] | None = None,
) -> dict:
```

Further down in the same function (currently around lines 94–104), replace:

```python
    fmap = futbin_to_ea or {}
    trade_log = [{
        "futbin_id": t.ea_id,
        "ea_id": fmap.get(t.ea_id, t.ea_id),
        "qty": t.quantity,
        "buy_price": t.buy_price,
        "sell_price": t.sell_price,
        "net_profit": t.net_profit,
        "buy_time": t.buy_time.isoformat(),
        "sell_time": t.sell_time.isoformat(),
    } for t in trades]
```

With:

```python
    trade_log = [{
        "ea_id": t.ea_id,
        "qty": t.quantity,
        "buy_price": t.buy_price,
        "sell_price": t.sell_price,
        "net_profit": t.net_profit,
        "buy_time": t.buy_time.isoformat(),
        "sell_time": t.sell_time.isoformat(),
    } for t in trades]
```

- [ ] **Step 2: Remove `futbin_to_ea` from `_worker_run_combos`**

In `_worker_run_combos` (currently starts at line 248), replace:

```python
def _worker_run_combos(
    sorted_timeline: list[tuple[datetime, list[tuple[int, int]]]],
    last_prices: dict[int, tuple[datetime, int]],
    combo_specs: list[tuple[str, dict]],
    budget: int,
    futbin_to_ea: dict[int, int] | None = None,
    created_at_map: dict[int, datetime] | None = None,
) -> list[dict]:
    """Worker function for parallel sweep. Runs in a subprocess.

    Args:
        sorted_timeline: [(timestamp, [(ea_id, price), ...]), ...] pre-sorted.
        last_prices: {ea_id: (timestamp, price)} for force-selling.
        combo_specs: [(strategy_module_path, params), ...] to instantiate.
        budget: Starting coin balance for each combo.
        futbin_to_ea: {futbin_id: ea_id} mapping for trade log.
    """
```

With:

```python
def _worker_run_combos(
    sorted_timeline: list[tuple[datetime, list[tuple[int, int]]]],
    last_prices: dict[int, tuple[datetime, int]],
    combo_specs: list[tuple[str, dict]],
    budget: int,
    created_at_map: dict[int, datetime] | None = None,
) -> list[dict]:
    """Worker function for parallel sweep. Runs in a subprocess.

    Args:
        sorted_timeline: [(timestamp, [(ea_id, price), ...]), ...] pre-sorted.
        last_prices: {ea_id: (timestamp, price)} for force-selling.
        combo_specs: [(strategy_module_path, params), ...] to instantiate.
        budget: Starting coin balance for each combo.
    """
```

Further down in the same function (currently around lines 314–327), replace:

```python
        # Build trade log
        fmap = futbin_to_ea or {}
        trade_log = []
        for t in trades:
            trade_log.append({
                "futbin_id": t.ea_id,
                "ea_id": fmap.get(t.ea_id, t.ea_id),
                "qty": t.quantity,
                "buy_price": t.buy_price,
                "sell_price": t.sell_price,
                "net_profit": t.net_profit,
                "buy_time": t.buy_time.isoformat(),
                "sell_time": t.sell_time.isoformat(),
            })
```

With:

```python
        trade_log = []
        for t in trades:
            trade_log.append({
                "ea_id": t.ea_id,
                "qty": t.quantity,
                "buy_price": t.buy_price,
                "sell_price": t.sell_price,
                "net_profit": t.net_profit,
                "buy_time": t.buy_time.isoformat(),
                "sell_time": t.sell_time.isoformat(),
            })
```

- [ ] **Step 3: Remove `futbin_to_ea` from `run_sweep_parallel`**

In `run_sweep_parallel` (currently starts at line 345), replace the signature:

```python
def run_sweep_parallel(
    strategy_classes: list[type],
    price_data: dict[int, list[tuple[datetime, int]]],
    budget: int = 1_000_000,
    max_workers: int | None = None,
    futbin_to_ea: dict[int, int] | None = None,
    created_at_map: dict[int, datetime] | None = None,
    use_hourly_grid: bool = False,
) -> list[dict]:
```

With (note: `use_hourly_grid` will be removed in Task 4, leave it for now):

```python
def run_sweep_parallel(
    strategy_classes: list[type],
    price_data: dict[int, list[tuple[datetime, int]]],
    budget: int = 1_000_000,
    max_workers: int | None = None,
    created_at_map: dict[int, datetime] | None = None,
    use_hourly_grid: bool = False,
) -> list[dict]:
```

And the `pool.submit` call (currently line 404), replace:

```python
        futures = [
            pool.submit(_worker_run_combos, sorted_timeline, last_prices, chunk, budget, futbin_to_ea, created_at_map)
            for chunk in chunks
        ]
```

With:

```python
        futures = [
            pool.submit(_worker_run_combos, sorted_timeline, last_prices, chunk, budget, created_at_map)
            for chunk in chunks
        ]
```

- [ ] **Step 4: Remove `futbin_to_ea` local and kwargs from `run_cli`**

In `run_cli` (currently starts at line 613), delete the `futbin_to_ea: dict[int, int] | None = None` local (currently line 634). Also delete the assignment on line 651 where `load_price_data` is called — but since `load_price_data` itself will be deleted in Task 5, for this task just stop passing `futbin_to_ea` to `run_backtest` and `run_sweep_parallel`.

Replace (around lines 689–697):

```python
        result = run_backtest(strategy, price_data, budget, futbin_to_ea=futbin_to_ea, created_at_map=created_at_map)
```

With:

```python
        result = run_backtest(strategy, price_data, budget, created_at_map=created_at_map)
```

Replace:

```python
        total_results = run_sweep_parallel(
            classes, price_data, budget,
            futbin_to_ea=futbin_to_ea,
            created_at_map=created_at_map,
            use_hourly_grid=use_market_snapshots,
        )
```

With:

```python
        total_results = run_sweep_parallel(
            classes, price_data, budget,
            created_at_map=created_at_map,
            use_hourly_grid=use_market_snapshots,
        )
```

(`use_market_snapshots` and `use_hourly_grid` still exist at this point — they're killed in Task 4 and Task 5. This task only removes `futbin_to_ea`.)

- [ ] **Step 5: Remove `futbin_id` references from trade-log printing**

In `run_cli` (currently lines 760 and 770), replace:

```python
            fid = t.get("futbin_id", t["ea_id"])
            ea_id = t.get("ea_id", 0)
            name = player_names.get(fid) or player_names.get(ea_id) or f"#{fid}"
```

With (two places, both identical):

```python
            ea_id = t["ea_id"]
            name = player_names.get(ea_id) or f"#{ea_id}"
```

- [ ] **Step 6: Run the algo test suite**

Run: `python -m pytest tests/algo/ -v`

Expected: all tests PASS. The existing `test_scraper.py` still passes (it only tests parser helpers that don't touch engine). `test_integration.py` passes (was rewritten in Task 2). `test_engine.py` passes.

- [ ] **Step 7: Commit**

```bash
git add src/algo/engine.py
git commit -m "refactor(algo): remove futbin_to_ea plumbing from backtester"
```

---

## Task 4: Drop `use_hourly_grid`, always prefer `param_grid_hourly`

**Files:**
- Modify: `src/algo/engine.py` (signature + grid selection in `run_sweep_parallel`, call site in `run_cli`)

- [ ] **Step 1: Remove `use_hourly_grid` from `run_sweep_parallel`**

Replace signature (currently line 345):

```python
def run_sweep_parallel(
    strategy_classes: list[type],
    price_data: dict[int, list[tuple[datetime, int]]],
    budget: int = 1_000_000,
    max_workers: int | None = None,
    created_at_map: dict[int, datetime] | None = None,
    use_hourly_grid: bool = False,
) -> list[dict]:
```

With:

```python
def run_sweep_parallel(
    strategy_classes: list[type],
    price_data: dict[int, list[tuple[datetime, int]]],
    budget: int = 1_000_000,
    max_workers: int | None = None,
    created_at_map: dict[int, datetime] | None = None,
) -> list[dict]:
```

Replace grid selection (currently line 386):

```python
        grid = sample.param_grid_hourly() if use_hourly_grid and hasattr(sample, "param_grid_hourly") else sample.param_grid()
```

With (always prefer hourly grid when the strategy exposes one):

```python
        grid = sample.param_grid_hourly() if hasattr(sample, "param_grid_hourly") else sample.param_grid()
```

- [ ] **Step 2: Remove `use_hourly_grid=...` from the `run_cli` call site**

In `run_cli` (currently lines 694–699), replace:

```python
        total_results = run_sweep_parallel(
            classes, price_data, budget,
            created_at_map=created_at_map,
            use_hourly_grid=use_market_snapshots,
        )
```

With:

```python
        total_results = run_sweep_parallel(
            classes, price_data, budget,
            created_at_map=created_at_map,
        )
```

- [ ] **Step 3: Run the algo test suite**

Run: `python -m pytest tests/algo/ -v`

Expected: all tests PASS.

- [ ] **Step 4: Commit**

```bash
git add src/algo/engine.py
git commit -m "refactor(algo): always prefer param_grid_hourly in sweep"
```

---

## Task 5: Delete `load_price_data`, `--source`, collapse the loader branch

**Files:**
- Modify: `src/algo/engine.py` (delete function; kill CLI flag; collapse branch; fix error msg; fix player-name lookup)

- [ ] **Step 1: Delete `load_price_data`**

Remove the entire function `load_price_data` (currently lines 475–541). Nothing else references it after Task 3; `run_cli` still imports it implicitly via the branch we're about to remove.

- [ ] **Step 2: Collapse the loader branch in `run_cli`**

Replace (currently lines 622, 633–653):

Signature line (currently 622):

```python
    source: str = "price_history",
```

Remove that parameter entirely.

Replace body (currently 633–653):

```python
    use_market_snapshots = source == "market_snapshots"
    futbin_to_ea: dict[int, int] | None = None
    created_at_map: dict[int, dt] | None = None

    price_label = f" [source: {source}]"
    if days:
        price_label += f" (last {days} days)"
    if min_price:
        price_label += f" (min {min_price:,})"
    if max_price:
        price_label += f" (max {max_price:,})"
    logger.info(f"Loading price data from database{price_label}...")

    if use_market_snapshots:
        price_data, created_at_map = await load_market_snapshot_data(
            session_factory, min_price=min_price, max_price=max_price,
        )
    else:
        price_data, futbin_to_ea = await load_price_data(
            session_factory, min_price=min_price, max_price=max_price, days=days,
        )
    logger.info(f"Loaded {len(price_data)} players with price data")
```

With:

```python
    created_at_map: dict[int, dt] | None = None

    price_label = ""
    if days:
        price_label += f" (last {days} days)"
    if min_price:
        price_label += f" (min {min_price:,})"
    if max_price:
        price_label += f" (max {max_price:,})"
    logger.info(f"Loading price data from market_snapshots{price_label}...")

    price_data, created_at_map = await load_market_snapshot_data(
        session_factory,
        min_price=min_price,
        max_price=max_price,
        days=days,
    )
    logger.info(f"Loaded {len(price_data)} players with price data")
```

- [ ] **Step 3: Update the "no data" error message**

Replace (currently lines 656–660):

```python
    if not price_data:
        if use_market_snapshots:
            logger.error("No market snapshot data found. Is the scanner running?")
        else:
            logger.error("No price data found. Run the scraper first: python -m src.algo.scraper")
        await engine.dispose()
        return
```

With:

```python
    if not price_data:
        logger.error("No market snapshot data found. Is the scanner running?")
        await engine.dispose()
        return
```

- [ ] **Step 4: Simplify the player-name lookup**

Replace (currently lines 701–723):

```python
    # Load player name mapping for trade log
    player_names: dict[int, str] = {}
    try:
        if use_market_snapshots:
            async with session_factory() as session:
                rows = await session.execute(text(
                    "SELECT ea_id, name FROM players WHERE name IS NOT NULL"
                ))
                for row in rows.fetchall():
                    player_names[row[0]] = row[1]
        else:
            async with session_factory() as session:
                rows = await session.execute(text(
                    "SELECT DISTINCT ph.futbin_id, p.name "
                    "FROM price_history ph "
                    "JOIN players p ON ph.ea_id = p.ea_id "
                    "WHERE ph.futbin_id IS NOT NULL AND p.name IS NOT NULL"
                ))
                for row in rows.fetchall():
                    player_names[row[0]] = row[1]
        logger.info(f"Loaded {len(player_names)} player names")
    except Exception as e:
        logger.warning(f"Could not load player names: {e}")
```

With:

```python
    # Load player name mapping for trade log
    player_names: dict[int, str] = {}
    try:
        async with session_factory() as session:
            rows = await session.execute(text(
                "SELECT ea_id, name FROM players WHERE name IS NOT NULL"
            ))
            for row in rows.fetchall():
                player_names[row[0]] = row[1]
        logger.info(f"Loaded {len(player_names)} player names")
    except Exception as e:
        logger.warning(f"Could not load player names: {e}")
```

- [ ] **Step 5: Remove the `--source` CLI option and update the decorator**

Replace (currently lines 813–814):

```python
@click.option("--source", default="price_history", type=click.Choice(["price_history", "market_snapshots"]),
              help="Data source: price_history (FUTBIN daily) or market_snapshots (real hourly)")
```

Delete those two lines entirely.

Replace the `main` function signature (currently line 817):

```python
def main(strategy_name, all_strategies, params_json, budget, days, min_price, max_price, source, db_url, verbose):
```

With:

```python
def main(strategy_name, all_strategies, params_json, budget, days, min_price, max_price, db_url, verbose):
```

Replace the `asyncio.run` call inside `main` (currently line 823):

```python
    asyncio.run(run_cli(strategy_name, all_strategies, params_json, budget, db_url, days=days, min_price=min_price, max_price=max_price, source=source))
```

With:

```python
    asyncio.run(run_cli(strategy_name, all_strategies, params_json, budget, db_url, days=days, min_price=min_price, max_price=max_price))
```

- [ ] **Step 6: Run the algo test suite**

Run: `python -m pytest tests/algo/ -v`

Expected: all tests PASS.

- [ ] **Step 7: Smoke-test the CLI flag parser**

Run: `python -m src.algo run --help`

Expected: help text includes `--strategy`, `--all`, `--params`, `--budget`, `--days`, `--min-price`, `--max-price`, `--db-url`, `--verbose` — and does **NOT** include `--source`.

- [ ] **Step 8: Commit**

```bash
git add src/algo/engine.py
git commit -m "feat(algo): make market_snapshots the sole backtester data source"
```

---

## Task 6: Remove the `scrape` subcommand from `__main__.py`

**Files:**
- Modify: `src/algo/__main__.py` (lines 11–13, 24)

- [ ] **Step 1: Edit `src/algo/__main__.py`**

Replace the whole file:

```python
# src/algo/__main__.py
"""Entry point for python -m src.algo <command>."""
import sys


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    # Remove the subcommand from argv so Click doesn't see it
    sys.argv = [sys.argv[0]] + sys.argv[2:]

    if cmd == "run":
        from src.algo.engine import main as engine_main
        engine_main()
    elif cmd == "report":
        from src.algo.report import main as report_main
        report_main()
    else:
        print("Usage: python -m src.algo <command>")
        print()
        print("Commands:")
        print("  run      Run backtests (--strategy NAME | --all)")
        print("  report   View backtest results (--strategy NAME, --sort COLUMN)")
        sys.exit(1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Smoke-test the usage output**

Run: `python -m src.algo`

Expected: prints usage lines showing only `run` and `report`. Exits with status 1 (non-zero). No mention of `scrape`.

Run: `python -m src.algo scrape`

Expected: same usage output with exit status 1 (unknown subcommand).

- [ ] **Step 3: Commit**

```bash
git add src/algo/__main__.py
git commit -m "chore(algo): drop scrape subcommand"
```

---

## Task 7: Delete `scraper.py` and `test_scraper.py`

**Files:**
- Delete: `src/algo/scraper.py`
- Delete: `tests/algo/test_scraper.py`

- [ ] **Step 1: Delete both files and stage the deletions**

Run (bash):

```bash
git rm src/algo/scraper.py tests/algo/test_scraper.py
```

`git rm` removes the files from the working tree and stages the deletions in one step.

- [ ] **Step 2: Verify no remaining references in `src/` or `tests/`**

Use Grep for `from src.algo.scraper` and `import scraper` across `src/` and `tests/`.

Expected: zero matches.

Then run: `python -m pytest tests/algo/ -v`

Expected: all tests PASS. Specifically, no `ModuleNotFoundError` from a stale import.

- [ ] **Step 3: Verify import of `src.algo` still works**

Run: `python -c "import src.algo, src.algo.engine, src.algo.report; print('ok')"`

Expected: prints `ok`.

- [ ] **Step 4: Commit**

```bash
git commit -m "chore(algo): delete FUTBIN scraper and its test"
```

(Files were already staged by `git rm` in Step 1.)

---

## Task 8: Final verification

**Files:** none modified.

- [ ] **Step 1: Grep for any lingering references**

Use Grep with the pattern `futbin_to_ea|load_price_data|from src.algo.scraper|use_hourly_grid|use_market_snapshots|--source` across `src/algo/` and `tests/algo/`.

Expected findings: **zero** matches. If any appear, open the file, remove the stray reference, and add a step here documenting what was missed.

Also grep `src/algo/` for `price_history` — expected remaining matches: `src/algo/models_db.py` (the `PriceHistory` class is intentionally preserved). Nothing else.

Also grep `tests/algo/` for `price_history` — expected remaining matches: `test_models_db.py` only (verifies the table exists).

- [ ] **Step 2: Full algo test suite**

Run: `python -m pytest tests/algo/ -v`

Expected: all tests PASS. Specifically:
- `test_models_db.py::test_price_history_table_exists` still PASSES (we never touched the DB or the model).
- `test_integration.py::test_full_pipeline` PASSES on `market_snapshots`.
- `test_integration.py::test_all_strategies_run` PASSES on `market_snapshots`.
- `test_engine.py` — including the three new `load_market_snapshot_data` tests — PASSES.

- [ ] **Step 3: CLI smoke test**

Run: `python -m src.algo run --help`

Expected: `--source` is not present. `--days`, `--min-price`, `--max-price` are present.

Run: `python -m src.algo`

Expected: usage shows only `run` and `report`.

- [ ] **Step 4: Confirm `price_history` table is still declared**

Run:

```bash
python -c "from src.algo.models_db import PriceHistory; print(PriceHistory.__tablename__)"
```

Expected: prints `price_history`. (The ORM class stays as documented in the spec.)

- [ ] **Step 5: No commit needed**

This task is purely verification. If all steps passed, the implementation is complete.
