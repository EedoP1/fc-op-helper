# Algo Trading Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a live algo trading mode to the Chrome extension that executes the `promo_dip_buy` strategy — server generates BUY/SELL signals from market_snapshots, extension buys into unassigned pile and sells at live market price.

**Architecture:** Server-side signal engine runs `PromoDipBuyStrategy` against live market_snapshots data, writes signals to `algo_signals` table. Extension polls for signals via new API endpoints. Buy cycle reuses existing `executeBuyCycle` (minus listing step). New sell cycle discovers market price via transfer market search and lists from unassigned pile. New "Algo" tab in overlay panel for budget/status/positions.

**Tech Stack:** Python/FastAPI/SQLAlchemy (server), TypeScript/WXT (extension), APScheduler (signal engine scheduling), pytest (server tests)

---

### Task 1: Algo DB Models

**Files:**
- Modify: `src/server/models_db.py` (append new models)
- Create: `tests/algo/test_algo_models.py`

- [ ] **Step 1: Write test for AlgoConfig model**

```python
# tests/algo/test_algo_models.py
"""Tests for algo trading DB models."""
import pytest
from datetime import datetime
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from src.server.db import Base
from src.server.models_db import AlgoConfig, AlgoSignal, AlgoPosition


@pytest.fixture
def db():
    """In-memory SQLite for model tests."""
    from sqlalchemy import create_engine as sync_create
    engine = sync_create("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def test_algo_config_create(db):
    config = AlgoConfig(
        budget=5_000_000,
        is_active=True,
        strategy_params=None,
        created_at=datetime(2026, 4, 8),
        updated_at=datetime(2026, 4, 8),
    )
    db.add(config)
    db.commit()
    result = db.execute(select(AlgoConfig)).scalar_one()
    assert result.budget == 5_000_000
    assert result.is_active is True


def test_algo_signal_create(db):
    signal = AlgoSignal(
        ea_id=12345,
        action="BUY",
        quantity=3,
        reference_price=15000,
        status="PENDING",
        created_at=datetime(2026, 4, 8),
    )
    db.add(signal)
    db.commit()
    result = db.execute(select(AlgoSignal)).scalar_one()
    assert result.ea_id == 12345
    assert result.action == "BUY"
    assert result.quantity == 3
    assert result.status == "PENDING"


def test_algo_position_create(db):
    pos = AlgoPosition(
        ea_id=12345,
        quantity=3,
        buy_price=15000,
        buy_time=datetime(2026, 4, 8, 12, 0),
        peak_price=15000,
    )
    db.add(pos)
    db.commit()
    result = db.execute(select(AlgoPosition)).scalar_one()
    assert result.ea_id == 12345
    assert result.quantity == 3
    assert result.buy_price == 15000
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/algo/test_algo_models.py -v`
Expected: ImportError — `AlgoConfig`, `AlgoSignal`, `AlgoPosition` not defined.

- [ ] **Step 3: Implement the models**

Add to the end of `src/server/models_db.py`:

```python
class AlgoConfig(Base):
    """Algo trading configuration — single row."""

    __tablename__ = "algo_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    budget: Mapped[int] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    strategy_params: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    created_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime)


class AlgoSignal(Base):
    """A BUY or SELL signal emitted by the algo signal engine."""

    __tablename__ = "algo_signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ea_id: Mapped[int] = mapped_column(Integer, index=True)
    action: Mapped[str] = mapped_column(String(10))  # "BUY" | "SELL"
    quantity: Mapped[int] = mapped_column(Integer)
    reference_price: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="PENDING")  # PENDING | CLAIMED | DONE | CANCELLED
    created_at: Mapped[datetime] = mapped_column(DateTime)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_algo_signals_status_created", "status", "created_at"),
    )


class AlgoPosition(Base):
    """A held position in the algo trading portfolio."""

    __tablename__ = "algo_positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ea_id: Mapped[int] = mapped_column(Integer, index=True)
    quantity: Mapped[int] = mapped_column(Integer)
    buy_price: Mapped[int] = mapped_column(Integer)
    buy_time: Mapped[datetime] = mapped_column(DateTime)
    peak_price: Mapped[int] = mapped_column(Integer)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/algo/test_algo_models.py -v`
Expected: All 3 tests PASS.

- [ ] **Step 5: Register models in db.py table creation**

In `src/server/db.py`, line 74, add imports:

```python
from src.server.models_db import PlayerRecord, PlayerScore, MarketSnapshot, ListingObservation, DailyListingSummary, TradeAction, TradeRecord, PortfolioSlot, ScannerStatus, DailyTransactionCount, AlgoConfig, AlgoSignal, AlgoPosition  # noqa: F401
```

- [ ] **Step 6: Commit**

```bash
git add src/server/models_db.py src/server/db.py tests/algo/test_algo_models.py
git commit -m "feat(algo): add AlgoConfig, AlgoSignal, AlgoPosition DB models"
```

---

### Task 2: Signal Engine Core — Parity with Backtester

**Files:**
- Create: `src/server/algo_engine.py`
- Create: `tests/algo/test_signal_parity.py`

This is the critical task. The signal engine must produce **identical** signals to the backtester when fed the same data.

- [ ] **Step 1: Write the parity test**

```python
# tests/algo/test_signal_parity.py
"""Signal parity test — live engine must match backtester exactly."""
import pytest
from datetime import datetime, timedelta
from collections import defaultdict

from src.algo.strategies.promo_dip_buy import PromoDipBuyStrategy
from src.algo.models import Signal, Portfolio
from src.algo.engine import run_backtest
from src.server.algo_engine import AlgoSignalEngine


def _make_promo_batch_data(
    num_cards: int = 12,
    base_price: int = 30000,
    hours: int = 400,
) -> tuple[dict[int, list[tuple[datetime, int]]], dict[int, datetime]]:
    """Generate synthetic promo batch data that triggers buy+sell signals.

    Creates a Friday promo batch of num_cards cards. Each card:
    - Starts at base_price
    - Crashes 50% over first 48h
    - Recovers with 25%+ trend by hour 72 (triggers Layer 1 buy)
    - Plateaus and stalls by hour 200 (triggers sell)
    """
    # Friday 18:00 UTC — typical promo release time
    release = datetime(2026, 4, 3, 18, 0)  # A Friday
    assert release.weekday() == 4, "Must be Friday"

    price_data: dict[int, list[tuple[datetime, int]]] = {}
    created_at_map: dict[int, datetime] = {}

    for i in range(num_cards):
        ea_id = 100_000 + i
        created_at_map[ea_id] = release
        points = []

        for h in range(hours):
            ts = release + timedelta(hours=h)

            if h < 48:
                # Crash phase: linear decline to 50% of base
                price = int(base_price * (1.0 - 0.5 * h / 48))
            elif h < 72:
                # Recovery: bounce from 50% to 75% of base (25% trend from trough)
                progress = (h - 48) / 24
                price = int(base_price * (0.5 + 0.25 * progress))
            elif h < 200:
                # Plateau: hover around 75% of base with tiny growth
                price = int(base_price * (0.75 + 0.02 * (h - 72) / 128))
            else:
                # Stall: flat at ~77% of base (triggers sell after 3 consecutive stall hours)
                price = int(base_price * 0.77)

            points.append((ts, price))

        price_data[ea_id] = points

    return price_data, created_at_map


def _collect_backtester_signals(
    price_data: dict[int, list[tuple[datetime, int]]],
    created_at_map: dict[int, datetime],
    budget: int,
) -> list[tuple[datetime, str, int, int]]:
    """Run backtester and collect all signals as (timestamp, action, ea_id, quantity)."""
    params = PromoDipBuyStrategy({}).param_grid_hourly()[0]
    strategy = PromoDipBuyStrategy(params)
    strategy.set_created_at_map(created_at_map)

    portfolio = Portfolio(cash=budget)

    timeline: dict[datetime, list[tuple[int, int]]] = defaultdict(list)
    for ea_id, points in price_data.items():
        for ts, price in points:
            timeline[ts].append((ea_id, price))

    sorted_ts = sorted(timeline.keys())
    if sorted_ts:
        existing_ids = {ea_id for ea_id, _ in timeline[sorted_ts[0]]}
        strategy.set_existing_ids(existing_ids)

    all_signals = []
    for ts in sorted_ts:
        ticks = timeline[ts]
        signals = strategy.on_tick_batch(ticks, ts, portfolio)
        for sig in signals:
            all_signals.append((ts, sig.action, sig.ea_id, sig.quantity))
            sig_price = next((p for eid, p in ticks if eid == sig.ea_id), 0)
            if sig.action == "BUY":
                portfolio.buy(sig.ea_id, sig.quantity, sig_price, ts)
            elif sig.action == "SELL":
                portfolio.sell(sig.ea_id, sig.quantity, sig_price, ts)

    return all_signals


def _collect_engine_signals(
    price_data: dict[int, list[tuple[datetime, int]]],
    created_at_map: dict[int, datetime],
    budget: int,
) -> list[tuple[datetime, str, int, int]]:
    """Run the live signal engine tick-by-tick and collect all signals."""
    engine = AlgoSignalEngine(budget=budget, created_at_map=created_at_map)

    timeline: dict[datetime, list[tuple[int, int]]] = defaultdict(list)
    for ea_id, points in price_data.items():
        for ts, price in points:
            timeline[ts].append((ea_id, price))

    sorted_ts = sorted(timeline.keys())

    all_signals = []
    for ts in sorted_ts:
        ticks = timeline[ts]
        signals = engine.process_tick(ticks, ts)
        for action, ea_id, quantity, price in signals:
            all_signals.append((ts, action, ea_id, quantity))

    return all_signals


def test_signal_parity_buy_layer_1():
    """BUY Layer 1 (strong signal) fires for same ea_ids at same ticks."""
    price_data, created_at_map = _make_promo_batch_data()
    budget = 5_000_000

    bt_signals = _collect_backtester_signals(price_data, created_at_map, budget)
    eng_signals = _collect_engine_signals(price_data, created_at_map, budget)

    bt_buys = [(ts, eid, qty) for ts, action, eid, qty in bt_signals if action == "BUY"]
    eng_buys = [(ts, eid, qty) for ts, action, eid, qty in eng_signals if action == "BUY"]

    assert len(bt_buys) > 0, "Backtester should produce at least one BUY signal"
    assert bt_buys == eng_buys, (
        f"BUY signal mismatch.\n"
        f"Backtester: {bt_buys}\n"
        f"Engine:     {eng_buys}"
    )


def test_signal_parity_sell():
    """SELL signals fire for same ea_ids at same ticks."""
    price_data, created_at_map = _make_promo_batch_data()
    budget = 5_000_000

    bt_signals = _collect_backtester_signals(price_data, created_at_map, budget)
    eng_signals = _collect_engine_signals(price_data, created_at_map, budget)

    bt_sells = [(ts, eid, qty) for ts, action, eid, qty in bt_signals if action == "SELL"]
    eng_sells = [(ts, eid, qty) for ts, action, eid, qty in eng_signals if action == "SELL"]

    assert len(bt_sells) > 0, "Backtester should produce at least one SELL signal"
    assert bt_sells == eng_sells, (
        f"SELL signal mismatch.\n"
        f"Backtester: {bt_sells}\n"
        f"Engine:     {eng_sells}"
    )


def test_signal_parity_full_sequence():
    """Full signal sequence (all BUYs and SELLs in order) matches exactly."""
    price_data, created_at_map = _make_promo_batch_data()
    budget = 5_000_000

    bt_signals = _collect_backtester_signals(price_data, created_at_map, budget)
    eng_signals = _collect_engine_signals(price_data, created_at_map, budget)

    assert len(bt_signals) > 0, "Should produce signals"
    assert bt_signals == eng_signals, (
        f"Signal sequence mismatch.\n"
        f"Backtester ({len(bt_signals)} signals): {bt_signals[:10]}...\n"
        f"Engine     ({len(eng_signals)} signals): {eng_signals[:10]}..."
    )


def test_signal_parity_position_sizing():
    """Quantities match exactly — same integer division, same max_position_pct cap."""
    price_data, created_at_map = _make_promo_batch_data(num_cards=15)
    budget = 2_000_000  # Smaller budget to stress position sizing

    bt_signals = _collect_backtester_signals(price_data, created_at_map, budget)
    eng_signals = _collect_engine_signals(price_data, created_at_map, budget)

    assert bt_signals == eng_signals


def test_signal_parity_snapshot_layer():
    """BUY Layer 2 (snapshot at 176h) fires for same cards."""
    # Use cards that DON'T hit 21% trend early — only snapshot layer buys them
    price_data, created_at_map = _make_promo_batch_data(
        num_cards=12, base_price=30000, hours=400,
    )

    # Modify prices so only some cards have weak trend (below 21%) but positive:
    # These should only be picked up by snapshot layer at 176h
    for ea_id in list(price_data.keys())[:5]:
        points = price_data[ea_id]
        new_points = []
        for ts, price in points:
            h = int((ts - datetime(2026, 4, 3, 18, 0)).total_seconds() / 3600)
            if h < 48:
                # Smaller crash: only 30%
                price = int(30000 * (1.0 - 0.3 * h / 48))
            elif h < 200:
                # Slow recovery: 10% trend (below 21% threshold)
                progress = (h - 48) / 152
                price = int(30000 * (0.7 + 0.10 * progress))
            else:
                price = int(30000 * 0.80)
            new_points.append((ts, price))
        price_data[ea_id] = new_points

    budget = 5_000_000
    bt_signals = _collect_backtester_signals(price_data, created_at_map, budget)
    eng_signals = _collect_engine_signals(price_data, created_at_map, budget)

    assert bt_signals == eng_signals
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/algo/test_signal_parity.py -v`
Expected: ImportError — `AlgoSignalEngine` not defined.

- [ ] **Step 3: Implement AlgoSignalEngine**

```python
# src/server/algo_engine.py
"""Live signal engine — wraps PromoDipBuyStrategy for real-time signal generation.

Produces identical signals to the backtester given the same data.
The engine owns a strategy instance and a Portfolio, processes ticks,
and returns (action, ea_id, quantity, reference_price) tuples.
"""
from datetime import datetime
from collections import defaultdict

from src.algo.strategies.promo_dip_buy import PromoDipBuyStrategy
from src.algo.models import Portfolio


class AlgoSignalEngine:
    """Wraps PromoDipBuyStrategy + Portfolio for live signal generation.

    Produces the exact same signals as run_backtest() in engine.py when
    fed the same tick data in the same order.
    """

    def __init__(
        self,
        budget: int,
        created_at_map: dict[int, datetime] | None = None,
        params: dict | None = None,
    ):
        self.budget = budget
        grid = PromoDipBuyStrategy({}).param_grid_hourly()
        self.params = params or grid[0]
        self.strategy = PromoDipBuyStrategy(self.params)
        self.portfolio = Portfolio(cash=budget)
        self._initialized_existing = False

        if created_at_map:
            self.strategy.set_created_at_map(created_at_map)

    def process_tick(
        self,
        ticks: list[tuple[int, int]],
        timestamp: datetime,
    ) -> list[tuple[str, int, int, int]]:
        """Process one timestamp of price data. Returns list of (action, ea_id, quantity, price).

        Mirrors the backtester's engine.py loop exactly:
        1. Call strategy.on_tick_batch() to get signals
        2. Execute each signal on the portfolio (buy/sell)
        3. Return the signals with their prices
        """
        if not self._initialized_existing:
            existing_ids = {ea_id for ea_id, _ in ticks}
            self.strategy.set_existing_ids(existing_ids)
            self._initialized_existing = True

        signals = self.strategy.on_tick_batch(ticks, timestamp, self.portfolio)

        results = []
        for signal in signals:
            sig_price = next((p for eid, p in ticks if eid == signal.ea_id), 0)
            if signal.action == "BUY":
                self.portfolio.buy(signal.ea_id, signal.quantity, sig_price, timestamp)
            elif signal.action == "SELL":
                self.portfolio.sell(signal.ea_id, signal.quantity, sig_price, timestamp)
            results.append((signal.action, signal.ea_id, signal.quantity, sig_price))

        return results

    @property
    def cash(self) -> int:
        return self.portfolio.cash

    @property
    def positions(self):
        return self.portfolio.positions

    @property
    def trades(self):
        return self.portfolio.trades
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/algo/test_signal_parity.py -v`
Expected: All 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/server/algo_engine.py tests/algo/test_signal_parity.py
git commit -m "feat(algo): add AlgoSignalEngine with backtester signal parity"
```

---

### Task 3: Signal Engine DB Integration

**Files:**
- Create: `src/server/algo_runner.py`
- Create: `tests/algo/test_algo_runner.py`

The runner loads market_snapshots from the DB, feeds them to `AlgoSignalEngine`, and writes signals to `algo_signals`.

- [ ] **Step 1: Write test for the runner**

```python
# tests/algo/test_algo_runner.py
"""Tests for algo_runner — DB integration for signal engine."""
import pytest
import asyncio
from datetime import datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from src.server.db import Base
from src.server.models_db import (
    AlgoConfig, AlgoSignal, AlgoPosition, PlayerRecord, MarketSnapshot,
)
from src.server.algo_runner import run_signal_engine


@pytest.fixture
async def db():
    """Async in-memory SQLite for integration tests."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    yield sf
    await engine.dispose()


async def _seed_promo_batch(sf, num_cards=12, hours=250):
    """Seed a Friday promo batch into players + market_snapshots."""
    release = datetime(2026, 4, 3, 18, 0)  # Friday

    async with sf() as session:
        for i in range(num_cards):
            ea_id = 200_000 + i
            session.add(PlayerRecord(
                ea_id=ea_id,
                name=f"Player {i}",
                rating=88,
                position="CM",
                nation="Test",
                league="Test",
                club="Test",
                card_type="TOTS",
                created_at=release,
            ))

            for h in range(hours):
                ts = release + timedelta(hours=h)
                if h < 48:
                    price = int(30000 * (1.0 - 0.5 * h / 48))
                elif h < 72:
                    progress = (h - 48) / 24
                    price = int(30000 * (0.5 + 0.25 * progress))
                elif h < 200:
                    price = int(30000 * (0.75 + 0.02 * (h - 72) / 128))
                else:
                    price = int(30000 * 0.77)

                session.add(MarketSnapshot(
                    ea_id=ea_id,
                    captured_at=ts,
                    current_lowest_bin=price,
                    listing_count=50,
                ))

        session.add(AlgoConfig(
            budget=5_000_000,
            is_active=True,
            strategy_params=None,
            created_at=datetime(2026, 4, 3),
            updated_at=datetime(2026, 4, 3),
        ))
        await session.commit()


@pytest.mark.asyncio
async def test_runner_generates_signals(db):
    await _seed_promo_batch(db)
    await run_signal_engine(db)

    async with db() as session:
        signals = (await session.execute(select(AlgoSignal))).scalars().all()

    assert len(signals) > 0, "Should generate at least one signal"
    buy_signals = [s for s in signals if s.action == "BUY"]
    assert len(buy_signals) > 0, "Should have BUY signals"
    for s in signals:
        assert s.status == "PENDING"
        assert s.quantity > 0


@pytest.mark.asyncio
async def test_runner_skips_when_inactive(db):
    await _seed_promo_batch(db)
    # Deactivate
    async with db() as session:
        config = (await session.execute(select(AlgoConfig))).scalar_one()
        config.is_active = False
        await session.commit()

    await run_signal_engine(db)

    async with db() as session:
        signals = (await session.execute(select(AlgoSignal))).scalars().all()
    assert len(signals) == 0


@pytest.mark.asyncio
async def test_runner_deduplicates_signals(db):
    await _seed_promo_batch(db)
    await run_signal_engine(db)

    async with db() as session:
        count_1 = len((await session.execute(select(AlgoSignal))).scalars().all())

    # Run again — should not create duplicate signals
    await run_signal_engine(db)

    async with db() as session:
        count_2 = len((await session.execute(select(AlgoSignal))).scalars().all())

    assert count_2 == count_1, "Should not create duplicate signals"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/algo/test_algo_runner.py -v`
Expected: ImportError — `run_signal_engine` not defined.

- [ ] **Step 3: Implement the runner**

```python
# src/server/algo_runner.py
"""Algo signal engine runner — loads data from DB, runs strategy, writes signals.

Called on a schedule by the scanner process or as a standalone task.
Replays market_snapshots through AlgoSignalEngine and writes new
BUY/SELL signals to algo_signals table.
"""
import logging
from datetime import datetime, timedelta, timezone
from collections import defaultdict

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.server.models_db import (
    AlgoConfig, AlgoSignal, AlgoPosition, PlayerRecord, MarketSnapshot,
)
from src.server.algo_engine import AlgoSignalEngine

logger = logging.getLogger(__name__)


async def run_signal_engine(session_factory: async_sessionmaker) -> int:
    """Run the algo signal engine against current market_snapshots.

    Returns the number of new signals written.
    """
    # Step 1: Load config
    async with session_factory() as session:
        config = (await session.execute(select(AlgoConfig))).scalar_one_or_none()

    if config is None or not config.is_active:
        logger.debug("Algo engine inactive — skipping")
        return 0

    budget = config.budget

    # Step 2: Load existing positions to reconstruct portfolio state
    async with session_factory() as session:
        positions = (await session.execute(select(AlgoPosition))).scalars().all()

    # Step 3: Load created_at map from players table
    async with session_factory() as session:
        rows = await session.execute(
            text("SELECT ea_id, created_at FROM players WHERE created_at IS NOT NULL")
        )
        created_at_map = {}
        for ea_id, created_at in rows.fetchall():
            if isinstance(created_at, str):
                created_at = datetime.fromisoformat(created_at)
            if created_at.tzinfo:
                created_at = created_at.replace(tzinfo=None)
            created_at_map[ea_id] = created_at

    # Step 4: Load market_snapshots for last 14 days (max_hold_hours = 336h)
    cutoff = datetime.utcnow() - timedelta(days=14)
    async with session_factory() as session:
        snapshot_rows = await session.execute(
            text(
                "SELECT ea_id, captured_at, current_lowest_bin "
                "FROM market_snapshots "
                "WHERE captured_at >= :cutoff AND current_lowest_bin > 0 "
                "ORDER BY captured_at"
            ),
            {"cutoff": cutoff},
        )
        raw_snapshots = snapshot_rows.fetchall()

    if not raw_snapshots:
        logger.info("No market snapshots in last 14 days — nothing to process")
        return 0

    # Aggregate to one price per player per hour (last snapshot in each hour)
    hourly: dict[tuple[int, datetime], int] = {}
    for ea_id, captured_at, price in raw_snapshots:
        if isinstance(captured_at, str):
            captured_at = datetime.fromisoformat(captured_at)
        if captured_at.tzinfo:
            captured_at = captured_at.replace(tzinfo=None)
        hour_key = captured_at.replace(minute=0, second=0, microsecond=0)
        # Last snapshot per hour wins (same as backtester's DISTINCT ON)
        hourly[(ea_id, hour_key)] = price

    # Build timeline: {timestamp: [(ea_id, price), ...]}
    timeline: dict[datetime, list[tuple[int, int]]] = defaultdict(list)
    for (ea_id, hour_ts), price in hourly.items():
        timeline[hour_ts].append((ea_id, price))

    sorted_timestamps = sorted(timeline.keys())

    # Step 5: Initialize engine
    engine = AlgoSignalEngine(
        budget=budget,
        created_at_map=created_at_map,
    )

    # Reconstruct portfolio state from existing positions
    for pos in positions:
        bt = pos.buy_time
        if bt.tzinfo:
            bt = bt.replace(tzinfo=None)
        engine.portfolio.buy(pos.ea_id, pos.quantity, pos.buy_price, bt)
        # Mark as already bought in strategy state
        engine.strategy._bought.add(pos.ea_id)
        engine.strategy._buy_ts[pos.ea_id] = bt
        engine.strategy._peak_prices[pos.ea_id] = pos.peak_price

    # Step 6: Replay all ticks
    all_signals: list[tuple[datetime, str, int, int, int]] = []
    for ts in sorted_timestamps:
        ticks = timeline[ts]
        results = engine.process_tick(ticks, ts)
        for action, ea_id, quantity, price in results:
            all_signals.append((ts, action, ea_id, quantity, price))

    if not all_signals:
        logger.info("No signals from engine run")
        return 0

    # Step 7: Load existing pending signals for dedup
    async with session_factory() as session:
        existing = await session.execute(
            select(AlgoSignal.ea_id, AlgoSignal.action).where(
                AlgoSignal.status.in_(["PENDING", "CLAIMED"])
            )
        )
        existing_set = {(r.ea_id, r.action) for r in existing.all()}

    # Step 8: Write new signals (dedup by ea_id + action)
    new_count = 0
    async with session_factory() as session:
        for ts, action, ea_id, quantity, price in all_signals:
            if (ea_id, action) in existing_set:
                continue
            session.add(AlgoSignal(
                ea_id=ea_id,
                action=action,
                quantity=quantity,
                reference_price=price,
                status="PENDING",
                created_at=ts,
            ))
            existing_set.add((ea_id, action))
            new_count += 1
        await session.commit()

    logger.info(f"Algo engine: {new_count} new signals from {len(all_signals)} total")
    return new_count
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/algo/test_algo_runner.py -v`
Expected: All 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/server/algo_runner.py tests/algo/test_algo_runner.py
git commit -m "feat(algo): add signal engine DB runner with dedup and replay"
```

---

### Task 4: Algo API Endpoints

**Files:**
- Create: `src/server/api/algo.py`
- Modify: `src/server/main.py` (register router)
- Create: `tests/algo/test_algo_api.py`

- [ ] **Step 1: Write tests for API endpoints**

```python
# tests/algo/test_algo_api.py
"""Tests for algo trading API endpoints."""
import pytest
import pytest_asyncio
from datetime import datetime
from httpx import AsyncClient, ASGITransport

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from src.server.db import Base
from src.server.models_db import AlgoConfig, AlgoSignal, AlgoPosition, PlayerRecord
from src.server.main import app


@pytest_asyncio.fixture
async def client():
    """Test client with in-memory SQLite."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    app.state.session_factory = sf
    app.state.read_session_factory = sf
    app.state.engine = engine

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await engine.dispose()


@pytest.mark.asyncio
async def test_algo_start(client):
    res = await client.post("/api/v1/algo/start", json={"budget": 5000000})
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert data["budget"] == 5000000
    assert data["cash"] == 5000000


@pytest.mark.asyncio
async def test_algo_stop(client):
    await client.post("/api/v1/algo/start", json={"budget": 5000000})
    res = await client.post("/api/v1/algo/stop")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_algo_status_empty(client):
    res = await client.get("/api/v1/algo/status")
    assert res.status_code == 200
    data = res.json()
    assert data["is_active"] is False


@pytest.mark.asyncio
async def test_algo_status_active(client):
    await client.post("/api/v1/algo/start", json={"budget": 5000000})
    res = await client.get("/api/v1/algo/status")
    data = res.json()
    assert data["is_active"] is True
    assert data["budget"] == 5000000
    assert data["cash"] == 5000000
    assert data["positions"] == []


@pytest.mark.asyncio
async def test_algo_signal_pending_empty(client):
    res = await client.get("/api/v1/algo/signals/pending")
    assert res.status_code == 200
    assert res.json()["signal"] is None


@pytest.mark.asyncio
async def test_algo_signal_claim(client):
    # Seed a signal directly
    sf = app.state.session_factory
    async with sf() as session:
        session.add(PlayerRecord(
            ea_id=12345, name="Test Player", rating=88, position="CM",
            nation="Test", league="Test", club="Test", card_type="TOTS",
        ))
        session.add(AlgoSignal(
            ea_id=12345, action="BUY", quantity=3, reference_price=15000,
            status="PENDING", created_at=datetime(2026, 4, 8),
        ))
        await session.commit()

    res = await client.get("/api/v1/algo/signals/pending")
    data = res.json()
    assert data["signal"] is not None
    assert data["signal"]["ea_id"] == 12345
    assert data["signal"]["action"] == "BUY"
    assert data["signal"]["quantity"] == 3
    assert data["signal"]["player_name"] == "Test Player"


@pytest.mark.asyncio
async def test_algo_signal_complete_bought(client):
    sf = app.state.session_factory
    async with sf() as session:
        session.add(AlgoConfig(
            budget=5000000, is_active=True, strategy_params=None,
            created_at=datetime(2026, 4, 8), updated_at=datetime(2026, 4, 8),
        ))
        session.add(PlayerRecord(
            ea_id=12345, name="Test Player", rating=88, position="CM",
            nation="Test", league="Test", club="Test", card_type="TOTS",
        ))
        signal = AlgoSignal(
            ea_id=12345, action="BUY", quantity=3, reference_price=15000,
            status="CLAIMED", created_at=datetime(2026, 4, 8),
            claimed_at=datetime(2026, 4, 8),
        )
        session.add(signal)
        await session.commit()
        signal_id = signal.id

    res = await client.post(
        f"/api/v1/algo/signals/{signal_id}/complete",
        json={"outcome": "bought", "price": 14800, "quantity": 3},
    )
    assert res.status_code == 200

    # Check position was created
    async with sf() as session:
        from sqlalchemy import select
        pos = (await session.execute(
            select(AlgoPosition).where(AlgoPosition.ea_id == 12345)
        )).scalar_one()
        assert pos.quantity == 3
        assert pos.buy_price == 14800

    # Check cash was deducted
    async with sf() as session:
        config = (await session.execute(select(AlgoConfig))).scalar_one()
        assert config.budget == 5000000  # budget unchanged
        # Cash is tracked via positions, not config


@pytest.mark.asyncio
async def test_algo_signal_complete_sold(client):
    sf = app.state.session_factory
    async with sf() as session:
        session.add(AlgoConfig(
            budget=5000000, is_active=True, strategy_params=None,
            created_at=datetime(2026, 4, 8), updated_at=datetime(2026, 4, 8),
        ))
        session.add(PlayerRecord(
            ea_id=12345, name="Test Player", rating=88, position="CM",
            nation="Test", league="Test", club="Test", card_type="TOTS",
        ))
        session.add(AlgoPosition(
            ea_id=12345, quantity=3, buy_price=15000,
            buy_time=datetime(2026, 4, 7), peak_price=18000,
        ))
        signal = AlgoSignal(
            ea_id=12345, action="SELL", quantity=3, reference_price=18000,
            status="CLAIMED", created_at=datetime(2026, 4, 8),
            claimed_at=datetime(2026, 4, 8),
        )
        session.add(signal)
        await session.commit()
        signal_id = signal.id

    res = await client.post(
        f"/api/v1/algo/signals/{signal_id}/complete",
        json={"outcome": "sold", "price": 18000, "quantity": 3},
    )
    assert res.status_code == 200

    # Position should be removed
    async with sf() as session:
        from sqlalchemy import select
        positions = (await session.execute(
            select(AlgoPosition).where(AlgoPosition.ea_id == 12345)
        )).scalars().all()
        assert len(positions) == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/algo/test_algo_api.py -v`
Expected: ImportError or 404 — algo endpoints not defined.

- [ ] **Step 3: Implement the API**

```python
# src/server/api/algo.py
"""Algo trading API endpoints for Chrome extension integration."""
import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select

from src.server.models_db import (
    AlgoConfig, AlgoSignal, AlgoPosition, PlayerRecord, MarketSnapshot,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/algo")

EA_TAX_RATE = 0.05


class StartPayload(BaseModel):
    budget: int


class CompletePayload(BaseModel):
    outcome: str  # "bought" | "sold" | "failed" | "skipped"
    price: int
    quantity: int


@router.post("/start")
async def algo_start(payload: StartPayload, request: Request):
    """Activate algo trading mode with given budget."""
    sf = request.app.state.session_factory
    now = datetime.utcnow()
    async with sf() as session:
        config = (await session.execute(select(AlgoConfig))).scalar_one_or_none()
        if config is None:
            config = AlgoConfig(
                budget=payload.budget,
                is_active=True,
                strategy_params=None,
                created_at=now,
                updated_at=now,
            )
            session.add(config)
        else:
            config.budget = payload.budget
            config.is_active = True
            config.updated_at = now
        await session.commit()

    return {"status": "ok", "budget": payload.budget, "cash": payload.budget}


@router.post("/stop")
async def algo_stop(request: Request):
    """Deactivate algo trading mode. Cancel pending signals."""
    sf = request.app.state.session_factory
    async with sf() as session:
        config = (await session.execute(select(AlgoConfig))).scalar_one_or_none()
        if config:
            config.is_active = False
            config.updated_at = datetime.utcnow()

        # Cancel pending signals
        pending = (await session.execute(
            select(AlgoSignal).where(AlgoSignal.status.in_(["PENDING", "CLAIMED"]))
        )).scalars().all()
        for s in pending:
            s.status = "CANCELLED"

        await session.commit()

    return {"status": "ok"}


@router.get("/status")
async def algo_status(request: Request):
    """Return current algo trading state."""
    sf = request.app.state.session_factory
    async with sf() as session:
        config = (await session.execute(select(AlgoConfig))).scalar_one_or_none()
        if config is None:
            return {
                "is_active": False, "budget": 0, "cash": 0,
                "positions": [], "pending_signals": 0, "total_pnl": 0,
            }

        positions = (await session.execute(select(AlgoPosition))).scalars().all()
        pending_count = len((await session.execute(
            select(AlgoSignal).where(AlgoSignal.status.in_(["PENDING", "CLAIMED"]))
        )).scalars().all())

        # Calculate cash: budget - cost of held positions
        held_cost = sum(p.buy_price * p.quantity for p in positions)
        cash = config.budget - held_cost

        # Get current prices for unrealized P&L
        pos_data = []
        for pos in positions:
            snapshot = (await session.execute(
                select(MarketSnapshot)
                .where(MarketSnapshot.ea_id == pos.ea_id)
                .order_by(MarketSnapshot.captured_at.desc())
                .limit(1)
            )).scalar_one_or_none()
            current_price = snapshot.current_lowest_bin if snapshot else pos.buy_price

            revenue = int(current_price * pos.quantity * (1 - EA_TAX_RATE))
            cost = pos.buy_price * pos.quantity
            unrealized = revenue - cost

            # Get player name
            player = (await session.execute(
                select(PlayerRecord).where(PlayerRecord.ea_id == pos.ea_id)
            )).scalar_one_or_none()

            pos_data.append({
                "ea_id": pos.ea_id,
                "name": player.name if player else str(pos.ea_id),
                "quantity": pos.quantity,
                "buy_price": pos.buy_price,
                "buy_time": pos.buy_time.isoformat(),
                "current_price": current_price,
                "peak_price": pos.peak_price,
                "unrealized_pnl": unrealized,
            })

        # Realized P&L from completed SELL signals
        done_sells = (await session.execute(
            select(AlgoSignal).where(
                AlgoSignal.action == "SELL",
                AlgoSignal.status == "DONE",
            )
        )).scalars().all()
        # Simple approximation — full P&L tracking is in the signal complete handler
        total_unrealized = sum(p["unrealized_pnl"] for p in pos_data)

    return {
        "is_active": config.is_active,
        "budget": config.budget,
        "cash": cash,
        "positions": pos_data,
        "pending_signals": pending_count,
        "total_pnl": total_unrealized,
    }


@router.get("/signals/pending")
async def algo_signal_pending(request: Request):
    """Claim next pending signal for extension to execute."""
    sf = request.app.state.session_factory
    now = datetime.utcnow()
    stale_cutoff = now - timedelta(minutes=5)

    async with sf() as session:
        # Reset stale CLAIMED signals back to PENDING
        stale = (await session.execute(
            select(AlgoSignal).where(
                AlgoSignal.status == "CLAIMED",
                AlgoSignal.claimed_at < stale_cutoff,
            )
        )).scalars().all()
        for s in stale:
            s.status = "PENDING"
            s.claimed_at = None

        # Check for already-claimed in-progress signal
        in_progress = (await session.execute(
            select(AlgoSignal)
            .where(AlgoSignal.status == "CLAIMED")
            .order_by(AlgoSignal.claimed_at)
            .limit(1)
        )).scalar_one_or_none()

        if in_progress is not None:
            player = (await session.execute(
                select(PlayerRecord).where(PlayerRecord.ea_id == in_progress.ea_id)
            )).scalar_one_or_none()

            await session.commit()
            return {"signal": _signal_dict(in_progress, player)}

        # Claim next PENDING
        pending = (await session.execute(
            select(AlgoSignal)
            .where(AlgoSignal.status == "PENDING")
            .order_by(AlgoSignal.created_at)
            .limit(1)
        )).scalar_one_or_none()

        if pending is None:
            await session.commit()
            return {"signal": None}

        pending.status = "CLAIMED"
        pending.claimed_at = now

        player = (await session.execute(
            select(PlayerRecord).where(PlayerRecord.ea_id == pending.ea_id)
        )).scalar_one_or_none()

        await session.commit()
        return {"signal": _signal_dict(pending, player)}


def _signal_dict(signal: AlgoSignal, player: PlayerRecord | None) -> dict:
    return {
        "id": signal.id,
        "ea_id": signal.ea_id,
        "action": signal.action,
        "quantity": signal.quantity,
        "reference_price": signal.reference_price,
        "player_name": player.name if player else str(signal.ea_id),
        "rating": player.rating if player else 0,
        "position": player.position if player else "",
        "card_type": player.card_type if player else "",
    }


@router.post("/signals/{signal_id}/complete")
async def algo_signal_complete(signal_id: int, payload: CompletePayload, request: Request):
    """Record signal execution outcome."""
    sf = request.app.state.session_factory
    now = datetime.utcnow()

    async with sf() as session:
        signal = (await session.execute(
            select(AlgoSignal).where(AlgoSignal.id == signal_id)
        )).scalar_one_or_none()

        if signal is None:
            raise HTTPException(status_code=404, detail="Signal not found")

        signal.status = "DONE"
        signal.completed_at = now

        if payload.outcome == "bought":
            session.add(AlgoPosition(
                ea_id=signal.ea_id,
                quantity=payload.quantity,
                buy_price=payload.price,
                buy_time=now,
                peak_price=payload.price,
            ))

        elif payload.outcome == "sold":
            # Remove position
            pos = (await session.execute(
                select(AlgoPosition).where(AlgoPosition.ea_id == signal.ea_id)
            )).scalar_one_or_none()
            if pos:
                await session.delete(pos)

        elif payload.outcome in ("failed", "skipped"):
            signal.status = "CANCELLED"

        await session.commit()

    return {"status": "ok"}
```

- [ ] **Step 4: Register the router in main.py**

Add to `src/server/main.py`:

```python
from src.server.api.algo import router as algo_router
```

And add before the dashboard section:

```python
app.include_router(algo_router)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/algo/test_algo_api.py -v`
Expected: All tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/server/api/algo.py src/server/main.py tests/algo/test_algo_api.py
git commit -m "feat(algo): add API endpoints for start/stop/status/signals"
```

---

### Task 5: Schedule Signal Engine in Scanner Process

**Files:**
- Modify: `src/server/scheduler.py`
- Modify: `src/server/scanner_main.py`

- [ ] **Step 1: Add algo engine job to scheduler**

In `src/server/scheduler.py`, add a new function parameter and job:

```python
def create_scheduler(scanner, algo_runner=None) -> AsyncIOScheduler:
```

Add after the cleanup job (before `return scheduler`):

```python
    # Algo signal engine: generate trading signals from market snapshots
    if algo_runner is not None:
        scheduler.add_job(
            algo_runner,
            trigger=IntervalTrigger(minutes=10),
            id="algo_signal_engine",
            max_instances=1,
            coalesce=True,
            replace_existing=True,
            name="Algo signal engine",
        )
```

- [ ] **Step 2: Wire it up in scanner_main.py**

In `src/server/scanner_main.py`, after `session_factory` is created, add:

```python
    from src.server.algo_runner import run_signal_engine
    async def _run_algo():
        await run_signal_engine(session_factory)
```

Change the `create_scheduler` call to:

```python
    scheduler = create_scheduler(scanner, algo_runner=_run_algo)
```

- [ ] **Step 3: Commit**

```bash
git add src/server/scheduler.py src/server/scanner_main.py
git commit -m "feat(algo): schedule signal engine every 10 min in scanner process"
```

---

### Task 6: Extension Message Types

**Files:**
- Modify: `extension/src/messages.ts`

- [ ] **Step 1: Add algo types and message variants**

Add after the `ActionNeeded` type in `extension/src/messages.ts`:

```typescript
/** Algo signal from GET /algo/signals/pending. */
export type AlgoSignal = {
  id: number;
  ea_id: number;
  action: 'BUY' | 'SELL';
  quantity: number;
  reference_price: number;
  player_name: string;
  rating: number;
  position: string;
  card_type: string;
};

/** Algo position from GET /algo/status. */
export type AlgoPosition = {
  ea_id: number;
  name: string;
  quantity: number;
  buy_price: number;
  buy_time: string;
  current_price: number;
  peak_price: number;
  unrealized_pnl: number;
};

/** Full response shape from GET /algo/status. */
export type AlgoStatusData = {
  is_active: boolean;
  budget: number;
  cash: number;
  positions: AlgoPosition[];
  pending_signals: number;
  total_pnl: number;
};
```

Add to the `ExtensionMessage` union:

```typescript
  // Algo trading mode
  | { type: 'ALGO_START'; budget: number }
  | { type: 'ALGO_START_RESULT'; success: boolean; budget?: number; cash?: number; error?: string }
  | { type: 'ALGO_STOP' }
  | { type: 'ALGO_STOP_RESULT'; success: boolean; error?: string }
  | { type: 'ALGO_STATUS_REQUEST' }
  | { type: 'ALGO_STATUS_RESULT'; data: AlgoStatusData | null; error?: string }
  | { type: 'ALGO_SIGNAL_REQUEST' }
  | { type: 'ALGO_SIGNAL_RESULT'; signal: AlgoSignal | null; error?: string }
  | { type: 'ALGO_SIGNAL_COMPLETE'; signal_id: number; outcome: string; price: number; quantity: number }
  | { type: 'ALGO_SIGNAL_COMPLETE_RESULT'; success: boolean; error?: string }
```

- [ ] **Step 2: Commit**

```bash
git add extension/src/messages.ts
git commit -m "feat(algo): add extension message types for algo trading"
```

---

### Task 7: Service Worker Handlers

**Files:**
- Modify: `extension/entrypoints/background.ts`

- [ ] **Step 1: Add handler functions**

Add these functions at the end of `extension/entrypoints/background.ts` (before the closing of `defineBackground`):

```typescript
async function handleAlgoStart(budget: number): Promise<ExtensionMessage> {
  try {
    const res = await fetch(`${BACKEND_URL}/api/v1/algo/start`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ budget }),
    });
    if (!res.ok) {
      return { type: 'ALGO_START_RESULT', success: false, error: `Backend ${res.status}` };
    }
    const data = await res.json();
    return { type: 'ALGO_START_RESULT', success: true, budget: data.budget, cash: data.cash };
  } catch (e) {
    return { type: 'ALGO_START_RESULT', success: false, error: String(e) };
  }
}

async function handleAlgoStop(): Promise<ExtensionMessage> {
  try {
    const res = await fetch(`${BACKEND_URL}/api/v1/algo/stop`, { method: 'POST' });
    if (!res.ok) {
      return { type: 'ALGO_STOP_RESULT', success: false, error: `Backend ${res.status}` };
    }
    return { type: 'ALGO_STOP_RESULT', success: true };
  } catch (e) {
    return { type: 'ALGO_STOP_RESULT', success: false, error: String(e) };
  }
}

async function handleAlgoStatus(): Promise<ExtensionMessage> {
  try {
    const res = await fetch(`${BACKEND_URL}/api/v1/algo/status`);
    if (!res.ok) {
      return { type: 'ALGO_STATUS_RESULT', data: null, error: `Backend ${res.status}` };
    }
    const data = await res.json();
    return { type: 'ALGO_STATUS_RESULT', data };
  } catch (e) {
    return { type: 'ALGO_STATUS_RESULT', data: null, error: String(e) };
  }
}

async function handleAlgoSignalRequest(): Promise<ExtensionMessage> {
  try {
    const res = await fetch(`${BACKEND_URL}/api/v1/algo/signals/pending`);
    if (!res.ok) {
      return { type: 'ALGO_SIGNAL_RESULT', signal: null, error: `Backend ${res.status}` };
    }
    const data = await res.json();
    return { type: 'ALGO_SIGNAL_RESULT', signal: data.signal };
  } catch (e) {
    return { type: 'ALGO_SIGNAL_RESULT', signal: null, error: String(e) };
  }
}

async function handleAlgoSignalComplete(
  signal_id: number, outcome: string, price: number, quantity: number,
): Promise<ExtensionMessage> {
  try {
    const res = await fetch(`${BACKEND_URL}/api/v1/algo/signals/${signal_id}/complete`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ outcome, price, quantity }),
    });
    if (!res.ok) {
      return { type: 'ALGO_SIGNAL_COMPLETE_RESULT', success: false, error: `Backend ${res.status}` };
    }
    return { type: 'ALGO_SIGNAL_COMPLETE_RESULT', success: true };
  } catch (e) {
    return { type: 'ALGO_SIGNAL_COMPLETE_RESULT', success: false, error: String(e) };
  }
}
```

- [ ] **Step 2: Add cases to the message switch**

In the `chrome.runtime.onMessage.addListener` switch statement, add before the default return false block:

```typescript
        case 'ALGO_START':
          handleAlgoStart(msg.budget).then(sendResponse);
          return true;
        case 'ALGO_STOP':
          handleAlgoStop().then(sendResponse);
          return true;
        case 'ALGO_STATUS_REQUEST':
          handleAlgoStatus().then(sendResponse);
          return true;
        case 'ALGO_SIGNAL_REQUEST':
          handleAlgoSignalRequest().then(sendResponse);
          return true;
        case 'ALGO_SIGNAL_COMPLETE':
          handleAlgoSignalComplete(msg.signal_id, msg.outcome, msg.price, msg.quantity).then(sendResponse);
          return true;
        case 'ALGO_START_RESULT':
        case 'ALGO_STOP_RESULT':
        case 'ALGO_STATUS_RESULT':
        case 'ALGO_SIGNAL_RESULT':
        case 'ALGO_SIGNAL_COMPLETE_RESULT':
          return false;
```

- [ ] **Step 3: Add cases to content script switch**

In `extension/entrypoints/ea-webapp.content.ts`, add these cases in the `handleMessage` switch before the `default`:

```typescript
        case 'ALGO_START':
        case 'ALGO_STOP':
        case 'ALGO_STATUS_REQUEST':
        case 'ALGO_SIGNAL_REQUEST':
        case 'ALGO_SIGNAL_COMPLETE':
          return false;
        case 'ALGO_START_RESULT':
        case 'ALGO_STOP_RESULT':
        case 'ALGO_STATUS_RESULT':
        case 'ALGO_SIGNAL_RESULT':
        case 'ALGO_SIGNAL_COMPLETE_RESULT':
          return false;
```

- [ ] **Step 4: Commit**

```bash
git add extension/entrypoints/background.ts extension/entrypoints/ea-webapp.content.ts
git commit -m "feat(algo): add service worker handlers for algo trading messages"
```

---

### Task 8: Algo Buy Cycle (No Listing)

**Files:**
- Create: `extension/src/algo-buy-cycle.ts`

- [ ] **Step 1: Create the algo buy cycle**

This is a modified `executeBuyCycle` that skips the listing step. Cards stay in unassigned pile.

```typescript
// extension/src/algo-buy-cycle.ts
/**
 * Algo buy cycle — search, price guard, buy, but do NOT list.
 * Card stays in unassigned pile until a SELL signal fires.
 *
 * Reuses the same DOM interaction pattern as buy-cycle.ts but:
 * - Uses signal's reference_price for price guard (not portfolio slot)
 * - Skips the "List on Transfer Market" step after buying
 * - Navigates back to search page after buy
 */
import * as SELECTORS from './selectors';
import {
  requireElement,
  clickElement,
  waitForElement,
  waitForSearchResults,
  typePrice,
  jitter,
  AutomationError,
} from './automation';
import { navigateToTransferMarket, isOnSearchPage } from './navigation';
import type { AlgoSignal } from './messages';

export type AlgoBuyCycleResult =
  | { outcome: 'bought'; buyPrice: number; quantity: number }
  | { outcome: 'skipped'; reason: string }
  | { outcome: 'error'; reason: string };

/** Module-level cache-bust counter (same pattern as buy-cycle.ts D-09). */
let cacheBustBid = 0;

function readBinPrice(item: Element): number {
  const el = item.querySelector(SELECTORS.ITEM_BIN_PRICE);
  if (!el) return NaN;
  return parseInt(el.textContent?.replace(/,/g, '') ?? '', 10);
}

function getPriceInputs(): HTMLInputElement[] {
  return Array.from(
    document.querySelectorAll<HTMLInputElement>(SELECTORS.SEARCH_PRICE_INPUT),
  );
}

async function setPriceInput(
  inputs: HTMLInputElement[],
  index: number,
  value: number,
): Promise<void> {
  const input = inputs[index];
  if (!input) return;
  await typePrice(input, value);
}

function verifyCard(item: Element, expectedRating: number, expectedPosition: string): boolean {
  const ratingEl = item.querySelector(SELECTORS.ITEM_RATING);
  const positionEl = item.querySelector(SELECTORS.ITEM_POSITION);
  const rating = parseInt(ratingEl?.textContent?.trim() ?? '', 10);
  const position = positionEl?.textContent?.trim() ?? '';
  if (isNaN(rating) || rating !== expectedRating) return false;
  if (position.toUpperCase() !== expectedPosition.toUpperCase()) return false;
  return true;
}

/**
 * Execute buy cycle for an algo signal. Buy ONE card, skip listing.
 *
 * Note: The signal has a quantity field, but EA only allows buying one card
 * at a time. The automation loop calls this repeatedly for signal.quantity
 * times or until budget runs out.
 */
export async function executeAlgoBuyCycle(
  signal: AlgoSignal,
  sendMessage: (msg: any) => Promise<any>,
): Promise<AlgoBuyCycleResult> {
  const PRICE_GUARD_MULTIPLIER = 1.10; // Allow 10% above reference for algo trades
  const MAX_RETRIES = 3;
  const MAX_BIN_STEP_PCT = 0.05;
  const MAX_BIN_STEPS = 5;

  try {
    if (!isOnSearchPage()) {
      await navigateToTransferMarket();
    }

    await jitter();
    const nameInput = requireElement<HTMLInputElement>(
      'SEARCH_PLAYER_NAME_INPUT',
      SELECTORS.SEARCH_PLAYER_NAME_INPUT,
    );

    nameInput.focus();
    nameInput.value = '';
    nameInput.dispatchEvent(new Event('input', { bubbles: true }));
    await jitter(300, 600);

    nameInput.value = signal.player_name;
    nameInput.dispatchEvent(new Event('input', { bubbles: true }));
    nameInput.dispatchEvent(new Event('change', { bubbles: true }));

    await jitter(1000, 2000);
    try {
      const suggestionList = await waitForElement(
        'SEARCH_PLAYER_SUGGESTIONS',
        SELECTORS.SEARCH_PLAYER_SUGGESTIONS,
        document,
        5_000,
      );
      const buttons = Array.from(suggestionList.querySelectorAll('button'));
      const match = buttons.find(
        btn => btn.textContent?.trim().toLowerCase().includes(signal.player_name.toLowerCase()),
      ) ?? buttons[0];
      if (match) {
        await clickElement(match);
        await jitter();
      }
    } catch {
      // No suggestions — continue
    }

    let retries = 0;
    let maxBin = signal.reference_price;
    const priceGuard = Math.floor(signal.reference_price * PRICE_GUARD_MULTIPLIER);

    for (let step = 0; step <= MAX_BIN_STEPS; step++) {
      cacheBustBid += 50;
      if (cacheBustBid > 1000) cacheBustBid = 50;

      const priceInputs = getPriceInputs();
      if (priceInputs.length >= 4) {
        await setPriceInput(priceInputs, 0, cacheBustBid);
        await jitter(200, 400);
        await setPriceInput(priceInputs, 3, maxBin);
        await jitter(200, 400);
      }

      const searchBtn = requireElement<HTMLElement>(
        'SEARCH_SUBMIT_BUTTON',
        SELECTORS.SEARCH_SUBMIT_BUTTON,
      );
      await clickElement(searchBtn);

      const searchResult = await waitForSearchResults();

      if (searchResult.outcome === 'timeout' || searchResult.outcome === 'empty') {
        const backBtn = document.querySelector<HTMLElement>(SELECTORS.NAV_BACK_BUTTON);
        if (backBtn) {
          await clickElement(backBtn);
          await jitter(1000, 2000);
        }
        maxBin = Math.floor(maxBin * (1 + MAX_BIN_STEP_PCT));
        if (maxBin > priceGuard) {
          return { outcome: 'skipped', reason: 'Price above guard' };
        }
        continue;
      }

      const resultsList = document.querySelector(SELECTORS.SEARCH_RESULTS_LIST)!;
      const resultItems = Array.from(
        resultsList.querySelectorAll<Element>(SELECTORS.TRANSFER_LIST_ITEM),
      );

      let binPrice = Infinity;
      let cheapestItem: Element | null = null;

      for (const item of resultItems) {
        const itemBin = readBinPrice(item);
        if (isNaN(itemBin)) continue;
        if (!verifyCard(item, signal.rating, signal.position)) continue;
        if (itemBin < binPrice) {
          binPrice = itemBin;
          cheapestItem = item;
        }
      }

      if (!cheapestItem || binPrice === Infinity) {
        const backBtn = document.querySelector<HTMLElement>(SELECTORS.NAV_BACK_BUTTON);
        if (backBtn) {
          await clickElement(backBtn);
          await jitter(1000, 2000);
        }
        maxBin = Math.floor(maxBin * (1 + MAX_BIN_STEP_PCT));
        if (maxBin > priceGuard) {
          return { outcome: 'skipped', reason: 'No verified cards within price guard' };
        }
        continue;
      }

      if (binPrice > priceGuard) {
        return { outcome: 'skipped', reason: 'Cheapest card above price guard' };
      }

      // Buy the card
      let bought = false;
      let actualPrice = binPrice;

      while (retries < MAX_RETRIES) {
        let attemptFailed = false;

        await clickElement(cheapestItem);
        await jitter();

        const buyNowBtn = document.querySelector<HTMLElement>(SELECTORS.BUY_NOW_BUTTON);
        if (!buyNowBtn) {
          attemptFailed = true;
        }

        if (!attemptFailed) {
          await clickElement(buyNowBtn!);
          await jitter();
          try {
            await waitForElement(
              'EA_DIALOG_PRIMARY_BUTTON',
              SELECTORS.EA_DIALOG_PRIMARY_BUTTON,
              document,
              5_000,
            );
          } catch {
            attemptFailed = true;
          }
        }

        if (!attemptFailed) {
          const confirmBtn = document.querySelector<HTMLElement>(SELECTORS.EA_DIALOG_PRIMARY_BUTTON);
          if (!confirmBtn) {
            attemptFailed = true;
          } else {
            await clickElement(confirmBtn);
            await jitter(500, 1000);

            // Wait for post-buy state — accordion means buy succeeded
            try {
              await waitForElement(
                'LIST_ON_MARKET_ACCORDION',
                SELECTORS.LIST_ON_MARKET_ACCORDION,
                document,
                8_000,
              );
              bought = true;
              break;
            } catch {
              attemptFailed = true;
            }
          }
        }

        retries++;
        if (retries >= MAX_RETRIES) {
          return { outcome: 'skipped', reason: 'Sniped 3 times' };
        }

        const backBtn = document.querySelector<HTMLElement>(SELECTORS.NAV_BACK_BUTTON);
        if (backBtn) {
          await clickElement(backBtn);
          await jitter(1000, 2000);
        }

        // Re-search
        cacheBustBid += 50;
        if (cacheBustBid > 1000) cacheBustBid = 50;
        const retryInputs = getPriceInputs();
        if (retryInputs.length >= 4) {
          await setPriceInput(retryInputs, 0, cacheBustBid);
          await jitter(200, 400);
        }
        const refreshBtn = await waitForElement<HTMLElement>(
          'SEARCH_SUBMIT_BUTTON', SELECTORS.SEARCH_SUBMIT_BUTTON, document, 8_000,
        );
        await clickElement(refreshBtn);
        await jitter(1500, 3000);

        const refreshedList = document.querySelector(SELECTORS.SEARCH_RESULTS_LIST);
        const freshItems = refreshedList
          ? Array.from(refreshedList.querySelectorAll<Element>(SELECTORS.TRANSFER_LIST_ITEM))
          : [];
        if (freshItems.length === 0) {
          return { outcome: 'skipped', reason: 'Sniped and no fresh results' };
        }
        cheapestItem = freshItems[0];
        const freshBin = readBinPrice(freshItems[0]);
        if (isNaN(freshBin) || freshBin > priceGuard) {
          return { outcome: 'skipped', reason: 'Post-snipe price above guard' };
        }
        actualPrice = freshBin;
        continue;
      }

      if (!bought) {
        return { outcome: 'skipped', reason: 'Sniped 3 times' };
      }

      // DO NOT LIST — card stays in unassigned pile.
      // Navigate back to search page for next buy.
      await jitter();
      const backBtn = document.querySelector<HTMLElement>(SELECTORS.NAV_BACK_BUTTON);
      if (backBtn) {
        await clickElement(backBtn);
        await jitter(1000, 2000);
      }

      return { outcome: 'bought', buyPrice: actualPrice, quantity: 1 };
    }

    return { outcome: 'skipped', reason: 'Price above guard' };
  } catch (err) {
    if (err instanceof AutomationError) {
      return { outcome: 'error', reason: err.message };
    }
    const msg = err instanceof Error ? err.message : String(err);
    return { outcome: 'error', reason: `Unexpected: ${msg}` };
  }
}
```

- [ ] **Step 2: Commit**

```bash
git add extension/src/algo-buy-cycle.ts
git commit -m "feat(algo): add algo buy cycle (buy without listing)"
```

---

### Task 9: Algo Sell Cycle

**Files:**
- Create: `extension/src/algo-sell-cycle.ts`

- [ ] **Step 1: Create the sell cycle**

```typescript
// extension/src/algo-sell-cycle.ts
/**
 * Algo sell cycle — find card in unassigned pile, discover market price,
 * list at market BIN.
 *
 * Steps:
 * 1. Navigate to unassigned pile
 * 2. Find the matching card by name + rating
 * 3. Search transfer market for that player to discover cheapest BIN
 * 4. Navigate back to unassigned, click the card, list at discovered price
 */
import * as SELECTORS from './selectors';
import {
  clickElement,
  waitForElement,
  waitForSearchResults,
  typePrice,
  jitter,
  AutomationError,
  requireElement,
} from './automation';
import { navigateToTransferMarket } from './navigation';
import type { AlgoSignal } from './messages';

export type AlgoSellCycleResult =
  | { outcome: 'listed'; sellPrice: number; quantity: number }
  | { outcome: 'skipped'; reason: string }
  | { outcome: 'error'; reason: string };

/**
 * Read the cheapest BIN from transfer market search results.
 * Returns NaN if no verified cards found.
 */
function readCheapestBin(expectedRating: number, expectedPosition: string): number {
  const resultsList = document.querySelector(SELECTORS.SEARCH_RESULTS_LIST);
  if (!resultsList) return NaN;

  const items = Array.from(
    resultsList.querySelectorAll<Element>(SELECTORS.TRANSFER_LIST_ITEM),
  );

  let cheapest = Infinity;
  for (const item of items) {
    const binEl = item.querySelector(SELECTORS.ITEM_BIN_PRICE);
    if (!binEl) continue;
    const bin = parseInt(binEl.textContent?.replace(/,/g, '') ?? '', 10);
    if (isNaN(bin)) continue;

    // Verify card matches
    const ratingEl = item.querySelector(SELECTORS.ITEM_RATING);
    const positionEl = item.querySelector(SELECTORS.ITEM_POSITION);
    const rating = parseInt(ratingEl?.textContent?.trim() ?? '', 10);
    const position = positionEl?.textContent?.trim() ?? '';
    if (rating !== expectedRating) continue;
    if (position.toUpperCase() !== expectedPosition.toUpperCase()) continue;

    if (bin < cheapest) cheapest = bin;
  }

  return cheapest === Infinity ? NaN : cheapest;
}

/**
 * Find the "List on Transfer Market" accordion button. Same logic as buy-cycle.
 */
function findListConfirmButton(): HTMLButtonElement | null {
  const buttons = document.querySelectorAll<HTMLButtonElement>(
    `.${SELECTORS.QUICK_LIST_CONFIRM_CLASS.split(' ').join('.')}`,
  );
  for (const btn of Array.from(buttons)) {
    const text = btn.textContent?.trim() ?? '';
    if (text.includes('List for Transfer') || text.includes('List on Transfer Market')) {
      return btn;
    }
  }
  return document.querySelector<HTMLButtonElement>(
    `${SELECTORS.QUICK_LIST_PANEL} button.btn-standard.primary`,
  );
}

/**
 * Execute the sell cycle for one card from an algo SELL signal.
 *
 * 1. Go to unassigned pile
 * 2. Click the matching card
 * 3. Search transfer market for market price
 * 4. Go back to unassigned, re-select card, list at market price
 */
export async function executeAlgoSellCycle(
  signal: AlgoSignal,
  sendMessage: (msg: any) => Promise<any>,
): Promise<AlgoSellCycleResult> {
  try {
    // Step 1: Navigate to unassigned pile
    const transfersBtn = document.querySelector<HTMLElement>(SELECTORS.NAV_TRANSFERS);
    if (!transfersBtn) {
      return { outcome: 'error', reason: 'Transfers nav button not found' };
    }
    await clickElement(transfersBtn);
    await jitter(1000, 2000);

    const unassignedTile = document.querySelector<HTMLElement>(SELECTORS.TILE_UNASSIGNED);
    if (!unassignedTile) {
      return { outcome: 'skipped', reason: 'No unassigned pile tile found' };
    }
    await clickElement(unassignedTile);
    await jitter(1000, 2000);

    // Step 2: Find the matching card
    const items = document.querySelectorAll<HTMLElement>(SELECTORS.TRANSFER_LIST_ITEM);
    let targetItem: HTMLElement | null = null;

    for (const item of Array.from(items)) {
      const ratingEl = item.querySelector(SELECTORS.ITEM_RATING);
      const rating = parseInt(ratingEl?.textContent?.trim() ?? '', 10);
      if (rating !== signal.rating) continue;

      // Name check — EA shows abbreviated names, use substring match
      const nameEl = item.querySelector('.name');
      const name = nameEl?.textContent?.trim().toLowerCase() ?? '';
      if (signal.player_name.toLowerCase().includes(name) || name.includes(signal.player_name.toLowerCase())) {
        targetItem = item;
        break;
      }
    }

    if (!targetItem) {
      return { outcome: 'skipped', reason: `Card not found in unassigned pile: ${signal.player_name}` };
    }

    // Step 3: Discover market price via transfer market search
    await navigateToTransferMarket();
    await jitter();

    const nameInput = requireElement<HTMLInputElement>(
      'SEARCH_PLAYER_NAME_INPUT',
      SELECTORS.SEARCH_PLAYER_NAME_INPUT,
    );
    nameInput.focus();
    nameInput.value = '';
    nameInput.dispatchEvent(new Event('input', { bubbles: true }));
    await jitter(300, 600);

    nameInput.value = signal.player_name;
    nameInput.dispatchEvent(new Event('input', { bubbles: true }));
    nameInput.dispatchEvent(new Event('change', { bubbles: true }));

    await jitter(1000, 2000);
    try {
      const suggestionList = await waitForElement(
        'SEARCH_PLAYER_SUGGESTIONS',
        SELECTORS.SEARCH_PLAYER_SUGGESTIONS,
        document,
        5_000,
      );
      const buttons = Array.from(suggestionList.querySelectorAll('button'));
      const match = buttons.find(
        btn => btn.textContent?.trim().toLowerCase().includes(signal.player_name.toLowerCase()),
      ) ?? buttons[0];
      if (match) {
        await clickElement(match);
        await jitter();
      }
    } catch {
      // No suggestions
    }

    const searchBtn = requireElement<HTMLElement>(
      'SEARCH_SUBMIT_BUTTON',
      SELECTORS.SEARCH_SUBMIT_BUTTON,
    );
    await clickElement(searchBtn);

    const searchResult = await waitForSearchResults();
    let marketPrice: number;

    if (searchResult.outcome === 'results') {
      const discovered = readCheapestBin(signal.rating, signal.position);
      if (isNaN(discovered)) {
        marketPrice = signal.reference_price; // Fallback to reference
      } else {
        marketPrice = discovered;
      }
    } else {
      marketPrice = signal.reference_price; // Fallback
    }

    // Step 4: Go back to unassigned pile and list the card
    // Navigate back from search results
    const backBtn1 = document.querySelector<HTMLElement>(SELECTORS.NAV_BACK_BUTTON);
    if (backBtn1) {
      await clickElement(backBtn1);
      await jitter(1000, 2000);
    }

    // Navigate to unassigned pile again
    const transfersBtn2 = document.querySelector<HTMLElement>(SELECTORS.NAV_TRANSFERS);
    if (transfersBtn2) {
      await clickElement(transfersBtn2);
      await jitter(1000, 2000);
    }
    const unassignedTile2 = document.querySelector<HTMLElement>(SELECTORS.TILE_UNASSIGNED);
    if (unassignedTile2) {
      await clickElement(unassignedTile2);
      await jitter(1000, 2000);
    }

    // Re-find the card (DOM was rebuilt after navigation)
    const items2 = document.querySelectorAll<HTMLElement>(SELECTORS.TRANSFER_LIST_ITEM);
    let targetItem2: HTMLElement | null = null;
    for (const item of Array.from(items2)) {
      const ratingEl = item.querySelector(SELECTORS.ITEM_RATING);
      const rating = parseInt(ratingEl?.textContent?.trim() ?? '', 10);
      if (rating !== signal.rating) continue;
      const nameEl = item.querySelector('.name');
      const name = nameEl?.textContent?.trim().toLowerCase() ?? '';
      if (signal.player_name.toLowerCase().includes(name) || name.includes(signal.player_name.toLowerCase())) {
        targetItem2 = item;
        break;
      }
    }

    if (!targetItem2) {
      return { outcome: 'error', reason: 'Card disappeared from unassigned pile after price discovery' };
    }

    // Click the card and list it
    await clickElement(targetItem2);
    await jitter();

    const accordionBtn = await waitForElement<HTMLElement>(
      'LIST_ON_MARKET_ACCORDION',
      SELECTORS.LIST_ON_MARKET_ACCORDION,
      document,
      8_000,
    );
    await clickElement(accordionBtn);
    await jitter();

    await waitForElement('QUICK_LIST_PANEL', SELECTORS.QUICK_LIST_PANEL, document, 8_000);

    const listInputs = Array.from(
      document.querySelectorAll<HTMLInputElement>(SELECTORS.QUICK_LIST_PRICE_INPUTS),
    );
    if (listInputs.length < 2) {
      return { outcome: 'error', reason: 'Quick list panel inputs not found' };
    }

    const startPrice = Math.max(marketPrice - 100, 200);
    await typePrice(listInputs[0], startPrice);
    await jitter();
    await typePrice(listInputs[1], marketPrice);
    await jitter();

    const listBtn = findListConfirmButton();
    if (!listBtn) {
      return { outcome: 'error', reason: 'List for Transfer button not found' };
    }
    await clickElement(listBtn);
    await jitter(1500, 3000);

    // Verify listing succeeded
    const panelStillVisible = document.querySelector(SELECTORS.QUICK_LIST_PANEL) !== null;
    if (panelStillVisible) {
      return { outcome: 'error', reason: 'Listing failed — TL may be full' };
    }

    return { outcome: 'listed', sellPrice: marketPrice, quantity: 1 };
  } catch (err) {
    if (err instanceof AutomationError) {
      return { outcome: 'error', reason: err.message };
    }
    const msg = err instanceof Error ? err.message : String(err);
    return { outcome: 'error', reason: `Unexpected: ${msg}` };
  }
}
```

- [ ] **Step 2: Commit**

```bash
git add extension/src/algo-sell-cycle.ts
git commit -m "feat(algo): add sell cycle (unassigned -> price discover -> list)"
```

---

### Task 10: Algo Automation Loop

**Files:**
- Create: `extension/src/algo-automation-loop.ts`

- [ ] **Step 1: Create the algo automation loop**

```typescript
// extension/src/algo-automation-loop.ts
/**
 * Algo trading automation loop — polls for signals and executes buy/sell cycles.
 *
 * Separate from the OP sell automation loop. Runs when algo mode is active.
 * Polls GET /algo/signals/pending, executes BUY (without listing) or SELL
 * (from unassigned pile at market price), and reports completion.
 */
import { AutomationEngine, jitter } from './automation';
import { executeAlgoBuyCycle, type AlgoBuyCycleResult } from './algo-buy-cycle';
import { executeAlgoSellCycle, type AlgoSellCycleResult } from './algo-sell-cycle';
import type { ExtensionMessage, AlgoSignal } from './messages';

/**
 * Run the algo trading automation loop until stopped.
 *
 * @param engine     AutomationEngine state machine (shared with OP sell mode)
 * @param sendMessage  Callback to relay messages to the service worker
 */
export async function runAlgoAutomationLoop(
  engine: AutomationEngine,
  sendMessage: (msg: any) => Promise<any>,
): Promise<void> {
  const signal = engine.getAbortSignal();
  const stopped = () => signal?.aborted ?? false;

  try {
    while (!stopped()) {
      // Poll for next signal
      await engine.setState('SCANNING', 'Polling for algo signals');

      let algoSignal: AlgoSignal | null = null;
      try {
        const res = await sendMessage({ type: 'ALGO_SIGNAL_REQUEST' } satisfies ExtensionMessage);
        if (res && res.type === 'ALGO_SIGNAL_RESULT' && res.signal) {
          algoSignal = res.signal;
        }
      } catch {
        await engine.log('ALGO_SIGNAL_REQUEST failed');
      }

      if (stopped()) return;

      if (!algoSignal) {
        await engine.setState('IDLE', 'No algo signals — waiting');
        // Wait 30-60s before next poll
        let remaining = 30_000 + Math.random() * 30_000;
        while (remaining > 0 && !stopped()) {
          const chunk = Math.min(remaining, 10_000);
          await new Promise(r => setTimeout(r, chunk));
          remaining -= chunk;
        }
        continue;
      }

      if (algoSignal.action === 'BUY') {
        // Execute buy for each unit in signal quantity
        let totalBought = 0;
        for (let i = 0; i < algoSignal.quantity; i++) {
          if (stopped()) return;

          await engine.setState('BUYING', `Algo BUY: ${algoSignal.player_name} (${i + 1}/${algoSignal.quantity})`);
          const result: AlgoBuyCycleResult = await executeAlgoBuyCycle(algoSignal, sendMessage);

          if (result.outcome === 'bought') {
            totalBought++;
            await engine.setLastEvent(
              `Algo bought ${algoSignal.player_name} for ${result.buyPrice.toLocaleString()}`,
            );
          } else if (result.outcome === 'skipped') {
            await engine.setLastEvent(`Algo skip ${algoSignal.player_name}: ${result.reason}`);
            break; // Don't retry remaining units
          } else {
            await engine.setLastEvent(`Algo error ${algoSignal.player_name}: ${result.reason}`);
            break;
          }

          if (!stopped() && i < algoSignal.quantity - 1) {
            await jitter();
          }
        }

        // Report completion
        const outcome = totalBought > 0 ? 'bought' : 'skipped';
        try {
          await sendMessage({
            type: 'ALGO_SIGNAL_COMPLETE',
            signal_id: algoSignal.id,
            outcome,
            price: algoSignal.reference_price,
            quantity: totalBought,
          } satisfies ExtensionMessage);
        } catch {
          await engine.log(`Failed to report algo signal ${algoSignal.id} completion`);
        }

      } else if (algoSignal.action === 'SELL') {
        // Execute sell for each unit
        let totalSold = 0;
        let lastSellPrice = 0;
        for (let i = 0; i < algoSignal.quantity; i++) {
          if (stopped()) return;

          await engine.setState('LISTING', `Algo SELL: ${algoSignal.player_name} (${i + 1}/${algoSignal.quantity})`);
          const result: AlgoSellCycleResult = await executeAlgoSellCycle(algoSignal, sendMessage);

          if (result.outcome === 'listed') {
            totalSold++;
            lastSellPrice = result.sellPrice;
            await engine.setLastEvent(
              `Algo listed ${algoSignal.player_name} for ${result.sellPrice.toLocaleString()}`,
            );
          } else if (result.outcome === 'skipped') {
            await engine.setLastEvent(`Algo sell skip ${algoSignal.player_name}: ${result.reason}`);
            break;
          } else {
            await engine.setLastEvent(`Algo sell error ${algoSignal.player_name}: ${result.reason}`);
            // TL full — wait and retry later
            if (result.reason.includes('TL')) {
              await engine.setState('IDLE', 'Transfer list full — waiting');
              await new Promise(r => setTimeout(r, 60_000));
            }
            break;
          }

          if (!stopped() && i < algoSignal.quantity - 1) {
            await jitter();
          }
        }

        const outcome = totalSold > 0 ? 'sold' : 'skipped';
        try {
          await sendMessage({
            type: 'ALGO_SIGNAL_COMPLETE',
            signal_id: algoSignal.id,
            outcome,
            price: lastSellPrice,
            quantity: totalSold,
          } satisfies ExtensionMessage);
        } catch {
          await engine.log(`Failed to report algo signal ${algoSignal.id} completion`);
        }
      }

      if (!stopped()) {
        await jitter(3000, 5000);
      }
    }
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    await engine.setError(`Algo loop error: ${msg}`);
  }
}
```

- [ ] **Step 2: Commit**

```bash
git add extension/src/algo-automation-loop.ts
git commit -m "feat(algo): add algo automation loop (poll signals, execute buy/sell)"
```

---

### Task 11: Algo Tab in Overlay Panel

**Files:**
- Modify: `extension/src/overlay/panel.ts`
- Modify: `extension/entrypoints/ea-webapp.content.ts`

- [ ] **Step 1: Add Algo tab to the panel**

This task adds the "Algo" tab to the overlay panel. Because `panel.ts` is large (500+ lines), I'll describe the surgical additions needed rather than rewrite the file.

In `extension/src/overlay/panel.ts`, locate the tab bar creation code (search for `tabBar` or the existing tab buttons like "Portfolio", "Dashboard", "Automation").

Add a new tab button for "Algo" alongside the existing ones:

```typescript
const algoTab = document.createElement('button');
algoTab.textContent = 'Algo';
algoTab.dataset.tab = 'algo';
// Same styling as other tab buttons
```

Add a new tab content div:

```typescript
const algoContent = document.createElement('div');
algoContent.dataset.tabContent = 'algo';
algoContent.style.display = 'none';
```

Add the algo content builder function inside `createOverlayPanel()`:

```typescript
function renderAlgoTab(container: HTMLElement): void {
  container.innerHTML = '';

  // Budget input
  const budgetRow = document.createElement('div');
  budgetRow.style.cssText = 'display:flex;gap:8px;margin-bottom:12px;align-items:center;';
  const budgetInput = document.createElement('input');
  budgetInput.type = 'number';
  budgetInput.placeholder = 'Budget (coins)';
  budgetInput.style.cssText = 'flex:1;padding:6px 8px;background:#2a2a2a;border:1px solid #444;color:#fff;border-radius:4px;';
  budgetRow.appendChild(budgetInput);
  container.appendChild(budgetRow);

  // Start / Stop button
  const controlRow = document.createElement('div');
  controlRow.style.cssText = 'display:flex;gap:8px;margin-bottom:12px;';
  const startBtn = document.createElement('button');
  startBtn.textContent = 'Start Algo';
  startBtn.style.cssText = 'flex:1;padding:8px;background:#27ae60;color:#fff;border:none;border-radius:4px;cursor:pointer;font-weight:bold;';
  const stopBtn = document.createElement('button');
  stopBtn.textContent = 'Stop Algo';
  stopBtn.style.cssText = 'flex:1;padding:8px;background:#e74c3c;color:#fff;border:none;border-radius:4px;cursor:pointer;font-weight:bold;';
  controlRow.appendChild(startBtn);
  controlRow.appendChild(stopBtn);
  container.appendChild(controlRow);

  // Status display
  const statusDiv = document.createElement('div');
  statusDiv.style.cssText = 'padding:8px;background:#1a1a2e;border-radius:4px;margin-bottom:12px;font-size:12px;color:#ccc;';
  statusDiv.textContent = 'Status: Inactive';
  container.appendChild(statusDiv);

  // Positions list
  const positionsDiv = document.createElement('div');
  positionsDiv.style.cssText = 'font-size:12px;color:#ccc;';
  positionsDiv.textContent = 'No positions';
  container.appendChild(positionsDiv);

  // Wire up buttons
  startBtn.addEventListener('click', async () => {
    const budget = parseInt(budgetInput.value, 10);
    if (isNaN(budget) || budget <= 0) {
      statusDiv.textContent = 'Status: Enter a valid budget';
      return;
    }
    statusDiv.textContent = 'Status: Starting...';
    const res = await chrome.runtime.sendMessage({ type: 'ALGO_START', budget });
    if (res?.success) {
      statusDiv.textContent = `Status: Active | Budget: ${budget.toLocaleString()}`;
      document.dispatchEvent(new CustomEvent('op-seller-algo-start'));
    } else {
      statusDiv.textContent = `Status: Error — ${res?.error ?? 'unknown'}`;
    }
  });

  stopBtn.addEventListener('click', async () => {
    statusDiv.textContent = 'Status: Stopping...';
    await chrome.runtime.sendMessage({ type: 'ALGO_STOP' });
    document.dispatchEvent(new CustomEvent('op-seller-algo-stop'));
    statusDiv.textContent = 'Status: Stopped';
  });

  // Refresh status periodically
  async function refreshStatus() {
    const res = await chrome.runtime.sendMessage({ type: 'ALGO_STATUS_REQUEST' });
    if (res?.type === 'ALGO_STATUS_RESULT' && res.data) {
      const d = res.data;
      const activeText = d.is_active ? 'Active' : 'Inactive';
      statusDiv.innerHTML = `
        <div>Status: <strong>${activeText}</strong></div>
        <div>Cash: ${d.cash.toLocaleString()} | Pending: ${d.pending_signals}</div>
        <div>P&L: <span style="color:${d.total_pnl >= 0 ? '#27ae60' : '#e74c3c'}">${d.total_pnl.toLocaleString()}</span></div>
      `;

      if (d.positions.length > 0) {
        positionsDiv.innerHTML = '<div style="margin-bottom:4px;font-weight:bold;">Positions:</div>' +
          d.positions.map(p =>
            `<div style="display:flex;justify-content:space-between;padding:2px 0;">` +
            `<span>${p.name} x${p.quantity}</span>` +
            `<span style="color:${p.unrealized_pnl >= 0 ? '#27ae60' : '#e74c3c'}">${p.unrealized_pnl.toLocaleString()}</span>` +
            `</div>`
          ).join('');
      } else {
        positionsDiv.textContent = 'No positions';
      }
    }
  }

  // Initial fetch + interval
  refreshStatus();
  const intervalId = setInterval(refreshStatus, 15_000);
  // Clean up on tab switch (caller should handle)
  (container as any)._algoCleanup = () => clearInterval(intervalId);
}
```

Wire up the tab switch to call `renderAlgoTab(algoContent)` when the Algo tab is selected, and call `(algoContent as any)._algoCleanup?.()` when switching away.

- [ ] **Step 2: Wire algo automation events in content script**

In `extension/entrypoints/ea-webapp.content.ts`, add after the existing automation event listeners:

```typescript
    // ── Algo trading event listeners ─────────────────────────────────────
    const algoEngine = new AutomationEngine(
      (msg) => chrome.runtime.sendMessage(msg),
    );

    document.addEventListener('op-seller-algo-start', async () => {
      const result = await algoEngine.start();
      if (result.success) {
        const { runAlgoAutomationLoop } = await import('../src/algo-automation-loop');
        runAlgoAutomationLoop(algoEngine, (msg) => chrome.runtime.sendMessage(msg))
          .catch(err => algoEngine.setError(err instanceof Error ? err.message : String(err)));
      }
    });

    document.addEventListener('op-seller-algo-stop', async () => {
      await algoEngine.stop();
    });
```

- [ ] **Step 3: Commit**

```bash
git add extension/src/overlay/panel.ts extension/entrypoints/ea-webapp.content.ts
git commit -m "feat(algo): add Algo tab to overlay panel with start/stop/status"
```

---

### Task 12: End-to-End Parity Test with DB

**Files:**
- Create: `tests/algo/test_signal_parity_db.py`

This test verifies that the full pipeline (market_snapshots -> algo_runner -> algo_signals) produces the same signals as running the backtester directly on the same data.

- [ ] **Step 1: Write the E2E parity test**

```python
# tests/algo/test_signal_parity_db.py
"""End-to-end parity: DB runner signals match backtester signals exactly."""
import pytest
from datetime import datetime, timedelta
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from src.server.db import Base
from src.server.models_db import (
    AlgoConfig, AlgoSignal, AlgoPosition, PlayerRecord, MarketSnapshot,
)
from src.server.algo_runner import run_signal_engine
from src.algo.strategies.promo_dip_buy import PromoDipBuyStrategy
from src.algo.models import Portfolio


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    yield sf
    await engine.dispose()


def _generate_promo_data(num_cards=12, hours=300):
    """Same data generator as test_signal_parity.py but returns raw tuples for DB seeding."""
    release = datetime(2026, 4, 3, 18, 0)
    snapshots = []
    created_at_map = {}

    for i in range(num_cards):
        ea_id = 300_000 + i
        created_at_map[ea_id] = release

        for h in range(hours):
            ts = release + timedelta(hours=h)
            if h < 48:
                price = int(30000 * (1.0 - 0.5 * h / 48))
            elif h < 72:
                progress = (h - 48) / 24
                price = int(30000 * (0.5 + 0.25 * progress))
            elif h < 200:
                price = int(30000 * (0.75 + 0.02 * (h - 72) / 128))
            else:
                price = int(30000 * 0.77)
            snapshots.append((ea_id, ts, price))

    return snapshots, created_at_map


async def _seed_db(sf, snapshots, created_at_map):
    async with sf() as session:
        for ea_id, created_at in created_at_map.items():
            session.add(PlayerRecord(
                ea_id=ea_id, name=f"Player {ea_id}", rating=88, position="CM",
                nation="Test", league="Test", club="Test", card_type="TOTS",
                created_at=created_at,
            ))
        for ea_id, ts, price in snapshots:
            session.add(MarketSnapshot(
                ea_id=ea_id, captured_at=ts,
                current_lowest_bin=price, listing_count=50,
            ))
        session.add(AlgoConfig(
            budget=5_000_000, is_active=True, strategy_params=None,
            created_at=datetime(2026, 4, 3), updated_at=datetime(2026, 4, 3),
        ))
        await session.commit()


def _run_backtester(snapshots, created_at_map, budget=5_000_000):
    """Run backtester and return signal list for comparison."""
    # Build price_data from snapshots (hourly)
    price_data: dict[int, list[tuple[datetime, int]]] = defaultdict(list)
    for ea_id, ts, price in snapshots:
        price_data[ea_id].append((ts, price))
    for ea_id in price_data:
        price_data[ea_id].sort(key=lambda x: x[0])

    params = PromoDipBuyStrategy({}).param_grid_hourly()[0]
    strategy = PromoDipBuyStrategy(params)
    strategy.set_created_at_map(created_at_map)

    portfolio = Portfolio(cash=budget)

    timeline: dict[datetime, list[tuple[int, int]]] = defaultdict(list)
    for ea_id, points in price_data.items():
        for ts, price in points:
            timeline[ts].append((ea_id, price))

    sorted_ts = sorted(timeline.keys())
    if sorted_ts:
        existing = {eid for eid, _ in timeline[sorted_ts[0]]}
        strategy.set_existing_ids(existing)

    all_signals = []
    for ts in sorted_ts:
        ticks = timeline[ts]
        signals = strategy.on_tick_batch(ticks, ts, portfolio)
        for sig in signals:
            all_signals.append((sig.action, sig.ea_id, sig.quantity))
            sig_price = next((p for eid, p in ticks if eid == sig.ea_id), 0)
            if sig.action == "BUY":
                portfolio.buy(sig.ea_id, sig.quantity, sig_price, ts)
            elif sig.action == "SELL":
                portfolio.sell(sig.ea_id, sig.quantity, sig_price, ts)

    return all_signals


@pytest.mark.asyncio
async def test_db_runner_matches_backtester(db):
    """Full pipeline parity: same data -> same signals."""
    snapshots, created_at_map = _generate_promo_data()
    await _seed_db(db, snapshots, created_at_map)

    # Run DB pipeline
    await run_signal_engine(db)

    async with db() as session:
        db_signals = (await session.execute(
            select(AlgoSignal).order_by(AlgoSignal.created_at, AlgoSignal.ea_id)
        )).scalars().all()

    db_signal_list = [(s.action, s.ea_id, s.quantity) for s in db_signals]

    # Run backtester
    bt_signal_list = _run_backtester(snapshots, created_at_map)

    assert len(bt_signal_list) > 0, "Backtester should produce signals"
    assert db_signal_list == bt_signal_list, (
        f"DB runner signal mismatch.\n"
        f"Backtester ({len(bt_signal_list)}): {bt_signal_list[:10]}\n"
        f"DB runner  ({len(db_signal_list)}): {db_signal_list[:10]}"
    )
```

- [ ] **Step 2: Run test**

Run: `python -m pytest tests/algo/test_signal_parity_db.py -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/algo/test_signal_parity_db.py
git commit -m "test(algo): add E2E parity test — DB runner vs backtester"
```

---

## Summary

| Task | What | Files |
|------|------|-------|
| 1 | DB models (AlgoConfig, AlgoSignal, AlgoPosition) | models_db.py, db.py |
| 2 | AlgoSignalEngine core + parity tests | algo_engine.py, test_signal_parity.py |
| 3 | DB runner (load snapshots, run engine, write signals) | algo_runner.py, test_algo_runner.py |
| 4 | API endpoints (start/stop/status/signals) | api/algo.py, main.py, test_algo_api.py |
| 5 | Schedule engine in scanner process | scheduler.py, scanner_main.py |
| 6 | Extension message types | messages.ts |
| 7 | Service worker handlers | background.ts, ea-webapp.content.ts |
| 8 | Algo buy cycle (no listing) | algo-buy-cycle.ts |
| 9 | Algo sell cycle (unassigned -> market price) | algo-sell-cycle.ts |
| 10 | Algo automation loop | algo-automation-loop.ts |
| 11 | Algo tab in overlay panel | panel.ts, ea-webapp.content.ts |
| 12 | E2E parity test (DB pipeline vs backtester) | test_signal_parity_db.py |

**Dependencies:** Tasks 1-5 are server-side (sequential). Tasks 6-11 are extension-side (sequential). Task 12 depends on Tasks 1-3.

Server tasks (1-5) and extension tasks (6-11) can be executed in parallel by separate agents.
