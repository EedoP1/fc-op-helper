"""Portfolio lifecycle logic: action derivation, stale resets, and trade record validation.

Extracted from src/server/api/actions.py so endpoint code stays as thin orchestration.
"""
import logging
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import select

from src.server.models_db import PortfolioSlot, TradeAction, TradeRecord

logger = logging.getLogger(__name__)

STALE_TIMEOUT = timedelta(minutes=5)

# Maps trade outcome strings to the action_type that produced them.
OUTCOME_TO_ACTION_TYPE = {
    "bought": "buy",
    "listed": "list",
    "sold": "list",      # sold is the result of a list action
    "expired": "list",   # expired is the result of a list action
}


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _reset_stale_actions(session) -> None:
    """Reset IN_PROGRESS actions older than STALE_TIMEOUT back to PENDING."""
    from sqlalchemy import update
    stale_cutoff = datetime.utcnow() - STALE_TIMEOUT
    await session.execute(
        update(TradeAction)
        .where(TradeAction.status == "IN_PROGRESS")
        .where(TradeAction.claimed_at < stale_cutoff)
        .values(status="PENDING", claimed_at=None)
    )


async def _derive_next_action(session) -> TradeAction | None:
    """Derive the next needed action from portfolio_slots + trade_records.

    For each slot, determines lifecycle state from the most recent trade_record outcome:
    - No records           -> BUY (active only; leftovers -> LIST)
    - Most recent "bought" -> LIST
    - Most recent "listed" -> waiting (card on market), skip
    - Most recent "sold"   -> BUY (active only; leftovers auto-cleaned)
    - Most recent "expired"-> RELIST

    Leftover slots never generate BUY actions — the player is already owned.

    Returns an unsaved TradeAction if work is needed, else None.
    """
    slots_result = await session.execute(select(PortfolioSlot))
    slots = slots_result.scalars().all()

    for slot in slots:
        # Double-check for existing active action (belt-and-suspenders with the lock).
        existing_result = await session.execute(
            select(TradeAction)
            .where(
                TradeAction.ea_id == slot.ea_id,
                TradeAction.status.in_(["PENDING", "IN_PROGRESS"]),
            )
            .limit(1)
        )
        if existing_result.scalar_one_or_none() is not None:
            continue

        # Get the most recent trade record for this slot
        records_result = await session.execute(
            select(TradeRecord)
            .where(TradeRecord.ea_id == slot.ea_id)
            .order_by(TradeRecord.recorded_at.desc())
            .limit(1)
        )
        latest_record = records_result.scalar_one_or_none()

        action_type: str | None = None
        target_price: int | None = None

        if latest_record is None:
            if slot.is_leftover:
                # Leftover with no records — player is in club, need to LIST
                action_type = "LIST"
                target_price = slot.sell_price
            else:
                # Active slot, no records — need to BUY
                action_type = "BUY"
                target_price = slot.buy_price
        elif latest_record.outcome == "bought":
            # Bought but not listed yet — need to LIST
            action_type = "LIST"
            target_price = slot.sell_price
        elif latest_record.outcome == "expired":
            # Listing expired — need to RELIST
            action_type = "RELIST"
            target_price = slot.sell_price
        elif latest_record.outcome == "sold":
            if slot.is_leftover:
                # Leftover sold — auto-cleanup (delete slot), skip action
                await session.delete(slot)
                await session.flush()
                continue
            else:
                # Active slot, cycle complete — restart with BUY
                action_type = "BUY"
                target_price = slot.buy_price
        else:
            # "listed" — card is on market, nothing to do yet
            continue

        now = datetime.utcnow()
        new_action = TradeAction(
            ea_id=slot.ea_id,
            action_type=action_type,
            status="PENDING",
            target_price=target_price,
            player_name=f"Player {slot.ea_id}",
            created_at=now,
        )
        session.add(new_action)
        await session.flush()  # get the id without committing
        return new_action

    return None


async def _claim_action(session, action: TradeAction) -> dict:
    """Mark action IN_PROGRESS, flush, and return response shape."""
    now = datetime.utcnow()
    action.status = "IN_PROGRESS"
    action.claimed_at = now
    await session.flush()
    return {
        "id": action.id,
        "ea_id": action.ea_id,
        "action_type": action.action_type,
        "target_price": action.target_price,
        "player_name": action.player_name,
    }


async def validate_trade_record(
    session, ea_id: int, outcome: str, price: int
) -> tuple["PortfolioSlot | None", "str | None"]:
    """Validate ea_id is in portfolio and check for duplicates.

    Returns (slot, error_message). error_message is None if valid.

    Args:
        session: SQLAlchemy async session.
        ea_id: Player EA ID to validate.
        outcome: Trade outcome string to validate and deduplicate.
        price: Trade price (unused in validation but kept for symmetry).

    Returns:
        Tuple of (PortfolioSlot, None) if valid, or (None, error_string) if invalid.
        If the record is a duplicate (same outcome as latest), returns (slot, "deduplicated").
    """
    result = await session.execute(
        select(PortfolioSlot).where(PortfolioSlot.ea_id == ea_id)
    )
    slot = result.scalar_one_or_none()
    if slot is None:
        return None, f"ea_id {ea_id} not in portfolio"

    # Server-side dedup: only record if outcome differs from the most recent
    # record for this player. Mirrors the client-side last-status dedup.
    latest_stmt = (
        select(TradeRecord.outcome)
        .where(TradeRecord.ea_id == ea_id)
        .order_by(TradeRecord.id.desc())
        .limit(1)
    )
    latest_result = await session.execute(latest_stmt)
    latest_outcome = latest_result.scalar_one_or_none()
    if latest_outcome == outcome:
        return slot, "deduplicated"

    return slot, None
