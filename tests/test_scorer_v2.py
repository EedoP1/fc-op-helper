"""
Tests for scorer_v2: daily-listing-summary-based scoring.

Tests verify:
- expected_profit_per_hour formula correctness
- Margin selection picks the one maximizing expected_profit_per_hour
- Quality threshold guard (MIN_TOTAL_RESOLVED_OBSERVATIONS)
- No-observations guard
- Insufficient OP observations guard
- Return dict shape
- max_price_range enforcement
"""
from __future__ import annotations

import pytest
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from src.server.db import create_engine_and_tables
from src.server.models_db import DailyListingSummary
from src.server.scorer_v2 import score_player_v2
from src.config import MIN_OP_OBSERVATIONS, MIN_TOTAL_RESOLVED_OBSERVATIONS, MARGINS


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
async def db():
    """In-memory SQLite engine + session factory."""
    engine, session_factory = await create_engine_and_tables("sqlite+aiosqlite:///:memory:")
    yield engine, session_factory
    await engine.dispose()


def _today_str() -> str:
    """Return today's date as YYYY-MM-DD string."""
    return datetime.utcnow().strftime("%Y-%m-%d")


def seed_summaries(
    ea_id: int,
    date: str,
    total_listed: int,
    total_sold: int,
    total_expired: int,
    margin_data: dict[int, tuple[int, int, int]],
) -> list[DailyListingSummary]:
    """Build DailyListingSummary rows for all margin tiers.

    Args:
        ea_id: Player EA ID.
        date: Date string (YYYY-MM-DD).
        total_listed: Total observations resolved on this date.
        total_sold: Total sold observations.
        total_expired: Total expired observations.
        margin_data: Dict mapping margin_pct -> (op_listed, op_sold, op_expired).
            Margins not in this dict get zeros for OP counts.

    Returns:
        List of DailyListingSummary objects to add to session.
    """
    summaries = []
    for margin_pct in MARGINS:
        op_listed, op_sold, op_expired = margin_data.get(margin_pct, (0, 0, 0))
        summaries.append(DailyListingSummary(
            ea_id=ea_id,
            date=date,
            margin_pct=margin_pct,
            op_listed_count=op_listed,
            op_sold_count=op_sold,
            op_expired_count=op_expired,
            total_listed_count=total_listed,
            total_sold_count=total_sold,
            total_expired_count=total_expired,
        ))
    return summaries


# ── Tests ─────────────────────────────────────────────────────────────────────

async def test_expected_profit_per_hour(db):
    """
    Given summary data: 8 OP listings at 10% (5 sold, 3 expired), buy_price=10000,
    total=20 observations.
    Expected:
      op_sell_rate = 5/8 = 0.625
      sell_price = 11000, ea_tax = 550, net_profit = 450
      expected_profit_per_hour = 450 * 0.625 = 281.25
    """
    _, session_factory = db
    ea_id = 1001
    buy_price = 10_000
    today = _today_str()

    # At margin 10%: 8 OP listed (5 sold, 3 expired)
    # Cumulative: same counts at lower margins (8, 5, 3)
    margin_data = {}
    for m in MARGINS:
        if m <= 10:
            margin_data[m] = (8, 5, 3)

    summaries = seed_summaries(ea_id, today, total_listed=20, total_sold=17, total_expired=3, margin_data=margin_data)

    async with session_factory() as session:
        session.add_all(summaries)
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
    than margin 15%, the scorer picks the one with the best EPPH.

    Margin 15% and above: only 2 OP total (below MIN_OP_OBSERVATIONS=3) -> skipped.
    Margin 10% and below: 17 OP total (12 sold, 5 expired) -> viable.
    Result: margin 10% is chosen.
    """
    _, session_factory = db
    ea_id = 1002
    buy_price = 10_000
    today = _today_str()

    # margin 15% and above: 2 OP (below min, skipped)
    # margin 10% and below: 17 OP (12 sold, 5 expired)
    margin_data = {}
    for m in MARGINS:
        if m >= 15:
            margin_data[m] = (2, 2, 0)
        else:
            margin_data[m] = (17, 12, 5)

    summaries = seed_summaries(ea_id, today, total_listed=22, total_sold=19, total_expired=3, margin_data=margin_data)

    async with session_factory() as session:
        session.add_all(summaries)
        await session.commit()

        result = await score_player_v2(ea_id=ea_id, session=session, buy_price=buy_price)

    assert result is not None
    # margin 10% should be chosen since margins >= 15% are below MIN_OP_OBSERVATIONS
    assert result["margin_pct"] == 10


async def test_bootstrap_min(db):
    """Given only 5 total observations (below MIN_TOTAL_RESOLVED_OBSERVATIONS=20), returns None."""
    _, session_factory = db
    ea_id = 1003
    buy_price = 10_000
    today = _today_str()

    margin_data = {10: (5, 5, 0), 8: (5, 5, 0), 5: (5, 5, 0), 3: (5, 5, 0)}
    summaries = seed_summaries(ea_id, today, total_listed=5, total_sold=5, total_expired=0, margin_data=margin_data)

    async with session_factory() as session:
        session.add_all(summaries)
        await session.commit()

        result = await score_player_v2(ea_id=ea_id, session=session, buy_price=buy_price)

    assert result is None, f"Expected None for 5 obs (below bootstrap), got {result}"


async def test_no_resolved_observations(db):
    """Given zero summary data, score_player_v2 returns None."""
    _, session_factory = db
    ea_id = 1004
    buy_price = 10_000

    async with session_factory() as session:
        result = await score_player_v2(ea_id=ea_id, session=session, buy_price=buy_price)

    assert result is None


async def test_insufficient_op_observations(db):
    """
    Given 20 total observations but only 2 OP at every margin tier
    (below MIN_OP_OBSERVATIONS=3), returns None.
    """
    _, session_factory = db
    ea_id = 1005
    buy_price = 10_000
    today = _today_str()

    # Only 2 OP at highest margin, cumulative down
    margin_data = {m: (2, 2, 0) for m in MARGINS}
    summaries = seed_summaries(ea_id, today, total_listed=20, total_sold=18, total_expired=2, margin_data=margin_data)

    async with session_factory() as session:
        session.add_all(summaries)
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
    today = _today_str()

    # 8 OP at 10% (5 sold, 3 expired), 23 total
    margin_data = {}
    for m in MARGINS:
        if m <= 10:
            margin_data[m] = (8, 5, 3)

    summaries = seed_summaries(ea_id, today, total_listed=23, total_sold=20, total_expired=3, margin_data=margin_data)

    async with session_factory() as session:
        session.add_all(summaries)
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
    Player with 15 total observations (above 10 but below
    MIN_TOTAL_RESOLVED_OBSERVATIONS=20) returns None.
    """
    _, session_factory = db
    ea_id = 2001
    buy_price = 10_000
    today = _today_str()

    margin_data = {}
    for m in MARGINS:
        if m <= 10:
            margin_data[m] = (5, 5, 0)

    summaries = seed_summaries(ea_id, today, total_listed=15, total_sold=15, total_expired=0, margin_data=margin_data)

    assert 15 < MIN_TOTAL_RESOLVED_OBSERVATIONS, "Sanity: 15 < quality threshold"

    async with session_factory() as session:
        session.add_all(summaries)
        await session.commit()

        result = await score_player_v2(ea_id=ea_id, session=session, buy_price=buy_price)

    assert result is None, (
        f"Expected None for 15 obs (below MIN_TOTAL_RESOLVED_OBSERVATIONS={MIN_TOTAL_RESOLVED_OBSERVATIONS}), "
        f"got {result}"
    )


# ── max_price_range enforcement tests ────────────────────────────────────────


async def test_max_price_range_caps_sell_price(db):
    """When max_price_range is set, margins whose sell_price exceeds it are skipped.

    Setup:
      buy_price = 10_000
      max_price_range = 10_999   (just below 10% sell_price = 11_000)

    Without max_price_range guard: scorer would pick margin 10% (higher EPPH).
    With max_price_range=10_999: sell_price at 10% is 11_000 > 10_999 -> skipped.
    Scorer must fall back to margin 8% (sell_price=10_800 <= 10_999).
    """
    _, session_factory = db
    ea_id = 3001
    buy_price = 10_000
    max_price_range = 10_999  # 11_000 - 1 -> blocks 10% margin
    today = _today_str()

    # OP data at margins 10% and below (8 OP: 5 sold, 3 expired)
    margin_data = {}
    for m in MARGINS:
        if m <= 10:
            margin_data[m] = (8, 5, 3)

    summaries = seed_summaries(ea_id, today, total_listed=23, total_sold=20, total_expired=3, margin_data=margin_data)

    async with session_factory() as session:
        session.add_all(summaries)
        await session.commit()

        # Without cap: should choose margin 10%
        result_no_cap = await score_player_v2(
            ea_id=ea_id, session=session, buy_price=buy_price, max_price_range=None
        )
        # With cap just below 10%: must fall back to margin 8%
        result_capped = await score_player_v2(
            ea_id=ea_id, session=session, buy_price=buy_price, max_price_range=max_price_range
        )

    # Without cap: freely picks best margin (10%)
    assert result_no_cap is not None
    assert result_no_cap["margin_pct"] == 10
    assert result_no_cap["sell_price"] == 11_000  # exceeds cap

    # With cap: 10% is blocked -> falls back to margin 8%
    assert result_capped is not None, (
        "Expected a result at margin 8% when 10% is capped by max_price_range"
    )
    assert result_capped["margin_pct"] == 8, (
        f"Expected margin_pct=8 (10% blocked by cap), got {result_capped['margin_pct']}"
    )
    assert result_capped["sell_price"] <= max_price_range, (
        f"REGRESSION: sell_price={result_capped['sell_price']} > max_price_range={max_price_range}"
    )


async def test_max_price_range_all_margins_blocked_returns_none(db):
    """When max_price_range is set so low that ALL viable margins are blocked, returns None.

    buy_price = 10_000, max_price_range = 10_000 (no margin at all possible).
    All sell_price computations (buy_price * (1 + any_margin)) > max_price_range.
    Scorer must return None rather than a capped/invalid sell_price.
    """
    _, session_factory = db
    ea_id = 3002
    buy_price = 10_000
    max_price_range = 10_000  # equal to buy_price — no positive margin possible
    today = _today_str()

    margin_data = {}
    for m in MARGINS:
        if m <= 3:
            margin_data[m] = (8, 5, 3)

    summaries = seed_summaries(ea_id, today, total_listed=23, total_sold=20, total_expired=3, margin_data=margin_data)

    async with session_factory() as session:
        session.add_all(summaries)
        await session.commit()

        result = await score_player_v2(
            ea_id=ea_id, session=session, buy_price=buy_price, max_price_range=max_price_range
        )

    assert result is None, (
        f"Expected None when all margins produce sell_price > max_price_range, got {result}"
    )


async def test_above_min_total_resolved_observations(db):
    """
    Player with 25 total observations (above MIN_TOTAL_RESOLVED_OBSERVATIONS=20)
    with viable OP data is not None.
    """
    _, session_factory = db
    ea_id = 2002
    buy_price = 10_000
    today = _today_str()

    margin_data = {}
    for m in MARGINS:
        if m <= 10:
            margin_data[m] = (5, 5, 0)

    summaries = seed_summaries(ea_id, today, total_listed=25, total_sold=25, total_expired=0, margin_data=margin_data)

    assert 25 >= MIN_TOTAL_RESOLVED_OBSERVATIONS, "Sanity: 25 >= quality threshold"

    async with session_factory() as session:
        session.add_all(summaries)
        await session.commit()

        result = await score_player_v2(ea_id=ea_id, session=session, buy_price=buy_price)

    assert result is not None, "Expected a score result for 25 observations with viable OP data"
