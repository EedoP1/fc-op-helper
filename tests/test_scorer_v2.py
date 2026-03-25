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
from src.config import BOOTSTRAP_MIN_OBSERVATIONS, MIN_OP_OBSERVATIONS


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
    Given 20 resolved observations over 10 hours at margin 10%:
    8 OP listings (5 sold, 3 expired), buy_price=10000.
    Expected:
      op_sell_rate = 5/8 = 0.625
      op_sales_per_hour = 5/10 = 0.5
      sell_price = 11000, ea_tax = 550, net_profit = 450
      expected_profit_per_hour = 450 * 0.625 * 0.5 = 140.625
    """
    _, session_factory = db
    ea_id = 1001
    buy_price = 10_000
    market_price = 10_000
    op_threshold = int(market_price * 1.10)  # 11000

    now = datetime.utcnow()
    t_start = now - timedelta(hours=10)

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
    # 12 non-OP sold
    for i in range(12):
        observations.append(make_observation(
            ea_id=ea_id,
            fingerprint=f"non-op-{i}",
            buy_now_price=market_price - 100,  # below OP threshold
            market_price_at_obs=market_price,
            outcome="sold",
            first_seen_at=t_start + timedelta(hours=i * 0.7),
            last_seen_at=t_start + timedelta(hours=i * 0.7, minutes=30),
            resolved_at=t_start + timedelta(hours=i * 0.7, minutes=30),
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
    assert abs(result["op_sales_per_hour"] - 0.5) < 0.001
    assert result["net_profit"] == 450  # 11000 - 550 - 10000
    assert abs(result["expected_profit_per_hour"] - 140.625) < 0.1


async def test_margin_selection(db):
    """
    Given observations at two margins where margin 10% produces higher
    expected_profit_per_hour than 20%, the scorer picks 10%.
    """
    _, session_factory = db
    ea_id = 1002
    buy_price = 10_000
    market_price = 10_000

    now = datetime.utcnow()
    t_start = now - timedelta(hours=20)

    observations = []
    # Margin 10%: 10 sold, 2 expired (high volume, lower net_profit)
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
    for i in range(2):
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
    # Margin 20%: only 3 sold, 1 expired (meets MIN_OP_OBSERVATIONS but lower volume)
    for i in range(3):
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
    for i in range(1):
        observations.append(make_observation(
            ea_id=ea_id,
            fingerprint=f"m20-expired-{i}",
            buy_now_price=int(market_price * 1.20),
            market_price_at_obs=market_price,
            outcome="expired",
            first_seen_at=t_start + timedelta(hours=18 + i),
            last_seen_at=t_start + timedelta(hours=18 + i, minutes=30),
            resolved_at=t_start + timedelta(hours=18 + i, minutes=30),
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
    # margin 10% should be chosen since it has higher expected_profit_per_hour
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
    t_start = now - timedelta(hours=10)

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
    # 7 non-OP (to exceed bootstrap)
    for i in range(7):
        observations.append(make_observation(
            ea_id=ea_id,
            fingerprint=f"shape-fill-{i}",
            buy_now_price=market_price - 100,
            market_price_at_obs=market_price,
            outcome="sold",
            first_seen_at=t_start + timedelta(hours=i * 0.5),
            last_seen_at=t_start + timedelta(hours=i * 0.5, minutes=30),
            resolved_at=t_start + timedelta(hours=i * 0.5, minutes=30),
        ))

    async with session_factory() as session:
        for obs in observations:
            session.add(obs)
        await session.commit()

        result = await score_player_v2(ea_id=ea_id, session=session, buy_price=buy_price)

    assert result is not None
    required_keys = {
        "ea_id", "buy_price", "sell_price", "net_profit", "margin_pct",
        "op_sold", "op_total", "op_sell_rate", "op_sales_per_hour",
        "expected_profit_per_hour", "efficiency", "hours_of_data",
    }
    assert required_keys == set(result.keys()), (
        f"Missing keys: {required_keys - set(result.keys())}, "
        f"Extra keys: {set(result.keys()) - required_keys}"
    )
    assert result["ea_id"] == ea_id
    assert result["buy_price"] == buy_price
    assert result["efficiency"] == round(result["expected_profit_per_hour"] / buy_price, 6)
