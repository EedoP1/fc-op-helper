"""Stale cards endpoint.

Identifies cards that have been held longest without selling (longest_unsold)
and cards with the slowest sale frequency (avg_sale_time). Uses FIFO buy-sell
matching to determine which buys remain unsold.
"""
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Query, Request
from sqlalchemy import select

from src.server.models_db import TradeRecord, PlayerRecord
from src.server.api.profit import _parse_since

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")


@router.get("/portfolio/stale")
async def get_stale_cards(
    request: Request,
    since: Optional[str] = Query(default=None),
):
    """Return stale card analysis with two views.

    View 1 (longest_unsold): Cards bought but not yet sold, ranked by hold time.
    Uses FIFO matching to find unmatched buys.

    View 2 (avg_sale_time): Per-card sale frequency. Cards with no sales are
    penalized to the bottom.

    Args:
        request: FastAPI Request (app.state carries session_factory).
        since: Optional time filter -- '1h', '24h', '7d', '30d', 'all', or None.

    Returns:
        Dict with 'longest_unsold' and 'avg_sale_time' lists.
    """
    cutoff = _parse_since(since)
    now = datetime.now(timezone.utc)

    session_factory = request.app.state.read_session_factory
    async with session_factory() as session:
        # Fetch trade records, optionally filtered by time
        stmt = (
            select(TradeRecord.ea_id, TradeRecord.outcome, TradeRecord.price, TradeRecord.recorded_at)
            .order_by(TradeRecord.recorded_at, TradeRecord.id)
        )
        if cutoff is not None:
            stmt = stmt.where(TradeRecord.recorded_at >= cutoff)
        trades_result = await session.execute(stmt)
        trades = trades_result.all()

        # Latest outcome per ea_id (always unfiltered for status)
        latest_outcome_stmt = (
            select(TradeRecord.ea_id, TradeRecord.outcome)
            .order_by(TradeRecord.recorded_at.desc(), TradeRecord.id.desc())
        )
        latest_result = await session.execute(latest_outcome_stmt)
        latest_outcome_map: dict[int, str] = {}
        for row in latest_result.all():
            if row.ea_id not in latest_outcome_map:
                latest_outcome_map[row.ea_id] = row.outcome

        # Player names
        name_result = await session.execute(
            select(PlayerRecord.ea_id, PlayerRecord.name)
        )
        name_map: dict[int, str] = {row.ea_id: row.name for row in name_result.all()}

    # Group trades by ea_id
    trades_by_player: dict[int, list] = {}
    for row in trades:
        trades_by_player.setdefault(row.ea_id, []).append(row)

    # -- View 1: longest_unsold --------------------------------------------------
    longest_unsold = []

    for ea_id, player_trades in trades_by_player.items():
        buy_queue: list[tuple[int, datetime]] = []  # (price, recorded_at)

        for trade in player_trades:
            if trade.outcome == "bought":
                buy_queue.append((trade.price, trade.recorded_at))
            elif trade.outcome == "sold" and buy_queue:
                buy_queue.pop(0)  # FIFO match

        # Remaining unmatched buys are unsold
        for buy_price, bought_at in buy_queue:
            latest = latest_outcome_map.get(ea_id, "bought")
            status = "LISTED" if latest == "listed" else "BOUGHT"
            delta_hours = (now - bought_at).total_seconds() / 3600.0

            longest_unsold.append({
                "ea_id": ea_id,
                "name": name_map.get(ea_id, f"Player {ea_id}"),
                "buy_price": buy_price,
                "bought_at": bought_at.isoformat(),
                "time_since_buy_hours": round(delta_hours, 1),
                "status": status,
            })

    # Sort by hold time descending
    longest_unsold.sort(key=lambda x: x["time_since_buy_hours"], reverse=True)

    # -- View 2: avg_sale_time ---------------------------------------------------
    avg_sale_time = []

    for ea_id, player_trades in trades_by_player.items():
        sell_count = sum(1 for t in player_trades if t.outcome == "sold")
        timestamps = [t.recorded_at for t in player_trades]
        first_activity = min(timestamps)
        last_activity = max(timestamps)
        time_period_hours = (last_activity - first_activity).total_seconds() / 3600.0

        if sell_count > 0:
            avg_hours = time_period_hours / sell_count
        else:
            avg_hours = None

        avg_sale_time.append({
            "ea_id": ea_id,
            "name": name_map.get(ea_id, f"Player {ea_id}"),
            "total_sales": sell_count,
            "first_activity": first_activity.isoformat(),
            "last_activity": last_activity.isoformat(),
            "time_period_hours": round(time_period_hours, 1),
            "avg_hours_between_sales": round(avg_hours, 1) if avg_hours is not None else None,
        })

    # Sort: cards with sales by avg_hours desc (slowest first), no-sales at bottom
    with_sales = [e for e in avg_sale_time if e["avg_hours_between_sales"] is not None]
    without_sales = [e for e in avg_sale_time if e["avg_hours_between_sales"] is None]
    with_sales.sort(key=lambda x: x["avg_hours_between_sales"], reverse=True)
    avg_sale_time = with_sales + without_sales

    return {
        "longest_unsold": longest_unsold,
        "avg_sale_time": avg_sale_time,
    }
