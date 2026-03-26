"""Portfolio optimization endpoint.

Given a budget, returns the best set of players to OP sell,
using stored scores and the existing optimize_portfolio() engine.
Also provides DELETE endpoint to swap a player out of the portfolio.
"""
import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Query, Request, Path, HTTPException
from sqlalchemy import select, func, update, delete

from src.server.models_db import PlayerRecord, PlayerScore, PortfolioSlot, TradeAction
from src.config import STALE_THRESHOLD_HOURS
from src.optimizer import optimize_portfolio

logger = logging.getLogger(__name__)

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


@router.delete("/portfolio/{ea_id}")
async def delete_portfolio_player(
    request: Request,
    ea_id: int = Path(..., description="EA ID of the player to remove"),
    budget: int = Query(..., gt=0, description="Total portfolio budget in coins"),
):
    """Remove a player from the portfolio and return replacement suggestions.

    Cancels any PENDING or IN_PROGRESS trade actions for the removed player,
    deletes the PortfolioSlot, then runs the optimizer with the freed budget
    to suggest replacement players.

    Trade history (TradeRecord rows) is preserved — only the active slot and
    pending actions are removed.

    Args:
        request: FastAPI Request (app.state carries session_factory).
        ea_id: EA ID of the player to remove.
        budget: Total portfolio budget used to compute freed_budget context.

    Returns:
        Dict with keys: removed_ea_id, freed_budget, replacements (list).

    Raises:
        HTTPException 404: If ea_id is not in portfolio_slots.
    """
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        # 1. Look up the slot
        slot_result = await session.execute(
            select(PortfolioSlot).where(PortfolioSlot.ea_id == ea_id)
        )
        slot = slot_result.scalar_one_or_none()
        if slot is None:
            raise HTTPException(status_code=404, detail="Player not in portfolio")

        freed_budget = slot.buy_price

        # 2. Cancel pending/in-progress actions for this player
        await session.execute(
            update(TradeAction)
            .where(
                TradeAction.ea_id == ea_id,
                TradeAction.status.in_(["PENDING", "IN_PROGRESS"]),
            )
            .values(status="CANCELLED")
        )

        # 3. Delete the portfolio slot
        await session.execute(
            delete(PortfolioSlot).where(PortfolioSlot.ea_id == ea_id)
        )

        # 4. Get remaining portfolio ea_ids (for exclusion from candidates)
        remaining_result = await session.execute(select(PortfolioSlot.ea_id))
        remaining_ea_ids = {row[0] for row in remaining_result.all()}

        # 5. Query viable candidates (same pattern as get_portfolio)
        latest_subq = (
            select(
                PlayerScore.ea_id,
                func.max(PlayerScore.scored_at).label("max_scored_at"),
            )
            .where(PlayerScore.is_viable == True)  # noqa: E712
            .group_by(PlayerScore.ea_id)
            .subquery()
        )

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
        rows_result = await session.execute(stmt)
        rows = rows_result.all()

        await session.commit()

    # 6. Build scored candidates excluding removed player and remaining slots
    excluded = remaining_ea_ids | {ea_id}
    scored_candidates = [
        _build_scored_entry(score, record)
        for score, record in rows
        if score.ea_id not in excluded
    ]

    # 7. Run optimizer for replacements within freed budget
    replacements_raw = optimize_portfolio(scored_candidates, freed_budget) if scored_candidates else []

    # 8. Serialize replacements
    replacements = [
        {
            "ea_id": entry["ea_id"],
            "name": entry["name"],
            "buy_price": entry["buy_price"],
            "sell_price": entry["sell_price"],
            "margin_pct": entry["margin_pct"],
            "expected_profit_per_hour": round(entry["expected_profit_per_hour"], 2)
            if entry.get("expected_profit_per_hour")
            else None,
        }
        for entry in replacements_raw
    ]

    logger.info(
        "Removed ea_id=%d from portfolio (freed=%d), returning %d replacements",
        ea_id, freed_budget, len(replacements),
    )

    return {
        "removed_ea_id": ea_id,
        "freed_budget": freed_budget,
        "replacements": replacements,
    }
