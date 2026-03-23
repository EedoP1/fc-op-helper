"""Supply scorer — thin markets are better for OP selling."""


def compute_supply_score(listing_count: int) -> float:
    """
    Score 0-100 where 100 = very thin supply (few listings), 0 = flooded market.

    Fewer listings means less competition and buyers are more likely to buy
    at inflated prices because they have fewer options to compare.
    """
    if listing_count <= 0:
        return 50.0  # no data

    # Sweet spot: 1-5 listings is ideal, 50+ is bad
    if listing_count <= 3:
        return 100.0
    elif listing_count <= 5:
        return 90.0
    elif listing_count <= 10:
        return 75.0
    elif listing_count <= 20:
        return 55.0
    elif listing_count <= 50:
        return 30.0
    else:
        return max(10.0, 30.0 - (listing_count - 50) * 0.5)
