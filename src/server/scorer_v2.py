"""
OP sell scorer v2: listing-observation-based scoring via SQL aggregation.

Pushes margin classification and sold/expired counting to Postgres via a
single query with CASE/FILTER, returning ~10 rows (one per margin tier)
instead of loading thousands of ORM objects into Python.

Verified to produce identical results to the Python-loop approach across
20 test players (see research_verify_match.py).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import (
    EA_TAX_RATE,
    LISTING_RETENTION_DAYS,
    MIN_TOTAL_RESOLVED_OBSERVATIONS,
    MIN_OP_OBSERVATIONS,
    MAX_OP_MARGIN_PCT,
)

logger = logging.getLogger(__name__)

# SQL query: classify each observation into its highest qualifying margin,
# then aggregate sold/expired counts per margin tier (cumulative).
_SCORE_SQL = text("""
    WITH classified AS (
        SELECT outcome,
            CASE
                WHEN buy_now_price >= (market_price_at_obs * 1.40)::int THEN 40
                WHEN buy_now_price >= (market_price_at_obs * 1.35)::int THEN 35
                WHEN buy_now_price >= (market_price_at_obs * 1.30)::int THEN 30
                WHEN buy_now_price >= (market_price_at_obs * 1.25)::int THEN 25
                WHEN buy_now_price >= (market_price_at_obs * 1.20)::int THEN 20
                WHEN buy_now_price >= (market_price_at_obs * 1.15)::int THEN 15
                WHEN buy_now_price >= (market_price_at_obs * 1.10)::int THEN 10
                WHEN buy_now_price >= (market_price_at_obs * 1.08)::int THEN 8
                WHEN buy_now_price >= (market_price_at_obs * 1.05)::int THEN 5
                WHEN buy_now_price >= (market_price_at_obs * 1.03)::int THEN 3
                ELSE 0
            END as max_margin
        FROM listing_observations
        WHERE ea_id = :ea_id
          AND outcome IS NOT NULL
          AND first_seen_at >= :cutoff
          AND buy_now_price < (market_price_at_obs * CAST(:max_op_factor AS double precision))::int
          AND market_price_at_obs > 0
    )
    SELECT
        m.margin_pct,
        COUNT(*) FILTER (WHERE outcome = 'sold' AND max_margin >= m.margin_pct) as op_sold,
        COUNT(*) FILTER (WHERE outcome = 'expired' AND max_margin >= m.margin_pct) as op_expired
    FROM classified
    CROSS JOIN (VALUES (40),(35),(30),(25),(20),(15),(10),(8),(5),(3)) AS m(margin_pct)
    WHERE max_margin >= m.margin_pct
    GROUP BY m.margin_pct
    ORDER BY m.margin_pct DESC
""")

_TOTAL_COUNT_SQL = text("""
    SELECT COUNT(*) FROM listing_observations
    WHERE ea_id = :ea_id AND outcome IS NOT NULL AND first_seen_at >= :cutoff
""")


async def score_player_v2(
    ea_id: int,
    session: AsyncSession,
    buy_price: int,
    max_price_range: int | None = None,
) -> dict | None:
    """
    Score a player for OP selling using SQL-aggregated listing observation data.

    Pushes margin classification and counting to Postgres, returning ~10 rows
    instead of loading thousands of ORM objects. Produces identical results to
    the previous Python-loop approach.

    Args:
        ea_id: The player's EA numeric ID.
        session: Active async SQLAlchemy session.
        buy_price: Current BIN price to use as the buy cost basis.
        max_price_range: EA's maximum BIN price for this card (from
            priceRange.maxPrice). Margin tiers whose sell_price exceeds this
            value are skipped — those prices cannot be listed on the market.
            None disables the filter (no data = no restriction).

    Returns:
        Scoring result dict on success, or None if insufficient data.
    """
    import time as _time
    _t0 = _time.monotonic()

    cutoff = datetime.utcnow() - timedelta(days=LISTING_RETENTION_DAYS)
    max_op_factor = 1 + MAX_OP_MARGIN_PCT / 100.0

    # Aggregate sold/expired per margin tier in SQL (skip separate COUNT query —
    # we can check total from the aggregation result itself)
    result = await session.execute(
        _SCORE_SQL,
        {"ea_id": ea_id, "cutoff": cutoff, "max_op_factor": max_op_factor},
    )
    margin_rows = result.all()
    _t_agg = _time.monotonic()

    # Quality guard: sum all sold+expired across the lowest margin tier (most inclusive)
    # If no rows returned, there are zero OP observations
    total_obs = 0
    if margin_rows:
        # The lowest margin row (last in DESC order) has the cumulative count
        lowest = margin_rows[-1]
        total_obs = lowest[1] + lowest[2]  # op_sold + op_expired at lowest margin

    if total_obs < MIN_TOTAL_RESOLVED_OBSERVATIONS:
        logger.debug(
            "score_player_v2: ea_id=%d skipped — only %d OP observations (quality min %d) agg=%.1fs",
            ea_id, total_obs, MIN_TOTAL_RESOLVED_OBSERVATIONS, _t_agg - _t0,
        )
        return None

    # Find best margin by expected_profit_per_hour
    best_epph = -1.0
    best: dict | None = None

    for row in margin_rows:
        margin_pct, op_sold, op_expired = row[0], row[1], row[2]
        op_total = op_sold + op_expired

        if op_total < MIN_OP_OBSERVATIONS:
            continue

        op_sell_rate = op_sold / op_total
        margin = margin_pct / 100.0
        sell_price = int(buy_price * (1 + margin))

        # Skip margins that produce a sell_price above EA's max BIN cap —
        # those listings cannot physically be placed on the transfer market.
        if max_price_range is not None and sell_price > max_price_range:
            continue

        ea_tax = int(sell_price * EA_TAX_RATE)
        net_profit = sell_price - ea_tax - buy_price

        if net_profit <= 0:
            continue

        epph = net_profit * op_sell_rate

        if epph > best_epph:
            best_epph = epph
            best = {
                "ea_id": ea_id,
                "buy_price": buy_price,
                "sell_price": sell_price,
                "net_profit": net_profit,
                "margin_pct": margin_pct,
                "op_sold": op_sold,
                "op_total": op_total,
                "op_sell_rate": op_sell_rate,
                "expected_profit_per_hour": round(epph, 2),
                "efficiency": round(epph / buy_price, 6),
            }

    _t_end = _time.monotonic()
    if _t_end - _t0 > 2.0:
        logger.warning(
            "SCORE_TIMING ea_id=%d agg=%.1fs total=%.1fs",
            ea_id, _t_agg - _t0, _t_end - _t0,
        )

    if best is None:
        logger.debug(
            "score_player_v2: ea_id=%d — no viable margin found (%d total obs)",
            ea_id, total_obs,
        )
    else:
        logger.debug(
            "score_player_v2: ea_id=%d margin=%d%% op_sold=%d/%d rate=%.1f%% epph=%.2f",
            ea_id, best["margin_pct"], best["op_sold"], best["op_total"],
            best["op_sell_rate"] * 100, best["expected_profit_per_hour"],
        )
    return best
