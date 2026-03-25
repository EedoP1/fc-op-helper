"""Configuration constants for the OP Seller tool."""

# EA tax rate on sales
EA_TAX_RATE = 0.05

# Portfolio constraints
TARGET_PLAYER_COUNT = 100

# Scanner scheduling intervals (seconds)
SCAN_INTERVAL_HOT = 30 * 60       # 30 minutes
SCAN_INTERVAL_NORMAL = 60 * 60    # 1 hour
SCAN_INTERVAL_COLD = int(2.5 * 3600)  # 2.5 hours
STALE_THRESHOLD_HOURS = 4         # per D-12: stale after 4 hours

# Circuit breaker
CB_FAILURE_THRESHOLD = 5          # failures before OPEN
CB_RECOVERY_TIMEOUT = 60.0        # seconds before HALF_OPEN
CB_SUCCESS_THRESHOLD = 2          # successes in HALF_OPEN before CLOSED

# Scanner concurrency
SCAN_CONCURRENCY = 5              # lower than CLI's 10 for 24/7 safety
SCAN_DISPATCH_INTERVAL = 30       # seconds between dispatch checks

# Initial scoring (one-time after bootstrap)
INITIAL_SCORING_CONCURRENCY = 10  # double normal concurrency for fast one-time pass
INITIAL_SCORING_BATCH_SIZE = 50   # players per batch to avoid overwhelming event loop

# Price range for scanner discovery
SCANNER_MIN_PRICE = 11_000
SCANNER_MAX_PRICE = 200_000

# Tier promotion thresholds
TIER_PROFIT_THRESHOLD = 500       # expected_profit above this promotes to "hot" regardless of activity (per API-04)

# Adaptive scheduling (per-player interval adjustment within tiers)
ADAPTIVE_CHANGE_THRESHOLD = 0.25     # 25% change in sales_per_hour triggers interval shortening
ADAPTIVE_MIN_INTERVAL_SECONDS = 300  # 5-minute floor for adaptive intervals

# Market data retention
MARKET_DATA_RETENTION_DAYS = 30  # days to keep raw market snapshots

# Listing tracking
LISTING_RETENTION_DAYS = 7            # days to keep individual listing observations (per D-12)
BOOTSTRAP_MIN_OBSERVATIONS = 10       # min resolved listings before v2 scorer activates per player
LISTING_SCAN_BUFFER_SECONDS = 240     # 4-minute safety buffer before youngest listing expiry (per D-06)
SCORING_JOB_INTERVAL_MINUTES = 15     # how often the v2 scoring job runs
AGGREGATION_HOUR_UTC = 3              # UTC hour for nightly daily summary aggregation
MIN_OP_OBSERVATIONS = 3               # minimum OP listings at a margin to consider it viable

# Database
DATABASE_URL = "sqlite+aiosqlite:///D:/op-seller/op_seller.db"
