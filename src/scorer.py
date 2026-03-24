"""
OP sell scorer.

For each player, finds the best OP margin by checking how many sales
happened above market price AT THE TIME of the sale. Requires 3+ verified
OP sales at a given margin for it to count.
"""

from __future__ import annotations

from datetime import timedelta

from src.config import EA_TAX_RATE
from src.models import PlayerMarketData

# Margins to try, highest first (we pick the first with 3+ OP sales)
MARGINS = [40, 35, 30, 25, 20, 15, 10, 8, 5, 3]
MIN_OP_SALES = 3
MIN_TOTAL_SALES = 5
MIN_LIVE_LISTINGS = 20
MIN_SALES_PER_HOUR = 7


def score_player(md: PlayerMarketData) -> dict | None:
    """
    Score a player for OP selling.

    Returns a dict with scoring data, or None if the player isn't viable.
    Picks the highest margin that has 3+ verified OP sales (checked
    against the market price at the time each sale happened).
    """
    buy_price = md.current_lowest_bin
    if buy_price <= 0:
        return None

    if len(md.sales) < MIN_TOTAL_SALES or len(md.live_auction_prices) < MIN_LIVE_LISTINGS:
        return None

    # Time span of sales data
    sorted_sales = sorted(md.sales, key=lambda s: s.sold_at)
    time_span_hrs = (sorted_sales[-1].sold_at - sorted_sales[0].sold_at).total_seconds() / 3600
    if time_span_hrs < 0.5:
        time_span_hrs = 0.5

    total_sales = len(md.sales)
    sales_per_hour = total_sales / time_span_hrs
    if sales_per_hour < MIN_SALES_PER_HOUR:
        return None

    # Build price-at-time lookup from hourly history
    price_by_hour = {
        point.recorded_at.strftime("%Y-%m-%dT%H"): point.lowest_bin
        for point in md.price_history
    }

    # Try each margin (highest first) — pick the first with 3+ OP sales
    for margin_pct in MARGINS:
        margin = margin_pct / 100.0

        # Count OP sales verified against price at time of sale
        op_sales = 0
        for s in md.sales:
            price_at_time = _get_price_at_time(s.sold_at, price_by_hour, buy_price)
            if s.sold_price >= int(price_at_time * (1 + margin)):
                op_sales += 1

        if op_sales < MIN_OP_SALES:
            continue

        # Calculate profit at current price
        sell_price = int(buy_price * (1 + margin))
        ea_tax = int(sell_price * EA_TAX_RATE)
        net_profit = sell_price - ea_tax - buy_price
        if net_profit <= 0:
            continue

        op_ratio = op_sales / total_sales
        return {
            "player": md.player,
            "buy_price": buy_price,
            "sell_price": sell_price,
            "net_profit": net_profit,
            "margin_pct": margin_pct,
            "op_sales": op_sales,
            "total_sales": total_sales,
            "op_ratio": op_ratio,
            "op_sales_24h": round(op_sales / time_span_hrs * 24, 1),
            "expected_profit": net_profit * op_ratio,
            "sales_per_hour": round(sales_per_hour, 1),
            "time_span_hrs": round(time_span_hrs, 1),
        }

    return None


def _get_price_at_time(sale_time, price_by_hour: dict, fallback: int) -> int:
    """Get market price at sale time, interpolating to nearest available hour."""
    hour_key = sale_time.strftime("%Y-%m-%dT%H")
    if hour_key in price_by_hour:
        return price_by_hour[hour_key]
    for delta in [-1, 1, -2, 2]:
        nearby = (sale_time + timedelta(hours=delta)).strftime("%Y-%m-%dT%H")
        if nearby in price_by_hour:
            return price_by_hour[nearby]
    return fallback
