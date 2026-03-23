"""
Historical OP Success Score (HOSS).

Uses FUTBIN's sold/expired data to determine:
1. The real sell-through rate at each OP premium level
2. The optimal OP margin (maximizes expected profit per listing)
3. How many sales per hour we can expect for our single listing

When FUTBIN data is available, we use REAL sell-through rates.
When not available, falls back to fut.gg completedAuctions analysis.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from src.config import (
    EA_TAX_RATE,
    HOSS_MIN_EVENTS_FOR_CONFIDENCE,
    OP_MARGIN_MIN,
    OP_MARGIN_MAX,
)
from src.models import HOSSResult, PlayerMarketData, PricePoint, SaleRecord

logger = logging.getLogger(__name__)


def compute_hoss(
    market_data: PlayerMarketData,
    futbin_sell_rates: dict[int, float] | None = None,
    futbin_floor: int = 0,
    futbin_total_listings: int = 0,
    futbin_total_sold: int = 0,
    futbin_time_span_hours: float = 24.0,
) -> HOSSResult:
    """
    Compute the Historical OP Success Score for a player.

    If futbin_sell_rates is provided (from FUTBIN scrape), uses real
    sell-through rates. Otherwise falls back to fut.gg analysis.

    futbin_sell_rates: {premium_pct: sell_rate} e.g. {5: 0.67, 10: 0.64, ...}
    """
    sales = market_data.sales
    history = market_data.price_history
    current_bin = market_data.current_lowest_bin

    if not sales and not futbin_sell_rates:
        return _empty_result()

    # ── Use FUTBIN data if available ────────────────────────────
    if futbin_sell_rates:
        return _compute_from_futbin(
            current_bin=current_bin,
            sell_rates=futbin_sell_rates,
            futbin_floor=futbin_floor,
            total_listings=futbin_total_listings,
            total_sold=futbin_total_sold,
            time_span_hours=futbin_time_span_hours,
            sales=sales,
        )

    # ── Fallback to fut.gg analysis ─────────────────────────────
    return _compute_from_futgg(market_data)


def _compute_from_futbin(
    current_bin: int,
    sell_rates: dict[int, float],
    futbin_floor: int,
    total_listings: int,
    total_sold: int,
    time_span_hours: float,
    sales: list[SaleRecord],
) -> HOSSResult:
    """Use real FUTBIN sell-through rates to find optimal margin and profit/hr."""

    # Find the best margin: maximize (net_profit × sell_rate)
    best_margin = 0.05
    best_expected = 0.0

    for pct, sell_rate in sell_rates.items():
        margin = pct / 100.0
        # net_profit per unit of buy_price
        net_if_sold = margin - (1 + margin) * EA_TAX_RATE
        expected = net_if_sold * sell_rate

        if expected > best_expected:
            best_expected = expected
            best_margin = margin

    # Calculate sales velocity
    # total_sold listings sold in time_span_hours
    # Each listing is 1 hour. If 338 sold in 24 hours, that's ~14 sold/hr
    # But there are many listings per hour, so per-listing sell rate =
    # total_sold / total_listings (which is the sell-through rate)
    # And listings turn over every ~1 hour, so per hour for OUR listing:
    # my_sell_rate_per_hour ≈ sell_rate_at_our_margin (the FUTBIN rate IS the answer)
    # Because: if 67% of listings at this price sell within their 1hr window,
    # then our 1 listing has a 67% chance of selling per hour.

    sell_rate_at_best = sell_rates.get(int(best_margin * 100), 0.5)

    # Sales per hour from fut.gg data
    sales_per_hour = 0.0
    op_sales_per_hour = 0.0
    if len(sales) >= 2:
        sorted_sales = sorted(sales, key=lambda s: s.sold_at)
        span = (sorted_sales[-1].sold_at - sorted_sales[0].sold_at).total_seconds() / 3600
        if span > 0:
            sales_per_hour = len(sales) / span

    # Confidence
    confidence = min(total_listings / 20, 1.0)  # 20+ listings = full confidence

    # OP event count (for scoring)
    op_event_count = total_sold
    total_count = total_listings
    op_sell_rate_overall = total_sold / total_listings if total_listings > 0 else 0

    # Score: combines sell rate at best margin + how high the margin is
    premium_factor = min(best_margin / 0.15, 1.0)
    rate_factor = sell_rate_at_best
    score = (rate_factor * 0.6 + premium_factor * 0.4) * 100 * confidence

    return HOSSResult(
        score=round(min(score, 100.0), 2),
        op_event_count=op_event_count,
        total_sales=total_count,
        op_sell_rate=round(op_sell_rate_overall, 4),
        avg_op_premium=round(best_margin, 4),
        best_op_margin=round(best_margin, 4),
        confidence=round(confidence, 2),
        active_days=1,
        sales_per_hour=round(sales_per_hour, 4),
        op_sales_per_hour=round(sales_per_hour * sell_rate_at_best, 4),
        my_op_sells_per_hour=round(sell_rate_at_best, 4),
    )


def _compute_from_futgg(market_data: PlayerMarketData) -> HOSSResult:
    """Fallback: compute HOSS from fut.gg completedAuctions only."""
    sales = market_data.sales
    history = market_data.price_history
    current_bin = market_data.current_lowest_bin
    op_listing_count = market_data.op_listing_count

    floor_by_hour = _build_floor_lookup(history, current_bin)

    op_premiums = []
    total_sales = len(sales)
    active_days = _count_active_days(sales)

    for sale in sales:
        floor = _get_floor_at_time(sale.sold_at, floor_by_hour, current_bin)
        if floor <= 0:
            continue
        premium = (sale.sold_price - floor) / floor
        if OP_MARGIN_MIN <= premium <= OP_MARGIN_MAX:
            op_premiums.append(premium)

    op_event_count = len(op_premiums)
    op_sell_rate = op_event_count / total_sales if total_sales > 0 else 0.0
    avg_op_premium = sum(op_premiums) / len(op_premiums) if op_premiums else 0.0
    best_margin = _find_best_margin(sales, floor_by_hour, current_bin)
    confidence = min(op_event_count / HOSS_MIN_EVENTS_FOR_CONFIDENCE, 1.0)

    sales_per_hour = 0.0
    op_sales_per_hour = 0.0
    if len(sales) >= 2:
        sorted_sales = sorted(sales, key=lambda s: s.sold_at)
        span = (sorted_sales[-1].sold_at - sorted_sales[0].sold_at).total_seconds() / 3600
        if span > 0:
            sales_per_hour = total_sales / span
            op_sales_per_hour = op_event_count / span

    my_op_sells_per_hour = op_sales_per_hour / op_listing_count

    if op_event_count == 0:
        raw_score = 0.0
    else:
        velocity_factor = min(op_sales_per_hour / 0.5, 1.0)
        rate = op_sell_rate
        premium_factor = min(avg_op_premium / 0.15, 1.0)
        raw_score = velocity_factor * 0.35 + rate * 0.35 + premium_factor * 0.30

    score = min(raw_score * 100, 100.0) * confidence

    return HOSSResult(
        score=round(score, 2),
        op_event_count=op_event_count,
        total_sales=total_sales,
        op_sell_rate=round(op_sell_rate, 4),
        avg_op_premium=round(avg_op_premium, 4),
        best_op_margin=round(best_margin, 4),
        confidence=round(confidence, 2),
        active_days=active_days,
        sales_per_hour=round(sales_per_hour, 4),
        op_sales_per_hour=round(op_sales_per_hour, 4),
        my_op_sells_per_hour=round(my_op_sells_per_hour, 4),
    )


def _empty_result() -> HOSSResult:
    return HOSSResult(
        score=50.0, op_event_count=0, total_sales=0, op_sell_rate=0.0,
        avg_op_premium=0.0, best_op_margin=0.05, confidence=0.0,
        active_days=0, sales_per_hour=0.0, op_sales_per_hour=0.0,
        my_op_sells_per_hour=0.0,
    )


def _build_floor_lookup(history: list[PricePoint], fallback: int) -> dict[str, int]:
    floor_by_hour = {}
    for point in history:
        hour_key = point.recorded_at.strftime("%Y-%m-%dT%H")
        if hour_key not in floor_by_hour or point.lowest_bin < floor_by_hour[hour_key]:
            floor_by_hour[hour_key] = point.lowest_bin
    return floor_by_hour


def _get_floor_at_time(sale_time: datetime, floor_by_hour: dict[str, int], fallback: int) -> int:
    hour_key = sale_time.strftime("%Y-%m-%dT%H")
    if hour_key in floor_by_hour:
        return floor_by_hour[hour_key]
    from datetime import timedelta
    for delta in [-1, 1, -2, 2]:
        nearby_key = (sale_time + timedelta(hours=delta)).strftime("%Y-%m-%dT%H")
        if nearby_key in floor_by_hour:
            return floor_by_hour[nearby_key]
    return fallback


def _count_active_days(sales: list[SaleRecord]) -> int:
    return len({sale.sold_at.date() for sale in sales})


def _find_best_margin(sales, floor_by_hour, current_bin) -> float:
    if not sales:
        return 0.05
    premiums = []
    for sale in sales:
        floor = _get_floor_at_time(sale.sold_at, floor_by_hour, current_bin)
        if floor <= 0:
            continue
        premiums.append((sale.sold_price - floor) / floor)
    if not premiums:
        return 0.05
    total = len(premiums)
    best_margin = 0.05
    best_expected = 0.0
    for margin_pct in range(3, 41):
        margin = margin_pct / 100.0
        sales_at = sum(1 for p in premiums if p >= margin)
        prob = sales_at / total
        net = margin - (1 + margin) * EA_TAX_RATE
        expected = net * prob
        if expected > best_expected:
            best_expected = expected
            best_margin = margin
    return best_margin
