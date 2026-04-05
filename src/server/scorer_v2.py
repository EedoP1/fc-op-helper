"""
OP sell scorer v2: scores players using pre-aggregated daily_listing_summaries.

Reads from daily_listing_summaries (tiny table, ~140K rows) instead of the
raw listing_observations table (8M rows). Aggregation into summaries happens
in real-time during resolve_outcomes().

Produces identical results to the previous listing_observations approach —
margin selection, EPPH calculation, and quality guards are unchanged.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import (
    EA_TAX_RATE,
    LISTING_RETENTION_DAYS,
    MARGINS,
    MIN_TOTAL_RESOLVED_OBSERVATIONS,
    MIN_OP_OBSERVATIONS,
)

logger = logging.getLogger(__name__)

# Read pre-aggregated sold/expired counts per margin tier from daily summaries.
_SCORE_SQL = text("""
    SELECT margin_pct,
           SUM(op_sold_count) AS op_sold,
           SUM(op_expired_count) AS op_expired
    FROM daily_listing_summaries
    WHERE ea_id = :ea_id AND date >= :cutoff_date
    GROUP BY margin_pct
    ORDER BY margin_pct DESC
""")

# Total observation count: use the lowest configured margin tier (always present)
# to avoid multiplying by the number of margin tiers.
_TOTAL_COUNT_SQL = text(f"""
    SELECT COALESCE(SUM(total_listed_count), 0)
    FROM daily_listing_summaries
    WHERE ea_id = :ea_id AND date >= :cutoff_date AND margin_pct = {min(MARGINS)}
""")


async def score_player_v2(
    ea_id: int,
    session: AsyncSession,
    buy_price: int,
    max_price_range: int | None = None,
) -> dict | None:
    """
    Score a player for OP selling using pre-aggregated daily listing summaries.

    Reads from daily_listing_summaries instead of raw listing_observations.
    Margin selection, EPPH calculation, and quality guards are unchanged
    from the previous approach.

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

    cutoff_date = (datetime.utcnow() - timedelta(days=LISTING_RETENTION_DAYS)).strftime("%Y-%m-%d")

    # Aggregate sold/expired per margin tier from daily summaries
    result = await session.execute(
        _SCORE_SQL,
        {"ea_id": ea_id, "cutoff_date": cutoff_date},
    )
    margin_rows = result.all()
    _t_agg = _time.monotonic()

    # Quality guard: count ALL resolved observations (not just OP ones) to ensure
    # we have a statistically meaningful sample before trusting the OP ratios.
    total_result = await session.execute(
        _TOTAL_COUNT_SQL,
        {"ea_id": ea_id, "cutoff_date": cutoff_date},
    )
    total_obs = total_result.scalar() or 0

    if total_obs < MIN_TOTAL_RESOLVED_OBSERVATIONS:
        logger.debug(
            "score_player_v2: ea_id=%d skipped — only %d observations (quality min %d) agg=%.1fs",
            ea_id, total_obs, MIN_TOTAL_RESOLVED_OBSERVATIONS, _t_agg - _t0,
        )
        return None

    # Find best margin by expected_profit_per_hour
    best_epph = -1.0
    best: dict | None = None

    _allowed_margins = set(MARGINS)

    for row in margin_rows:
        margin_pct, op_sold, op_expired = row[0], row[1], row[2]

        if margin_pct not in _allowed_margins:
            continue

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
