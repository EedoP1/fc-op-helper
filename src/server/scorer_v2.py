"""
OP sell scorer v2: listing-observation-based scoring.

Reads accumulated ListingObservation rows for a player and computes
expected_profit_per_hour using the corrected formula:
  expected_profit_per_hour = net_profit * sell_rate
where:
  sell_rate = op_sold / (op_sold + op_expired)

The previous op_sales_per_hour multiplier penalised players with longer
observation windows and has been removed. Sell-through probability alone
determines the expected profit weight.

Unlike the v1 scorer (which infers OP behaviour from completedAuctions
snapshots), v2 operates on directly observed listing outcomes (sold/expired),
giving a more accurate sell-through rate that accounts for failed OP listings.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import (
    EA_TAX_RATE,
    LISTING_RETENTION_DAYS,
    BOOTSTRAP_MIN_OBSERVATIONS,
    MARGINS,
    MIN_OP_OBSERVATIONS,
)
from src.server.models_db import ListingObservation

logger = logging.getLogger(__name__)


async def score_player_v2(
    ea_id: int,
    session: AsyncSession,
    buy_price: int,
) -> dict | None:
    """
    Score a player for OP selling using accumulated listing observation data.

    Reads resolved ListingObservation rows within the retention window and
    evaluates each margin tier via ``expected_profit_per_hour = net_profit *
    sell_rate`` (where sell_rate = op_sold / (op_sold + op_expired)), returning
    the tier that maximises the metric.

    Args:
        ea_id: The player's EA numeric ID.
        session: Active async SQLAlchemy session.
        buy_price: Current BIN price to use as the buy cost basis.

    Returns:
        Scoring result dict on success, or None if:
        - fewer than BOOTSTRAP_MIN_OBSERVATIONS resolved listings exist
        - no margin tier has MIN_OP_OBSERVATIONS or more OP listings
        - net_profit is non-positive at every viable margin
    """
    # ── 1. Query resolved observations within retention window ────────────────
    cutoff = datetime.utcnow() - timedelta(days=LISTING_RETENTION_DAYS)
    result = await session.execute(
        select(ListingObservation).where(
            ListingObservation.ea_id == ea_id,
            ListingObservation.outcome.isnot(None),
            ListingObservation.first_seen_at >= cutoff,
        )
    )
    observations = result.scalars().all()

    # ── 2. Bootstrap guard ────────────────────────────────────────────────────
    if len(observations) < BOOTSTRAP_MIN_OBSERVATIONS:
        logger.debug(
            "score_player_v2: ea_id=%d skipped — only %d resolved observations (min %d)",
            ea_id, len(observations), BOOTSTRAP_MIN_OBSERVATIONS,
        )
        return None

    # ── 3. Evaluate each margin tier ──────────────────────────────────────────
    best_expected_profit_per_hour = -1.0
    best: dict | None = None

    for margin_pct in MARGINS:
        margin = margin_pct / 100.0
        op_threshold_factor = 1 + margin

        op_sold = 0
        op_expired = 0

        for obs in observations:
            is_op = obs.buy_now_price >= int(obs.market_price_at_obs * op_threshold_factor)
            if not is_op:
                continue
            if obs.outcome == "sold":
                op_sold += 1
            elif obs.outcome == "expired":
                op_expired += 1

        op_total = op_sold + op_expired

        if op_total < MIN_OP_OBSERVATIONS:
            continue

        # OP sell-through rate: sold / (sold + expired)
        op_sell_rate = op_sold / op_total

        sell_price = int(buy_price * (1 + margin))
        ea_tax = int(sell_price * EA_TAX_RATE)
        net_profit = sell_price - ea_tax - buy_price

        if net_profit <= 0:
            continue

        expected_profit_per_hour = net_profit * op_sell_rate

        if expected_profit_per_hour > best_expected_profit_per_hour:
            best_expected_profit_per_hour = expected_profit_per_hour
            best = {
                "ea_id": ea_id,
                "buy_price": buy_price,
                "sell_price": sell_price,
                "net_profit": net_profit,
                "margin_pct": margin_pct,
                "op_sold": op_sold,
                "op_total": op_total,
                "op_sell_rate": op_sell_rate,
                "expected_profit_per_hour": round(expected_profit_per_hour, 2),
                "efficiency": round(expected_profit_per_hour / buy_price, 6),
            }

    # ── 5. Return best margin result or None ──────────────────────────────────
    if best is None:
        logger.debug(
            "score_player_v2: ea_id=%d — no viable margin found across %d observations",
            ea_id, len(observations),
        )
    return best
