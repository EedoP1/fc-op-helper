"""Profit summary endpoint.

Returns total coins spent, earned (net of EA 5% tax), net profit, and
trade count — both as overall totals and broken down per player.
"""
import logging

from fastapi import APIRouter, Request
from sqlalchemy import select, func, case

from src.server.models_db import TradeRecord, PlayerRecord
from src.config import EA_TAX_RATE

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")


@router.get("/profit/summary")
async def get_profit_summary(request: Request):
    """Return total and per-player profit breakdown.

    Aggregates trade_records: sums 'bought' prices as spent,
    sums 'sold' prices × (1 - EA_TAX_RATE) as earned, and computes
    net profit. Results include totals and per_player breakdown.

    Args:
        request: FastAPI Request (app.state carries session_factory).

    Returns:
        Dict with keys:
            totals: {total_spent, total_earned, net_profit, trade_count}
            per_player: list of {ea_id, name, total_spent, total_earned, net_profit, trade_count}
    """
    session_factory = request.app.state.read_session_factory
    async with session_factory() as session:
        # Aggregate per ea_id: sum bought prices, sum sold prices, count all records
        agg_stmt = (
            select(
                TradeRecord.ea_id,
                func.sum(
                    case((TradeRecord.outcome == "bought", TradeRecord.price), else_=0)
                ).label("total_spent"),
                func.sum(
                    case((TradeRecord.outcome == "sold", TradeRecord.price), else_=0)
                ).label("total_earned_gross"),
                func.count(TradeRecord.id).label("trade_count"),
            )
            .group_by(TradeRecord.ea_id)
        )
        agg_result = await session.execute(agg_stmt)
        agg_rows = agg_result.all()

        # Fetch player names in one query
        if agg_rows:
            ea_ids = [row.ea_id for row in agg_rows]
            name_stmt = select(PlayerRecord.ea_id, PlayerRecord.name).where(
                PlayerRecord.ea_id.in_(ea_ids)
            )
            name_result = await session.execute(name_stmt)
            name_map: dict[int, str] = {row.ea_id: row.name for row in name_result.all()}
        else:
            name_map = {}

    # Build per-player breakdown
    per_player = []
    for row in agg_rows:
        total_earned = int(row.total_earned_gross * (1 - EA_TAX_RATE))
        net = total_earned - int(row.total_spent)
        per_player.append({
            "ea_id": row.ea_id,
            "name": name_map.get(row.ea_id, f"Player {row.ea_id}"),
            "total_spent": int(row.total_spent),
            "total_earned": total_earned,
            "net_profit": net,
            "trade_count": int(row.trade_count),
        })

    # Compute overall totals
    total_spent = sum(p["total_spent"] for p in per_player)
    total_earned = sum(p["total_earned"] for p in per_player)
    trade_count = sum(p["trade_count"] for p in per_player)

    return {
        "totals": {
            "total_spent": total_spent,
            "total_earned": total_earned,
            "net_profit": total_earned - total_spent,
            "trade_count": trade_count,
        },
        "per_player": per_player,
    }
