"""
Protocols (interfaces) for swappable dependencies.

These define what each component must provide, without tying
the code to any specific implementation. To swap fut.gg for
another data source, just implement MarketDataClient.
"""

from __future__ import annotations

from typing import Optional, Protocol

from src.models import PlayerMarketData


class MarketDataClient(Protocol):
    """Interface for any market data provider (fut.gg, FUTBIN, mock, etc.)."""

    async def start(self) -> None: ...
    async def stop(self) -> None: ...

    async def discover_players(
        self, budget: int, min_price: int = 0, max_price: int = 0,
    ) -> list[dict]:
        """Return list of candidate dicts with at least 'ea_id' and 'price' keys."""
        ...

    async def get_batch_market_data(
        self, ea_ids: list[int], concurrency: int = 5,
    ) -> list[Optional[PlayerMarketData]]:
        """Fetch full market data for multiple players."""
        ...
