"""Data models for the OP Seller tool."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel


class ConfidenceTier(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class Player(BaseModel):
    """Core player card data."""
    resource_id: int
    name: str
    rating: int
    position: str
    nation: str
    league: str
    club: str
    card_type: str  # gold, icon, hero, tots, toty, if, etc.
    pace: int = 0
    shooting: int = 0
    passing: int = 0
    dribbling: int = 0
    defending: int = 0
    physical: int = 0


class SaleRecord(BaseModel):
    """A single completed sale from fut.gg."""
    resource_id: int
    sold_at: datetime
    sold_price: int
    lowest_bin_at_time: int  # floor price at the time of sale


class PricePoint(BaseModel):
    """A single price observation over time."""
    resource_id: int
    recorded_at: datetime
    lowest_bin: int
    median_bin: Optional[int] = None
    listing_count: Optional[int] = None


class PlayerMarketData(BaseModel):
    """Full market data for a player, assembled from fut.gg."""
    player: Player
    current_lowest_bin: int
    listing_count: int  # total live listings
    op_listing_count: int  # listings priced above floor (OP competitors)
    price_history: list[PricePoint]  # 30-day history
    sales: list[SaleRecord]  # 30-day sales
    live_auction_prices: list[int] = []  # BIN prices of currently active listings
    live_auction_end_times: list[str] = []  # ISO timestamps of when each listing expires
    base_player_ea_id: int = 0
    base_player_slug: str = ""


class HOSSResult(BaseModel):
    """Output of the Historical OP Success Score calculator."""
    score: float  # 0-100
    op_event_count: int  # how many sales were above floor
    total_sales: int
    op_sell_rate: float  # op_event_count / total_sales
    avg_op_premium: float  # average % premium paid on OP sales
    best_op_margin: float  # the margin with the highest expected profit
    confidence: float  # 0.0-1.0
    active_days: int
    sales_per_hour: float  # total market sales velocity
    op_sales_per_hour: float  # total market OP sales velocity
    my_op_sells_per_hour: float  # OUR single listing's expected sell rate


class PlayerScore(BaseModel):
    """All sub-scores and composite for a player."""
    resource_id: int
    hoss: float = 0.0
    profit_margin: float = 0.0
    price_stability: float = 0.0
    supply: float = 0.0
    tier_peer: float = 0.0
    buyer_psychology: float = 0.0
    market_timing: float = 0.0
    composite: float = 0.0


class Recommendation(BaseModel):
    """A single player recommendation in the final output list."""
    rank: int
    player: Player
    current_buy_price: int
    recommended_list_price: int
    expected_net_profit: int
    expected_net_profit_pct: float
    best_op_margin: float  # data-driven optimal margin for this player
    op_sales_per_hour: float  # how often this card sells at OP price
    expected_profit_per_hour: float  # net_profit × op_sales_per_hour
    confidence: ConfidenceTier
    hoss_score: float
    composite_score: float
    price_tier: str
    risk_flags: list[str] = []
    upside_flags: list[str] = []


class PortfolioSummary(BaseModel):
    """Summary stats for the full 100-player portfolio."""
    total_budget: int
    total_used: int
    total_expected_profit: int  # profit if all sell once
    total_profit_per_hour: float  # sum of all profit_per_hour
    expected_profit_pct: float
    player_count: int
    high_confidence_count: int
    medium_confidence_count: int
    low_confidence_count: int
    recommendations: list[Recommendation]
