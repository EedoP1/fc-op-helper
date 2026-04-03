"""Portfolio write endpoints — mutations (confirm, rebalance, delete)."""
import logging
from datetime import datetime

from fastapi import APIRouter, Query, Request, Path, HTTPException
from sqlalchemy import select, update, delete

from src.server.models_db import PlayerRecord, PortfolioSlot, TradeAction, TradeRecord
from src.config import TARGET_PLAYER_COUNT
from src.optimizer import optimize_portfolio
from src.server.api._helpers import _read_session_factory, ConfirmPlayer, ConfirmRequest
from src.server.api.portfolio_query import _build_scored_entry, _fetch_latest_viable_scores

logger = logging.getLogger(__name__)

router = APIRouter()


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

    # Server-side safety cap: active (non-leftover) slots must never exceed TARGET_PLAYER_COUNT.
    # The client is authoritative about which players to keep, but it cannot create more than
    # TARGET_PLAYER_COUNT active slots. Truncate to the cap so a buggy or replayed request
    # cannot inflate the portfolio regardless of what the client sends.
    if len(deduped) > TARGET_PLAYER_COUNT:
        # Keep the first TARGET_PLAYER_COUNT in original submission order
        ordered = list(dict.fromkeys(p.ea_id for p in body.players if p.ea_id in deduped))
        deduped = {ea_id: deduped[ea_id] for ea_id in ordered[:TARGET_PLAYER_COUNT]}
        logger.warning(
            "confirm_portfolio: client sent %d players, capped to %d",
            len(body.players), TARGET_PLAYER_COUNT,
        )

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

    The remaining slot count is read inside the same write transaction,
    immediately after the DELETE and before commit. This makes the count
    atomic with the deletion: two concurrent removes each commit their own
    delete and observe the correct post-delete count, so neither overshoots
    TARGET_PLAYER_COUNT when the caller adds the suggested replacements.

    Replacements returned = min(needed_to_reach_100, optimizer_output).

    Trade history (TradeRecord rows) is preserved — only the active slot and
    pending actions are removed.

    Args:
        request: FastAPI Request (app.state carries session_factory).
        ea_id: EA ID of the player to remove.
        budget: Total portfolio budget used to compute freed_budget context.

    Returns:
        Dict with keys: removed_ea_id, freed_budget, replacements (list),
        remaining_count (int).

    Raises:
        HTTPException 404: If ea_id is not in portfolio_slots.
    """
    session_factory = request.app.state.session_factory

    # Phase 1: Write transaction — cancel actions, delete slot, count remaining, commit.
    # remaining_ea_ids is read AFTER the delete executes (within the same transaction)
    # so the count reflects the post-deletion state and is consistent under concurrency.
    async with session_factory() as session:
        # 1. Look up the slot
        slot_result = await session.execute(
            select(PortfolioSlot).where(PortfolioSlot.ea_id == ea_id)
        )
        slot = slot_result.scalar_one_or_none()
        if slot is None:
            raise HTTPException(status_code=404, detail="Player not in portfolio")

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

        # 4. Count + capture remaining ea_ids and buy_prices AFTER deletion, within the
        # same transaction. This is the key race-condition fix: the count is atomic with
        # the delete, so two concurrent removes each see their own correct post-delete
        # portfolio size.
        remaining_result = await session.execute(
            select(PortfolioSlot.ea_id, PortfolioSlot.buy_price)
        )
        remaining_rows = remaining_result.all()
        remaining_ea_ids = {row[0] for row in remaining_rows}
        remaining_total_cost = sum(row[1] for row in remaining_rows)

        # freed_budget = total budget minus what's still tied up in remaining slots.
        # This is the correct amount available to the optimizer for replacements —
        # not just the removed player's buy_price (which ignores unspent budget).
        freed_budget = budget - remaining_total_cost

        await session.commit()

    # How many slots need to be filled to reach TARGET_PLAYER_COUNT?
    # remaining_count is already post-deletion so needed >= 0.
    remaining_count = len(remaining_ea_ids)
    needed = max(0, TARGET_PLAYER_COUNT - remaining_count)

    # Phase 2: Read-only queries for replacement suggestions.
    sf = _read_session_factory(request)
    async with sf() as session:
        # 5. Query viable candidates (same pattern as get_portfolio)
        rows = await _fetch_latest_viable_scores(session)

    # 6. Build scored candidates excluding removed player and remaining slots
    excluded = remaining_ea_ids | {ea_id}
    scored_candidates = [
        _build_scored_entry(score, record)
        for score, record in rows
        if score.ea_id not in excluded
    ]

    # 7. Run optimizer for replacements within freed budget, then cap to `needed`.
    # Capping prevents overshoot when the optimizer returns multiple cheap players.
    # needed=0 means portfolio is already at or above TARGET_PLAYER_COUNT — no replacements.
    if scored_candidates and needed > 0:
        replacements_raw = optimize_portfolio(scored_candidates, freed_budget)
        replacements_raw = replacements_raw[:needed]
    else:
        replacements_raw = []

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
        "Removed ea_id=%d from portfolio (freed=%d, remaining=%d, needed=%d), "
        "returning %d replacements",
        ea_id, freed_budget, remaining_count, needed, len(replacements),
    )

    return {
        "removed_ea_id": ea_id,
        "freed_budget": freed_budget,
        "replacements": replacements,
        "remaining_count": remaining_count,
    }
