"""Action queue endpoints for the Chrome extension automation loop.

Provides:
- GET  /api/v1/actions/pending           — claim next pending action (stale reset + lifecycle derivation)
- POST /api/v1/actions/{id}/complete     — record trade outcome and mark action DONE
- POST /api/v1/portfolio/slots           — seed/update portfolio slots for action derivation
"""
import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import select, update, func

from src.server.models_db import PortfolioSlot, TradeAction, TradeRecord

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")

STALE_TIMEOUT = timedelta(minutes=5)


# ── Pydantic request/response models ──────────────────────────────────────────

class CompleteActionPayload(BaseModel):
    """Payload for POST /api/v1/actions/{id}/complete."""

    price: int
    outcome: str  # "bought" | "listed" | "sold" | "expired"


class SlotEntry(BaseModel):
    """A single portfolio slot entry for seeding."""

    ea_id: int
    buy_price: int
    sell_price: int
    player_name: str


class SeedSlotsPayload(BaseModel):
    """Payload for POST /api/v1/portfolio/slots."""

    slots: list[SlotEntry]


class DirectTradeRecordPayload(BaseModel):
    """Payload for POST /api/v1/trade-records/direct.

    Used by the trade observer for bootstrap reporting — records outcomes
    without requiring an existing TradeAction (Pitfall 2 resolution).
    """

    ea_id: int
    price: int
    outcome: str  # "bought" | "listed" | "sold" | "expired"


class BatchTradeRecordPayload(BaseModel):
    """Payload for POST /api/v1/trade-records/batch."""

    records: list[DirectTradeRecordPayload]


# ── Constants ──────────────────────────────────────────────────────────────────

_OUTCOME_TO_ACTION_TYPE = {
    "bought": "buy",
    "listed": "list",
    "sold": "list",      # sold is the result of a list action
    "expired": "list",   # expired is the result of a list action
}


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _reset_stale_actions(session) -> None:
    """Reset IN_PROGRESS actions older than STALE_TIMEOUT back to PENDING."""
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
    - No records           -> BUY
    - Most recent "bought" -> LIST
    - Most recent "listed" -> waiting (card on market), skip
    - Most recent "sold"   -> cycle complete, start new BUY
    - Most recent "expired"-> RELIST

    Returns an unsaved TradeAction if work is needed, else None.
    """
    slots_result = await session.execute(select(PortfolioSlot))
    slots = slots_result.scalars().all()

    for slot in slots:
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
            # No records — need to BUY
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
            # Cycle complete — restart with BUY
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


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/actions/pending")
async def get_pending_action(request: Request):
    """Return the next pending action for the Chrome extension to execute.

    Steps:
    1. Reset stale IN_PROGRESS actions (claimed > 5 min ago) back to PENDING.
    2. Check for an existing PENDING action — claim and return it.
    3. If none, derive the next action from portfolio_slots + trade_records.
    4. If nothing to do, return {"action": null}.

    Returns:
        Dict with key "action": action dict or null.
    """
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        # Step 1: reset stale
        await _reset_stale_actions(session)

        # Step 2: check for an already-claimed IN_PROGRESS action (idempotent return)
        result = await session.execute(
            select(TradeAction)
            .where(TradeAction.status == "IN_PROGRESS")
            .order_by(TradeAction.claimed_at)
            .limit(1)
        )
        in_progress = result.scalar_one_or_none()
        if in_progress is not None:
            # Already claimed — return same action without touching it
            await session.commit()
            return {
                "action": {
                    "id": in_progress.id,
                    "ea_id": in_progress.ea_id,
                    "action_type": in_progress.action_type,
                    "target_price": in_progress.target_price,
                    "player_name": in_progress.player_name,
                }
            }

        # Step 3: find existing PENDING action
        result = await session.execute(
            select(TradeAction)
            .where(TradeAction.status == "PENDING")
            .order_by(TradeAction.created_at)
            .limit(1)
        )
        pending = result.scalar_one_or_none()

        if pending is None:
            # Step 4: derive from portfolio lifecycle
            pending = await _derive_next_action(session)

        if pending is None:
            await session.commit()
            return {"action": None}

        action_data = await _claim_action(session, pending)
        await session.commit()

    logger.info(
        "Claimed action id=%d type=%s ea_id=%d",
        action_data["id"], action_data["action_type"], action_data["ea_id"],
    )
    return {"action": action_data}


@router.post("/actions/{action_id}/complete")
async def complete_action(action_id: int, payload: CompleteActionPayload, request: Request):
    """Record the outcome of an action and mark it DONE.

    Inserts a TradeRecord and updates the TradeAction status.

    Args:
        action_id: ID of the TradeAction to complete.
        payload: Contains price (int) and outcome (str).
        request: FastAPI request (session_factory on app.state).

    Returns:
        Dict with status "ok" and trade_record_id.

    Raises:
        HTTPException 404 if action not found.
    """
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        result = await session.execute(
            select(TradeAction).where(TradeAction.id == action_id)
        )
        action = result.scalar_one_or_none()

        if action is None:
            raise HTTPException(status_code=404, detail="Action not found")

        now = datetime.utcnow()

        record = TradeRecord(
            ea_id=action.ea_id,
            action_type=action.action_type,
            price=payload.price,
            outcome=payload.outcome,
            recorded_at=now,
        )
        session.add(record)
        await session.flush()

        action.status = "DONE"
        action.completed_at = now
        await session.commit()

        record_id = record.id

    logger.info(
        "Completed action id=%d outcome=%s price=%d record_id=%d",
        action_id, payload.outcome, payload.price, record_id,
    )
    return {"status": "ok", "trade_record_id": record_id}


@router.post("/portfolio/slots", status_code=201)
async def seed_portfolio_slots(payload: SeedSlotsPayload, request: Request):
    """Seed or update portfolio slots so the action queue has data to work with.

    For each slot entry:
    - If a PortfolioSlot with this ea_id already exists: update buy_price and sell_price.
    - If not: insert a new PortfolioSlot.

    Returns 200 (not 201) when the slot list is empty.

    Args:
        payload: Contains a list of SlotEntry objects.
        request: FastAPI request (session_factory on app.state).

    Returns:
        Dict with status "ok" and count of slots processed.
    """
    if not payload.slots:
        return Response(content='{"status":"ok","count":0}', status_code=200, media_type="application/json")

    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        for entry in payload.slots:
            result = await session.execute(
                select(PortfolioSlot).where(PortfolioSlot.ea_id == entry.ea_id)
            )
            existing = result.scalar_one_or_none()

            if existing is not None:
                existing.buy_price = entry.buy_price
                existing.sell_price = entry.sell_price
            else:
                session.add(PortfolioSlot(
                    ea_id=entry.ea_id,
                    buy_price=entry.buy_price,
                    sell_price=entry.sell_price,
                    added_at=datetime.utcnow(),
                ))

        await session.commit()

    logger.info("Seeded %d portfolio slots", len(payload.slots))
    return {"status": "ok", "count": len(payload.slots)}


@router.post("/trade-records/direct", status_code=201)
async def direct_trade_record(payload: DirectTradeRecordPayload, request: Request):
    """Record a trade outcome directly without an action_id.

    Used by the trade observer for bootstrap: when the extension first scans
    the Transfer List after portfolio confirmation, no TradeActions exist yet.
    This endpoint inserts a TradeRecord directly so _derive_next_action can
    correctly determine the next lifecycle step.

    Validates that ea_id exists in portfolio_slots (D-03: only track portfolio players).

    Args:
        payload: Contains ea_id, price, outcome.
        request: FastAPI request (session_factory on app.state).

    Returns:
        Dict with status "ok" and trade_record_id.

    Raises:
        HTTPException 400 if outcome is invalid.
        HTTPException 404 if ea_id not in portfolio_slots.
    """
    action_type = _OUTCOME_TO_ACTION_TYPE.get(payload.outcome)
    if action_type is None:
        raise HTTPException(status_code=400, detail=f"Invalid outcome: {payload.outcome}")

    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        # Validate ea_id exists in portfolio
        result = await session.execute(
            select(PortfolioSlot).where(PortfolioSlot.ea_id == payload.ea_id)
        )
        slot = result.scalar_one_or_none()
        if slot is None:
            raise HTTPException(status_code=404, detail=f"ea_id {payload.ea_id} not in portfolio")

        # Server-side dedup: only record if outcome differs from the most recent
        # record for this player. Mirrors the client-side last-status dedup.
        latest_stmt = (
            select(TradeRecord.outcome)
            .where(TradeRecord.ea_id == payload.ea_id)
            .order_by(TradeRecord.id.desc())
            .limit(1)
        )
        latest_result = await session.execute(latest_stmt)
        latest_outcome = latest_result.scalar_one_or_none()
        if latest_outcome == payload.outcome:
            return {"status": "ok", "trade_record_id": -1, "deduplicated": True}

        now = datetime.utcnow()
        record = TradeRecord(
            ea_id=payload.ea_id,
            action_type=action_type,
            price=payload.price,
            outcome=payload.outcome,
            recorded_at=now,
        )
        session.add(record)
        await session.flush()
        record_id = record.id
        await session.commit()

    logger.info(
        "Direct trade record ea_id=%d outcome=%s price=%d record_id=%d",
        payload.ea_id, payload.outcome, payload.price, record_id,
    )
    return {"status": "ok", "trade_record_id": record_id}


@router.post("/trade-records/batch", status_code=201)
async def batch_trade_records(payload: BatchTradeRecordPayload, request: Request):
    """Record multiple trade outcomes in a single DB transaction.

    Validates all ea_ids exist in portfolio_slots, deduplicates within 5-minute
    window, inserts all records in one commit. Much faster than N individual calls
    when the scanner holds the write lock.
    """
    session_factory = request.app.state.session_factory
    succeeded = []
    failed = []

    async with session_factory() as session:
        # Load all portfolio ea_ids in one query
        all_ea_ids = [r.ea_id for r in payload.records]
        slot_result = await session.execute(
            select(PortfolioSlot.ea_id).where(PortfolioSlot.ea_id.in_(all_ea_ids))
        )
        valid_ea_ids = {row.ea_id for row in slot_result.all()}

        # Load latest outcome per player for dedup (mirrors client-side last-status logic)
        latest_subq = (
            select(TradeRecord.ea_id, func.max(TradeRecord.id).label("max_id"))
            .where(TradeRecord.ea_id.in_(all_ea_ids))
            .group_by(TradeRecord.ea_id)
            .subquery()
        )
        latest_result = await session.execute(
            select(TradeRecord.ea_id, TradeRecord.outcome)
            .join(latest_subq, (TradeRecord.ea_id == latest_subq.c.ea_id) & (TradeRecord.id == latest_subq.c.max_id))
        )
        last_outcome: dict[int, str] = {r.ea_id: r.outcome for r in latest_result.all()}

        now = datetime.utcnow()
        for record in payload.records:
            action_type = _OUTCOME_TO_ACTION_TYPE.get(record.outcome)
            if action_type is None or record.ea_id not in valid_ea_ids:
                failed.append(record.ea_id)
                continue
            if last_outcome.get(record.ea_id) == record.outcome:
                succeeded.append(record.ea_id)  # deduped = success
                continue
            session.add(TradeRecord(
                ea_id=record.ea_id,
                action_type=action_type,
                price=record.price,
                outcome=record.outcome,
                recorded_at=now,
            ))
            succeeded.append(record.ea_id)

        await session.commit()

    logger.info("Batch trade records: %d succeeded, %d failed", len(succeeded), len(failed))
    return {"status": "ok", "succeeded": succeeded, "failed": failed}
