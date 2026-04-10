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

    # Step 6: Replay all ticks — warm up strategy state on history,
    # but only keep signals from the LAST (current) tick.
    # The strategy needs the full price history to compute trends,
    # but only the current market state should produce actionable signals.
    last_ts = sorted_timestamps[-1]
    all_signals: list[tuple[datetime, str, int, int, int]] = []
    for ts in sorted_timestamps:
        ticks = timeline[ts]
        results = engine.process_tick(ticks, ts)
        if ts == last_ts:
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
