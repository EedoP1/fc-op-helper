"""Market timing scorer — when is the best time to OP sell."""

from datetime import datetime, timezone

from src.models import SaleRecord


def compute_timing_score(
    sales: list[SaleRecord],
    current_bin: int,
) -> float:
    """
    Score 0-100 based on current market timing conditions.

    Looks at recent sales velocity and price momentum to determine
    if now is a good time to OP sell this player.
    """
    if not sales:
        return 50.0

    now = datetime.now(timezone.utc)

    # Recent sales velocity (last 2 hours)
    recent_sales = [s for s in sales if (now - s.sold_at).total_seconds() < 7200]
    velocity = len(recent_sales)

    # High velocity = more buyers = better chance of OP sell
    if velocity >= 10:
        velocity_score = 100.0
    elif velocity >= 5:
        velocity_score = 75.0
    elif velocity >= 2:
        velocity_score = 50.0
    elif velocity >= 1:
        velocity_score = 30.0
    else:
        velocity_score = 10.0

    # Price momentum: are recent sales trending up or down?
    if len(recent_sales) >= 2:
        prices = [s.sold_price for s in sorted(recent_sales, key=lambda s: s.sold_at)]
        first_half = prices[:len(prices)//2]
        second_half = prices[len(prices)//2:]
        avg_first = sum(first_half) / len(first_half) if first_half else 0
        avg_second = sum(second_half) / len(second_half) if second_half else 0

        if avg_first > 0:
            momentum = (avg_second - avg_first) / avg_first
            if momentum > 0.02:
                momentum_score = 80.0  # prices rising
            elif momentum > -0.02:
                momentum_score = 60.0  # stable
            else:
                momentum_score = 30.0  # prices dropping
        else:
            momentum_score = 50.0
    else:
        momentum_score = 50.0

    return velocity_score * 0.6 + momentum_score * 0.4
