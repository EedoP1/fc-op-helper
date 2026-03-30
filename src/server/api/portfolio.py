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
from sqlalchemy import select, func, update, delete, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.server.models_db import PlayerRecord, PlayerScore, PortfolioSlot, TradeAction, TradeRecord
from src.config import STALE_THRESHOLD_HOURS
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


async def _fetch_latest_viable_scores(session: AsyncSession) -> list[tuple]:
    """Fetch the latest viable PlayerScore + PlayerRecord for every active player.

    Replaces the subquery+nested-loop ORM pattern
    (JOIN (SELECT ea_id, MAX(scored_at) ... GROUP BY ea_id) ...) which degrades
    to O(N) random index lookups on cold cache with 500k+ rows (~33s).

    ROW_NUMBER() OVER (PARTITION BY ea_id ORDER BY scored_at DESC) lets
    PostgreSQL use an incremental sort on the (ea_id, scored_at) index in a
    single forward pass (~4s cold, ~1s warm).

    Returns:
        List of (PlayerScore, PlayerRecord) tuples, one per active+viable player.
    """
    sql = text("""
        SELECT
            ps.id, ps.ea_id, ps.scored_at,
            ps.buy_price, ps.sell_price, ps.net_profit, ps.margin_pct,
            ps.op_sales, ps.total_sales, ps.op_ratio, ps.expected_profit,
            ps.efficiency, ps.sales_per_hour, ps.is_viable,
            ps.expected_profit_per_hour, ps.scorer_version,
            pr.ea_id   AS pr_ea_id, pr.name, pr.rating, pr.position,
            pr.nation, pr.league, pr.club, pr.card_type, pr.scan_tier,
            pr.last_scanned_at, pr.next_scan_at, pr.is_active,
            pr.listing_count, pr.sales_per_hour AS pr_sales_per_hour,
            pr.futgg_url
        FROM (
            SELECT *,
                   ROW_NUMBER() OVER (
                       PARTITION BY ea_id
                       ORDER BY scored_at DESC
                   ) AS rn
            FROM player_scores
            WHERE is_viable = TRUE
        ) ps
        JOIN players pr ON pr.ea_id = ps.ea_id
        WHERE ps.rn = 1
          AND pr.is_active = TRUE
    """)
    result = await session.execute(sql)
    raw_rows = result.mappings().all()

    # Reconstruct ORM-like objects so callers can use score.field / record.field
    pairs = []
    for row in raw_rows:
        score = PlayerScore(
            id=row["id"],
            ea_id=row["ea_id"],
            scored_at=row["scored_at"],
            buy_price=row["buy_price"],
            sell_price=row["sell_price"],
            net_profit=row["net_profit"],
            margin_pct=row["margin_pct"],
            op_sales=row["op_sales"],
            total_sales=row["total_sales"],
            op_ratio=row["op_ratio"],
            expected_profit=row["expected_profit"],
            efficiency=row["efficiency"],
            sales_per_hour=row["sales_per_hour"],
            is_viable=row["is_viable"],
            expected_profit_per_hour=row["expected_profit_per_hour"],
            scorer_version=row["scorer_version"],
        )
        record = PlayerRecord(
            ea_id=row["pr_ea_id"],
            name=row["name"],
            rating=row["rating"],
            position=row["position"],
            nation=row["nation"],
            league=row["league"],
            club=row["club"],
            card_type=row["card_type"],
            scan_tier=row["scan_tier"],
            last_scanned_at=row["last_scanned_at"],
            next_scan_at=row["next_scan_at"],
            is_active=row["is_active"],
            listing_count=row["listing_count"],
            sales_per_hour=row["pr_sales_per_hour"],
            futgg_url=row["futgg_url"],
        )
        pairs.append((score, record))
    return pairs


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
        rows = await _fetch_latest_viable_scores(session)

        # Filter out base icons (rarityName exactly "Icon")
        before_icon = len(rows)
        rows = [(s, r) for s, r in rows if r.card_type != "Icon"]
        icon_removed = before_icon - len(rows)
        if icon_removed:
            logger.info("Icon filter removed %d base icons from candidates", icon_removed)

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
        # Strip timezone info for comparison (Postgres returns tz-aware, utcnow() is naive)
        if last_scanned_at is not None and last_scanned_at.tzinfo is not None:
            last_scanned_at = last_scanned_at.replace(tzinfo=None)
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
        rows = await _fetch_latest_viable_scores(session)

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
        if last_scanned_at is not None and last_scanned_at.tzinfo is not None:
            last_scanned_at = last_scanned_at.replace(tzinfo=None)
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
    """Confirm a new portfolio, preserving unsold players from the old one as leftovers.

    For each old slot, checks the latest TradeRecord outcome:
    - bought/listed/expired → player is still in your club, kept as is_leftover=True
    - sold/None (never bought) → deleted

    New portfolio players are inserted with is_leftover=False. If a new player
    overlaps with a leftover (same ea_id), the leftover is promoted back to active
    with updated prices.

    Args:
        request: FastAPI Request (app.state carries session_factory).
        body: ConfirmRequest with list of players (ea_id, buy_price, sell_price).

    Returns:
        Dict with keys: confirmed (int), leftovers (int), status ("ok").
    """
    session_factory = request.app.state.session_factory

    # Deduplicate by ea_id — last occurrence wins (prevents UNIQUE constraint violation)
    deduped: dict[int, ConfirmPlayer] = {}
    for p in body.players:
        deduped[p.ea_id] = p
    new_ea_ids = set(deduped.keys())

    async with session_factory() as session:
        # Step 1: Load all existing slots
        old_slots_result = await session.execute(select(PortfolioSlot))
        old_slots = old_slots_result.scalars().all()

        # Step 2: For each old slot, check latest trade outcome to decide fate
        leftover_count = 0
        for slot in old_slots:
            if slot.ea_id in new_ea_ids:
                # Overlap: new portfolio includes this player — promote to active
                p = deduped[slot.ea_id]
                slot.buy_price = p.buy_price
                slot.sell_price = p.sell_price
                slot.is_leftover = False
                slot.added_at = datetime.utcnow()
                # Remove from deduped so we don't double-insert
                del deduped[slot.ea_id]
                continue

            # Check latest trade record for this player
            latest_result = await session.execute(
                select(TradeRecord.outcome)
                .where(TradeRecord.ea_id == slot.ea_id)
                .order_by(TradeRecord.id.desc())
                .limit(1)
            )
            latest_outcome = latest_result.scalar_one_or_none()

            if latest_outcome in ("bought", "listed", "expired"):
                # Player is still in club — mark as leftover
                slot.is_leftover = True
                leftover_count += 1
            else:
                # sold or never bought (None) — safe to delete
                await session.delete(slot)

        # Step 3: Cancel pending/in-progress actions for players no longer in active portfolio
        # (leftovers will get new actions derived from their trade state)
        leftover_ea_ids = {s.ea_id for s in old_slots if s.ea_id not in new_ea_ids}
        if leftover_ea_ids:
            await session.execute(
                update(TradeAction)
                .where(
                    TradeAction.ea_id.in_(leftover_ea_ids),
                    TradeAction.status.in_(["PENDING", "IN_PROGRESS"]),
                )
                .values(status="CANCELLED")
            )

        # Step 4: Insert remaining new players (those that didn't overlap with old slots)
        now = datetime.utcnow()
        for p in deduped.values():
            session.add(PortfolioSlot(
                ea_id=p.ea_id,
                buy_price=p.buy_price,
                sell_price=p.sell_price,
                added_at=now,
                is_leftover=False,
            ))

        await session.commit()

    confirmed_count = len(new_ea_ids)
    logger.info(
        "Confirmed %d portfolio slots, %d leftovers preserved",
        confirmed_count, leftover_count,
    )

    return {"confirmed": confirmed_count, "leftovers": leftover_count, "status": "ok"}


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
        rows = await _fetch_latest_viable_scores(session)

    # Filter out excluded ea_ids before running optimizer
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

    Separates active portfolio players from leftovers (unsold players from
    previous portfolios).

    Args:
        request: FastAPI Request (app.state carries session_factory).

    Returns:
        Dict with keys: portfolio (list), leftovers (list), count (int), leftover_count (int).
    """
    sf = _read_session_factory(request)
    async with sf() as session:
        stmt = (
            select(PortfolioSlot, PlayerRecord)
            .join(PlayerRecord, PlayerRecord.ea_id == PortfolioSlot.ea_id)
        )
        result = await session.execute(stmt)
        rows = result.all()

    data = []
    portfolio = []
    leftovers = []
    for slot, record in rows:
        entry = {
            "ea_id": slot.ea_id,
            "name": record.name,
            "rating": record.rating,
            "position": record.position,
            "buy_price": slot.buy_price,
            "sell_price": slot.sell_price,
            "futgg_url": record.futgg_url,
            "is_leftover": slot.is_leftover,
        }
        data.append(entry)
        if slot.is_leftover:
            leftovers.append(entry)
        else:
            portfolio.append(entry)

    return {
        "data": data,
        "portfolio": portfolio,
        "leftovers": leftovers,
        "count": len(portfolio),
        "leftover_count": len(leftovers),
    }


@router.get("/portfolio/actions-needed")
async def get_actions_needed(request: Request):
    """Return a flat, prioritized list of exactly what to do for every player.

    For each portfolio slot (active + leftover), derives the next needed action
    from the latest TradeRecord outcome. Returns a single list sorted by priority:
    LIST first (sell what you have), then RELIST, then BUY.

    Players currently "listed" (on market, waiting) are included as WAIT entries
    so the user has a complete picture.

    Args:
        request: FastAPI Request (app.state carries session_factory).

    Returns:
        Dict with keys:
            actions: list of {ea_id, name, rating, position, action, target_price,
                              is_leftover, futgg_url}
            summary: {to_buy, to_list, to_relist, waiting}
    """
    sf = _read_session_factory(request)
    async with sf() as session:
        # Load all slots with player metadata
        stmt = (
            select(PortfolioSlot, PlayerRecord)
            .join(PlayerRecord, PlayerRecord.ea_id == PortfolioSlot.ea_id)
        )
        result = await session.execute(stmt)
        rows = result.all()

        if not rows:
            return {
                "actions": [],
                "summary": {"to_buy": 0, "to_list": 0, "to_relist": 0, "waiting": 0},
            }

        # Get latest trade outcome per ea_id
        ea_ids = [slot.ea_id for slot, _ in rows]
        latest_subq = (
            select(TradeRecord.ea_id, func.max(TradeRecord.id).label("max_id"))
            .where(TradeRecord.ea_id.in_(ea_ids))
            .group_by(TradeRecord.ea_id)
            .subquery()
        )
        outcome_stmt = (
            select(TradeRecord.ea_id, TradeRecord.outcome)
            .join(
                latest_subq,
                (TradeRecord.ea_id == latest_subq.c.ea_id)
                & (TradeRecord.id == latest_subq.c.max_id),
            )
        )
        outcome_result = await session.execute(outcome_stmt)
        outcome_map = {r.ea_id: r.outcome for r in outcome_result.all()}

    # Derive action for each player
    actions = []
    summary = {"to_buy": 0, "to_list": 0, "to_relist": 0, "waiting": 0}

    # Priority order for sorting: LIST=0, RELIST=1, BUY=2, WAIT=3
    priority_order = {"LIST": 0, "RELIST": 1, "BUY": 2, "WAIT": 3}

    for slot, record in rows:
        latest_outcome = outcome_map.get(slot.ea_id)

        if slot.is_leftover:
            # Leftovers: never BUY — only LIST/RELIST/WAIT
            if latest_outcome is None or latest_outcome == "bought":
                action = "LIST"
                target_price = slot.sell_price
                summary["to_list"] += 1
            elif latest_outcome == "expired":
                action = "RELIST"
                target_price = slot.sell_price
                summary["to_relist"] += 1
            elif latest_outcome == "listed":
                action = "WAIT"
                target_price = slot.sell_price
                summary["waiting"] += 1
            else:
                # "sold" — shouldn't happen (auto-cleanup removes sold leftovers)
                continue
        else:
            # Active portfolio: normal lifecycle
            if latest_outcome is None:
                action = "BUY"
                target_price = slot.buy_price
                summary["to_buy"] += 1
            elif latest_outcome == "bought":
                action = "LIST"
                target_price = slot.sell_price
                summary["to_list"] += 1
            elif latest_outcome == "listed":
                action = "WAIT"
                target_price = slot.sell_price
                summary["waiting"] += 1
            elif latest_outcome == "expired":
                action = "RELIST"
                target_price = slot.sell_price
                summary["to_relist"] += 1
            elif latest_outcome == "sold":
                action = "BUY"
                target_price = slot.buy_price
                summary["to_buy"] += 1
            else:
                continue

        actions.append({
            "ea_id": slot.ea_id,
            "name": record.name,
            "rating": record.rating,
            "position": record.position,
            "action": action,
            "target_price": target_price,
            "buy_price": slot.buy_price,
            "sell_price": slot.sell_price,
            "is_leftover": slot.is_leftover,
            "futgg_url": record.futgg_url,
        })

    # Sort by priority: LIST first, then RELIST, then BUY, then WAIT
    actions.sort(key=lambda x: priority_order.get(x["action"], 99))

    return {"actions": actions, "summary": summary}


class RebalanceRequest(BaseModel):
    """Request body for POST /portfolio/rebalance."""

    budget: int = Field(..., gt=0, description="Total budget in coins")


@router.post("/portfolio/rebalance")
async def rebalance_portfolio(
    request: Request,
    body: RebalanceRequest,
):
    """Rebalance the portfolio: keep existing players, fill remaining budget with new picks.

    Reads current portfolio_slots as "kept" players. If their total cost exceeds
    the budget, drops the least efficient ones until they fit. Then fills the
    remaining budget with new picks from viable scored players (excluding kept ea_ids).

    Args:
        request: FastAPI Request (app.state carries session_factory).
        body: RebalanceRequest with budget field.

    Returns:
        Dict with keys: kept, new, dropped, budget, budget_used, budget_remaining.
    """
    sf = _read_session_factory(request)

    # Step 1: Read current portfolio slots
    async with sf() as session:
        slot_stmt = (
            select(PortfolioSlot, PlayerRecord)
            .join(PlayerRecord, PlayerRecord.ea_id == PortfolioSlot.ea_id)
        )
        slot_result = await session.execute(slot_stmt)
        slot_rows = slot_result.all()

    # Step 2: Build kept list with efficiency from latest scores
    kept_entries = []
    if slot_rows:
        slot_ea_ids = [slot.ea_id for slot, record in slot_rows]

        async with sf() as session:
            # Get latest viable score per kept player for efficiency ranking
            latest_subq = (
                select(
                    PlayerScore.ea_id,
                    func.max(PlayerScore.scored_at).label("max_scored_at"),
                )
                .where(
                    PlayerScore.is_viable == True,  # noqa: E712
                    PlayerScore.ea_id.in_(slot_ea_ids),
                )
                .group_by(PlayerScore.ea_id)
                .subquery()
            )
            score_stmt = (
                select(PlayerScore)
                .join(
                    latest_subq,
                    (PlayerScore.ea_id == latest_subq.c.ea_id)
                    & (PlayerScore.scored_at == latest_subq.c.max_scored_at),
                )
            )
            score_result = await session.execute(score_stmt)
            score_map = {s.ea_id: s for s in score_result.scalars().all()}

        for slot, record in slot_rows:
            score = score_map.get(slot.ea_id)
            kept_entries.append({
                "ea_id": slot.ea_id,
                "name": record.name,
                "rating": record.rating,
                "position": record.position,
                "price": slot.buy_price,
                "sell_price": slot.sell_price,
                "margin_pct": score.margin_pct if score else 0,
                "efficiency": score.efficiency if score else 0.0,
            })

    # Step 3: Drop least efficient players if budget is too tight
    kept_entries.sort(key=lambda x: x["efficiency"], reverse=True)
    kept = []
    dropped = []
    kept_cost = 0
    for entry in kept_entries:
        if kept_cost + entry["price"] <= body.budget:
            kept.append(entry)
            kept_cost += entry["price"]
        else:
            dropped.append(entry)

    # Step 4: Fill remaining budget with new picks
    remaining_budget = body.budget - kept_cost
    kept_ea_ids = {e["ea_id"] for e in kept}

    new_entries = []
    if remaining_budget > 0:
        async with sf() as session:
            rows = await _fetch_latest_viable_scores(session)

        # Exclude kept players
        excluded = kept_ea_ids | {e["ea_id"] for e in dropped}
        candidates = [
            _build_scored_entry(score, record)
            for score, record in rows
            if score.ea_id not in excluded
        ]

        if candidates:
            selected = optimize_portfolio(candidates, remaining_budget)
            for entry in selected:
                new_entries.append({
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
                })

    new_cost = sum(e["price"] for e in new_entries)
    budget_used = kept_cost + new_cost

    return {
        "kept": kept,
        "new": new_entries,
        "dropped": dropped,
        "budget": body.budget,
        "budget_used": budget_used,
        "budget_remaining": body.budget - budget_used,
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

    # Phase 1: Fast write transaction — cancel actions, delete slot, commit.
    # Releases row locks immediately so cleanup_tables and other queries don't block.
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

        await session.commit()

    # Phase 2: Read-only queries for replacement suggestions.
    sf = _read_session_factory(request)
    async with sf() as session:
        # 4. Get remaining portfolio ea_ids (for exclusion from candidates)
        remaining_result = await session.execute(select(PortfolioSlot.ea_id))
        remaining_ea_ids = {row[0] for row in remaining_result.all()}

        # 5. Query viable candidates (same pattern as get_portfolio)
        rows = await _fetch_latest_viable_scores(session)

    # 6. Build scored candidates excluding removed player and remaining slots
    excluded = remaining_ea_ids | {ea_id}
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
