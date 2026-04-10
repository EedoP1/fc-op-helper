# Algo Trading Research Tool — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a backtesting engine that simulates algo trading strategies against historical FC26 player price data, with parameter sweeping and results comparison.

**Architecture:** New `src/algo/` package, independent from existing server/scanner code. Reuses `src/server/db.py` for database connection and `src/server/models_db.py` Base class. Scraper fetches historical data from fut.gg, engine walks time once calling all strategies per tick, results stored in DB for comparison via CLI.

**Tech Stack:** Python 3.12, SQLAlchemy async ORM (PostgreSQL), httpx (scraper), Click (CLI), Rich (output tables), pytest + pytest-asyncio (tests).

---

## File Structure

```
src/algo/
├── __init__.py              — Package marker
├── models.py                — Signal, Portfolio, Position, TradeLog, BacktestConfig
├── models_db.py             — ORM tables: PriceHistory, BacktestResult
├── scraper.py               — One-time fut.gg price history scrape
├── engine.py                — Backtesting engine (main loop, signal execution)
├── report.py                — CLI for viewing/comparing results
├── strategies/
│   ├── __init__.py          — Auto-discovery of strategy classes
│   ├── base.py              — Strategy abstract base class
│   ├── mean_reversion.py    — Mean reversion strategy
│   ├── momentum.py          — Momentum / trend following strategy
│   ├── weekly_cycle.py      — Weekly price cycle strategy
│   └── bollinger.py         — Bollinger Bands strategy
tests/algo/
├── __init__.py
├── test_models.py           — Signal, Portfolio, Position tests
├── test_engine.py           — Backtesting engine tests
├── test_strategies.py       — Strategy logic tests
├── test_scraper.py          — Scraper parsing tests
```

---

### Task 1: ORM Tables (PriceHistory, BacktestResult)

**Files:**
- Create: `src/algo/__init__.py`
- Create: `src/algo/models_db.py`
- Create: `tests/algo/__init__.py`
- Create: `tests/algo/test_models_db.py`

- [ ] **Step 1: Write failing test for PriceHistory table creation**

```python
# tests/algo/test_models_db.py
import pytest
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from src.server.db import Base


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    yield session_factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_price_history_table_exists(db):
    from src.algo.models_db import PriceHistory  # noqa: F401
    async with db() as session:
        conn = await session.connection()
        tables = await conn.run_sync(
            lambda sync_conn: inspect(sync_conn).get_table_names()
        )
        assert "price_history" in tables
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/algo/test_models_db.py::test_price_history_table_exists -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.algo'`

- [ ] **Step 3: Create package and ORM models**

```python
# src/algo/__init__.py
```

```python
# src/algo/models_db.py
"""SQLAlchemy ORM tables for the algo trading backtester."""
from datetime import datetime
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import Integer, Float, DateTime, String, Text, Index
from src.server.db import Base


class PriceHistory(Base):
    """Hourly price data per player from fut.gg historical scrape."""

    __tablename__ = "price_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ea_id: Mapped[int] = mapped_column(Integer, index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime)
    price: Mapped[int] = mapped_column(Integer)

    __table_args__ = (
        Index("ix_price_history_ea_id_timestamp", "ea_id", "timestamp"),
    )


class BacktestResult(Base):
    """Output of a single backtest run (one strategy + one param combo)."""

    __tablename__ = "backtest_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy_name: Mapped[str] = mapped_column(String(100))
    params: Mapped[str] = mapped_column(Text)  # JSON-encoded param dict
    started_budget: Mapped[int] = mapped_column(Integer)
    final_budget: Mapped[int] = mapped_column(Integer)
    total_pnl: Mapped[int] = mapped_column(Integer)
    total_trades: Mapped[int] = mapped_column(Integer)
    win_rate: Mapped[float] = mapped_column(Float)
    max_drawdown: Mapped[float] = mapped_column(Float)
    sharpe_ratio: Mapped[float] = mapped_column(Float)
    run_at: Mapped[datetime] = mapped_column(DateTime)

    __table_args__ = (
        Index("ix_backtest_results_strategy", "strategy_name"),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/algo/test_models_db.py::test_price_history_table_exists -v`
Expected: PASS

- [ ] **Step 5: Write and run test for BacktestResult table**

```python
# Append to tests/algo/test_models_db.py
@pytest.mark.asyncio
async def test_backtest_results_table_exists(db):
    from src.algo.models_db import BacktestResult  # noqa: F401
    async with db() as session:
        conn = await session.connection()
        tables = await conn.run_sync(
            lambda sync_conn: inspect(sync_conn).get_table_names()
        )
        assert "backtest_results" in tables
```

Run: `pytest tests/algo/test_models_db.py -v`
Expected: 2 PASSED

- [ ] **Step 6: Commit**

```bash
git add src/algo/__init__.py src/algo/models_db.py tests/algo/__init__.py tests/algo/test_models_db.py
git commit -m "feat(algo): add PriceHistory and BacktestResult ORM tables"
```

---

### Task 2: Domain Models (Signal, Position, Portfolio)

**Files:**
- Create: `src/algo/models.py`
- Create: `tests/algo/test_models.py`

- [ ] **Step 1: Write failing test for Signal**

```python
# tests/algo/test_models.py
from src.algo.models import Signal


def test_buy_signal():
    sig = Signal(action="BUY", ea_id=12345, quantity=1)
    assert sig.action == "BUY"
    assert sig.ea_id == 12345
    assert sig.quantity == 1


def test_sell_signal():
    sig = Signal(action="SELL", ea_id=12345, quantity=1)
    assert sig.action == "SELL"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/algo/test_models.py::test_buy_signal -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Implement domain models**

```python
# src/algo/models.py
"""Domain models for the algo trading backtester."""
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Signal:
    """A trading signal emitted by a strategy."""
    action: str  # "BUY" or "SELL"
    ea_id: int
    quantity: int


@dataclass
class Position:
    """An open position (player cards held)."""
    ea_id: int
    quantity: int
    buy_price: int
    buy_time: datetime


@dataclass
class Trade:
    """A completed round-trip trade."""
    ea_id: int
    quantity: int
    buy_price: int
    sell_price: int
    buy_time: datetime
    sell_time: datetime
    net_profit: int  # after 5% EA tax


class Portfolio:
    """Read-only view of current trading state, passed to strategies."""

    def __init__(self, cash: int):
        self._cash = cash
        self._positions: list[Position] = []
        self._trades: list[Trade] = []
        self._balance_history: list[tuple[datetime, int]] = []

    @property
    def cash(self) -> int:
        return self._cash

    @property
    def positions(self) -> list[Position]:
        return list(self._positions)

    @property
    def trades(self) -> list[Trade]:
        return list(self._trades)

    @property
    def balance_history(self) -> list[tuple[datetime, int]]:
        return list(self._balance_history)

    def total_value(self, current_prices: dict[int, int]) -> int:
        """Cash + market value of all open positions."""
        held_value = sum(
            current_prices.get(p.ea_id, p.buy_price) * p.quantity
            for p in self._positions
        )
        return self._cash + held_value

    def holdings(self, ea_id: int) -> int:
        """Total quantity held for a given player."""
        return sum(p.quantity for p in self._positions if p.ea_id == ea_id)

    def buy(self, ea_id: int, quantity: int, price: int, timestamp: datetime):
        """Execute a buy. Deducts cash, creates position."""
        cost = price * quantity
        if cost > self._cash:
            return  # skip if insufficient funds
        self._cash -= cost
        self._positions.append(Position(
            ea_id=ea_id, quantity=quantity, buy_price=price, buy_time=timestamp,
        ))
        self._balance_history.append((timestamp, self._cash))

    def sell(self, ea_id: int, quantity: int, price: int, timestamp: datetime):
        """Execute a sell. Adds cash (after 5% tax), records trade."""
        remaining = quantity
        to_remove = []
        for i, pos in enumerate(self._positions):
            if pos.ea_id != ea_id or remaining <= 0:
                continue
            sold_qty = min(pos.quantity, remaining)
            revenue = int(price * sold_qty * 0.95)  # 5% EA tax
            net_profit = revenue - (pos.buy_price * sold_qty)
            self._cash += revenue
            self._trades.append(Trade(
                ea_id=ea_id,
                quantity=sold_qty,
                buy_price=pos.buy_price,
                sell_price=price,
                buy_time=pos.buy_time,
                sell_time=timestamp,
                net_profit=net_profit,
            ))
            remaining -= sold_qty
            if sold_qty >= pos.quantity:
                to_remove.append(i)
            else:
                pos.quantity -= sold_qty
        for i in reversed(to_remove):
            self._positions.pop(i)
        self._balance_history.append((timestamp, self._cash))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/algo/test_models.py -v`
Expected: PASS

- [ ] **Step 5: Write tests for Portfolio buy/sell mechanics**

```python
# Append to tests/algo/test_models.py
from datetime import datetime
from src.algo.models import Portfolio


def test_portfolio_buy():
    p = Portfolio(cash=100_000)
    p.buy(ea_id=1, quantity=1, price=50_000, timestamp=datetime(2026, 1, 1))
    assert p.cash == 50_000
    assert p.holdings(1) == 1
    assert len(p.positions) == 1


def test_portfolio_buy_insufficient_funds():
    p = Portfolio(cash=10_000)
    p.buy(ea_id=1, quantity=1, price=50_000, timestamp=datetime(2026, 1, 1))
    assert p.cash == 10_000
    assert p.holdings(1) == 0


def test_portfolio_sell_with_tax():
    p = Portfolio(cash=100_000)
    p.buy(ea_id=1, quantity=1, price=50_000, timestamp=datetime(2026, 1, 1))
    p.sell(ea_id=1, quantity=1, price=60_000, timestamp=datetime(2026, 1, 2))
    # Revenue: 60000 * 0.95 = 57000
    assert p.cash == 50_000 + 57_000
    assert p.holdings(1) == 0
    assert len(p.trades) == 1
    assert p.trades[0].net_profit == 57_000 - 50_000  # 7000


def test_portfolio_sell_partial():
    p = Portfolio(cash=200_000)
    p.buy(ea_id=1, quantity=3, price=50_000, timestamp=datetime(2026, 1, 1))
    p.sell(ea_id=1, quantity=2, price=60_000, timestamp=datetime(2026, 1, 2))
    assert p.holdings(1) == 1
    assert len(p.trades) == 1
    assert p.trades[0].quantity == 2


def test_portfolio_total_value():
    p = Portfolio(cash=50_000)
    p.buy(ea_id=1, quantity=1, price=50_000, timestamp=datetime(2026, 1, 1))
    assert p.total_value({1: 60_000}) == 50_000 + 60_000
```

- [ ] **Step 6: Run all model tests**

Run: `pytest tests/algo/test_models.py -v`
Expected: 7 PASSED

- [ ] **Step 7: Commit**

```bash
git add src/algo/models.py tests/algo/test_models.py
git commit -m "feat(algo): add Signal, Position, Portfolio domain models"
```

---

### Task 3: Strategy Base Class + Auto-Discovery

**Files:**
- Create: `src/algo/strategies/__init__.py`
- Create: `src/algo/strategies/base.py`
- Create: `tests/algo/test_strategies.py`

- [ ] **Step 1: Write failing test for strategy interface**

```python
# tests/algo/test_strategies.py
from datetime import datetime
from src.algo.strategies.base import Strategy
from src.algo.models import Portfolio


class DummyStrategy(Strategy):
    name = "dummy"

    def __init__(self, params: dict):
        self.params = params

    def on_tick(self, ea_id, price, timestamp, portfolio):
        return []

    def param_grid(self):
        return [{"x": 1}, {"x": 2}]


def test_strategy_interface():
    s = DummyStrategy({"x": 1})
    assert s.name == "dummy"
    signals = s.on_tick(1, 50000, datetime(2026, 1, 1), Portfolio(100000))
    assert signals == []


def test_param_grid():
    s = DummyStrategy({})
    grid = s.param_grid()
    assert len(grid) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/algo/test_strategies.py::test_strategy_interface -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement base class**

```python
# src/algo/strategies/base.py
"""Abstract base class for trading strategies."""
from abc import ABC, abstractmethod
from datetime import datetime
from src.algo.models import Signal, Portfolio


class Strategy(ABC):
    """All strategies must implement this interface."""

    name: str

    @abstractmethod
    def __init__(self, params: dict):
        ...

    @abstractmethod
    def on_tick(
        self, ea_id: int, price: int, timestamp: datetime, portfolio: Portfolio,
    ) -> list[Signal]:
        """Process a single price tick. Return BUY/SELL signals or empty list.

        Called once per player per hour. The strategy only sees current and
        past data — never future prices.
        """
        ...

    @abstractmethod
    def param_grid(self) -> list[dict]:
        """Return all parameter combinations to sweep."""
        ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/algo/test_strategies.py -v`
Expected: 2 PASSED

- [ ] **Step 5: Implement auto-discovery**

```python
# src/algo/strategies/__init__.py
"""Auto-discover all Strategy subclasses in this package."""
import importlib
import pkgutil
from src.algo.strategies.base import Strategy


def discover_strategies() -> dict[str, type[Strategy]]:
    """Import all modules in this package and return {name: class} for each Strategy subclass."""
    strategies = {}
    package_path = __path__
    for importer, modname, ispkg in pkgutil.iter_modules(package_path):
        if modname == "base":
            continue
        module = importlib.import_module(f"src.algo.strategies.{modname}")
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if (
                isinstance(attr, type)
                and issubclass(attr, Strategy)
                and attr is not Strategy
                and hasattr(attr, "name")
            ):
                strategies[attr.name] = attr
    return strategies
```

- [ ] **Step 6: Write and run test for auto-discovery**

```python
# Append to tests/algo/test_strategies.py
from src.algo.strategies import discover_strategies


def test_discover_finds_nothing_initially():
    # Only base.py exists, no concrete strategies yet
    strategies = discover_strategies()
    assert isinstance(strategies, dict)
    # Will find strategies once we add them in later tasks
```

Run: `pytest tests/algo/test_strategies.py -v`
Expected: 3 PASSED

- [ ] **Step 7: Commit**

```bash
git add src/algo/strategies/__init__.py src/algo/strategies/base.py tests/algo/test_strategies.py
git commit -m "feat(algo): add Strategy base class with auto-discovery"
```

---

### Task 4: Backtesting Engine

**Files:**
- Create: `src/algo/engine.py`
- Create: `tests/algo/test_engine.py`

- [ ] **Step 1: Write failing test for basic engine run**

```python
# tests/algo/test_engine.py
import pytest
from datetime import datetime, timedelta
from src.algo.engine import run_backtest
from src.algo.strategies.base import Strategy
from src.algo.models import Signal, Portfolio


class AlwaysBuyStrategy(Strategy):
    """Buys once per player, sells next tick. For testing."""
    name = "always_buy"

    def __init__(self, params: dict):
        self.params = params
        self._bought: set[int] = set()

    def on_tick(self, ea_id, price, timestamp, portfolio):
        if ea_id not in self._bought and portfolio.cash >= price:
            self._bought.add(ea_id)
            return [Signal(action="BUY", ea_id=ea_id, quantity=1)]
        if portfolio.holdings(ea_id) > 0:
            return [Signal(action="SELL", ea_id=ea_id, quantity=1)]
        return []

    def param_grid(self):
        return [{}]


def make_price_data():
    """Two players, 5 hours of data."""
    base = datetime(2026, 1, 1)
    return {
        1: [(base + timedelta(hours=h), 10_000 + h * 100) for h in range(5)],
        2: [(base + timedelta(hours=h), 20_000 + h * 200) for h in range(5)],
    }


def test_engine_basic_run():
    strategy = AlwaysBuyStrategy({})
    price_data = make_price_data()
    result = run_backtest(strategy, price_data, budget=100_000)
    assert result["strategy_name"] == "always_buy"
    assert result["started_budget"] == 100_000
    assert result["total_trades"] > 0
    assert "final_budget" in result
    assert "total_pnl" in result
    assert "win_rate" in result
    assert "max_drawdown" in result
    assert "sharpe_ratio" in result
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/algo/test_engine.py::test_engine_basic_run -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Implement the backtesting engine**

```python
# src/algo/engine.py
"""Backtesting engine — walks historical price data and executes strategy signals."""
import json
import math
import logging
from datetime import datetime
from collections import defaultdict
from src.algo.models import Portfolio

logger = logging.getLogger(__name__)

EA_TAX_RATE = 0.05


def run_backtest(
    strategy,
    price_data: dict[int, list[tuple[datetime, int]]],
    budget: int = 1_000_000,
) -> dict:
    """Run a single strategy against historical price data.

    Args:
        strategy: A Strategy instance (already initialized with params).
        price_data: {ea_id: [(timestamp, price), ...]} sorted by timestamp.
        budget: Starting coin balance.

    Returns:
        Dict with performance metrics.
    """
    portfolio = Portfolio(cash=budget)

    # Build a timeline: {timestamp: [(ea_id, price), ...]}
    timeline: dict[datetime, list[tuple[int, int]]] = defaultdict(list)
    for ea_id, points in price_data.items():
        for ts, price in points:
            timeline[ts].append((ea_id, price))

    sorted_timestamps = sorted(timeline.keys())

    # Walk through time
    for ts in sorted_timestamps:
        for ea_id, price in timeline[ts]:
            signals = strategy.on_tick(ea_id, price, ts, portfolio)
            for signal in signals:
                if signal.action == "BUY":
                    portfolio.buy(signal.ea_id, signal.quantity, price, ts)
                elif signal.action == "SELL":
                    portfolio.sell(signal.ea_id, signal.quantity, price, ts)

    # Force-sell open positions at last known price
    last_prices: dict[int, tuple[datetime, int]] = {}
    for ea_id, points in price_data.items():
        if points:
            last_prices[ea_id] = points[-1]

    for pos in list(portfolio.positions):
        if pos.ea_id in last_prices:
            ts, price = last_prices[pos.ea_id]
            portfolio.sell(pos.ea_id, pos.quantity, price, ts)

    # Calculate metrics
    total_pnl = portfolio.cash - budget
    trades = portfolio.trades
    total_trades = len(trades)
    winning = sum(1 for t in trades if t.net_profit > 0)
    win_rate = winning / total_trades if total_trades > 0 else 0.0

    max_drawdown = _calc_max_drawdown(portfolio.balance_history, budget)
    sharpe = _calc_sharpe_ratio(trades)

    return {
        "strategy_name": strategy.name,
        "params": json.dumps(strategy.params) if hasattr(strategy, "params") else "{}",
        "started_budget": budget,
        "final_budget": portfolio.cash,
        "total_pnl": total_pnl,
        "total_trades": total_trades,
        "win_rate": win_rate,
        "max_drawdown": max_drawdown,
        "sharpe_ratio": sharpe,
    }


def _calc_max_drawdown(balance_history: list[tuple[datetime, int]], budget: int) -> float:
    """Maximum peak-to-trough decline as a fraction (0.0 to 1.0)."""
    if not balance_history:
        return 0.0
    peak = budget
    max_dd = 0.0
    for _, balance in balance_history:
        if balance > peak:
            peak = balance
        dd = (peak - balance) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
    return max_dd


def _calc_sharpe_ratio(trades: list) -> float:
    """Simplified Sharpe ratio: mean(returns) / std(returns). Risk-free rate = 0."""
    if len(trades) < 2:
        return 0.0
    returns = [t.net_profit / (t.buy_price * t.quantity) for t in trades if t.buy_price > 0]
    if len(returns) < 2:
        return 0.0
    mean_r = sum(returns) / len(returns)
    var_r = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)
    std_r = math.sqrt(var_r)
    if std_r == 0:
        return 0.0
    return mean_r / std_r
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/algo/test_engine.py::test_engine_basic_run -v`
Expected: PASS

- [ ] **Step 5: Write test for force-sell at end**

```python
# Append to tests/algo/test_engine.py
class BuyAndHoldStrategy(Strategy):
    """Buys once, never sells. Tests force-sell at end."""
    name = "buy_and_hold"

    def __init__(self, params: dict):
        self.params = params
        self._bought = False

    def on_tick(self, ea_id, price, timestamp, portfolio):
        if not self._bought and ea_id == 1 and portfolio.cash >= price:
            self._bought = True
            return [Signal(action="BUY", ea_id=1, quantity=1)]
        return []

    def param_grid(self):
        return [{}]


def test_engine_force_sells_open_positions():
    strategy = BuyAndHoldStrategy({})
    price_data = make_price_data()
    result = run_backtest(strategy, price_data, budget=100_000)
    # Should have 1 trade from force-sell
    assert result["total_trades"] == 1
    # Final budget = 100000 - 10000 (buy at h0) + 10400 * 0.95 (sell at h4 with tax)
    expected_revenue = int(10_400 * 0.95)
    assert result["final_budget"] == 100_000 - 10_000 + expected_revenue
```

- [ ] **Step 6: Write test for insufficient funds**

```python
# Append to tests/algo/test_engine.py
def test_engine_insufficient_funds_skips_buy():
    strategy = AlwaysBuyStrategy({})
    base = datetime(2026, 1, 1)
    price_data = {
        1: [(base, 90_000)],  # costs 90k
        2: [(base, 90_000)],  # can't afford second
    }
    result = run_backtest(strategy, price_data, budget=100_000)
    # Can only buy one player (90k), not enough for second
    assert result["total_trades"] <= 1
```

- [ ] **Step 7: Run all engine tests**

Run: `pytest tests/algo/test_engine.py -v`
Expected: 3 PASSED

- [ ] **Step 8: Commit**

```bash
git add src/algo/engine.py tests/algo/test_engine.py
git commit -m "feat(algo): implement backtesting engine with signal execution"
```

---

### Task 5: Parameter Sweep Runner

**Files:**
- Modify: `src/algo/engine.py`
- Modify: `tests/algo/test_engine.py`

- [ ] **Step 1: Write failing test for sweep runner**

```python
# Append to tests/algo/test_engine.py
from src.algo.engine import run_sweep


class ThresholdStrategy(Strategy):
    name = "threshold"

    def __init__(self, params: dict):
        self.params = params
        self.threshold = params.get("threshold", 0.05)

    def on_tick(self, ea_id, price, timestamp, portfolio):
        return []

    def param_grid(self):
        return [
            {"threshold": 0.05},
            {"threshold": 0.10},
            {"threshold": 0.15},
        ]


def test_sweep_runs_all_param_combos():
    price_data = make_price_data()
    results = run_sweep(ThresholdStrategy, price_data, budget=100_000)
    assert len(results) == 3
    assert all(r["strategy_name"] == "threshold" for r in results)
    # Each result should have different params
    params_set = {r["params"] for r in results}
    assert len(params_set) == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/algo/test_engine.py::test_sweep_runs_all_param_combos -v`
Expected: FAIL — `ImportError: cannot import name 'run_sweep'`

- [ ] **Step 3: Implement run_sweep**

```python
# Add to src/algo/engine.py, after run_backtest function

def run_sweep(
    strategy_class: type,
    price_data: dict[int, list[tuple[datetime, int]]],
    budget: int = 1_000_000,
) -> list[dict]:
    """Run a strategy across all its parameter combos.

    Args:
        strategy_class: The Strategy class (not an instance).
        price_data: {ea_id: [(timestamp, price), ...]} sorted by timestamp.
        budget: Starting coin balance for each run.

    Returns:
        List of result dicts, one per param combo.
    """
    # Get param grid from a throwaway instance
    sample = strategy_class({})
    grid = sample.param_grid()

    results = []
    for i, params in enumerate(grid):
        logger.info(
            f"[{strategy_class.name}] Running combo {i + 1}/{len(grid)}: {params}"
        )
        strategy = strategy_class(params)
        result = run_backtest(strategy, price_data, budget)
        results.append(result)

    return results
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/algo/test_engine.py::test_sweep_runs_all_param_combos -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/algo/engine.py tests/algo/test_engine.py
git commit -m "feat(algo): add parameter sweep runner"
```

---

### Task 6: Mean Reversion Strategy

**Files:**
- Create: `src/algo/strategies/mean_reversion.py`
- Modify: `tests/algo/test_strategies.py`

- [ ] **Step 1: Write failing test**

```python
# Append to tests/algo/test_strategies.py
from src.algo.models import Signal, Portfolio
from datetime import datetime, timedelta


def test_mean_reversion_buys_on_dip():
    from src.algo.strategies.mean_reversion import MeanReversionStrategy

    s = MeanReversionStrategy({"window": 4, "threshold": 0.10, "position_pct": 0.05})
    portfolio = Portfolio(cash=100_000)

    # Feed 4 hours of stable prices to build the window
    base = datetime(2026, 1, 1)
    for h in range(4):
        s.on_tick(1, 10_000, base + timedelta(hours=h), portfolio)

    # Price drops 15% — should trigger buy
    signals = s.on_tick(1, 8_500, base + timedelta(hours=4), portfolio)
    assert len(signals) == 1
    assert signals[0].action == "BUY"


def test_mean_reversion_no_buy_when_stable():
    from src.algo.strategies.mean_reversion import MeanReversionStrategy

    s = MeanReversionStrategy({"window": 4, "threshold": 0.10, "position_pct": 0.05})
    portfolio = Portfolio(cash=100_000)

    base = datetime(2026, 1, 1)
    for h in range(4):
        s.on_tick(1, 10_000, base + timedelta(hours=h), portfolio)

    # Price is stable — no buy
    signals = s.on_tick(1, 10_000, base + timedelta(hours=4), portfolio)
    assert signals == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/algo/test_strategies.py::test_mean_reversion_buys_on_dip -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement mean reversion**

```python
# src/algo/strategies/mean_reversion.py
"""Mean reversion strategy — buy when price drops below rolling average."""
from datetime import datetime
from collections import defaultdict
from src.algo.models import Signal, Portfolio
from src.algo.strategies.base import Strategy


class MeanReversionStrategy(Strategy):
    """Buy when price drops X% below N-hour rolling average. Sell when it recovers."""

    name = "mean_reversion"

    def __init__(self, params: dict):
        self.params = params
        self.window: int = params.get("window", 24)
        self.threshold: float = params.get("threshold", 0.10)
        self.position_pct: float = params.get("position_pct", 0.02)
        self._history: dict[int, list[int]] = defaultdict(list)

    def on_tick(
        self, ea_id: int, price: int, timestamp: datetime, portfolio: Portfolio,
    ) -> list[Signal]:
        history = self._history[ea_id]
        history.append(price)

        # Not enough data to compute average yet
        if len(history) < self.window:
            return []

        # Rolling average over the last N prices
        window_prices = history[-self.window:]
        avg = sum(window_prices) / len(window_prices)

        signals = []

        if portfolio.holdings(ea_id) > 0:
            # Sell when price recovers to the average
            if price >= avg:
                signals.append(Signal(action="SELL", ea_id=ea_id, quantity=portfolio.holdings(ea_id)))
        else:
            # Buy when price drops below threshold
            drop_pct = (avg - price) / avg if avg > 0 else 0
            if drop_pct >= self.threshold:
                buy_budget = int(portfolio.cash * self.position_pct)
                quantity = max(1, buy_budget // price) if price > 0 else 0
                if quantity > 0:
                    signals.append(Signal(action="BUY", ea_id=ea_id, quantity=quantity))

        return signals

    def param_grid(self) -> list[dict]:
        combos = []
        for window in [12, 24, 48, 72]:
            for threshold in [0.05, 0.10, 0.15, 0.20]:
                for position_pct in [0.01, 0.02, 0.05]:
                    combos.append({
                        "window": window,
                        "threshold": threshold,
                        "position_pct": position_pct,
                    })
        return combos
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/algo/test_strategies.py -v`
Expected: 5 PASSED (3 old + 2 new)

- [ ] **Step 5: Test auto-discovery picks it up**

```python
# Append to tests/algo/test_strategies.py
def test_discover_finds_mean_reversion():
    strategies = discover_strategies()
    assert "mean_reversion" in strategies
```

Run: `pytest tests/algo/test_strategies.py::test_discover_finds_mean_reversion -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/algo/strategies/mean_reversion.py tests/algo/test_strategies.py
git commit -m "feat(algo): add mean reversion strategy"
```

---

### Task 7: Momentum Strategy

**Files:**
- Create: `src/algo/strategies/momentum.py`
- Modify: `tests/algo/test_strategies.py`

- [ ] **Step 1: Write failing test**

```python
# Append to tests/algo/test_strategies.py
def test_momentum_buys_on_uptrend():
    from src.algo.strategies.momentum import MomentumStrategy

    s = MomentumStrategy({"trend_length": 3, "trailing_stop": 0.05, "position_pct": 0.05})
    portfolio = Portfolio(cash=100_000)

    base = datetime(2026, 1, 1)
    # 3 consecutive rising prices
    s.on_tick(1, 10_000, base + timedelta(hours=0), portfolio)
    s.on_tick(1, 10_100, base + timedelta(hours=1), portfolio)
    signals = s.on_tick(1, 10_200, base + timedelta(hours=2), portfolio)
    assert len(signals) == 1
    assert signals[0].action == "BUY"


def test_momentum_sells_on_trailing_stop():
    from src.algo.strategies.momentum import MomentumStrategy

    s = MomentumStrategy({"trend_length": 2, "trailing_stop": 0.05, "position_pct": 0.05})
    portfolio = Portfolio(cash=100_000)

    base = datetime(2026, 1, 1)
    # Build uptrend and buy
    s.on_tick(1, 10_000, base + timedelta(hours=0), portfolio)
    s.on_tick(1, 10_100, base + timedelta(hours=1), portfolio)
    # Now holding — price rises then drops 6% from peak
    s.on_tick(1, 11_000, base + timedelta(hours=2), portfolio)
    # Peak was 11000, trailing stop at 5% = sell below 10450
    signals = s.on_tick(1, 10_400, base + timedelta(hours=3), portfolio)
    assert len(signals) == 1
    assert signals[0].action == "SELL"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/algo/test_strategies.py::test_momentum_buys_on_uptrend -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement momentum strategy**

```python
# src/algo/strategies/momentum.py
"""Momentum strategy — buy on uptrends, sell on trailing stop."""
from datetime import datetime
from collections import defaultdict
from src.algo.models import Signal, Portfolio
from src.algo.strategies.base import Strategy


class MomentumStrategy(Strategy):
    """Detect rising trends and ride them. Exit on trailing stop loss."""

    name = "momentum"

    def __init__(self, params: dict):
        self.params = params
        self.trend_length: int = params.get("trend_length", 12)
        self.trailing_stop: float = params.get("trailing_stop", 0.05)
        self.position_pct: float = params.get("position_pct", 0.02)
        self._history: dict[int, list[int]] = defaultdict(list)
        self._peak_since_buy: dict[int, int] = {}

    def on_tick(
        self, ea_id: int, price: int, timestamp: datetime, portfolio: Portfolio,
    ) -> list[Signal]:
        history = self._history[ea_id]
        history.append(price)

        signals = []

        if portfolio.holdings(ea_id) > 0:
            # Track peak price since buying
            if ea_id in self._peak_since_buy:
                if price > self._peak_since_buy[ea_id]:
                    self._peak_since_buy[ea_id] = price
                peak = self._peak_since_buy[ea_id]
                drop_from_peak = (peak - price) / peak if peak > 0 else 0
                if drop_from_peak >= self.trailing_stop:
                    signals.append(Signal(
                        action="SELL", ea_id=ea_id,
                        quantity=portfolio.holdings(ea_id),
                    ))
                    del self._peak_since_buy[ea_id]
        else:
            # Check for N consecutive rising prices
            if len(history) >= self.trend_length:
                recent = history[-self.trend_length:]
                is_rising = all(recent[i] > recent[i - 1] for i in range(1, len(recent)))
                if is_rising:
                    buy_budget = int(portfolio.cash * self.position_pct)
                    quantity = max(1, buy_budget // price) if price > 0 else 0
                    if quantity > 0:
                        signals.append(Signal(action="BUY", ea_id=ea_id, quantity=quantity))
                        self._peak_since_buy[ea_id] = price

        return signals

    def param_grid(self) -> list[dict]:
        combos = []
        for trend_length in [6, 12, 24]:
            for trailing_stop in [0.03, 0.05, 0.10]:
                for position_pct in [0.01, 0.02, 0.05]:
                    combos.append({
                        "trend_length": trend_length,
                        "trailing_stop": trailing_stop,
                        "position_pct": position_pct,
                    })
        return combos
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/algo/test_strategies.py -v`
Expected: All PASSED

- [ ] **Step 5: Commit**

```bash
git add src/algo/strategies/momentum.py tests/algo/test_strategies.py
git commit -m "feat(algo): add momentum strategy"
```

---

### Task 8: Weekly Cycle Strategy

**Files:**
- Create: `src/algo/strategies/weekly_cycle.py`
- Modify: `tests/algo/test_strategies.py`

- [ ] **Step 1: Write failing test**

```python
# Append to tests/algo/test_strategies.py
def test_weekly_cycle_buys_on_buy_day():
    from src.algo.strategies.weekly_cycle import WeeklyCycleStrategy

    # Thursday = weekday 3, buy_hour = 18
    s = WeeklyCycleStrategy({
        "buy_day": 3, "buy_hour": 18,
        "sell_day": 5, "sell_hour": 12,
        "position_pct": 0.05,
    })
    portfolio = Portfolio(cash=100_000)

    # Thursday 18:00
    ts = datetime(2026, 1, 1, 18, 0)  # 2026-01-01 is a Thursday
    signals = s.on_tick(1, 10_000, ts, portfolio)
    assert len(signals) == 1
    assert signals[0].action == "BUY"


def test_weekly_cycle_sells_on_sell_day():
    from src.algo.strategies.weekly_cycle import WeeklyCycleStrategy

    s = WeeklyCycleStrategy({
        "buy_day": 3, "buy_hour": 18,
        "sell_day": 5, "sell_hour": 12,
        "position_pct": 0.05,
    })
    portfolio = Portfolio(cash=100_000)

    # Buy on Thursday
    ts_buy = datetime(2026, 1, 1, 18, 0)
    s.on_tick(1, 10_000, ts_buy, portfolio)

    # Saturday 12:00 — should sell
    ts_sell = datetime(2026, 1, 3, 12, 0)
    signals = s.on_tick(1, 11_000, ts_sell, portfolio)
    assert len(signals) == 1
    assert signals[0].action == "SELL"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/algo/test_strategies.py::test_weekly_cycle_buys_on_buy_day -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement weekly cycle strategy**

```python
# src/algo/strategies/weekly_cycle.py
"""Weekly cycle strategy — exploit predictable day-of-week price patterns."""
from datetime import datetime
from src.algo.models import Signal, Portfolio
from src.algo.strategies.base import Strategy


class WeeklyCycleStrategy(Strategy):
    """Buy on a specific day/hour, sell on another. Exploits weekly patterns."""

    name = "weekly_cycle"

    def __init__(self, params: dict):
        self.params = params
        self.buy_day: int = params.get("buy_day", 3)    # 0=Mon, 3=Thu
        self.buy_hour: int = params.get("buy_hour", 18)
        self.sell_day: int = params.get("sell_day", 5)   # 5=Sat
        self.sell_hour: int = params.get("sell_hour", 12)
        self.position_pct: float = params.get("position_pct", 0.02)

    def on_tick(
        self, ea_id: int, price: int, timestamp: datetime, portfolio: Portfolio,
    ) -> list[Signal]:
        weekday = timestamp.weekday()
        hour = timestamp.hour

        signals = []

        if portfolio.holdings(ea_id) > 0:
            # Sell window
            if weekday == self.sell_day and hour == self.sell_hour:
                signals.append(Signal(
                    action="SELL", ea_id=ea_id,
                    quantity=portfolio.holdings(ea_id),
                ))
        else:
            # Buy window
            if weekday == self.buy_day and hour == self.buy_hour:
                buy_budget = int(portfolio.cash * self.position_pct)
                quantity = max(1, buy_budget // price) if price > 0 else 0
                if quantity > 0:
                    signals.append(Signal(action="BUY", ea_id=ea_id, quantity=quantity))

        return signals

    def param_grid(self) -> list[dict]:
        combos = []
        buy_slots = [(3, 18), (3, 21), (4, 0)]   # Thu 18h, Thu 21h, Fri 0h
        sell_slots = [(5, 12), (5, 18), (6, 12)]  # Sat 12h, Sat 18h, Sun 12h
        for buy_day, buy_hour in buy_slots:
            for sell_day, sell_hour in sell_slots:
                for position_pct in [0.01, 0.02, 0.05]:
                    combos.append({
                        "buy_day": buy_day,
                        "buy_hour": buy_hour,
                        "sell_day": sell_day,
                        "sell_hour": sell_hour,
                        "position_pct": position_pct,
                    })
        return combos
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/algo/test_strategies.py -v`
Expected: All PASSED

- [ ] **Step 5: Commit**

```bash
git add src/algo/strategies/weekly_cycle.py tests/algo/test_strategies.py
git commit -m "feat(algo): add weekly cycle strategy"
```

---

### Task 9: Bollinger Bands Strategy

**Files:**
- Create: `src/algo/strategies/bollinger.py`
- Modify: `tests/algo/test_strategies.py`

- [ ] **Step 1: Write failing test**

```python
# Append to tests/algo/test_strategies.py
def test_bollinger_buys_at_lower_band():
    from src.algo.strategies.bollinger import BollingerStrategy
    import math

    s = BollingerStrategy({"window": 4, "num_std": 2.0, "position_pct": 0.05})
    portfolio = Portfolio(cash=100_000)

    base = datetime(2026, 1, 1)
    # Feed stable prices: [10000, 10000, 10000, 10000]
    # Mean = 10000, std = 0 — any deviation triggers
    # Use varied prices instead: [10000, 10200, 9800, 10000]
    prices = [10_000, 10_200, 9_800, 10_000]
    for h, p in enumerate(prices):
        s.on_tick(1, p, base + timedelta(hours=h), portfolio)

    # Mean=10000, std~82. Lower band = 10000 - 2*82 = 9836
    # Price at 9700 is below lower band — should buy
    signals = s.on_tick(1, 9_700, base + timedelta(hours=4), portfolio)
    assert len(signals) == 1
    assert signals[0].action == "BUY"


def test_bollinger_sells_at_upper_band():
    from src.algo.strategies.bollinger import BollingerStrategy

    s = BollingerStrategy({"window": 4, "num_std": 2.0, "position_pct": 0.05})
    portfolio = Portfolio(cash=100_000)

    base = datetime(2026, 1, 1)
    prices = [10_000, 10_200, 9_800, 10_000]
    for h, p in enumerate(prices):
        s.on_tick(1, p, base + timedelta(hours=h), portfolio)

    # Buy below lower band
    s.on_tick(1, 9_700, base + timedelta(hours=4), portfolio)

    # Price rises above upper band (10000 + 2*82 = 10164)
    signals = s.on_tick(1, 10_300, base + timedelta(hours=5), portfolio)
    assert len(signals) == 1
    assert signals[0].action == "SELL"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/algo/test_strategies.py::test_bollinger_buys_at_lower_band -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement Bollinger Bands strategy**

```python
# src/algo/strategies/bollinger.py
"""Bollinger Bands strategy — buy at lower band, sell at upper band."""
import math
from datetime import datetime
from collections import defaultdict
from src.algo.models import Signal, Portfolio
from src.algo.strategies.base import Strategy


class BollingerStrategy(Strategy):
    """Buy when price touches lower Bollinger Band, sell at upper band."""

    name = "bollinger"

    def __init__(self, params: dict):
        self.params = params
        self.window: int = params.get("window", 24)
        self.num_std: float = params.get("num_std", 2.0)
        self.position_pct: float = params.get("position_pct", 0.02)
        self._history: dict[int, list[int]] = defaultdict(list)

    def on_tick(
        self, ea_id: int, price: int, timestamp: datetime, portfolio: Portfolio,
    ) -> list[Signal]:
        history = self._history[ea_id]
        history.append(price)

        if len(history) < self.window:
            return []

        window_prices = history[-self.window:]
        mean = sum(window_prices) / len(window_prices)
        variance = sum((p - mean) ** 2 for p in window_prices) / len(window_prices)
        std = math.sqrt(variance)

        upper_band = mean + self.num_std * std
        lower_band = mean - self.num_std * std

        signals = []

        if portfolio.holdings(ea_id) > 0:
            if price >= upper_band:
                signals.append(Signal(
                    action="SELL", ea_id=ea_id,
                    quantity=portfolio.holdings(ea_id),
                ))
        else:
            if price <= lower_band:
                buy_budget = int(portfolio.cash * self.position_pct)
                quantity = max(1, buy_budget // price) if price > 0 else 0
                if quantity > 0:
                    signals.append(Signal(action="BUY", ea_id=ea_id, quantity=quantity))

        return signals

    def param_grid(self) -> list[dict]:
        combos = []
        for window in [12, 24, 48]:
            for num_std in [1.0, 1.5, 2.0]:
                for position_pct in [0.01, 0.02, 0.05]:
                    combos.append({
                        "window": window,
                        "num_std": num_std,
                        "position_pct": position_pct,
                    })
        return combos
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/algo/test_strategies.py -v`
Expected: All PASSED

- [ ] **Step 5: Commit**

```bash
git add src/algo/strategies/bollinger.py tests/algo/test_strategies.py
git commit -m "feat(algo): add Bollinger Bands strategy"
```

---

### Task 10: Price History Scraper

**Files:**
- Create: `src/algo/scraper.py`
- Create: `tests/algo/test_scraper.py`

- [ ] **Step 1: Write failing test for price history parsing**

```python
# tests/algo/test_scraper.py
from src.algo.scraper import parse_price_history


def test_parse_price_history():
    """Test parsing fut.gg API response into (timestamp, price) tuples."""
    raw = {
        "history": [
            {"date": "2025-09-30T12:00:00Z", "price": 15000},
            {"date": "2025-09-30T13:00:00Z", "price": 15200},
            {"date": "2025-09-30T14:00:00Z", "price": 14800},
        ]
    }
    result = parse_price_history(12345, raw)
    assert len(result) == 3
    assert result[0] == (12345, "2025-09-30T12:00:00+00:00", 15000)
    assert result[1] == (12345, "2025-09-30T13:00:00+00:00", 15200)
    assert result[2] == (12345, "2025-09-30T14:00:00+00:00", 14800)


def test_parse_price_history_skips_bad_records():
    raw = {
        "history": [
            {"date": "2025-09-30T12:00:00Z", "price": 15000},
            {"bad_key": "missing fields"},
            {"date": "2025-09-30T14:00:00Z", "price": 14800},
        ]
    }
    result = parse_price_history(12345, raw)
    assert len(result) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/algo/test_scraper.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Implement scraper**

```python
# src/algo/scraper.py
"""One-time scraper to fetch full price history from fut.gg into the database."""
import asyncio
import logging
from datetime import datetime

import click
import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from src.config import DATABASE_URL
from src.server.db import Base

logger = logging.getLogger(__name__)

_MIN_REQUEST_INTERVAL = 0.25  # match existing rate limit
BASE_URL = "https://www.fut.gg"


def parse_price_history(ea_id: int, prices_data: dict) -> list[tuple[int, str, int]]:
    """Parse price history from fut.gg API response.

    Returns list of (ea_id, iso_timestamp_str, price) tuples.
    """
    results = []
    for point in prices_data.get("history", []):
        try:
            ts_str = point["date"].replace("Z", "+00:00")
            price = point["price"]
            results.append((ea_id, ts_str, price))
        except (KeyError, TypeError):
            continue
    return results


async def fetch_player_price_history(
    client: httpx.AsyncClient, ea_id: int,
) -> list[tuple[int, str, int]]:
    """Fetch full price history for a single player from fut.gg."""
    try:
        resp = await client.get(f"{BASE_URL}/api/fut/player-prices/26/{ea_id}/")
        resp.raise_for_status()
        data = resp.json()
        prices = data.get("data", {})
        return parse_price_history(ea_id, prices)
    except Exception as e:
        logger.error(f"Failed to fetch price history for {ea_id}: {e}")
        return []


async def scrape_all(db_url: str = DATABASE_URL, concurrency: int = 5):
    """Fetch price history for all known players and insert into price_history table.

    Reads ea_ids from the players table, fetches history from fut.gg,
    and bulk-inserts into price_history.
    """
    engine = create_async_engine(db_url)

    # Ensure table exists
    from src.algo.models_db import PriceHistory  # noqa: F401
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    # Get all active player ea_ids
    async with session_factory() as session:
        result = await session.execute(
            text("SELECT ea_id FROM players WHERE is_active = true")
        )
        ea_ids = [row[0] for row in result.fetchall()]

    logger.info(f"Scraping price history for {len(ea_ids)} players")

    sem = asyncio.Semaphore(concurrency)
    last_request_time = 0.0

    async def fetch_with_rate_limit(client: httpx.AsyncClient, ea_id: int):
        nonlocal last_request_time
        async with sem:
            now = asyncio.get_event_loop().time()
            wait = _MIN_REQUEST_INTERVAL - (now - last_request_time)
            if wait > 0:
                await asyncio.sleep(wait)
            last_request_time = asyncio.get_event_loop().time()
            return await fetch_player_price_history(client, ea_id)

    async with httpx.AsyncClient(timeout=30.0) as client:
        total = len(ea_ids)
        inserted = 0

        for i in range(0, total, concurrency):
            batch_ids = ea_ids[i : i + concurrency]
            tasks = [fetch_with_rate_limit(client, eid) for eid in batch_ids]
            results = await asyncio.gather(*tasks)

            # Bulk insert this batch
            rows = []
            for points in results:
                for ea_id, ts_str, price in points:
                    rows.append({
                        "ea_id": ea_id,
                        "timestamp": ts_str,
                        "price": price,
                    })

            if rows:
                async with session_factory() as session:
                    await session.execute(
                        text(
                            "INSERT INTO price_history (ea_id, timestamp, price) "
                            "VALUES (:ea_id, :timestamp, :price)"
                        ),
                        rows,
                    )
                    await session.commit()
                inserted += len(rows)

            logger.info(
                f"Progress: {min(i + concurrency, total)}/{total} players, "
                f"{inserted} price points inserted"
            )

    await engine.dispose()
    logger.info(f"Scrape complete: {inserted} total price points")


@click.command()
@click.option("--concurrency", default=5, help="Max concurrent API requests")
@click.option("--db-url", default=DATABASE_URL, help="Database URL")
def main(concurrency: int, db_url: str):
    """Scrape full price history from fut.gg for all known players."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    asyncio.run(scrape_all(db_url=db_url, concurrency=concurrency))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/algo/test_scraper.py -v`
Expected: 2 PASSED

- [ ] **Step 5: Commit**

```bash
git add src/algo/scraper.py tests/algo/test_scraper.py
git commit -m "feat(algo): add fut.gg price history scraper"
```

---

### Task 11: CLI — Engine Runner

**Files:**
- Modify: `src/algo/engine.py` — add CLI entry point
- Modify: `tests/algo/test_engine.py`

- [ ] **Step 1: Write failing test for full sweep with DB storage**

```python
# Append to tests/algo/test_engine.py
@pytest.mark.asyncio
async def test_run_and_save_results():
    """Test that run results can be saved to and loaded from DB."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from sqlalchemy import text
    from src.server.db import Base
    from src.algo.models_db import BacktestResult  # noqa: F401
    from src.algo.engine import save_result

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    result = {
        "strategy_name": "test",
        "params": "{}",
        "started_budget": 100_000,
        "final_budget": 110_000,
        "total_pnl": 10_000,
        "total_trades": 5,
        "win_rate": 0.6,
        "max_drawdown": 0.05,
        "sharpe_ratio": 1.2,
    }

    await save_result(session_factory, result)

    async with session_factory() as session:
        rows = await session.execute(text("SELECT * FROM backtest_results"))
        all_rows = rows.fetchall()
        assert len(all_rows) == 1
        assert all_rows[0].strategy_name == "test"

    await engine.dispose()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/algo/test_engine.py::test_run_and_save_results -v`
Expected: FAIL — `ImportError: cannot import name 'save_result'`

- [ ] **Step 3: Add save_result and CLI to engine.py**

```python
# Add to top of src/algo/engine.py imports
import asyncio
import click
from datetime import datetime as dt
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession, create_async_engine
from sqlalchemy import text
from src.config import DATABASE_URL
from src.server.db import Base


# Add after run_sweep function

async def save_result(session_factory: async_sessionmaker[AsyncSession], result: dict):
    """Save a single backtest result to the database."""
    from src.algo.models_db import BacktestResult  # noqa: F401
    async with session_factory() as session:
        await session.execute(
            text(
                "INSERT INTO backtest_results "
                "(strategy_name, params, started_budget, final_budget, total_pnl, "
                "total_trades, win_rate, max_drawdown, sharpe_ratio, run_at) "
                "VALUES (:strategy_name, :params, :started_budget, :final_budget, "
                ":total_pnl, :total_trades, :win_rate, :max_drawdown, :sharpe_ratio, :run_at)"
            ),
            {**result, "run_at": dt.utcnow().isoformat()},
        )
        await session.commit()


async def load_price_data(
    session_factory: async_sessionmaker[AsyncSession],
    min_price: int = 0,
    max_price: int = 0,
    min_data_points: int = 24,
) -> dict[int, list[tuple[dt, int]]]:
    """Load price history from DB into memory.

    Returns {ea_id: [(timestamp, price), ...]} sorted by timestamp.
    """
    async with session_factory() as session:
        result = await session.execute(
            text("SELECT ea_id, timestamp, price FROM price_history ORDER BY ea_id, timestamp")
        )
        rows = result.fetchall()

    from collections import defaultdict
    data: dict[int, list[tuple[dt, int]]] = defaultdict(list)
    for row in rows:
        ea_id, ts, price = row
        if isinstance(ts, str):
            ts = dt.fromisoformat(ts)
        if min_price and price < min_price:
            continue
        if max_price and price > max_price:
            continue
        data[ea_id].append((ts, price))

    # Filter out players with too few data points
    return {
        ea_id: points for ea_id, points in data.items()
        if len(points) >= min_data_points
    }


async def run_cli(
    strategy_name: str | None,
    all_strategies: bool,
    params_json: str | None,
    budget: int,
    db_url: str,
):
    """CLI entrypoint: load data, run strategies, save results."""
    from src.algo.strategies import discover_strategies

    engine = create_async_engine(db_url)
    from src.algo.models_db import BacktestResult  # noqa: F401
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    # Load price data
    logger.info("Loading price data from database...")
    price_data = await load_price_data(session_factory)
    logger.info(f"Loaded {len(price_data)} players with price history")

    if not price_data:
        logger.error("No price data found. Run the scraper first: python -m src.algo.scraper")
        await engine.dispose()
        return

    # Discover strategies
    available = discover_strategies()
    if not available:
        logger.error("No strategies found")
        await engine.dispose()
        return

    to_run: list[tuple[str, type]] = []
    if all_strategies:
        to_run = list(available.items())
    elif strategy_name:
        if strategy_name not in available:
            logger.error(f"Unknown strategy: {strategy_name}. Available: {list(available.keys())}")
            await engine.dispose()
            return
        to_run = [(strategy_name, available[strategy_name])]
    else:
        logger.error("Specify --strategy or --all")
        await engine.dispose()
        return

    # Run sweeps
    total_results = []
    for name, cls in to_run:
        if params_json:
            # Single param combo
            strategy = cls(json.loads(params_json))
            result = run_backtest(strategy, price_data, budget)
            await save_result(session_factory, result)
            total_results.append(result)
        else:
            results = run_sweep(cls, price_data, budget)
            for r in results:
                await save_result(session_factory, r)
            total_results.extend(results)

    # Print summary
    total_results.sort(key=lambda r: r["total_pnl"], reverse=True)
    logger.info(f"\nCompleted {len(total_results)} backtest runs")
    for r in total_results[:10]:
        logger.info(
            f"  {r['strategy_name']:20s} PnL: {r['total_pnl']:>10,} "
            f"Win: {r['win_rate']:.1%} Trades: {r['total_trades']}"
        )

    await engine.dispose()


@click.command()
@click.option("--strategy", "strategy_name", default=None, help="Strategy name to run")
@click.option("--all", "all_strategies", is_flag=True, help="Run all strategies")
@click.option("--params", "params_json", default=None, help="JSON params for single run")
@click.option("--budget", default=1_000_000, help="Starting budget in coins")
@click.option("--db-url", default=DATABASE_URL, help="Database URL")
def main(strategy_name, all_strategies, params_json, budget, db_url):
    """Run algo trading backtests."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    asyncio.run(run_cli(strategy_name, all_strategies, params_json, budget, db_url))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/algo/test_engine.py::test_run_and_save_results -v`
Expected: PASS

- [ ] **Step 5: Run all engine tests to verify nothing broke**

Run: `pytest tests/algo/test_engine.py -v`
Expected: All PASSED

- [ ] **Step 6: Commit**

```bash
git add src/algo/engine.py tests/algo/test_engine.py
git commit -m "feat(algo): add DB persistence and CLI entry point for engine"
```

---

### Task 12: CLI — Results Report

**Files:**
- Create: `src/algo/report.py`

- [ ] **Step 1: Implement the report CLI**

```python
# src/algo/report.py
"""CLI for viewing and comparing backtest results."""
import asyncio
import logging

import click
from rich.console import Console
from rich.table import Table
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from src.config import DATABASE_URL

console = Console(force_terminal=True)
logger = logging.getLogger(__name__)


async def show_results(
    db_url: str,
    strategy_name: str | None = None,
    sort_by: str = "total_pnl",
    limit: int = 50,
):
    """Query backtest_results and display a ranked table."""
    engine = create_async_engine(db_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    query = "SELECT * FROM backtest_results"
    params = {}
    if strategy_name:
        query += " WHERE strategy_name = :name"
        params["name"] = strategy_name

    valid_sorts = {"total_pnl", "win_rate", "sharpe_ratio", "max_drawdown", "total_trades"}
    if sort_by not in valid_sorts:
        sort_by = "total_pnl"

    order = "ASC" if sort_by == "max_drawdown" else "DESC"
    query += f" ORDER BY {sort_by} {order} LIMIT :limit"
    params["limit"] = limit

    async with session_factory() as session:
        result = await session.execute(text(query), params)
        rows = result.fetchall()

    await engine.dispose()

    if not rows:
        console.print("[yellow]No backtest results found.[/yellow]")
        return

    table = Table(title="Backtest Results", show_lines=True)
    table.add_column("#", style="dim", width=4)
    table.add_column("Strategy", style="cyan")
    table.add_column("Params", max_width=40)
    table.add_column("P&L", justify="right", style="green")
    table.add_column("Win Rate", justify="right")
    table.add_column("Trades", justify="right")
    table.add_column("Max DD", justify="right", style="red")
    table.add_column("Sharpe", justify="right")
    table.add_column("Final Budget", justify="right")

    for i, row in enumerate(rows, 1):
        pnl_color = "green" if row.total_pnl > 0 else "red"
        table.add_row(
            str(i),
            row.strategy_name,
            row.params[:40] if row.params else "",
            f"[{pnl_color}]{row.total_pnl:>+,}[/{pnl_color}]",
            f"{row.win_rate:.1%}",
            f"{row.total_trades:,}",
            f"{row.max_drawdown:.1%}",
            f"{row.sharpe_ratio:.2f}",
            f"{row.final_budget:>,}",
        )

    console.print(table)


@click.command()
@click.option("--strategy", default=None, help="Filter by strategy name")
@click.option("--sort", "sort_by", default="total_pnl", help="Sort column: total_pnl, win_rate, sharpe_ratio, max_drawdown, total_trades")
@click.option("--limit", default=50, help="Max rows to show")
@click.option("--db-url", default=DATABASE_URL, help="Database URL")
def main(strategy, sort_by, limit, db_url):
    """View and compare backtest results."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    asyncio.run(show_results(db_url, strategy, sort_by, limit))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify it imports cleanly**

Run: `python -c "from src.algo.report import show_results; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add src/algo/report.py
git commit -m "feat(algo): add backtest results report CLI"
```

---

### Task 13: Integration Test — Full Pipeline

**Files:**
- Create: `tests/algo/test_integration.py`

- [ ] **Step 1: Write end-to-end test**

```python
# tests/algo/test_integration.py
"""End-to-end test: scrape mock data → run strategies → check results."""
import pytest
from datetime import datetime, timedelta
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import text
from src.server.db import Base


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    from src.algo.models_db import PriceHistory, BacktestResult  # noqa: F401
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    yield session_factory
    await engine.dispose()


async def seed_price_data(session_factory, num_players=3, num_hours=200):
    """Insert synthetic price data with mean-reverting patterns."""
    import math
    base = datetime(2026, 1, 1)
    async with session_factory() as session:
        for pid in range(1, num_players + 1):
            base_price = 10_000 * pid
            for h in range(num_hours):
                # Sine wave creates mean-reverting price action
                price = int(base_price + base_price * 0.15 * math.sin(h / 12 * math.pi))
                await session.execute(
                    text(
                        "INSERT INTO price_history (ea_id, timestamp, price) "
                        "VALUES (:ea_id, :timestamp, :price)"
                    ),
                    {"ea_id": pid, "timestamp": (base + timedelta(hours=h)).isoformat(), "price": price},
                )
        await session.commit()


@pytest.mark.asyncio
async def test_full_pipeline(db):
    from src.algo.engine import load_price_data, run_sweep, save_result
    from src.algo.strategies.mean_reversion import MeanReversionStrategy

    # Seed data
    await seed_price_data(db, num_players=3, num_hours=200)

    # Load
    price_data = await load_price_data(db, min_data_points=10)
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
    from src.algo.engine import run_backtest
    from src.algo.strategies import discover_strategies

    await seed_price_data(db, num_players=2, num_hours=100)
    from src.algo.engine import load_price_data
    price_data = await load_price_data(db, min_data_points=10)

    strategies = discover_strategies()
    assert len(strategies) >= 4, f"Expected 4+ strategies, found {list(strategies.keys())}"

    for name, cls in strategies.items():
        # Run with first param combo only
        grid = cls({}).param_grid()
        strategy = cls(grid[0])
        result = run_backtest(strategy, price_data, budget=100_000)
        assert result["strategy_name"] == name
        assert result["started_budget"] == 100_000
```

- [ ] **Step 2: Run integration tests**

Run: `pytest tests/algo/test_integration.py -v`
Expected: 2 PASSED

- [ ] **Step 3: Run all algo tests together**

Run: `pytest tests/algo/ -v`
Expected: All PASSED (should be ~20 tests)

- [ ] **Step 4: Commit**

```bash
git add tests/algo/test_integration.py
git commit -m "test(algo): add full pipeline integration tests"
```

---

### Task 14: Add `__main__.py` Entry Points

**Files:**
- Create: `src/algo/__main__.py`

- [ ] **Step 1: Create module entry point that routes to subcommands**

```python
# src/algo/__main__.py
"""Entry point for python -m src.algo <command>."""
import sys


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    # Remove the subcommand from argv so Click doesn't see it
    sys.argv = [sys.argv[0]] + sys.argv[2:]

    if cmd == "scrape":
        from src.algo.scraper import main as scrape_main
        scrape_main()
    elif cmd == "run":
        from src.algo.engine import main as engine_main
        engine_main()
    elif cmd == "report":
        from src.algo.report import main as report_main
        report_main()
    else:
        print("Usage: python -m src.algo <command>")
        print()
        print("Commands:")
        print("  scrape   Fetch full price history from fut.gg")
        print("  run      Run backtests (--strategy NAME | --all)")
        print("  report   View backtest results (--strategy NAME, --sort COLUMN)")
        sys.exit(1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify help output works**

Run: `python -m src.algo`
Expected: Usage message with scrape/run/report commands listed

Run: `python -m src.algo run --help`
Expected: Click help output with --strategy, --all, --budget options

- [ ] **Step 3: Commit**

```bash
git add src/algo/__main__.py
git commit -m "feat(algo): add __main__.py CLI entry point"
```
