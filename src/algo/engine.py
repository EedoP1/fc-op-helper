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

    # Single walk through timeline
    for ts in sorted_timestamps:
        for ea_id, price in timeline[ts]:
            for strategy, portfolio in combos:
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


def _worker_run_combos(
    sorted_timeline: list[tuple[datetime, list[tuple[int, int]]]],
    last_prices: dict[int, tuple[datetime, int]],
    combo_specs: list[tuple[str, dict]],
    budget: int,
) -> list[dict]:
    """Worker function for parallel sweep. Runs in a subprocess.

    Args:
        sorted_timeline: [(timestamp, [(ea_id, price), ...]), ...] pre-sorted.
        last_prices: {ea_id: (timestamp, price)} for force-selling.
        combo_specs: [(strategy_module_path, params), ...] to instantiate.
        budget: Starting coin balance for each combo.
    """
    from src.algo.strategies import discover_strategies
    available = discover_strategies()

    # Instantiate combos in this process
    combos = []
    for strategy_name, params in combo_specs:
        cls = available[strategy_name]
        strategy = cls(params)
        portfolio = Portfolio(cash=budget)
        combos.append((strategy, portfolio))

    # Walk timeline
    for ts, ticks in sorted_timeline:
        for ea_id, price in ticks:
            for strategy, portfolio in combos:
                signals = strategy.on_tick(ea_id, price, ts, portfolio)
                for signal in signals:
                    if signal.action == "BUY":
                        portfolio.buy(signal.ea_id, signal.quantity, price, ts)
                    elif signal.action == "SELL":
                        portfolio.sell(signal.ea_id, signal.quantity, price, ts)

    # Force-sell and compute metrics
    results = []
    for strategy, portfolio in combos:
        for pos in list(portfolio.positions):
            if pos.ea_id in last_prices:
                ts, price = last_prices[pos.ea_id]
                portfolio.sell(pos.ea_id, pos.quantity, price, ts)

        total_pnl = portfolio.cash - budget
        trades = portfolio.trades
        total_trades = len(trades)
        winning = sum(1 for t in trades if t.net_profit > 0)
        win_rate = winning / total_trades if total_trades > 0 else 0.0

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
        })

    return results


def run_sweep_parallel(
    strategy_classes: list[type],
    price_data: dict[int, list[tuple[datetime, int]]],
    budget: int = 1_000_000,
    max_workers: int | None = None,
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
        grid = sample.param_grid()
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
            pool.submit(_worker_run_combos, sorted_timeline, last_prices, chunk, budget)
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
            {**result, "run_at": dt.now(tz=__import__('datetime').timezone.utc).isoformat()},
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

    logger.info("Loading price data from database...")
    price_data = await load_price_data(session_factory)
    logger.info(f"Loaded {len(price_data)} players with price history")

    if not price_data:
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
        result = run_backtest(strategy, price_data, budget)
        await save_result(session_factory, result)
        total_results.append(result)
    else:
        # Sweep mode (--all or --strategy without --params): parallel
        classes = [cls for _, cls in to_run]
        results = run_sweep_parallel(classes, price_data, budget)
        for r in results:
            await save_result(session_factory, r)
        total_results.extend(results)

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
