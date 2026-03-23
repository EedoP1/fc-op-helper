"""Buyer psychology scorer — emotional premium likelihood."""

# Leagues that command higher emotional premiums
META_LEAGUES = {
    "Premier League": 10,
    "LaLiga EA SPORTS": 8,
    "Serie A Enilive": 7,
    "Bundesliga": 6,
    "Ligue 1 McDonalds": 5,
}

# Card types that drive impulse buying
CARD_TYPE_BONUS = {
    "icon": 20,
    "hero": 15,
    "team-of-the-year": 15,
    "team-of-the-season": 12,
    "future-stars": 12,
    "fantasy-ut": 10,
    "totw": 8,
    "star-performer": 8,
    "rule-breakers": 6,
    "rare": 2,
}

# Meta positions (more desirable = more impulse buys)
META_POSITIONS = {
    "ST": 10, "CF": 8, "CAM": 8, "LW": 7, "RW": 7,
    "CB": 6, "CDM": 5, "CM": 5,
    "LB": 3, "RB": 3, "GK": 2,
}


def compute_psychology_score(
    card_type: str,
    league: str,
    position: str,
    rating: int,
    pace: int,
    meta_rank_score: float = 0.0,
) -> float:
    """
    Score 0-100 estimating how likely buyers are to overpay emotionally.

    Higher for: icons, special cards, meta leagues, popular positions,
    high pace, high rating.
    """
    score = 0.0

    # Card type bonus
    score += CARD_TYPE_BONUS.get(card_type, 0)

    # League bonus
    score += META_LEAGUES.get(league, 0)

    # Position bonus
    score += META_POSITIONS.get(position, 0)

    # Rating bonus: 87-90 is the sweet spot (SBC fodder + usable)
    if 87 <= rating <= 90:
        score += 10
    elif 91 <= rating <= 93:
        score += 8
    elif 85 <= rating <= 86:
        score += 5
    elif rating >= 94:
        score += 6

    # Pace bonus (meta stat)
    if pace >= 90:
        score += 8
    elif pace >= 85:
        score += 5
    elif pace >= 80:
        score += 2

    # Meta rank bonus (from fut.gg metarank)
    if meta_rank_score >= 90:
        score += 15
    elif meta_rank_score >= 80:
        score += 10
    elif meta_rank_score >= 70:
        score += 5

    return min(score, 100.0)
