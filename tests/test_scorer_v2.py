"""
Tests for scorer_v2: listing-observation-based scoring using D-10 formula.

Tests verify:
- expected_profit_per_hour formula correctness
- Margin selection picks the one maximizing expected_profit_per_hour
- Bootstrap threshold guard (BOOTSTRAP_MIN_OBSERVATIONS)
- No-observations guard
- Insufficient OP observations guard
- Return dict shape
"""
from __future__ import annotations

import pytest
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from src.server.db import create_engine_and_tables
from src.server.models_db import ListingObservation
from src.server.scorer_v2 import score_player_v2
from src.config import BOOTSTRAP_MIN_OBSERVATIONS, MIN_OP_OBSERVATIONS, MIN_TOTAL_RESOLVED_OBSERVATIONS, MIN_OBSERVATION_HISTORY_DAYS


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
async def db():
    """In-memory SQLite engine + session factory."""
    engine, session_factory = await create_engine_and_tables("sqlite+aiosqlite:///:memory:")
    yield engine, session_factory
    await engine.dispose()


def make_observation(
    ea_id: int,
    fingerprint: str,
    buy_now_price: int,
    market_price_at_obs: int,
    outcome: str | None,
    first_seen_at: datetime,
    last_seen_at: datetime | None = None,
    scan_count: int = 1,
    resolved_at: datetime | None = None,
) -> ListingObservation:
    """Build a ListingObservation for test seeding."""
    return ListingObservation(
        fingerprint=fingerprint,
        ea_id=ea_id,
        buy_now_price=buy_now_price,
        market_price_at_obs=market_price_at_obs,
        first_seen_at=first_seen_at,
        last_seen_at=last_seen_at or first_seen_at,
        scan_count=scan_count,
        outcome=outcome,
        resolved_at=resolved_at or (first_seen_at if outcome is not None else None),
    )


# ── Tests ─────────────────────────────────────────────────────────────────────

async def test_expected_profit_per_hour(db):
    """
    Given 20 resolved observations spanning 4 days at margin 10%:
    8 OP listings (5 sold, 3 expired), buy_price=10000.
    Expected:
      op_sell_rate = 5/8 = 0.625
      sell_price = 11000, ea_tax = 550, net_profit = 450
      expected_profit_per_hour = 450 * 0.625 = 281.25
    """
    _, session_factory = db
    ea_id = 1001
    buy_price = 10_000
    market_price = 10_000
    op_threshold = int(market_price * 1.10)  # 11000

    now = datetime.utcnow()
    # Span 4 days to satisfy MIN_OBSERVATION_HISTORY_DAYS=3
    t_start = now - timedelta(days=4)

    observations = []
    # 5 OP sold
    for i in range(5):
        observations.append(make_observation(
            ea_id=ea_id,
            fingerprint=f"op-sold-{i}",
            buy_now_price=op_threshold,
            market_price_at_obs=market_price,
            outcome="sold",
            first_seen_at=t_start + timedelta(hours=i),
            last_seen_at=t_start + timedelta(hours=i, minutes=30),
            resolved_at=t_start + timedelta(hours=i, minutes=30),
        ))
    # 3 OP expired
    for i in range(3):
        observations.append(make_observation(
            ea_id=ea_id,
            fingerprint=f"op-expired-{i}",
            buy_now_price=op_threshold,
            market_price_at_obs=market_price,
            outcome="expired",
            first_seen_at=t_start + timedelta(hours=5 + i),
            last_seen_at=t_start + timedelta(hours=5 + i, minutes=30),
            resolved_at=t_start + timedelta(hours=5 + i, minutes=30),
        ))
    # 12 non-OP sold — last one anchors latest last_seen_at to t_start + 10h
    for i in range(12):
        # spread evenly, last entry at t_start + 9.5h (last_seen_at = 10h)
        h = i * (9.5 / 11)
        observations.append(make_observation(
            ea_id=ea_id,
            fingerprint=f"non-op-{i}",
            buy_now_price=market_price - 100,  # below OP threshold
            market_price_at_obs=market_price,
            outcome="sold",
            first_seen_at=t_start + timedelta(hours=h),
            last_seen_at=t_start + timedelta(hours=h + 0.5),
            resolved_at=t_start + timedelta(hours=h + 0.5),
        ))

    async with session_factory() as session:
        for obs in observations:
            session.add(obs)
        await session.commit()

        result = await score_player_v2(ea_id=ea_id, session=session, buy_price=buy_price)

    assert result is not None
    assert result["margin_pct"] == 10
    assert result["op_sold"] == 5
    assert result["op_total"] == 8
    assert abs(result["op_sell_rate"] - 0.625) < 0.001
    assert result["net_profit"] == 450  # 11000 - 550 - 10000
    assert abs(result["expected_profit_per_hour"] - 281.25) < 0.1


async def test_margin_selection(db):
    """
    Given observations where margin 10% produces higher expected_profit_per_hour
    than margin 20%, the scorer picks 10%.

    Margin 10% only obs: 10 sold, 5 expired → strong sell-through rate.
    Margin 20% obs: only 2 sold (below MIN_OP_OBSERVATIONS=3) → tier skipped.
    Result: only margin 10% is viable, expected_profit_per_hour = 450 * (12/17) ≈ 317.
    (The 2 sold at 20% also count at the 10% threshold since 1.20 >= 1.10.)
    """
    _, session_factory = db
    ea_id = 1002
    buy_price = 10_000
    market_price = 10_000

    now = datetime.utcnow()
    # Span 4 days to satisfy MIN_OBSERVATION_HISTORY_DAYS=3
    t_start = now - timedelta(days=4)

    observations = []
    # 10 sold at exactly 10% margin (priced at market * 1.10, below 20% threshold)
    for i in range(10):
        observations.append(make_observation(
            ea_id=ea_id,
            fingerprint=f"m10-sold-{i}",
            buy_now_price=int(market_price * 1.10),
            market_price_at_obs=market_price,
            outcome="sold",
            first_seen_at=t_start + timedelta(hours=i),
            last_seen_at=t_start + timedelta(hours=i, minutes=30),
            resolved_at=t_start + timedelta(hours=i, minutes=30),
        ))
    # 5 expired at 10% margin
    for i in range(5):
        observations.append(make_observation(
            ea_id=ea_id,
            fingerprint=f"m10-expired-{i}",
            buy_now_price=int(market_price * 1.10),
            market_price_at_obs=market_price,
            outcome="expired",
            first_seen_at=t_start + timedelta(hours=10 + i),
            last_seen_at=t_start + timedelta(hours=10 + i, minutes=30),
            resolved_at=t_start + timedelta(hours=10 + i, minutes=30),
        ))
    # Only 2 sold at 20% margin → op_total=2 < MIN_OP_OBSERVATIONS=3 → tier skipped
    for i in range(2):
        observations.append(make_observation(
            ea_id=ea_id,
            fingerprint=f"m20-sold-{i}",
            buy_now_price=int(market_price * 1.20),
            market_price_at_obs=market_price,
            outcome="sold",
            first_seen_at=t_start + timedelta(hours=15 + i),
            last_seen_at=t_start + timedelta(hours=15 + i, minutes=30),
            resolved_at=t_start + timedelta(hours=15 + i, minutes=30),
        ))
    # Non-OP filler to reach BOOTSTRAP_MIN_OBSERVATIONS
    for i in range(5):
        observations.append(make_observation(
            ea_id=ea_id,
            fingerprint=f"filler-{i}",
            buy_now_price=market_price - 500,
            market_price_at_obs=market_price,
            outcome="sold",
            first_seen_at=t_start + timedelta(hours=2 + i),
            last_seen_at=t_start + timedelta(hours=2 + i, minutes=30),
            resolved_at=t_start + timedelta(hours=2 + i, minutes=30),
        ))

    async with session_factory() as session:
        for obs in observations:
            session.add(obs)
        await session.commit()

        result = await score_player_v2(ea_id=ea_id, session=session, buy_price=buy_price)

    assert result is not None
    # margin 10% should be chosen since margin 20% is below MIN_OP_OBSERVATIONS
    assert result["margin_pct"] == 10


async def test_bootstrap_min(db):
    """Given only 5 resolved observations (below BOOTSTRAP_MIN_OBSERVATIONS=10), returns None."""
    _, session_factory = db
    ea_id = 1003
    buy_price = 10_000
    market_price = 10_000
    now = datetime.utcnow()

    observations = []
    for i in range(5):
        observations.append(make_observation(
            ea_id=ea_id,
            fingerprint=f"boot-{i}",
            buy_now_price=int(market_price * 1.10),
            market_price_at_obs=market_price,
            outcome="sold",
            first_seen_at=now - timedelta(hours=5 - i),
            last_seen_at=now - timedelta(hours=5 - i, minutes=-30),
            resolved_at=now - timedelta(hours=5 - i, minutes=-30),
        ))

    async with session_factory() as session:
        for obs in observations:
            session.add(obs)
        await session.commit()

        result = await score_player_v2(ea_id=ea_id, session=session, buy_price=buy_price)

    assert result is None, f"Expected None for {len(observations)} obs (below bootstrap), got {result}"


async def test_no_resolved_observations(db):
    """Given zero resolved observations, score_player_v2 returns None."""
    _, session_factory = db
    ea_id = 1004
    buy_price = 10_000

    async with session_factory() as session:
        result = await score_player_v2(ea_id=ea_id, session=session, buy_price=buy_price)

    assert result is None


async def test_insufficient_op_observations(db):
    """
    Given 20 resolved observations but only 2 are OP at every margin tier
    (below MIN_OP_OBSERVATIONS=3), returns None.
    """
    _, session_factory = db
    ea_id = 1005
    buy_price = 10_000
    market_price = 10_000
    now = datetime.utcnow()
    t_start = now - timedelta(hours=20)

    observations = []
    # Only 2 OP sold (below min at all margins since 10% is lowest viable margin and 2 < 3)
    for i in range(2):
        observations.append(make_observation(
            ea_id=ea_id,
            fingerprint=f"op-only-{i}",
            buy_now_price=int(market_price * 1.40),  # highest margin
            market_price_at_obs=market_price,
            outcome="sold",
            first_seen_at=t_start + timedelta(hours=i),
            last_seen_at=t_start + timedelta(hours=i, minutes=30),
            resolved_at=t_start + timedelta(hours=i, minutes=30),
        ))
    # 18 non-OP resolved (filler to pass bootstrap threshold)
    for i in range(18):
        observations.append(make_observation(
            ea_id=ea_id,
            fingerprint=f"non-op-filler-{i}",
            buy_now_price=market_price - 100,
            market_price_at_obs=market_price,
            outcome="sold",
            first_seen_at=t_start + timedelta(hours=2 + i),
            last_seen_at=t_start + timedelta(hours=2 + i, minutes=30),
            resolved_at=t_start + timedelta(hours=2 + i, minutes=30),
        ))

    async with session_factory() as session:
        for obs in observations:
            session.add(obs)
        await session.commit()

        result = await score_player_v2(ea_id=ea_id, session=session, buy_price=buy_price)

    assert result is None, f"Expected None for insufficient OP obs, got {result}"


async def test_return_dict_shape(db):
    """
    score_player_v2 returns dict with all required keys when viable.
    """
    _, session_factory = db
    ea_id = 1006
    buy_price = 10_000
    market_price = 10_000
    now = datetime.utcnow()
    # Span 4 days to satisfy MIN_OBSERVATION_HISTORY_DAYS=3
    t_start = now - timedelta(days=4)

    observations = []
    # 5 OP sold at margin 10%
    for i in range(5):
        observations.append(make_observation(
            ea_id=ea_id,
            fingerprint=f"shape-op-{i}",
            buy_now_price=int(market_price * 1.10),
            market_price_at_obs=market_price,
            outcome="sold",
            first_seen_at=t_start + timedelta(hours=i),
            last_seen_at=t_start + timedelta(hours=i, minutes=30),
            resolved_at=t_start + timedelta(hours=i, minutes=30),
        ))
    # 3 OP expired
    for i in range(3):
        observations.append(make_observation(
            ea_id=ea_id,
            fingerprint=f"shape-exp-{i}",
            buy_now_price=int(market_price * 1.10),
            market_price_at_obs=market_price,
            outcome="expired",
            first_seen_at=t_start + timedelta(hours=5 + i),
            last_seen_at=t_start + timedelta(hours=5 + i, minutes=30),
            resolved_at=t_start + timedelta(hours=5 + i, minutes=30),
        ))
    # 15 non-OP filler to exceed MIN_TOTAL_RESOLVED_OBSERVATIONS=20 (5+3+15=23 total)
    for i in range(15):
        observations.append(make_observation(
            ea_id=ea_id,
            fingerprint=f"shape-fill-{i}",
            buy_now_price=market_price - 100,
            market_price_at_obs=market_price,
            outcome="sold",
            first_seen_at=t_start + timedelta(hours=i),
            last_seen_at=t_start + timedelta(hours=i, minutes=30),
            resolved_at=t_start + timedelta(hours=i, minutes=30),
        ))

    async with session_factory() as session:
        for obs in observations:
            session.add(obs)
        await session.commit()

        result = await score_player_v2(ea_id=ea_id, session=session, buy_price=buy_price)

    assert result is not None
    required_keys = {
        "ea_id", "buy_price", "sell_price", "net_profit", "margin_pct",
        "op_sold", "op_total", "op_sell_rate",
        "expected_profit_per_hour", "efficiency",
    }
    assert required_keys == set(result.keys()), (
        f"Missing keys: {required_keys - set(result.keys())}, "
        f"Extra keys: {set(result.keys()) - required_keys}"
    )
    assert result["ea_id"] == ea_id
    assert result["buy_price"] == buy_price
    assert result["efficiency"] == round(result["expected_profit_per_hour"] / buy_price, 6)


# ── Min-total-observations filter tests ───────────────────────────────────────

async def test_below_min_total_resolved_observations(db):
    """
    Player with 15 resolved observations (above BOOTSTRAP_MIN=10 but below
    MIN_TOTAL_RESOLVED_OBSERVATIONS=20) returns None.
    """
    _, session_factory = db
    ea_id = 2001
    buy_price = 10_000
    market_price = 10_000
    now = datetime.utcnow()
    t_start = now - timedelta(hours=20)

    observations = []
    # 5 OP sold at 10% margin
    for i in range(5):
        observations.append(make_observation(
            ea_id=ea_id,
            fingerprint=f"below-total-op-{i}",
            buy_now_price=int(market_price * 1.10),
            market_price_at_obs=market_price,
            outcome="sold",
            first_seen_at=t_start + timedelta(hours=i),
            last_seen_at=t_start + timedelta(hours=i, minutes=30),
            resolved_at=t_start + timedelta(hours=i, minutes=30),
        ))
    # 10 non-OP filler to reach exactly 15 (above bootstrap=10, below quality=20)
    for i in range(10):
        observations.append(make_observation(
            ea_id=ea_id,
            fingerprint=f"below-total-fill-{i}",
            buy_now_price=market_price - 100,
            market_price_at_obs=market_price,
            outcome="sold",
            first_seen_at=t_start + timedelta(hours=5 + i),
            last_seen_at=t_start + timedelta(hours=5 + i, minutes=30),
            resolved_at=t_start + timedelta(hours=5 + i, minutes=30),
        ))

    assert len(observations) == 15, "Fixture must seed exactly 15 observations"
    assert 15 < MIN_TOTAL_RESOLVED_OBSERVATIONS, "Sanity: 15 < quality threshold"

    async with session_factory() as session:
        for obs in observations:
            session.add(obs)
        await session.commit()

        result = await score_player_v2(ea_id=ea_id, session=session, buy_price=buy_price)

    assert result is None, (
        f"Expected None for {len(observations)} obs (below MIN_TOTAL_RESOLVED_OBSERVATIONS={MIN_TOTAL_RESOLVED_OBSERVATIONS}), "
        f"got {result}"
    )


async def test_above_min_total_resolved_observations(db):
    """
    Player with 25 resolved observations (above MIN_TOTAL_RESOLVED_OBSERVATIONS=20)
    with viable OP data is not None.
    """
    _, session_factory = db
    ea_id = 2002
    buy_price = 10_000
    market_price = 10_000
    now = datetime.utcnow()
    # Span 5 days to satisfy MIN_OBSERVATION_HISTORY_DAYS=3
    t_start = now - timedelta(days=5)

    observations = []
    # 5 OP sold at 10% margin
    for i in range(5):
        observations.append(make_observation(
            ea_id=ea_id,
            fingerprint=f"above-total-op-{i}",
            buy_now_price=int(market_price * 1.10),
            market_price_at_obs=market_price,
            outcome="sold",
            first_seen_at=t_start + timedelta(hours=i),
            last_seen_at=t_start + timedelta(hours=i, minutes=30),
            resolved_at=t_start + timedelta(hours=i, minutes=30),
        ))
    # 20 non-OP filler to reach exactly 25 observations (above quality=20)
    for i in range(20):
        observations.append(make_observation(
            ea_id=ea_id,
            fingerprint=f"above-total-fill-{i}",
            buy_now_price=market_price - 100,
            market_price_at_obs=market_price,
            outcome="sold",
            first_seen_at=t_start + timedelta(hours=5 + i),
            last_seen_at=t_start + timedelta(hours=5 + i, minutes=30),
            resolved_at=t_start + timedelta(hours=5 + i, minutes=30),
        ))

    assert len(observations) == 25, "Fixture must seed exactly 25 observations"
    assert 25 >= MIN_TOTAL_RESOLVED_OBSERVATIONS, "Sanity: 25 >= quality threshold"

    async with session_factory() as session:
        for obs in observations:
            session.add(obs)
        await session.commit()

        result = await score_player_v2(ea_id=ea_id, session=session, buy_price=buy_price)

    assert result is not None, "Expected a score result for 25 observations with viable OP data"


# ── Min-history-depth filter tests ────────────────────────────────────────────

async def test_below_min_observation_history_days(db):
    """
    Player with 20+ resolved observations all within the last 24 hours
    (below MIN_OBSERVATION_HISTORY_DAYS=3) returns None.
    """
    _, session_factory = db
    ea_id = 2003
    buy_price = 10_000
    market_price = 10_000
    now = datetime.utcnow()
    # All observations within the last 23 hours — history depth < 3 days
    t_start = now - timedelta(hours=23)

    observations = []
    # 5 OP sold
    for i in range(5):
        observations.append(make_observation(
            ea_id=ea_id,
            fingerprint=f"short-hist-op-{i}",
            buy_now_price=int(market_price * 1.10),
            market_price_at_obs=market_price,
            outcome="sold",
            first_seen_at=t_start + timedelta(hours=i * 2),
            last_seen_at=t_start + timedelta(hours=i * 2, minutes=30),
            resolved_at=t_start + timedelta(hours=i * 2, minutes=30),
        ))
    # 20 non-OP filler (total = 25 — above quality threshold)
    for i in range(20):
        observations.append(make_observation(
            ea_id=ea_id,
            fingerprint=f"short-hist-fill-{i}",
            buy_now_price=market_price - 100,
            market_price_at_obs=market_price,
            outcome="sold",
            first_seen_at=t_start + timedelta(minutes=i * 60),
            last_seen_at=t_start + timedelta(minutes=i * 60, seconds=30),
            resolved_at=t_start + timedelta(minutes=i * 60, seconds=30),
        ))

    assert len(observations) == 25
    assert MIN_OBSERVATION_HISTORY_DAYS == 3, "Sanity: threshold is 3 days"

    async with session_factory() as session:
        for obs in observations:
            session.add(obs)
        await session.commit()

        result = await score_player_v2(ea_id=ea_id, session=session, buy_price=buy_price)

    assert result is None, (
        f"Expected None for observations spanning only 23h (below {MIN_OBSERVATION_HISTORY_DAYS}d threshold), "
        f"got {result}"
    )


async def test_above_min_observation_history_days(db):
    """
    Player with 20+ resolved observations spanning 5 days
    (above MIN_OBSERVATION_HISTORY_DAYS=3) is not None when OP data is viable.
    """
    _, session_factory = db
    ea_id = 2004
    buy_price = 10_000
    market_price = 10_000
    now = datetime.utcnow()
    # Observations span 5 days — well above MIN_OBSERVATION_HISTORY_DAYS=3
    t_start = now - timedelta(days=5)

    observations = []
    # 5 OP sold spread across 5 days
    for i in range(5):
        observations.append(make_observation(
            ea_id=ea_id,
            fingerprint=f"long-hist-op-{i}",
            buy_now_price=int(market_price * 1.10),
            market_price_at_obs=market_price,
            outcome="sold",
            first_seen_at=t_start + timedelta(days=i),
            last_seen_at=t_start + timedelta(days=i, hours=1),
            resolved_at=t_start + timedelta(days=i, hours=1),
        ))
    # 20 non-OP filler (total = 25)
    for i in range(20):
        observations.append(make_observation(
            ea_id=ea_id,
            fingerprint=f"long-hist-fill-{i}",
            buy_now_price=market_price - 100,
            market_price_at_obs=market_price,
            outcome="sold",
            first_seen_at=t_start + timedelta(hours=6 + i * 5),
            last_seen_at=t_start + timedelta(hours=6 + i * 5, minutes=30),
            resolved_at=t_start + timedelta(hours=6 + i * 5, minutes=30),
        ))

    assert len(observations) == 25

    async with session_factory() as session:
        for obs in observations:
            session.add(obs)
        await session.commit()

        result = await score_player_v2(ea_id=ea_id, session=session, buy_price=buy_price)

    assert result is not None, (
        f"Expected a score result for observations spanning 5 days (above {MIN_OBSERVATION_HISTORY_DAYS}d threshold)"
    )
