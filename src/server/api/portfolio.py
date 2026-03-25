"""Portfolio optimization endpoint.

Given a budget, returns the best set of players to OP sell,
using stored scores and the existing optimize_portfolio() engine.
"""
from datetime import datetime, timedelta

from fastapi import APIRouter, Query, Request
from sqlalchemy import select, func

from src.server.models_db import PlayerRecord, PlayerScore
from src.config import STALE_THRESHOLD_HOURS
from src.optimizer import optimize_portfolio


router = APIRouter(prefix="/api/v1")


class _PlayerProxy:
    """Minimal proxy satisfying optimize_portfolio()'s entry['player'].resource_id access."""

    __slots__ = ("resource_id",)

    def __init__(self, ea_id: int):
        self.resource_id = ea_id


def _build_scored_entry(score: PlayerScore, record: PlayerRecord) -> dict:
    """Build a scored-entry dict from DB rows, matching optimize_portfolio()'s expected format.

    A fresh dict is built per request to avoid mutation issues
    (optimize_portfolio mutates input dicts).
    """
    return {
        "player": _PlayerProxy(score.ea_id),
        "buy_price": score.buy_price,
        "sell_price": score.sell_price,
        "net_profit": score.net_profit,
        "margin_pct": score.margin_pct,
        "op_sales": score.op_sales,
        "total_sales": score.total_sales,
        "op_ratio": score.op_ratio,
        "expected_profit": score.expected_profit,
        "efficiency": score.efficiency,
        "sales_per_hour": score.sales_per_hour,
        "ea_id": record.ea_id,
        "name": record.name,
        "rating": record.rating,
        "position": record.position,
        "scan_tier": record.scan_tier,
        "last_scanned_at": record.last_scanned_at,
        "expected_profit_per_hour": score.expected_profit_per_hour,
    }


@router.get("/portfolio")
async def get_portfolio(
    request: Request,
    budget: int = Query(..., gt=0, description="Total budget in coins"),
):
    """Return an optimized portfolio of players to OP sell within the given budget.

    Fetches the latest viable score per player from the DB, runs the
    optimizer, and returns a budget summary with player details.

    Args:
        request: FastAPI Request (app.state carries session_factory).
        budget: Total coin budget (must be > 0).

    Returns:
        Dict with keys: data (list), count, budget, budget_used, budget_remaining.
    """
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        # Subquery: latest scored_at per player for viable scores only
        latest_subq = (
            select(
                PlayerScore.ea_id,
                func.max(PlayerScore.scored_at).label("max_scored_at"),
            )
            .where(PlayerScore.is_viable == True)  # noqa: E712
            .group_by(PlayerScore.ea_id)
            .subquery()
        )

        # Join to get full score row + player record
        stmt = (
            select(PlayerScore, PlayerRecord)
            .join(
                latest_subq,
                (PlayerScore.ea_id == latest_subq.c.ea_id)
                & (PlayerScore.scored_at == latest_subq.c.max_scored_at),
            )
            .join(PlayerRecord, PlayerRecord.ea_id == PlayerScore.ea_id)
            .where(PlayerRecord.is_active == True)  # noqa: E712
        )

        result = await session.execute(stmt)
        rows = result.all()

    # Build fresh scored entries (never cache — optimizer mutates dicts)
    scored_list = [_build_scored_entry(score, record) for score, record in rows]

    # Return early with descriptive error when no viable players exist yet
    if not scored_list:
        return {
            "error": "Not enough listing data yet. The system needs to accumulate market observations before it can recommend players. This typically takes a few hours of scanning.",
            "data": [],
            "count": 0,
            "budget": budget,
            "budget_used": 0,
            "budget_remaining": budget,
        }

    # Run optimizer
    selected = optimize_portfolio(scored_list, budget)

    # Compute budget summary
    budget_used = sum(entry["buy_price"] for entry in selected)

    # Serialize response with staleness
    stale_cutoff = datetime.utcnow() - timedelta(hours=STALE_THRESHOLD_HOURS)
    data = []
    for entry in selected:
        last_scanned_at = entry["last_scanned_at"]
        is_stale = (
            last_scanned_at is None
            or last_scanned_at < stale_cutoff
        )
        epph = entry.get("expected_profit_per_hour")
        data.append({
            "ea_id": entry["ea_id"],
            "name": entry["name"],
            "rating": entry["rating"],
            "position": entry["position"],
            "price": entry["buy_price"],
            "margin_pct": entry["margin_pct"],
            "op_sales": entry["op_sales"],
            "total_sales": entry["total_sales"],
            "op_ratio": round(entry["op_ratio"], 3),
            "expected_profit": round(entry["expected_profit"], 1),
            "efficiency": round(entry["efficiency"], 4),
            "scan_tier": entry["scan_tier"],
            "is_stale": is_stale,
            "last_scanned": (
                last_scanned_at.isoformat() if last_scanned_at else None
            ),
            "expected_profit_per_hour": round(epph, 2) if epph else None,
        })

    return {
        "data": data,
        "count": len(data),
        "budget": budget,
        "budget_used": budget_used,
        "budget_remaining": budget - budget_used,
    }
