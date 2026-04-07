"""Data models for the OP Seller tool."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class Player(BaseModel):
    """Core player card data."""
    resource_id: int
    name: str
    rating: int
    position: str
    nation: str
    league: str
    club: str
    card_type: str
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


class PricePoint(BaseModel):
    """A single price observation over time."""
    resource_id: int
    recorded_at: datetime
    lowest_bin: int


class PlayerMarketData(BaseModel):
    """Full market data for a player, assembled from fut.gg."""
    player: Player
    current_lowest_bin: int
    listing_count: int
    price_history: list[PricePoint]
    sales: list[SaleRecord]
    live_auction_prices: list[int] = []
    live_auctions_raw: list[dict] = []  # Full liveAuctions entries with all fields preserved
    futgg_url: Optional[str] = None
    max_price_range: Optional[int] = None  # EA max BIN price for this card (priceRange.maxPrice)
    created_at: Optional[datetime] = None  # fut.gg createdAt — when EA released the card
