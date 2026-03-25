"""Top players API endpoint."""
from datetime import datetime, timedelta

from fastapi import APIRouter, Query, Request
from sqlalchemy import select, func

from src.server.models_db import PlayerRecord, PlayerScore
from src.config import STALE_THRESHOLD_HOURS, TARGET_PLAYER_COUNT

router = APIRouter(prefix="/api/v1")


@router.get("/players/top")
async def get_top_players(
    request: Request,
    limit: int = Query(default=TARGET_PLAYER_COUNT, ge=1, le=500),  # D-01: default 100
    offset: int = Query(default=0, ge=0),                            # D-03: pagination
    price_min: int = Query(default=0, ge=0),                         # D-02: filter
    price_max: int = Query(default=0, ge=0),                         # D-02: filter
):
    """Return top OP-sell players ranked by efficiency.

    Filters to the latest viable score per player, optionally by price range.
    Marks each player with is_stale if last_scanned_at is older than STALE_THRESHOLD_HOURS.

    Args:
        request: FastAPI Request (app.state carries session_factory).
        limit: Max players to return (default 100, max 500).
        offset: Pagination offset.
        price_min: Minimum buy_price filter (inclusive). 0 means no lower bound.
        price_max: Maximum buy_price filter (inclusive). 0 means no upper bound.

    Returns:
        Dict with keys: data (list), count, offset, limit.
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

        # Apply price filters (D-02)
        if price_min > 0:
            stmt = stmt.where(PlayerScore.buy_price >= price_min)
        if price_max > 0:
            stmt = stmt.where(PlayerScore.buy_price <= price_max)

        # Order by efficiency desc, paginate (D-03)
        stmt = stmt.order_by(PlayerScore.efficiency.desc()).offset(offset).limit(limit)

        result = await session.execute(stmt)
        rows = result.all()

    stale_cutoff = datetime.utcnow() - timedelta(hours=STALE_THRESHOLD_HOURS)
    players = []
    for score, player in rows:
        is_stale = (
            player.last_scanned_at is None
            or player.last_scanned_at < stale_cutoff  # D-12, D-13
        )
        players.append(
            {
                "ea_id": player.ea_id,                          # D-04
                "name": player.name,                            # D-04
                "price": score.buy_price,                       # D-04
                "margin_pct": score.margin_pct,                 # D-04
                "op_ratio": round(score.op_ratio, 3),           # D-04
                "expected_profit": round(score.expected_profit, 1),  # D-04
                "efficiency": round(score.efficiency, 4),       # D-04
                "last_scanned": (
                    player.last_scanned_at.isoformat()
                    if player.last_scanned_at else None
                ),                                              # D-04
                "is_stale": is_stale,                           # D-11, D-13
                "scan_tier": player.scan_tier,
                "rating": player.rating,
                "position": player.position,
            }
        )

    return {"data": players, "count": len(players), "offset": offset, "limit": limit}
