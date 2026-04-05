"""Portfolio status endpoint for dashboard.

Returns per-player trade status, cumulative stats, unrealized P&L,
and summary totals in a single call (D-07).
"""
import logging
from typing import Optional

from fastapi import APIRouter, Query, Request
from sqlalchemy import select, func, case

from src.server.models_db import PortfolioSlot, TradeRecord, MarketSnapshot, PlayerRecord
from src.server.api.profit import _parse_since
from src.config import EA_TAX_RATE

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")

# Maps TradeRecord.outcome values to dashboard status strings
OUTCOME_TO_STATUS = {
    "bought": "BOUGHT",
    "listed": "LISTED",
    "sold": "SOLD",
    "expired": "EXPIRED",
}

# Statuses where the player is considered "held" (unrealized P&L applies)
HELD_STATUSES = {"BOUGHT", "LISTED"}


@router.get("/portfolio/status")
async def get_portfolio_status(request: Request, since: Optional[str] = Query(default=None)):
    """Return per-player trade status, cumulative stats, unrealized P&L, and summary totals.

    Queries portfolio_slots, trade_records, market_snapshots, and players tables.
    Status is derived from the most recent TradeRecord outcome (by id, not recorded_at).
    Unrealized P&L is computed from the latest MarketSnapshot for held players only.

    Args:
        request: FastAPI Request (app.state carries session_factory).
        since: Optional time filter (e.g. '1h', '24h', '7d', '30d', 'all').
               Filters trade record aggregation only; status is always current.

    Returns:
        Dict with keys:
            summary: {realized_profit, unrealized_pnl, trade_counts: {bought, sold, expired}}
            players: list of per-player dicts (ea_id, name, status, times_sold,
                     realized_profit, unrealized_pnl, buy_price, sell_price, current_bin)
    """
    cutoff = _parse_since(since)
    session_factory = request.app.state.read_session_factory
    async with session_factory() as session:
        # Query 1 — All active portfolio slots
        slots_result = await session.execute(
            select(PortfolioSlot.ea_id, PortfolioSlot.buy_price, PortfolioSlot.sell_price, PortfolioSlot.is_leftover)
        )
        slots = slots_result.all()

        if not slots:
            return {
                "summary": {
                    "realized_profit": 0,
                    "unrealized_pnl": 0,
                    "trade_counts": {"bought": 0, "sold": 0, "expired": 0},
                },
                "players": [],
            }

        ea_ids = [row.ea_id for row in slots]

        # Query 2 — Trade record aggregation per ea_id
        agg_base = (
            select(
                TradeRecord.ea_id,
                func.sum(
                    case((TradeRecord.outcome == "sold", TradeRecord.price), else_=0)
                ).label("total_sold_gross"),
                func.sum(
                    case((TradeRecord.outcome == "bought", TradeRecord.price), else_=0)
                ).label("total_bought"),
                func.count(
                    case((TradeRecord.outcome == "sold", 1))
                ).label("times_sold"),
                func.count(
                    case((TradeRecord.outcome == "bought", 1))
                ).label("bought_count"),
                func.count(
                    case((TradeRecord.outcome == "expired", 1))
                ).label("expired_count"),
            )
            .where(TradeRecord.ea_id.in_(ea_ids))
        )
        if cutoff is not None:
            agg_base = agg_base.where(TradeRecord.recorded_at >= cutoff)
        agg_stmt = agg_base.group_by(TradeRecord.ea_id)
        agg_result = await session.execute(agg_stmt)
        agg_rows = {row.ea_id: row for row in agg_result.all()}

        # Query 3 — Most recent outcome per ea_id using MAX(id) not MAX(recorded_at)
        latest_record_subq = (
            select(TradeRecord.ea_id, func.max(TradeRecord.id).label("max_id"))
            .where(TradeRecord.ea_id.in_(ea_ids))
            .group_by(TradeRecord.ea_id)
            .subquery()
        )
        status_stmt = (
            select(TradeRecord.ea_id, TradeRecord.outcome)
            .join(
                latest_record_subq,
                (TradeRecord.ea_id == latest_record_subq.c.ea_id)
                & (TradeRecord.id == latest_record_subq.c.max_id),
            )
        )
        status_result = await session.execute(status_stmt)
        latest_outcome_map: dict[int, str] = {
            row.ea_id: row.outcome for row in status_result.all()
        }

        # Query 4 — Latest MarketSnapshot per ea_id using MAX(id)
        latest_snapshot_subq = (
            select(MarketSnapshot.ea_id, func.max(MarketSnapshot.id).label("max_id"))
            .where(MarketSnapshot.ea_id.in_(ea_ids))
            .group_by(MarketSnapshot.ea_id)
            .subquery()
        )
        bin_stmt = (
            select(MarketSnapshot.ea_id, MarketSnapshot.current_lowest_bin)
            .join(
                latest_snapshot_subq,
                (MarketSnapshot.ea_id == latest_snapshot_subq.c.ea_id)
                & (MarketSnapshot.id == latest_snapshot_subq.c.max_id),
            )
        )
        bin_result = await session.execute(bin_stmt)
        bin_map: dict[int, int] = {
            row.ea_id: row.current_lowest_bin for row in bin_result.all()
        }

        # Query 5 — Player names from PlayerRecord
        name_result = await session.execute(
            select(PlayerRecord.ea_id, PlayerRecord.name, PlayerRecord.futgg_url).where(
                PlayerRecord.ea_id.in_(ea_ids)
            )
        )
        player_info: dict[int, tuple[str, str | None]] = {
            row.ea_id: (row.name, row.futgg_url) for row in name_result.all()
        }

    # ── Assembly ────────────────────────────────────────────────────────────────

    players = []
    total_realized_profit = 0
    total_unrealized_pnl = 0
    total_bought_count = 0
    total_sold_count = 0
    total_expired_count = 0

    for slot in slots:
        ea_id = slot.ea_id
        buy_price = slot.buy_price

        # Derive status from latest trade outcome, default to PENDING
        latest_outcome = latest_outcome_map.get(ea_id)
        status = OUTCOME_TO_STATUS.get(latest_outcome, "PENDING") if latest_outcome else "PENDING"

        # Aggregated trade stats
        agg = agg_rows.get(ea_id)
        if agg:
            total_sold_gross = int(agg.total_sold_gross or 0)
            total_bought_cost = int(agg.total_bought or 0)
            times_sold = int(agg.times_sold or 0)
            bought_count = int(agg.bought_count or 0)
            expired_count = int(agg.expired_count or 0)
        else:
            total_sold_gross = 0
            total_bought_cost = 0
            times_sold = 0
            bought_count = 0
            expired_count = 0

        # Realized profit: revenue from completed sell cycles minus cost basis.
        # Only counts completed cycles (times_sold * buy_price_per_slot) — open positions
        # are not "realized" yet. EA tax applied in Python after SQL aggregation.
        realized_profit = int(total_sold_gross * (1 - EA_TAX_RATE)) - (times_sold * buy_price)

        # Unrealized P&L: only for held (BOUGHT or LISTED) players with a market snapshot
        current_bin = bin_map.get(ea_id)
        if status in HELD_STATUSES and current_bin is not None:
            unrealized_pnl: int | None = current_bin - buy_price
        else:
            unrealized_pnl = None

        # Accumulate summary totals
        total_realized_profit += realized_profit
        if unrealized_pnl is not None:
            total_unrealized_pnl += unrealized_pnl
        total_bought_count += bought_count
        total_sold_count += times_sold
        total_expired_count += expired_count

        info = player_info.get(ea_id)
        name = info[0] if info else f"Player {ea_id}"
        futgg_url = info[1] if info else None

        players.append({
            "ea_id": ea_id,
            "name": name,
            "futgg_url": futgg_url,
            "status": status,
            "times_sold": times_sold,
            "realized_profit": realized_profit,
            "unrealized_pnl": unrealized_pnl,
            "buy_price": buy_price,
            "sell_price": slot.sell_price,
            "current_bin": current_bin if status in HELD_STATUSES else None,
            "is_leftover": slot.is_leftover,
        })

    return {
        "summary": {
            "realized_profit": total_realized_profit,
            "unrealized_pnl": total_unrealized_pnl,
            "trade_counts": {
                "bought": total_bought_count,
                "sold": total_sold_count,
                "expired": total_expired_count,
            },
        },
        "players": players,
    }
