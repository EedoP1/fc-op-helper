"""Recent sales endpoint — FIFO-matched completed sales feed.

Returns your actual completed sales with buy price attached via FIFO matching,
most recent first. Used by the dashboard Recent Sales tab.
"""
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Query, Request
from sqlalchemy import select

from src.server.models_db import TradeRecord, PlayerRecord
from src.server.api.profit import _parse_since
from src.config import EA_TAX_RATE

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")


@router.get("/trades/recent")
async def get_recent_sales(
    request: Request,
    since: Optional[str] = Query(default=None),
):
    """Return completed sales with FIFO-matched buy prices.

    Args:
        request: FastAPI Request (app.state carries session_factory).
        since: Time filter — '1h', '24h', '7d', '30d', 'all', or omit for all data.

    Returns:
        Dict with 'sales' list of matched sale records.
    """
    cutoff = _parse_since(since)

    session_factory = request.app.state.read_session_factory
    async with session_factory() as session:
        stmt = (
            select(TradeRecord.ea_id, TradeRecord.outcome, TradeRecord.price, TradeRecord.recorded_at)
            .order_by(TradeRecord.recorded_at, TradeRecord.id)
        )
        if cutoff is not None:
            stmt = stmt.where(TradeRecord.recorded_at >= cutoff)

        trades_result = await session.execute(stmt)
        trades = trades_result.all()

        name_result = await session.execute(
            select(PlayerRecord.ea_id, PlayerRecord.name)
        )
        name_map: dict[int, str] = {row.ea_id: row.name for row in name_result.all()}

    # Group trades by ea_id
    trades_by_player: dict[int, list] = {}
    for row in trades:
        trades_by_player.setdefault(row.ea_id, []).append(row)

    # FIFO matching — collect completed sales with buy price
    sales = []
    for ea_id, player_trades in trades_by_player.items():
        buy_queue: list[int] = []
        for trade in player_trades:
            if trade.outcome == "bought":
                buy_queue.append(trade.price)
            elif trade.outcome == "sold" and buy_queue:
                buy_price = buy_queue.pop(0)
                earned = int(trade.price * (1 - EA_TAX_RATE))
                profit = earned - buy_price
                sales.append({
                    "ea_id": ea_id,
                    "name": name_map.get(ea_id, f"Player {ea_id}"),
                    "buy_price": buy_price,
                    "sell_price": trade.price,
                    "profit": profit,
                    "sold_at": trade.recorded_at.isoformat(),
                })

    # Sort by sold_at desc (most recent first)
    sales.sort(key=lambda x: x["sold_at"], reverse=True)

    return {"sales": sales}
