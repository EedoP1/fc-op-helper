"""Price stability scorer — measures price drop risk."""

from src.models import PricePoint


def compute_stability_score(history: list[PricePoint]) -> float:
    """
    Score 0-100 where 100 = very stable price, 0 = highly volatile/dropping.

    Factors:
    - Coefficient of variation (lower = more stable)
    - Trend direction (rising/flat = good, falling = bad)
    """
    if len(history) < 3:
        return 50.0  # neutral with insufficient data

    prices = [p.lowest_bin for p in history if p.lowest_bin > 0]
    if len(prices) < 3:
        return 50.0

    # Coefficient of variation
    mean_price = sum(prices) / len(prices)
    if mean_price == 0:
        return 0.0
    variance = sum((p - mean_price) ** 2 for p in prices) / len(prices)
    std_dev = variance ** 0.5
    cv = std_dev / mean_price

    # CV score: cv of 0 = 100, cv of 0.3+ = 0
    cv_score = max(0, (1 - cv / 0.3)) * 100

    # Trend score: compare recent prices to older prices
    mid = len(prices) // 2
    old_avg = sum(prices[:mid]) / mid if mid > 0 else mean_price
    new_avg = sum(prices[mid:]) / (len(prices) - mid) if len(prices) - mid > 0 else mean_price

    if old_avg == 0:
        trend_score = 50.0
    else:
        trend_change = (new_avg - old_avg) / old_avg
        # Rising = good (score 70-100), flat = good (50-70), falling = bad (0-50)
        if trend_change > 0.05:
            trend_score = min(70 + trend_change * 300, 100)
        elif trend_change > -0.05:
            trend_score = 60.0  # flat is good
        else:
            trend_score = max(50 + trend_change * 500, 0)

    # Combine: 60% CV, 40% trend
    return cv_score * 0.6 + trend_score * 0.4
