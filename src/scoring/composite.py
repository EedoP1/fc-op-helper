"""Composite scorer — combines all sub-scores into a single ranking."""

from src.config import SCORE_WEIGHTS
from src.models import PlayerScore


def compute_composite(
    hoss: float,
    profit_margin: float,
    price_stability: float,
    supply: float,
    tier_peer: float,
    buyer_psychology: float,
    market_timing: float,
    confidence: float = 1.0,
) -> PlayerScore:
    """
    Compute weighted composite score from all sub-scores.

    When confidence is low (new card, little data), HOSS and tier_peer
    weights are redistributed to profit_margin and supply.
    """
    weights = dict(SCORE_WEIGHTS)

    # Redistribute HOSS/tier weights when confidence is low
    if confidence < 1.0:
        displaced = (
            weights["hoss"] * (1 - confidence)
            + weights["tier_peer"] * (1 - confidence)
        )
        weights["hoss"] *= confidence
        weights["tier_peer"] *= confidence
        weights["profit_margin"] += displaced * 0.6
        weights["supply"] += displaced * 0.4

        # Renormalize
        total = sum(weights.values())
        weights = {k: v / total for k, v in weights.items()}

    composite = (
        hoss * weights["hoss"]
        + profit_margin * weights["profit_margin"]
        + price_stability * weights["price_stability"]
        + supply * weights["supply"]
        + tier_peer * weights["tier_peer"]
        + buyer_psychology * weights["buyer_psychology"]
        + market_timing * weights["market_timing"]
    )

    return PlayerScore(
        resource_id=0,  # set by caller
        hoss=round(hoss, 2),
        profit_margin=round(profit_margin, 2),
        price_stability=round(price_stability, 2),
        supply=round(supply, 2),
        tier_peer=round(tier_peer, 2),
        buyer_psychology=round(buyer_psychology, 2),
        market_timing=round(market_timing, 2),
        composite=round(composite, 2),
    )
