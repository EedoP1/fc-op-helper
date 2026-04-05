"""Profit summary endpoint.

Buy-anchored FIFO profit calculation:
- Each buy record is matched to the next chronological sell for that ea_id.
- Matched pairs → realized P&L.  Unmatched buys → unrealized P&L.
- Sells without buys are ignored (ghost / pre-bot events).
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import select, func

from src.server.models_db import TradeRecord, PlayerRecord, MarketSnapshot
from src.config import EA_TAX_RATE

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")

# Valid since parameter values and their timedelta offsets
_SINCE_OFFSETS = {
    "1h": timedelta(hours=1),
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}


def _parse_since(since: Optional[str]) -> Optional[datetime]:
    """Parse a ``since`` query parameter into a UTC datetime cutoff.

    Args:
        since: One of '1h', '24h', '7d', '30d', 'all', or None.

    Returns:
        UTC datetime cutoff, or None when no filter should be applied.

    Raises:
        HTTPException: 422 for unrecognised values.
    """
    if since is None or since == "all":
        return None
    if since in _SINCE_OFFSETS:
        return datetime.now(timezone.utc).replace(tzinfo=None) - _SINCE_OFFSETS[since]
    raise HTTPException(
        status_code=422,
        detail=f"Invalid since value '{since}'. Must be one of: 1h, 24h, 7d, 30d, all",
    )


@router.get("/profit/summary")
async def get_profit_summary(
    request: Request,
    since: Optional[str] = Query(default=None),
):
    """Return total and per-player profit breakdown using FIFO buy→sell matching.

    For each ea_id, trade records are sorted chronologically. Each 'bought'
    record is paired with the next 'sold' record (FIFO). Unmatched buys are
    considered held. Unmatched sells (ghost/pre-bot) are ignored.

    Args:
        request: FastAPI Request (app.state carries session_factory).
        since: Optional time filter — '1h', '24h', '7d', '30d', 'all', or None.

    Returns:
        Dict with keys:
            totals: {total_spent, total_earned, realized_profit, unrealized_pnl,
                     total_profit, buy_count, sell_count, held_count}
            per_player: list of per-player breakdown including profit_per_hour,
                     active_hours, first_buy_at, last_sell_at timestamps.
    """
    cutoff = _parse_since(since)

    session_factory = request.app.state.read_session_factory
    async with session_factory() as session:
        # All trade records ordered chronologically, optionally filtered by time
        stmt = (
            select(TradeRecord.ea_id, TradeRecord.outcome, TradeRecord.price, TradeRecord.recorded_at)
            .order_by(TradeRecord.recorded_at, TradeRecord.id)
        )
        if cutoff is not None:
            stmt = stmt.where(TradeRecord.recorded_at >= cutoff)
        trades_result = await session.execute(stmt)
        trades = trades_result.all()

        # Player names
        name_result = await session.execute(
            select(PlayerRecord.ea_id, PlayerRecord.name)
        )
        name_map: dict[int, str] = {row.ea_id: row.name for row in name_result.all()}

        # Latest market snapshot per ea_id for unrealized P&L
        latest_snap_subq = (
            select(MarketSnapshot.ea_id, func.max(MarketSnapshot.id).label("max_id"))
            .group_by(MarketSnapshot.ea_id)
            .subquery()
        )
        snap_result = await session.execute(
            select(MarketSnapshot.ea_id, MarketSnapshot.current_lowest_bin)
            .join(
                latest_snap_subq,
                (MarketSnapshot.ea_id == latest_snap_subq.c.ea_id)
                & (MarketSnapshot.id == latest_snap_subq.c.max_id),
            )
        )
        bin_map: dict[int, int] = {row.ea_id: row.current_lowest_bin for row in snap_result.all()}

    # Group trades by ea_id
    trades_by_player: dict[int, list] = {}
    for row in trades:
        trades_by_player.setdefault(row.ea_id, []).append(row)

    # FIFO matching per player
    per_player = []
    for ea_id, player_trades in trades_by_player.items():
        buy_queue: list[tuple[int, datetime]] = []  # (price, recorded_at)
        total_spent = 0
        total_earned = 0
        realized_profit = 0
        sell_count = 0
        first_buy_at: datetime | None = None
        last_sell_at: datetime | None = None

        for trade in player_trades:
            if trade.outcome == "bought":
                buy_queue.append((trade.price, trade.recorded_at))
                total_spent += trade.price
                if first_buy_at is None:
                    first_buy_at = trade.recorded_at
            elif trade.outcome == "sold" and buy_queue:
                buy_price, _buy_ts = buy_queue.pop(0)
                earned = int(trade.price * (1 - EA_TAX_RATE))
                total_earned += earned
                realized_profit += earned - buy_price
                sell_count += 1
                last_sell_at = trade.recorded_at
            # else: ghost sell (no buy to match) — ignored

        buy_count = sell_count + len(buy_queue)
        held_count = len(buy_queue)

        # Skip players with no buys (only ghost sells)
        if buy_count == 0:
            continue

        # Unrealized P&L for held cards
        unrealized_pnl = 0
        current_bin = bin_map.get(ea_id)
        if held_count > 0 and current_bin is not None:
            for held_buy_price, _held_ts in buy_queue:
                unrealized_pnl += int(current_bin * (1 - EA_TAX_RATE)) - held_buy_price

        # Profit rate calculation
        active_hours: float | None = None
        profit_per_hour: float | None = None
        if sell_count > 0 and first_buy_at is not None and last_sell_at is not None:
            delta = last_sell_at - first_buy_at
            active_hours = delta.total_seconds() / 3600.0
            if active_hours > 0:
                profit_per_hour = realized_profit / active_hours

        per_player.append({
            "ea_id": ea_id,
            "name": name_map.get(ea_id, f"Player {ea_id}"),
            "total_spent": total_spent,
            "total_earned": total_earned,
            "realized_profit": realized_profit,
            "unrealized_pnl": unrealized_pnl,
            "buy_count": buy_count,
            "sell_count": sell_count,
            "held_count": held_count,
            "profit_per_hour": profit_per_hour,
            "active_hours": active_hours,
            "first_buy_at": first_buy_at.isoformat() if first_buy_at else None,
            "last_sell_at": last_sell_at.isoformat() if last_sell_at else None,
        })

    # Totals
    total_spent = sum(p["total_spent"] for p in per_player)
    total_earned = sum(p["total_earned"] for p in per_player)
    realized_profit = sum(p["realized_profit"] for p in per_player)
    unrealized_pnl = sum(p["unrealized_pnl"] for p in per_player)
    buy_count = sum(p["buy_count"] for p in per_player)
    sell_count = sum(p["sell_count"] for p in per_player)
    held_count = sum(p["held_count"] for p in per_player)

    return {
        "totals": {
            "total_spent": total_spent,
            "total_earned": total_earned,
            "realized_profit": realized_profit,
            "unrealized_pnl": unrealized_pnl,
            "total_profit": realized_profit + unrealized_pnl,
            "buy_count": buy_count,
            "sell_count": sell_count,
            "held_count": held_count,
        },
        "per_player": per_player,
    }
