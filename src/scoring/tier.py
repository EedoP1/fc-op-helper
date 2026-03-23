"""Price tier peer comparison scorer."""

from src.config import PRICE_TIERS


def assign_tier(price: int) -> str:
    """Assign a price tier based on current price."""
    for name, low, high in PRICE_TIERS:
        if low <= price < high:
            return name
    return "elite"


def compute_tier_peer_score(
    player_hoss: float,
    all_hoss_in_tier: list[float],
) -> float:
    """
    Score 0-100 representing this player's HOSS percentile within their tier.

    100 = best OP seller in the tier, 0 = worst.
    """
    if not all_hoss_in_tier or len(all_hoss_in_tier) < 2:
        return 50.0  # neutral with insufficient peers

    sorted_scores = sorted(all_hoss_in_tier)
    n = len(sorted_scores)

    # Count how many scores are below this player's
    below = sum(1 for s in sorted_scores if s < player_hoss)
    percentile = (below / (n - 1)) * 100 if n > 1 else 50.0

    return min(percentile, 100.0)
