"""Configuration constants for the OP Seller tool."""
import os

# EA tax rate on sales
EA_TAX_RATE = 0.05

# Portfolio constraints
TARGET_PLAYER_COUNT = 100

# Scanner scheduling intervals (seconds)
SCAN_INTERVAL_SECONDS = 300       # 5-minute fixed scan interval
STALE_THRESHOLD_HOURS = 4         # per D-12: stale after 4 hours

# Circuit breaker
CB_FAILURE_THRESHOLD = 5          # failures before OPEN
CB_RECOVERY_TIMEOUT = 60.0        # seconds before HALF_OPEN
CB_SUCCESS_THRESHOLD = 2          # successes in HALF_OPEN before CLOSED

# Scanner concurrency
SCAN_CONCURRENCY = 10             # max concurrent scan tasks per dispatch cycle
SCAN_DISPATCH_BATCH_SIZE = 150    # max players fetched per dispatch cycle (caps task burst)
                                  # Must fit within dispatch interval: 150 * 2 calls = 300 reqs
                                  # at 10 req/s = 30s — matches SCAN_DISPATCH_INTERVAL
SCAN_DISPATCH_INTERVAL = 30       # seconds between dispatch checks

# Initial scoring (one-time after bootstrap)
INITIAL_SCORING_CONCURRENCY = 10  # double normal concurrency for fast one-time pass
INITIAL_SCORING_BATCH_SIZE = 50   # players per batch to avoid overwhelming event loop

# Price range for scanner discovery
SCANNER_MIN_PRICE = 11_000
# Sentinel: 0 = no upper-bound price cap. Keeps high-priced release-day
# TOTS/TOTY/Icon promos in the scanner's discovery set.
# (Interpreted by FutGGClient.discover_players and MockClient.)
SCANNER_MAX_PRICE = 0

# Market data retention
MARKET_DATA_RETENTION_DAYS = 30  # days to keep raw market snapshots

# Listing tracking
LISTING_RETENTION_DAYS = 7            # days to keep individual listing observations (per D-12)
MIN_TOTAL_RESOLVED_OBSERVATIONS = 20  # quality threshold: min total resolved observations for a trustworthy score
MIN_SALES_PER_HOUR = 7                # minimum real sales/hour from completedAuctions to be viable
AGGREGATION_HOUR_UTC = 3              # UTC hour for nightly daily summary aggregation
MIN_OP_OBSERVATIONS = 3               # minimum OP listings at a margin to consider it viable

# OP sell margin tiers (highest first)
MARGINS = [40, 35, 30, 25, 20, 15, 10, 8, 5, 3]

# Scorer v3 weights: score = sell_ratio^W1 × sph^W2 × net_profit^W3
SCORER_V3_W1 = 1  # sell_ratio weight (demand vs supply)
SCORER_V3_W2 = 1  # sales_per_hour weight (liquidity)
SCORER_V3_W3 = 1  # net_profit weight (profit per sale)
MAX_OP_MARGIN_PCT = 44  # ignore listings priced above this margin (junk/troll listings)

# Database
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql+asyncpg://op_seller:op_seller@localhost:5432/op_seller")
