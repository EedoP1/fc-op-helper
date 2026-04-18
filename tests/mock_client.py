"""Mock market data client for testing."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Optional

from src.models import Player, PlayerMarketData, PricePoint, SaleRecord


def make_player(
    ea_id: int = 1,
    name: str = "Test Player",
    rating: int = 88,
    price: int = 20000,
    num_sales: int = 100,
    op_sales_pct: float = 0.10,
    op_margin: float = 0.40,
    num_listings: int = 30,
    hours_of_data: float = 10.0,
) -> PlayerMarketData:
    """
    Create a PlayerMarketData with controllable parameters.

    Args:
        ea_id: EA resource ID
        name: Player name
        rating: Card rating
        price: Current lowest BIN
        num_sales: Total completed sales
        op_sales_pct: What fraction of sales are OP (0.0 to 1.0)
        op_margin: How far above market price the OP sales are
        num_listings: Number of live listings
        hours_of_data: Time span the sales cover
    """
    now = datetime.now(timezone.utc)
    op_count = int(num_sales * op_sales_pct)
    normal_count = num_sales - op_count

    # Build sales spread over hours_of_data
    sales = []
    for i in range(normal_count):
        t = now - timedelta(hours=hours_of_data * (i / max(num_sales, 1)))
        # Normal sales at or slightly below market price
        sales.append(SaleRecord(
            resource_id=ea_id,
            sold_at=t,
            sold_price=price - (i % 5) * 100,  # slight variance
        ))

    for i in range(op_count):
        t = now - timedelta(hours=hours_of_data * ((normal_count + i) / max(num_sales, 1)))
        # OP sales above market by op_margin
        op_price = int(price * (1 + op_margin)) + (i % 3) * 500
        sales.append(SaleRecord(
            resource_id=ea_id,
            sold_at=t,
            sold_price=op_price,
        ))

    # Price history — stable price over the data window
    price_history = []
    for h in range(int(hours_of_data) + 1):
        price_history.append(PricePoint(
            resource_id=ea_id,
            recorded_at=now - timedelta(hours=h),
            lowest_bin=price,
        ))

    # Live listings — mostly at market, some above
    live_prices = [price] * (num_listings - 5) + [int(price * 1.3)] * 5

    return PlayerMarketData(
        player=Player(
            resource_id=ea_id,
            name=name,
            rating=rating,
            position="ST",
            nation="Test",
            league="Test League",
            club="Test FC",
            card_type="gold",
        ),
        current_lowest_bin=price,
        listing_count=num_listings,
        price_history=price_history,
        sales=sales,
        live_auction_prices=live_prices,
    )


class MockClient:
    """Mock market data client that returns predefined players."""

    def __init__(self, players: list[PlayerMarketData]):
        self._players = {p.player.resource_id: p for p in players}

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def discover_players(
        self, budget: int, min_price: int = 0, max_price: int = 0,
    ) -> list[dict]:
        # Mirror real-client semantics: max_price <= 0 means "no upper bound".
        return [
            {"ea_id": p.player.resource_id, "price": p.current_lowest_bin}
            for p in self._players.values()
            if p.current_lowest_bin >= min_price
            and (max_price <= 0 or p.current_lowest_bin <= max_price)
        ]

    async def get_batch_market_data(
        self, ea_ids: list[int], concurrency: int = 5,
    ) -> list[Optional[PlayerMarketData]]:
        return [self._players.get(eid) for eid in ea_ids]
