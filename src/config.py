"""Configuration constants for the OP Seller tool."""

# fut.gg base URL
FUTGG_BASE_URL = "https://www.fut.gg"

# Scoring weights (sum to 1.0)
SCORE_WEIGHTS = {
    "hoss": 0.25,
    "profit_margin": 0.20,
    "price_stability": 0.18,
    "supply": 0.12,
    "tier_peer": 0.10,
    "buyer_psychology": 0.08,
    "market_timing": 0.07,
}

# Price tiers (name, min, max)
PRICE_TIERS = [
    ("micro", 250, 1_000),
    ("low", 1_000, 5_000),
    ("low_mid", 5_000, 15_000),
    ("mid", 15_000, 50_000),
    ("high_mid", 50_000, 150_000),
    ("high", 150_000, 500_000),
    ("elite", 500_000, 10_000_000),
]

# EA tax rate on sales
EA_TAX_RATE = 0.05

# HOSS settings
HOSS_LOOKBACK_DAYS = 30
HOSS_MIN_EVENTS_FOR_CONFIDENCE = 5

# OP margin discovery settings
# Instead of a fixed 10%, we analyze sales data per player to find the
# actual premium buyers pay. These are bounds for that discovery.
OP_MARGIN_MIN = 0.03  # ignore premiums below 3% (noise / rounding)
OP_MARGIN_MAX = 0.50  # ignore premiums above 50% (outliers / mistakes)

# Portfolio constraints
TARGET_PLAYER_COUNT = 100
COPIES_PER_PLAYER = 1
MIN_NET_PROFIT_PCT = 0.03  # minimum 3% net profit after tax to be considered

# Request settings
REQUEST_DELAY_SECONDS = 1.0
REQUEST_TIMEOUT_SECONDS = 30
