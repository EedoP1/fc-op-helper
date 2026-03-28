"""Top players API endpoint."""
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Query, Request
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
    session_factory = request.app.state.read_session_factory
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
                "rating": player.rating,
                "position": player.position,
                "expected_profit_per_hour": round(score.expected_profit_per_hour, 2) if score.expected_profit_per_hour else None,
            }
        )

    return {"data": players, "count": len(players), "offset": offset, "limit": limit}


# ── Trend computation ─────────────────────────────────────────────────────────

def _compute_trend(history: list[PlayerScore]) -> dict:
    """Compute trend indicators from score history (newest-first order).

    Args:
        history: PlayerScore rows ordered by scored_at DESC.

    Returns:
        Dict with direction ("up"/"down"/"stable"), price_change (int),
        efficiency_change (float).
    """
    if len(history) < 2:
        return {"direction": "stable", "price_change": 0, "efficiency_change": 0.0}
    newest = history[0]
    oldest = history[-1]
    price_delta = newest.buy_price - oldest.buy_price
    eff_delta = round(newest.efficiency - oldest.efficiency, 4)
    if eff_delta > 0.005:
        direction = "up"
    elif eff_delta < -0.005:
        direction = "down"
    else:
        direction = "stable"
    return {"direction": direction, "price_change": price_delta, "efficiency_change": eff_delta}


# ── Player detail endpoint ────────────────────────────────────────────────────

@router.get("/players/{ea_id}")
async def get_player(request: Request, ea_id: int):
    """Return full player detail with current score, history, and trend.

    Args:
        request: FastAPI Request (app.state carries session_factory).
        ea_id: EA resource ID of the player.

    Returns:
        Dict with player metadata, current_score, score_history (last 24),
        and trend indicators.

    Raises:
        HTTPException: 404 if the player is not found.
    """
    session_factory = request.app.state.read_session_factory
    async with session_factory() as session:
        record = await session.get(PlayerRecord, ea_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Player not found")

        # Latest viable score for this player
        latest_stmt = (
            select(PlayerScore)
            .where(PlayerScore.ea_id == ea_id, PlayerScore.is_viable == True)  # noqa: E712
            .order_by(PlayerScore.scored_at.desc())
            .limit(1)
        )
        latest_result = await session.execute(latest_stmt)
        latest = latest_result.scalars().first()

        # Score history: last 24 entries regardless of is_viable
        history_stmt = (
            select(PlayerScore)
            .where(PlayerScore.ea_id == ea_id)
            .order_by(PlayerScore.scored_at.desc())
            .limit(24)
        )
        history_result = await session.execute(history_stmt)
        history_rows = list(history_result.scalars().all())

    # Trend from viable scores only
    viable_history = [s for s in history_rows if s.is_viable]
    trend = _compute_trend(viable_history)

    # Staleness check (same as get_top_players)
    is_stale = (
        record.last_scanned_at is None
        or record.last_scanned_at < datetime.utcnow() - timedelta(hours=STALE_THRESHOLD_HOURS)
    )

    return {
        "ea_id": record.ea_id,
        "name": record.name,
        "rating": record.rating,
        "position": record.position,
        "nation": record.nation,
        "league": record.league,
        "club": record.club,
        "card_type": record.card_type,
        "last_scanned": record.last_scanned_at.isoformat() if record.last_scanned_at else None,
        "is_stale": is_stale,
        "current_score": {
            "buy_price": latest.buy_price,
            "sell_price": latest.sell_price,
            "net_profit": latest.net_profit,
            "margin_pct": latest.margin_pct,
            "op_sales": latest.op_sales,
            "total_sales": latest.total_sales,
            "op_ratio": round(latest.op_ratio, 3),
            "expected_profit": round(latest.expected_profit, 1),
            "efficiency": round(latest.efficiency, 4),
            "sales_per_hour": latest.sales_per_hour,
            "scored_at": latest.scored_at.isoformat(),
            "expected_profit_per_hour": round(latest.expected_profit_per_hour, 2) if latest.expected_profit_per_hour else None,
        } if latest else None,
        "score_history": [
            {
                "scored_at": s.scored_at.isoformat(),
                "buy_price": s.buy_price,
                "efficiency": round(s.efficiency, 4),
                "expected_profit": round(s.expected_profit, 1),
                "op_ratio": round(s.op_ratio, 3),
                "is_viable": s.is_viable,
                "expected_profit_per_hour": round(s.expected_profit_per_hour, 2) if s.expected_profit_per_hour else None,
            }
            for s in history_rows
        ],
        "trend": trend,
    }
