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
from src.server.algo_engine import AlgoSignalEngine


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    yield sf
    await engine.dispose()


def _generate_promo_data(num_cards=12, hours=300):
    """Same data generator as test_algo_runner.py but returns raw tuples for DB seeding."""
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
    """Run signal engine in-memory (same logic as DB runner) and return deduped signal list."""
    # Build price_data dict from flat snapshot list
    price_data: dict[int, list[tuple[datetime, int]]] = defaultdict(list)
    for ea_id, ts, price in snapshots:
        price_data[ea_id].append((ts, price))
    for ea_id in price_data:
        price_data[ea_id].sort(key=lambda x: x[0])

    engine = AlgoSignalEngine(budget=budget, created_at_map=created_at_map)

    timeline: dict[datetime, list[tuple[int, int]]] = defaultdict(list)
    for ea_id, points in price_data.items():
        for ts, price in points:
            timeline[ts].append((ea_id, price))

    sorted_ts = sorted(timeline.keys())

    all_signals: list[tuple[str, int, int]] = []
    # Dedup by (ea_id, action) — same logic as run_signal_engine's dedup step
    seen: set[tuple[int, str]] = set()
    for ts in sorted_ts:
        ticks = timeline[ts]
        results = engine.process_tick(ticks, ts)
        for action, ea_id, quantity, price in results:
            if (ea_id, action) in seen:
                continue
            seen.add((ea_id, action))
            all_signals.append((action, ea_id, quantity))

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

    # Run backtester (in-memory, same logic as DB runner)
    bt_signal_list = _run_backtester(snapshots, created_at_map)

    assert len(bt_signal_list) > 0, "Backtester should produce signals"
    assert db_signal_list == bt_signal_list, (
        f"DB runner signal mismatch.\n"
        f"Backtester ({len(bt_signal_list)}): {bt_signal_list[:10]}\n"
        f"DB runner  ({len(db_signal_list)}): {db_signal_list[:10]}"
    )
