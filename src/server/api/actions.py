"""Action queue endpoints for the Chrome extension automation loop.

Provides:
- GET  /api/v1/actions/pending           — claim next pending action (stale reset + lifecycle derivation)
- POST /api/v1/actions/{id}/complete     — record trade outcome and mark action DONE
- POST /api/v1/portfolio/slots           — seed/update portfolio slots for action derivation
"""
import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.server.models_db import PortfolioSlot, TradeAction, TradeRecord
from src.server.lifecycle import (
    OUTCOME_TO_ACTION_TYPE,
    _claim_action,
    _derive_next_action,
    _reset_stale_actions,
    validate_trade_record,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")

# Keep the private alias for internal use (backward compat with any existing callers)
_OUTCOME_TO_ACTION_TYPE = OUTCOME_TO_ACTION_TYPE


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
        # Serialize action derivation to prevent MVCC race where two concurrent
        # requests both see no PENDING action and both create one for the same slot.
        if session.bind.dialect.name != "sqlite":
            await session.execute(text("SELECT pg_advisory_xact_lock(42)"))

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

        # Auto-cleanup: if a leftover player was sold, remove the slot
        leftover_removed = False
        if payload.outcome == "sold":
            slot_result = await session.execute(
                select(PortfolioSlot).where(
                    PortfolioSlot.ea_id == action.ea_id,
                    PortfolioSlot.is_leftover == True,  # noqa: E712
                )
            )
            leftover_slot = slot_result.scalar_one_or_none()
            if leftover_slot is not None:
                await session.delete(leftover_slot)
                leftover_removed = True

        await session.commit()

        record_id = record.id

    logger.info(
        "Completed action id=%d outcome=%s price=%d record_id=%d%s",
        action_id, payload.outcome, payload.price, record_id,
        " (leftover removed)" if leftover_removed else "",
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
        now = datetime.utcnow()
        for entry in payload.slots:
            stmt = pg_insert(PortfolioSlot).values(
                ea_id=entry.ea_id,
                buy_price=entry.buy_price,
                sell_price=entry.sell_price,
                added_at=now,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["ea_id"],
                set_=dict(
                    buy_price=entry.buy_price,
                    sell_price=entry.sell_price,
                ),
            )
            await session.execute(stmt)

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
    action_type = OUTCOME_TO_ACTION_TYPE.get(payload.outcome)
    if action_type is None:
        raise HTTPException(status_code=400, detail=f"Invalid outcome: {payload.outcome}")

    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        slot, error = await validate_trade_record(session, payload.ea_id, payload.outcome, payload.price)
        if error == f"ea_id {payload.ea_id} not in portfolio":
            raise HTTPException(status_code=404, detail=error)
        if error == "deduplicated":
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

        # Auto-cleanup: if a leftover player was sold, remove the slot
        leftover_removed = False
        if payload.outcome == "sold" and slot.is_leftover:
            await session.delete(slot)
            leftover_removed = True

        await session.commit()

    logger.info(
        "Direct trade record ea_id=%d outcome=%s price=%d record_id=%d%s",
        payload.ea_id, payload.outcome, payload.price, record_id,
        " (leftover removed)" if leftover_removed else "",
    )
    return {"status": "ok", "trade_record_id": record_id}


@router.post("/trade-records/batch", status_code=201)
async def batch_trade_records(payload: BatchTradeRecordPayload, request: Request):
    """Record multiple trade outcomes in a single DB transaction.

    Validates all ea_ids exist in portfolio_slots, deduplicates within 5-minute
    window, inserts all records in one commit. Much faster than N individual calls
    when the scanner holds the write lock.
    """
    logger.warning("batch_trade_records: ENTER (%d records)", len(payload.records))
    session_factory = request.app.state.session_factory
    succeeded = []
    failed = []

    async with session_factory() as session:
        logger.warning("batch_trade_records: session acquired")
        # Load all portfolio slots in one query (need is_leftover for auto-cleanup)
        from sqlalchemy import func
        all_ea_ids = [r.ea_id for r in payload.records]
        slot_result = await session.execute(
            select(PortfolioSlot).where(PortfolioSlot.ea_id.in_(all_ea_ids))
        )
        slot_map = {s.ea_id: s for s in slot_result.scalars().all()}
        valid_ea_ids = set(slot_map.keys())
        logger.warning("batch_trade_records: valid_ea_ids=%s", valid_ea_ids)

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
        leftovers_removed = 0
        for record in payload.records:
            action_type = OUTCOME_TO_ACTION_TYPE.get(record.outcome)
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

            # Auto-cleanup: if a leftover player was sold, remove the slot
            slot = slot_map.get(record.ea_id)
            if record.outcome == "sold" and slot is not None and slot.is_leftover:
                await session.delete(slot)
                leftovers_removed += 1

        await session.commit()

    logger.info(
        "Batch trade records: %d succeeded, %d failed, %d leftovers removed",
        len(succeeded), len(failed), leftovers_removed,
    )
    return {"status": "ok", "succeeded": succeeded, "failed": failed}
