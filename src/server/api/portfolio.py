"""Portfolio optimization endpoint.

Given a budget, returns the best set of players to OP sell,
using stored scores and the existing optimize_portfolio() engine.
Also provides DELETE endpoint to swap a player out of the portfolio,
POST endpoints for two-step generate/confirm flow, and GET confirmed.
"""
import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Query, Request, Path, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, func, update, delete, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from src.server.models_db import PlayerRecord, PlayerScore, PortfolioSlot, TradeAction, MarketSnapshot
from src.config import STALE_THRESHOLD_HOURS, VOLATILITY_MAX_PRICE_INCREASE_PCT, VOLATILITY_LOOKBACK_DAYS
from src.optimizer import optimize_portfolio

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")


def _read_session_factory(request: Request):
    """Return the read-only session factory if available, else the default one."""
    return getattr(request.app.state, "read_session_factory", None) or request.app.state.session_factory


# ── Request models ─────────────────────────────────────────────────────────────

class GenerateRequest(BaseModel):
    """Request body for POST /portfolio/generate."""

    budget: int = Field(..., gt=0, description="Total budget in coins")


class ConfirmPlayer(BaseModel):
    """A single player entry in a confirm request."""

    ea_id: int
    buy_price: int
    sell_price: int


class ConfirmRequest(BaseModel):
    """Request body for POST /portfolio/confirm."""

    players: list[ConfirmPlayer]


class SwapPreviewRequest(BaseModel):
    """Request body for POST /portfolio/swap-preview."""

    freed_budget: int = Field(..., gt=0, description="Budget freed by removing a player")
    excluded_ea_ids: list[int]


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
        "futgg_url": record.futgg_url,
    }


async def _get_volatile_ea_ids(session: AsyncSession, ea_ids: list[int]) -> set[int]:
    """Return ea_ids whose price increased more than VOLATILITY_MAX_PRICE_INCREASE_PCT
    over the last VOLATILITY_LOOKBACK_DAYS days.

    For each player, compares the earliest MarketSnapshot.current_lowest_bin in the
    lookback window against the latest. If (latest - earliest) / earliest > threshold,
    the player is volatile.

    Players with fewer than 2 snapshots in the window are skipped (not enough data).

    Performance: uses three focused queries instead of the previous four-level nested
    subquery approach. The original single-query plan caused SQLite to scan ~1.5M rows
    four times (one pass per subquery materialisation), which took >30s for ~1800
    ea_ids and caused the endpoint to exceed the 30s client timeout.

    The new approach:
      1. One GROUP BY query — finds min/max captured_at and count per ea_id (uses the
         composite index on (ea_id, captured_at) efficiently).
      2. One bulk query — fetches the current_lowest_bin for all (ea_id, min_ts) pairs
         using an IN clause on the (ea_id, captured_at) pair.
      3. One bulk query — same for (ea_id, max_ts) pairs.
    Each of the three queries does at most one full-index range scan.

    Args:
        session: Active async DB session.
        ea_ids: List of ea_ids to check.

    Returns:
        Set of ea_ids considered volatile.
    """
    if not ea_ids:
        return set()

    cutoff = datetime.utcnow() - timedelta(days=VOLATILITY_LOOKBACK_DAYS)
    threshold = VOLATILITY_MAX_PRICE_INCREASE_PCT / 100.0

    # Query 1: single-pass GROUP BY to find boundary timestamps per ea_id.
    # HAVING filters out players with only one snapshot (insufficient data).
    # The ix_market_snapshots_ea_id_captured_at index covers (ea_id, captured_at).
    range_stmt = (
        select(
            MarketSnapshot.ea_id,
            func.min(MarketSnapshot.captured_at).label("min_ts"),
            func.max(MarketSnapshot.captured_at).label("max_ts"),
        )
        .where(
            MarketSnapshot.ea_id.in_(ea_ids),
            MarketSnapshot.captured_at >= cutoff,
        )
        .group_by(MarketSnapshot.ea_id)
        .having(func.count(MarketSnapshot.captured_at) >= 2)
    )

    range_result = await session.execute(range_stmt)
    ranges = range_result.all()  # [(ea_id, min_ts, max_ts), ...]

    if not ranges:
        return set()

    # Build lookup sets for boundary timestamps.
    min_ts_by_ea_id = {row.ea_id: row.min_ts for row in ranges}
    max_ts_by_ea_id = {row.ea_id: row.max_ts for row in ranges}

    # Query 2: fetch earliest bin for all ea_ids in one round-trip.
    # Each (ea_id, captured_at) pair is a primary-key-like index lookup.
    # SQLite will use ix_market_snapshots_ea_id_captured_at for each OR-branch.
    earliest_stmt = (
        select(MarketSnapshot.ea_id, MarketSnapshot.current_lowest_bin)
        .where(
            tuple_(MarketSnapshot.ea_id, MarketSnapshot.captured_at).in_(
                [(ea_id, ts) for ea_id, ts in min_ts_by_ea_id.items()]
            )
        )
    )
    earliest_result = await session.execute(earliest_stmt)
    earliest_bin_by_ea_id = {row.ea_id: row.current_lowest_bin for row in earliest_result.all()}

    # Query 3: fetch latest bin for all ea_ids in one round-trip.
    latest_stmt = (
        select(MarketSnapshot.ea_id, MarketSnapshot.current_lowest_bin)
        .where(
            tuple_(MarketSnapshot.ea_id, MarketSnapshot.captured_at).in_(
                [(ea_id, ts) for ea_id, ts in max_ts_by_ea_id.items()]
            )
        )
    )
    latest_result = await session.execute(latest_stmt)
    latest_bin_by_ea_id = {row.ea_id: row.current_lowest_bin for row in latest_result.all()}

    # Evaluate volatility in Python — O(N) over the candidate set.
    volatile = set()
    for ea_id in min_ts_by_ea_id:
        earliest_bin = earliest_bin_by_ea_id.get(ea_id)
        latest_bin = latest_bin_by_ea_id.get(ea_id)
        if earliest_bin and earliest_bin > 0 and latest_bin is not None:
            increase = (latest_bin - earliest_bin) / earliest_bin
            if increase > threshold:
                volatile.add(ea_id)

    return volatile


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
    sf = _read_session_factory(request)
    async with sf() as session:
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

        # Apply volatility filter while session is still open
        all_ea_ids = [score.ea_id for score, record in rows]
        volatile = await _get_volatile_ea_ids(session, all_ea_ids)
        if volatile:
            logger.info(
                "Volatility filter removed %d of %d candidates (GET /portfolio)",
                len(volatile), len(rows),
            )
        rows = [(s, r) for s, r in rows if s.ea_id not in volatile]

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
            "futgg_url": entry.get("futgg_url"),
        })

    return {
        "data": data,
        "count": len(data),
        "budget": budget,
        "budget_used": budget_used,
        "budget_remaining": budget - budget_used,
    }


@router.post("/portfolio/generate")
async def generate_portfolio(
    request: Request,
    body: GenerateRequest,
):
    """Return an optimized portfolio preview without persisting any DB rows.

    Runs the same optimizer logic as GET /portfolio but accepts a JSON body
    instead of a query parameter and never writes PortfolioSlot rows.

    Args:
        request: FastAPI Request (app.state carries session_factory).
        body: GenerateRequest with budget field.

    Returns:
        Dict with keys: data (list), count, budget, budget_used, budget_remaining.
        On no viable players: includes an error key and empty data.
    """
    sf = _read_session_factory(request)
    async with sf() as session:
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

        # Apply volatility filter while session is still open
        all_ea_ids = [score.ea_id for score, record in rows]
        volatile = await _get_volatile_ea_ids(session, all_ea_ids)
        if volatile:
            logger.info(
                "Volatility filter removed %d of %d candidates (POST /portfolio/generate)",
                len(volatile), len(rows),
            )
        rows = [(s, r) for s, r in rows if s.ea_id not in volatile]

    scored_list = [_build_scored_entry(score, record) for score, record in rows]

    if not scored_list:
        return {
            "error": "Not enough listing data yet. The system needs to accumulate market observations before it can recommend players. This typically takes a few hours of scanning.",
            "data": [],
            "count": 0,
            "budget": body.budget,
            "budget_used": 0,
            "budget_remaining": body.budget,
        }

    selected = optimize_portfolio(scored_list, body.budget)
    budget_used = sum(entry["buy_price"] for entry in selected)

    stale_cutoff = datetime.utcnow() - timedelta(hours=STALE_THRESHOLD_HOURS)
    data = []
    for entry in selected:
        last_scanned_at = entry["last_scanned_at"]
        is_stale = last_scanned_at is None or last_scanned_at < stale_cutoff
        epph = entry.get("expected_profit_per_hour")
        data.append({
            "ea_id": entry["ea_id"],
            "name": entry["name"],
            "rating": entry["rating"],
            "position": entry["position"],
            "price": entry["buy_price"],
            "sell_price": entry["sell_price"],
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
            "futgg_url": entry.get("futgg_url"),
        })

    return {
        "data": data,
        "count": len(data),
        "budget": body.budget,
        "budget_used": budget_used,
        "budget_remaining": body.budget - budget_used,
    }


@router.post("/portfolio/confirm")
async def confirm_portfolio(
    request: Request,
    body: ConfirmRequest,
):
    """Seed portfolio_slots with a confirmed player list (clean slate per D-06).

    Clears ALL existing PortfolioSlot rows then inserts new ones from the
    provided list. Supports calling confirm twice: the second call replaces
    the first entirely.

    Args:
        request: FastAPI Request (app.state carries session_factory).
        body: ConfirmRequest with list of players (ea_id, buy_price, sell_price).

    Returns:
        Dict with keys: confirmed (int), status ("ok").
    """
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        # Clean slate: remove all existing slots before inserting new ones
        await session.execute(delete(PortfolioSlot))

        # Insert new slots
        for p in body.players:
            session.add(PortfolioSlot(
                ea_id=p.ea_id,
                buy_price=p.buy_price,
                sell_price=p.sell_price,
                added_at=datetime.utcnow(),
            ))

        await session.commit()

    logger.info("Confirmed %d portfolio slots (clean slate)", len(body.players))

    return {"confirmed": len(body.players), "status": "ok"}


@router.post("/portfolio/swap-preview")
async def swap_preview(
    request: Request,
    body: SwapPreviewRequest,
):
    """Return replacement candidates for a freed budget, excluding specified ea_ids.

    Stateless — does not read or write PortfolioSlot rows. Used during the
    draft phase (D-07/D-08) before a confirm has been made.

    Args:
        request: FastAPI Request (app.state carries session_factory).
        body: SwapPreviewRequest with freed_budget and excluded_ea_ids.

    Returns:
        Dict with keys: replacements (list), count (int).
    """
    excluded = set(body.excluded_ea_ids)

    sf = _read_session_factory(request)
    async with sf() as session:
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

        result = await session.execute(stmt)
        rows = result.all()

        # Apply volatility filter before excluded_ea_ids filter, while session is open
        all_ea_ids = [score.ea_id for score, record in rows]
        volatile = await _get_volatile_ea_ids(session, all_ea_ids)
        if volatile:
            logger.info(
                "Volatility filter removed %d of %d candidates (POST /portfolio/swap-preview)",
                len(volatile), len(rows),
            )
        excluded = excluded | volatile

    # Filter out excluded ea_ids (includes volatile players) before running optimizer
    candidates = [
        _build_scored_entry(score, record)
        for score, record in rows
        if score.ea_id not in excluded
    ]

    replacements_raw = optimize_portfolio(candidates, body.freed_budget) if candidates else []

    replacements = [
        {
            "ea_id": entry["ea_id"],
            "name": entry["name"],
            "rating": entry["rating"],
            "position": entry["position"],
            "price": entry["buy_price"],
            "sell_price": entry["sell_price"],
            "margin_pct": entry["margin_pct"],
            "op_ratio": round(entry["op_ratio"], 3),
            "expected_profit": round(entry["expected_profit"], 1),
            "efficiency": round(entry["efficiency"], 4),
            "futgg_url": entry.get("futgg_url"),
        }
        for entry in replacements_raw
    ]

    return {"replacements": replacements, "count": len(replacements)}


@router.get("/portfolio/confirmed")
async def get_confirmed_portfolio(request: Request):
    """Return current portfolio_slots joined with PlayerRecord metadata.

    No optimizer run — purely reads the confirmed portfolio from DB.

    Args:
        request: FastAPI Request (app.state carries session_factory).

    Returns:
        Dict with keys: data (list), count (int).
        Each item has: ea_id, name, rating, position, buy_price, sell_price.
    """
    sf = _read_session_factory(request)
    async with sf() as session:
        stmt = (
            select(PortfolioSlot, PlayerRecord)
            .join(PlayerRecord, PlayerRecord.ea_id == PortfolioSlot.ea_id)
        )
        result = await session.execute(stmt)
        rows = result.all()

    data = [
        {
            "ea_id": slot.ea_id,
            "name": record.name,
            "rating": record.rating,
            "position": record.position,
            "buy_price": slot.buy_price,
            "sell_price": slot.sell_price,
            "futgg_url": record.futgg_url,
        }
        for slot, record in rows
    ]

    return {"data": data, "count": len(data)}


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

        # 6. Apply volatility filter before committing (session still open)
        all_candidate_ids = [score.ea_id for score, record in rows]
        volatile = await _get_volatile_ea_ids(session, all_candidate_ids)
        if volatile:
            logger.info(
                "Volatility filter removed %d of %d candidates (DELETE /portfolio/%d)",
                len(volatile), len(rows), ea_id,
            )

        await session.commit()

    # 7. Build scored candidates excluding removed player, remaining slots, and volatile players
    excluded = remaining_ea_ids | {ea_id} | volatile
    scored_candidates = [
        _build_scored_entry(score, record)
        for score, record in rows
        if score.ea_id not in excluded
    ]

    # 8. Run optimizer for replacements within freed budget
    replacements_raw = optimize_portfolio(scored_candidates, freed_budget) if scored_candidates else []

    # 9. Serialize replacements
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
