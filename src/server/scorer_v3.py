"""
OP sell scorer v3: weighted scoring based on supply/demand ratio + profit.

  score = sell_ratio^w1 × sph^w2 × net_profit^w3

Where:
  total_lph = visible_lph + sph - resolved_sold_per_hour
  sell_ratio = sph / total_lph
  net_profit = best margin from listing tracker OP sell data
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
    MIN_OP_OBSERVATIONS,
    SCORER_V3_W1,
    SCORER_V3_W2,
    SCORER_V3_W3,
)

logger = logging.getLogger(__name__)

_RESOLVED_SOLD_SQL = text("""
    SELECT COALESCE(SUM(total_sold_count), 0) AS resolved_sold,
           COALESCE(SUM(total_listed_count), 0) AS total_listed
    FROM daily_listing_summaries
    WHERE ea_id = :ea_id AND date >= :cutoff_date AND margin_pct = 3
""")

_MARGIN_DATA_SQL = text("""
    SELECT margin_pct,
           SUM(op_sold_count) AS op_sold,
           SUM(op_expired_count) AS op_expired
    FROM daily_listing_summaries
    WHERE ea_id = :ea_id AND date >= :cutoff_date
    GROUP BY margin_pct
    ORDER BY margin_pct DESC
""")


async def score_player_v3(
    ea_id: int,
    buy_price: int,
    sales_per_hour: float,
    visible_lph: float,
    session: AsyncSession,
    max_price_range: int | None = None,
) -> dict | None:
    """
    Score a player for OP selling using weighted supply/demand + profit model.

    Args:
        ea_id: The player's EA numeric ID.
        buy_price: Current lowest BIN price.
        sales_per_hour: Total sales per hour from completedAuctions.
        visible_lph: Listings per hour computed from liveAuctions timestamps.
        session: Active async SQLAlchemy session.
        max_price_range: EA's maximum BIN price for this card.

    Returns:
        Scoring result dict on success, or None if insufficient data.
    """
    if buy_price <= 0 or sales_per_hour <= 0 or visible_lph <= 0:
        logger.debug(
            "score_player_v3: ea_id=%d skipped — buy=%d sph=%.1f lph=%.1f",
            ea_id, buy_price, sales_per_hour, visible_lph,
        )
        return None

    cutoff_date = (datetime.utcnow() - timedelta(days=LISTING_RETENTION_DAYS)).strftime("%Y-%m-%d")

    # --- Sell ratio ---
    result = await session.execute(_RESOLVED_SOLD_SQL, {"ea_id": ea_id, "cutoff_date": cutoff_date})
    row = result.one()
    resolved_sold = row.resolved_sold

    cutoff_dt = datetime.strptime(cutoff_date, "%Y-%m-%d")
    hours_tracked = (datetime.utcnow() - cutoff_dt).total_seconds() / 3600
    if hours_tracked <= 0:
        hours_tracked = 1.0

    resolved_sold_per_hour = resolved_sold / hours_tracked
    total_lph = visible_lph + sales_per_hour - resolved_sold_per_hour
    total_lph = max(total_lph, sales_per_hour, visible_lph)
    sell_ratio = sales_per_hour / total_lph

    # --- Best margin from listing tracker ---
    result = await session.execute(_MARGIN_DATA_SQL, {"ea_id": ea_id, "cutoff_date": cutoff_date})
    margin_rows = result.all()

    allowed_margins = set(MARGINS)
    best_net_profit = 0
    best_margin_pct = 0
    best_op_sell_rate = 0.0
    best_op_sold = 0
    best_op_total = 0

    for margin_pct, op_sold, op_expired in margin_rows:
        if margin_pct not in allowed_margins:
            continue

        op_total = op_sold + op_expired
        if op_total < MIN_OP_OBSERVATIONS:
            continue

        margin = margin_pct / 100.0
        sell_price = int(buy_price * (1 + margin))

        if max_price_range is not None and sell_price > max_price_range:
            continue

        ea_tax = int(sell_price * EA_TAX_RATE)
        net_profit = sell_price - ea_tax - buy_price

        if net_profit <= 0:
            continue

        op_sell_rate = op_sold / op_total

        # Pick highest net_profit weighted by OP sell rate
        weighted_profit = net_profit * op_sell_rate
        if weighted_profit > best_net_profit * best_op_sell_rate if best_net_profit > 0 else True:
            best_net_profit = net_profit
            best_margin_pct = margin_pct
            best_op_sell_rate = op_sell_rate
            best_op_sold = op_sold
            best_op_total = op_total

    if best_net_profit <= 0:
        logger.debug(
            "score_player_v3: ea_id=%d — no viable margin found",
            ea_id,
        )
        return None

    # --- Final score ---
    score = (sell_ratio ** SCORER_V3_W1) * (sales_per_hour ** SCORER_V3_W2) * (best_net_profit ** SCORER_V3_W3)

    logger.debug(
        "score_player_v3: ea_id=%d margin=%d%% net=%d sell_ratio=%.4f sph=%.1f score=%.2f",
        ea_id, best_margin_pct, best_net_profit, sell_ratio, sales_per_hour, score,
    )

    return {
        "ea_id": ea_id,
        "buy_price": buy_price,
        "sell_price": int(buy_price * (1 + best_margin_pct / 100.0)),
        "net_profit": best_net_profit,
        "margin_pct": best_margin_pct,
        "op_sell_rate": round(best_op_sell_rate, 6),
        "op_sold_count": int(best_op_sold),
        "op_total_count": int(best_op_total),
        "sell_ratio": round(sell_ratio, 6),
        "sales_per_hour": round(sales_per_hour, 2),
        "visible_lph": round(visible_lph, 2),
        "total_lph": round(total_lph, 2),
        "weighted_score": round(score, 4),
    }
