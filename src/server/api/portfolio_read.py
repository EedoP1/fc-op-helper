"""Portfolio read endpoints — GET queries and stateless previews."""
import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Query, Request
from sqlalchemy import select, func

from src.server.models_db import PlayerRecord, PlayerScore, PortfolioSlot, TradeRecord
from src.config import STALE_THRESHOLD_HOURS, TARGET_PLAYER_COUNT
from src.optimizer import optimize_portfolio
from src.server.api._helpers import _read_session_factory, GenerateRequest, SwapPreviewRequest, RebalanceRequest
from src.server.api.portfolio_query import _build_scored_entry, _fetch_latest_viable_scores

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/portfolio")
async def get_portfolio(
    request: Request,
    budget: int = Query(..., gt=0, description="Total budget in coins"),
    exclude_card_types: str = Query(None, description="Comma-separated card types to exclude"),
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

    # Parse excluded card types
    excl = [t.strip() for t in exclude_card_types.split(",")] if exclude_card_types else None

    # Run optimizer
    selected = optimize_portfolio(scored_list, budget, exclude_card_types=excl)

    # Compute budget summary
    budget_used = sum(entry["buy_price"] for entry in selected)

    # Serialize response with staleness
    stale_cutoff = datetime.utcnow() - timedelta(hours=STALE_THRESHOLD_HOURS)
    data = []
    for entry in selected:
        last_scanned_at = entry["last_scanned_at"]
        # Normalize to naive datetime for comparison
        if isinstance(last_scanned_at, str):
            try:
                last_scanned_at = datetime.fromisoformat(last_scanned_at)
            except (ValueError, TypeError):
                last_scanned_at = None
        # Strip timezone info for comparison (Postgres returns tz-aware, utcnow() is naive)
        if last_scanned_at is not None and hasattr(last_scanned_at, 'tzinfo') and last_scanned_at.tzinfo is not None:
            last_scanned_at = last_scanned_at.replace(tzinfo=None)
        is_stale = (
            last_scanned_at is None
            or last_scanned_at < stale_cutoff
        )
        epph = entry.get("expected_profit_per_hour")
        sph = entry.get("sales_per_hour")
        # Real coins/hr: net_profit × sales/hr × OP success rate.
        net_profit = entry.get("net_profit") or 0
        op_ratio = entry.get("op_ratio") or 0.0
        coins_per_hour = (
            round(net_profit * sph * op_ratio, 2)
            if sph is not None else None
        )
        data.append({
            "ea_id": entry["ea_id"],
            "name": entry["name"],
            "rating": entry["rating"],
            "position": entry["position"],
            "price": entry["buy_price"],
            "sell_price": entry["sell_price"],
            "net_profit": entry["net_profit"],
            "margin_pct": entry["margin_pct"],
            "op_sales": entry["op_sales"],
            "total_sales": entry["total_sales"],
            "op_ratio": round(entry["op_ratio"], 3),
            "expected_profit": round(entry["expected_profit"], 1),
            "efficiency": round(entry["efficiency"], 4),
            "sales_per_hour": round(sph, 1) if sph is not None else None,
            "card_type": entry.get("card_type"),
            "is_stale": is_stale,
            "last_scanned": (
                last_scanned_at.isoformat() if last_scanned_at else None
            ),
            "expected_profit_per_hour": round(epph, 2) if epph else None,
            "coins_per_hour": coins_per_hour,
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

    # Exclude banned ea_ids before optimization — each removal regenerates from scratch
    banned = set(body.banned_ea_ids)
    scored_list = [e for e in scored_list if e["ea_id"] not in banned]

    if not scored_list:
        return {
            "error": "Not enough listing data yet. The system needs to accumulate market observations before it can recommend players. This typically takes a few hours of scanning.",
            "data": [],
            "count": 0,
            "budget": body.budget,
            "budget_used": 0,
            "budget_remaining": body.budget,
        }

    selected = optimize_portfolio(scored_list, body.budget, exclude_card_types=body.exclude_card_types or None)
    budget_used = sum(entry["buy_price"] for entry in selected)

    stale_cutoff = datetime.utcnow() - timedelta(hours=STALE_THRESHOLD_HOURS)
    data = []
    for entry in selected:
        last_scanned_at = entry["last_scanned_at"]
        if isinstance(last_scanned_at, str):
            try:
                last_scanned_at = datetime.fromisoformat(last_scanned_at)
            except (ValueError, TypeError):
                last_scanned_at = None
        if last_scanned_at is not None and hasattr(last_scanned_at, 'tzinfo') and last_scanned_at.tzinfo is not None:
            last_scanned_at = last_scanned_at.replace(tzinfo=None)
        is_stale = last_scanned_at is None or last_scanned_at < stale_cutoff
        epph = entry.get("expected_profit_per_hour")
        sph = entry.get("sales_per_hour")
        # Real coins/hr: net_profit × sales/hr × OP success rate.
        net_profit = entry.get("net_profit") or 0
        op_ratio = entry.get("op_ratio") or 0.0
        coins_per_hour = (
            round(net_profit * sph * op_ratio, 2)
            if sph is not None else None
        )
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
            "coins_per_hour": coins_per_hour,
            "futgg_url": entry.get("futgg_url"),
        })

    return {
        "data": data,
        "count": len(data),
        "budget": body.budget,
        "budget_used": budget_used,
        "budget_remaining": body.budget - budget_used,
    }


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

    # Cap replacements to the number of open slots in the draft.
    #
    # current_count is the draft size AFTER the client removed the player (post-splice).
    # needed = how many players the draft still needs to reach TARGET_PLAYER_COUNT.
    #
    # Examples:
    #   Remove 1 player from 100 → current_count=99, needed=1 → up to 1 replacement.
    #   Remove 5 players rapidly → last request has current_count=95, needed=5 → up to 5.
    #   A 30k card replaced by multiple 10k cards → optimizer returns several, but only
    #   needed are kept. Budget is maximised within the freed amount AND the slot cap.
    #
    # The confirm endpoint enforces TARGET_PLAYER_COUNT server-side as a final safety net,
    # so even if concurrent rapid removals cause transient overshoot in the draft, the
    # confirmed portfolio will never exceed TARGET_PLAYER_COUNT.
    needed = max(0, TARGET_PLAYER_COUNT - body.current_count)
    replacements_raw = replacements_raw[:needed]

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
            "card_type": record.card_type,
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
