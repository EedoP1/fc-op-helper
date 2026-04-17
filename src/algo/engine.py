"""Backtesting engine — walks historical price data and executes strategy signals."""
import asyncio
import json
import math
import logging
import os
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime as dt
from datetime import datetime

import click
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.algo.models import Portfolio
from src.config import DATABASE_URL
from src.server.db import Base

logger = logging.getLogger(__name__)

EA_TAX_RATE = 0.05


def run_backtest(
    strategy,
    price_data: dict[int, list[tuple[datetime, int]]],
    budget: int = 1_000_000,
    futbin_to_ea: dict[int, int] | None = None,
    created_at_map: dict[int, datetime] | None = None,
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

    # Tell strategy which IDs exist at the start of the data window
    if sorted_timestamps:
        first_ts = sorted_timestamps[0]
        existing_ids = {ea_id for ea_id, _ in timeline[first_ts]}
        strategy.set_existing_ids(existing_ids)

    if created_at_map:
        strategy.set_created_at_map(created_at_map)

    # Walk through time
    for ts in sorted_timestamps:
        ticks = timeline[ts]
        signals = strategy.on_tick_batch(ticks, ts, portfolio)
        for signal in signals:
            # Find price for this signal's ea_id
            sig_price = next((p for eid, p in ticks if eid == signal.ea_id), 0)
            if signal.action == "BUY":
                portfolio.buy(signal.ea_id, signal.quantity, sig_price, ts)
            elif signal.action == "SELL":
                portfolio.sell(signal.ea_id, signal.quantity, sig_price, ts)

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
        "trades": trade_log,
    }


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


def run_sweep_single_pass(
    strategy_classes: list[type],
    price_data: dict[int, list[tuple[datetime, int]]],
    budget: int = 1_000_000,
) -> list[dict]:
    """Run all strategy+param combos in a single timeline walk.

    Builds the timeline once and walks it once for all combos. Each combo
    gets its own independent Portfolio and strategy instance.

    Args:
        strategy_classes: List of Strategy classes to sweep.
        price_data: {ea_id: [(timestamp, price), ...]} sorted by timestamp.
        budget: Starting coin balance for each combo.

    Returns:
        List of result dicts, one per strategy+param combo.
    """
    # Build timeline ONCE
    timeline: dict[datetime, list[tuple[int, int]]] = defaultdict(list)
    for ea_id, points in price_data.items():
        for ts, price in points:
            timeline[ts].append((ea_id, price))

    sorted_timestamps = sorted(timeline.keys())

    # Instantiate all combos: (strategy_instance, portfolio)
    combos: list[tuple] = []
    for cls in strategy_classes:
        sample = cls({})
        grid = sample.param_grid()
        for params in grid:
            strategy = cls(params)
            portfolio = Portfolio(cash=budget)
            combos.append((strategy, portfolio))

    logger.info(f"Running {len(combos)} strategy combos in single pass...")

    # Tell strategies which IDs exist at the start
    if sorted_timestamps:
        existing_ids = {ea_id for ea_id, _ in timeline[sorted_timestamps[0]]}
        for strategy, _ in combos:
            strategy.set_existing_ids(existing_ids)

    # Single walk through timeline
    for ts in sorted_timestamps:
        ticks = timeline[ts]
        for strategy, portfolio in combos:
            signals = strategy.on_tick_batch(ticks, ts, portfolio)
            for signal in signals:
                sig_price = next((p for eid, p in ticks if eid == signal.ea_id), 0)
                if signal.action == "BUY":
                    portfolio.buy(signal.ea_id, signal.quantity, sig_price, ts)
                elif signal.action == "SELL":
                    portfolio.sell(signal.ea_id, signal.quantity, sig_price, ts)

    # Force-sell open positions at last known price
    last_prices: dict[int, tuple[datetime, int]] = {}
    for ea_id, points in price_data.items():
        if points:
            last_prices[ea_id] = points[-1]

    results = []
    for strategy, portfolio in combos:
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

        results.append({
            "strategy_name": strategy.name,
            "params": json.dumps(strategy.params) if hasattr(strategy, "params") else "{}",
            "started_budget": budget,
            "final_budget": portfolio.cash,
            "total_pnl": total_pnl,
            "total_trades": total_trades,
            "win_rate": win_rate,
            "max_drawdown": max_drawdown,
            "sharpe_ratio": sharpe,
        })

    return results


MAX_SELLS_PER_DAY = 500


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
    from src.algo.strategies import discover_strategies
    available = discover_strategies()

    # Instantiate combos in this process
    combos = []
    for strategy_name, params in combo_specs:
        cls = available[strategy_name]
        strategy = cls(params)
        portfolio = Portfolio(cash=budget)
        # Track sells per day: {date_str: count}
        sells_today: dict[str, int] = defaultdict(int)
        combos.append((strategy, portfolio, sells_today))

    # Tell strategies which IDs exist at the start
    if sorted_timeline:
        existing_ids = {ea_id for ea_id, _ in sorted_timeline[0][1]}
        for strategy, _, _ in combos:
            strategy.set_existing_ids(existing_ids)
            if created_at_map:
                strategy.set_created_at_map(created_at_map)

    # Walk timeline
    for ts, ticks in sorted_timeline:
        day_key = ts.strftime("%Y-%m-%d")
        for strategy, portfolio, sells_today in combos:
            signals = strategy.on_tick_batch(ticks, ts, portfolio)
            for signal in signals:
                sig_price = next((p for eid, p in ticks if eid == signal.ea_id), 0)
                if signal.action == "BUY":
                    portfolio.buy(signal.ea_id, signal.quantity, sig_price, ts)
                elif signal.action == "SELL":
                    if sells_today[day_key] < MAX_SELLS_PER_DAY:
                        portfolio.sell(signal.ea_id, signal.quantity, sig_price, ts)
                        sells_today[day_key] += signal.quantity

    # Force-sell and compute metrics
    results = []
    for strategy, portfolio, sells_today in combos:
        for pos in list(portfolio.positions):
            if pos.ea_id in last_prices:
                ts, price = last_prices[pos.ea_id]
                portfolio.sell(pos.ea_id, pos.quantity, price, ts)

        total_pnl = portfolio.cash - budget
        trades = portfolio.trades
        total_trades = len(trades)
        winning = sum(1 for t in trades if t.net_profit > 0)
        win_rate = winning / total_trades if total_trades > 0 else 0.0

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

        results.append({
            "strategy_name": strategy.name,
            "params": json.dumps(strategy.params) if hasattr(strategy, "params") else "{}",
            "started_budget": budget,
            "final_budget": portfolio.cash,
            "total_pnl": total_pnl,
            "total_trades": total_trades,
            "win_rate": win_rate,
            "max_drawdown": _calc_max_drawdown(portfolio.balance_history, budget),
            "sharpe_ratio": _calc_sharpe_ratio(trades),
            "trades": trade_log,
        })

    return results


def run_sweep_parallel(
    strategy_classes: list[type],
    price_data: dict[int, list[tuple[datetime, int]]],
    budget: int = 1_000_000,
    max_workers: int | None = None,
    futbin_to_ea: dict[int, int] | None = None,
    created_at_map: dict[int, datetime] | None = None,
    use_hourly_grid: bool = False,
) -> list[dict]:
    """Run all strategy+param combos in parallel across CPU cores.

    Builds the timeline once, then splits combos across worker processes.
    Each worker walks the full timeline for its subset of combos.

    Args:
        strategy_classes: List of Strategy classes to sweep.
        price_data: {ea_id: [(timestamp, price), ...]} sorted by timestamp.
        budget: Starting coin balance for each combo.
        max_workers: Number of processes (default: cpu_count).
    """
    if max_workers is None:
        max_workers = os.cpu_count() or 4

    # Build timeline ONCE
    timeline: dict[datetime, list[tuple[int, int]]] = defaultdict(list)
    for ea_id, points in price_data.items():
        for ts, price in points:
            timeline[ts].append((ea_id, price))

    sorted_timeline = [(ts, timeline[ts]) for ts in sorted(timeline.keys())]

    # Pre-compute last prices for force-selling
    last_prices: dict[int, tuple[datetime, int]] = {}
    for ea_id, points in price_data.items():
        if points:
            last_prices[ea_id] = points[-1]

    # Build combo specs: (strategy_name, params) — picklable
    combo_specs: list[tuple[str, dict]] = []
    for cls in strategy_classes:
        sample = cls({})
        grid = sample.param_grid_hourly() if use_hourly_grid and hasattr(sample, "param_grid_hourly") else sample.param_grid()
        for params in grid:
            combo_specs.append((cls.name, params))

    # Split combos across workers
    n_workers = min(max_workers, len(combo_specs))
    chunks = [[] for _ in range(n_workers)]
    for i, spec in enumerate(combo_specs):
        chunks[i % n_workers].append(spec)

    logger.info(
        f"Running {len(combo_specs)} combos across {n_workers} workers..."
    )

    # Dispatch to process pool
    all_results = []
    with ProcessPoolExecutor(max_workers=n_workers) as pool:
        futures = [
            pool.submit(_worker_run_combos, sorted_timeline, last_prices, chunk, budget, futbin_to_ea, created_at_map)
            for chunk in chunks
        ]
        for future in futures:
            all_results.extend(future.result())

    return all_results


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


_BIGINT_MAX = 2**63 - 1
_BIGINT_MIN = -(2**63)


def _clamp_bigint(value: int) -> int:
    """Clamp a Python int to PostgreSQL bigint range."""
    return max(_BIGINT_MIN, min(_BIGINT_MAX, value))


async def save_result(session_factory: async_sessionmaker[AsyncSession], result: dict):
    """Save a single backtest result to the database."""
    from src.algo.models_db import BacktestResult  # noqa: F401
    row = {
        **result,
        "final_budget": _clamp_bigint(result["final_budget"]),
        "total_pnl": _clamp_bigint(result["total_pnl"]),
        "run_at": dt.now(tz=__import__('datetime').timezone.utc).isoformat(),
    }
    async with session_factory() as session:
        await session.execute(
            text(
                "INSERT INTO backtest_results "
                "(strategy_name, params, started_budget, final_budget, total_pnl, "
                "total_trades, win_rate, max_drawdown, sharpe_ratio, run_at) "
                "VALUES (:strategy_name, :params, :started_budget, :final_budget, "
                ":total_pnl, :total_trades, :win_rate, :max_drawdown, :sharpe_ratio, :run_at)"
            ),
            row,
        )
        await session.commit()


async def load_price_data(
    session_factory: async_sessionmaker[AsyncSession],
    min_price: int = 0,
    max_price: int = 0,
    min_data_points: int = 24,
    days: int = 0,
) -> dict[int, list[tuple[dt, int]]]:
    """Load price history from DB into memory.

    Returns {ea_id: [(timestamp, price), ...]} sorted by timestamp.
    """
    if days > 0:
        cutoff = dt.utcnow() - __import__('datetime').timedelta(days=days)
        # Align cutoff to previous Sunday so existing_ids catches full weeks
        days_since_sunday = (cutoff.weekday() + 1) % 7  # Mon=0 -> 1, Sun=6 -> 0
        cutoff = cutoff - __import__('datetime').timedelta(days=days_since_sunday)
        cutoff = cutoff.replace(hour=0, minute=0, second=0, microsecond=0)
        query = text(
            "SELECT futbin_id, timestamp, price FROM price_history "
            "WHERE timestamp >= :cutoff AND futbin_id IS NOT NULL "
            "ORDER BY futbin_id, timestamp"
        )
        params = {"cutoff": cutoff}
    else:
        query = text(
            "SELECT futbin_id, timestamp, price FROM price_history "
            "WHERE futbin_id IS NOT NULL ORDER BY futbin_id, timestamp"
        )
        params = {}

    async with session_factory() as session:
        result = await session.execute(query, params)
        rows = result.fetchall()

    if days > 0:
        ea_query = text(
            "SELECT DISTINCT futbin_id, ea_id FROM price_history "
            "WHERE timestamp >= :cutoff AND futbin_id IS NOT NULL"
        )
        ea_params = {"cutoff": params["cutoff"]}
    else:
        ea_query = text(
            "SELECT DISTINCT futbin_id, ea_id FROM price_history "
            "WHERE futbin_id IS NOT NULL"
        )
        ea_params = {}

    async with session_factory() as session:
        ea_rows = await session.execute(ea_query, ea_params)
        futbin_to_ea = {r[0]: r[1] for r in ea_rows.fetchall()}

    data: dict[int, list[tuple[dt, int]]] = defaultdict(list)
    for row in rows:
        futbin_id, ts, price = row
        if isinstance(ts, str):
            ts = dt.fromisoformat(ts)
        if min_price and price < min_price:
            continue
        if max_price and price > max_price:
            continue
        data[futbin_id].append((ts, price))

    filtered = {
        fid: points for fid, points in data.items()
        if len(points) >= min_data_points
    }
    return filtered, futbin_to_ea


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


async def run_cli(
    strategy_name: str | None,
    all_strategies: bool,
    params_json: str | None,
    budget: int,
    db_url: str,
    days: int = 0,
    min_price: int = 0,
    max_price: int = 0,
    source: str = "price_history",
):
    """CLI entrypoint: load data, run strategies, save results."""
    from src.algo.strategies import discover_strategies

    engine = create_async_engine(db_url)
    from src.algo.models_db import BacktestResult  # noqa: F401
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

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

    if not price_data:
        if use_market_snapshots:
            logger.error("No market snapshot data found. Is the scanner running?")
        else:
            logger.error("No price data found. Run the scraper first: python -m src.algo.scraper")
        await engine.dispose()
        return

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

    total_results = []
    if params_json and len(to_run) == 1:
        # Single strategy with explicit params: use direct run_backtest
        name, cls = to_run[0]
        strategy = cls(json.loads(params_json))
        result = run_backtest(strategy, price_data, budget, futbin_to_ea=futbin_to_ea, created_at_map=created_at_map)
        total_results.append(result)
    else:
        # Sweep mode (--all or --strategy without --params): parallel
        classes = [cls for _, cls in to_run]
        total_results = run_sweep_parallel(
            classes, price_data, budget,
            futbin_to_ea=futbin_to_ea,
            created_at_map=created_at_map,
            use_hourly_grid=use_market_snapshots,
        )

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

    # Sort by sharpe ratio
    total_results.sort(key=lambda r: r["sharpe_ratio"], reverse=True)

    # Save full results with trades to JSON file
    try:
        results_file = "backtest_results.json"
        with open(results_file, "w") as f:
            json.dump(total_results, f, indent=2, default=str)
        logger.info(f"Full results saved to {results_file}")
    except Exception as e:
        logger.warning(f"JSON save failed: {e}")

    # Print summary table
    print(f"\n{'#':<4} {'Strategy':<18} {'PnL':>12} {'Params':<50} {'Win%':>7} {'Trades':>7} {'Sharpe':>8} {'MaxDD':>8}")
    print("-" * 120)
    for i, r in enumerate(total_results[:20], 1):
        params = json.loads(r["params"]) if isinstance(r["params"], str) else r["params"]
        params_str = ", ".join(f"{k}={v}" for k, v in params.items())
        print(f"{i:<4} {r['strategy_name']:<18} {r['total_pnl']:>+12,} {params_str:<50} {r['win_rate']:>6.1%} {r['total_trades']:>7,} {r['sharpe_ratio']:>8.3f} {r['max_drawdown']:>7.1%}")

    # Print trade log for top 3 combos
    for rank, r in enumerate(total_results[:3], 1):
        trades = r.get("trades", [])
        if not trades:
            continue
        params = json.loads(r["params"]) if isinstance(r["params"], str) else r["params"]
        params_str = ", ".join(f"{k}={v}" for k, v in params.items())
        print(f"\n{'='*80}")
        print(f"#{rank} {r['strategy_name']} ({params_str})")
        print(f"{'='*80}")
        print(f"{'Player':<30} {'EA ID':>8} {'Qty':>5} {'Buy':>8} {'Sell':>8} {'Profit':>10} {'Buy Date':<12} {'Sell Date':<12}")
        print("-" * 112)
        # Show top 20 trades by profit
        sorted_trades = sorted(trades, key=lambda t: t["net_profit"], reverse=True)
        for t in sorted_trades[:20]:
            fid = t.get("futbin_id", t["ea_id"])
            ea_id = t.get("ea_id", 0)
            name = player_names.get(fid) or player_names.get(ea_id) or f"#{fid}"
            buy_date = t["buy_time"][:10]
            sell_date = t["sell_time"][:10]
            print(f"{name[:29]:<30} {ea_id:>8} {t['qty']:>5} {t['buy_price']:>8,} {t['sell_price']:>8,} {t['net_profit']:>10,} {buy_date:<12} {sell_date:<12}")
        losing = sorted(trades, key=lambda t: t["net_profit"])
        if losing[0]["net_profit"] < 0:
            print(f"\n  Worst trades:")
            for t in losing[:5]:
                fid = t.get("futbin_id", t["ea_id"])
                ea_id = t.get("ea_id", 0)
                name = player_names.get(fid) or player_names.get(ea_id) or f"#{fid}"
                buy_date = t["buy_time"][:10]
                sell_date = t["sell_time"][:10]
                print(f"  {name[:29]:<30} {ea_id:>8} {t['qty']:>5} {t['buy_price']:>8,} {t['sell_price']:>8,} {t['net_profit']:>10,} {buy_date:<12} {sell_date:<12}")

    # Also save to JSON file (without trades for smaller file)
    try:
        summary_file = "backtest_summary.json"
        summary = [{k: v for k, v in r.items() if k != "trades"} for r in total_results]
        with open(summary_file, "w") as f:
            json.dump(summary, f, indent=2, default=str)
        logger.info(f"Results saved to {results_file}")
    except Exception as e:
        logger.warning(f"JSON save failed: {e}")

    # Then try DB save (non-fatal)
    for r in total_results:
        try:
            await save_result(session_factory, r)
        except Exception as e:
            logger.warning(f"DB save failed for {r['strategy_name']}: {e}")

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
@click.option("--days", default=0, help="Only use last N days of price data (0 = all)")
@click.option("--min-price", default=0, help="Only include cards with prices >= this value")
@click.option("--max-price", default=0, help="Only include cards with prices <= this value")
@click.option("--source", default="price_history", type=click.Choice(["price_history", "market_snapshots"]),
              help="Data source: price_history (FUTBIN daily) or market_snapshots (real hourly)")
@click.option("--db-url", default=DATABASE_URL, help="Database URL")
@click.option("--verbose", "-v", is_flag=True, help="Enable DEBUG logging for strategy decisions")
def main(strategy_name, all_strategies, params_json, budget, days, min_price, max_price, source, db_url, verbose):
    """Run algo trading backtests."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    if verbose:
        logging.getLogger("src.algo.strategies").setLevel(logging.DEBUG)
        logging.getLogger("src.algo.models").setLevel(logging.DEBUG)
    asyncio.run(run_cli(strategy_name, all_strategies, params_json, budget, db_url, days=days, min_price=min_price, max_price=max_price, source=source))


if __name__ == "__main__":
    main()
