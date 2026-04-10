"""Algo trading API endpoints.

Provides:
- POST /api/v1/algo/start         — activate algo trading with a budget
- POST /api/v1/algo/stop          — deactivate and cancel pending signals
- GET  /api/v1/algo/status        — current status, positions, pending signals
- GET  /api/v1/algo/signals/pending — claim next PENDING signal
- POST /api/v1/algo/signals/{id}/complete — record signal outcome
"""
import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select

from src.server.models_db import AlgoConfig, AlgoPosition, AlgoSignal, AlgoTrade, PlayerRecord

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")

EA_TAX_RATE = 0.05

# ── Pydantic request models ────────────────────────────────────────────────────


class StartPayload(BaseModel):
    """Payload for POST /api/v1/algo/start."""

    budget: int


class CompletePayload(BaseModel):
    """Payload for POST /api/v1/algo/signals/{id}/complete."""

    outcome: str   # "bought" | "sold" | "failed" | "skipped"
    price: int
    quantity: int


# ── Helpers ───────────────────────────────────────────────────────────────────


def _signal_dict(signal: AlgoSignal, player: PlayerRecord | None) -> dict:
    """Build the response dict for a signal, joining player metadata."""
    return {
        "id": signal.id,
        "ea_id": signal.ea_id,
        "action": signal.action,
        "quantity": signal.quantity,
        "reference_price": signal.reference_price,
        "player_name": player.name if player else None,
        "rating": player.rating if player else None,
        "position": player.position if player else None,
        "card_type": player.card_type if player else None,
    }


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.post("/algo/start", status_code=200)
async def algo_start(payload: StartPayload, request: Request):
    """Activate algo trading with the given budget.

    Creates a new AlgoConfig row if none exists, or updates the existing one
    to set is_active=True and update the budget.

    Args:
        payload: Contains budget (int).
        request: FastAPI request (session_factory on app.state).

    Returns:
        Dict with status "ok", is_active True, and budget.
    """
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        result = await session.execute(select(AlgoConfig).limit(1))
        config = result.scalar_one_or_none()
        now = datetime.utcnow()
        if config is None:
            config = AlgoConfig(
                budget=payload.budget,
                is_active=True,
                strategy_params=None,
                created_at=now,
                updated_at=now,
            )
            session.add(config)
        else:
            config.budget = payload.budget
            config.is_active = True
            config.updated_at = now
        await session.commit()

    logger.info("Algo started with budget=%d", payload.budget)
    return {"status": "ok", "is_active": True, "budget": payload.budget}


@router.post("/algo/stop", status_code=200)
async def algo_stop(request: Request):
    """Deactivate algo trading and cancel all PENDING/CLAIMED signals.

    Args:
        request: FastAPI request (session_factory on app.state).

    Returns:
        Dict with status "ok", is_active False, and cancelled count.
    """
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        result = await session.execute(select(AlgoConfig).limit(1))
        config = result.scalar_one_or_none()
        now = datetime.utcnow()
        if config is None:
            config = AlgoConfig(
                budget=0,
                is_active=False,
                strategy_params=None,
                created_at=now,
                updated_at=now,
            )
            session.add(config)
        else:
            config.is_active = False
            config.updated_at = now

        # Cancel all PENDING and CLAIMED signals
        pending_result = await session.execute(
            select(AlgoSignal).where(
                AlgoSignal.status.in_(["PENDING", "CLAIMED"])
            )
        )
        signals = pending_result.scalars().all()
        for sig in signals:
            sig.status = "CANCELLED"

        await session.commit()
        cancelled = len(signals)

    logger.info("Algo stopped, cancelled %d signals", cancelled)
    return {"status": "ok", "is_active": False, "cancelled": cancelled}


@router.get("/algo/status")
async def algo_status(request: Request):
    """Return current algo trading status.

    Includes is_active, budget, available cash (budget minus held cost),
    positions with unrealized P&L, and count of pending signals.

    Args:
        request: FastAPI request (session_factory on app.state).

    Returns:
        Dict with is_active, budget, cash, positions list, pending_signals.
    """
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        result = await session.execute(select(AlgoConfig).limit(1))
        config = result.scalar_one_or_none()

        if config is None:
            return {
                "is_active": False,
                "budget": 0,
                "cash": 0,
                "positions": [],
                "pending_signals": 0,
                "realized_pnl": 0,
            }

        # Load all positions with player metadata
        pos_result = await session.execute(select(AlgoPosition))
        positions = pos_result.scalars().all()

        # Load all player records needed for positions in one query
        ea_ids = [p.ea_id for p in positions]
        player_map: dict[int, PlayerRecord] = {}
        if ea_ids:
            players_result = await session.execute(
                select(PlayerRecord).where(PlayerRecord.ea_id.in_(ea_ids))
            )
            player_map = {p.ea_id: p for p in players_result.scalars().all()}

        # Load latest market snapshot for current price estimate
        from src.server.models_db import MarketSnapshot
        from sqlalchemy import func
        snapshot_map: dict[int, int] = {}
        if ea_ids:
            latest_subq = (
                select(
                    MarketSnapshot.ea_id,
                    func.max(MarketSnapshot.captured_at).label("max_at"),
                )
                .where(MarketSnapshot.ea_id.in_(ea_ids))
                .group_by(MarketSnapshot.ea_id)
                .subquery()
            )
            snap_result = await session.execute(
                select(MarketSnapshot.ea_id, MarketSnapshot.current_lowest_bin)
                .join(
                    latest_subq,
                    (MarketSnapshot.ea_id == latest_subq.c.ea_id)
                    & (MarketSnapshot.captured_at == latest_subq.c.max_at),
                )
            )
            snapshot_map = {row.ea_id: row.current_lowest_bin for row in snap_result.all()}

        held_cost = 0
        position_rows = []
        for pos in positions:
            player = player_map.get(pos.ea_id)
            cost = pos.buy_price * pos.quantity
            held_cost += cost
            current_price = snapshot_map.get(pos.ea_id, pos.buy_price)
            sell_net = int(current_price * (1 - EA_TAX_RATE))
            unrealized_pnl = (sell_net - pos.buy_price) * pos.quantity
            position_rows.append({
                "ea_id": pos.ea_id,
                "player_name": player.name if player else None,
                "quantity": pos.quantity,
                "buy_price": pos.buy_price,
                "current_price": current_price,
                "unrealized_pnl": unrealized_pnl,
                "listed_price": pos.listed_price,
                "listed_at": pos.listed_at.isoformat() if pos.listed_at else None,
            })

        # Count pending signals
        pending_count_result = await session.execute(
            select(func.count()).where(AlgoSignal.status == "PENDING")
        )
        pending_count = pending_count_result.scalar_one()

        from sqlalchemy import func as sa_func
        pnl_result = await session.execute(
            select(sa_func.coalesce(sa_func.sum(AlgoTrade.pnl), 0))
        )
        realized_pnl = pnl_result.scalar_one()

        cash = config.budget - held_cost

    return {
        "is_active": config.is_active,
        "budget": config.budget,
        "cash": cash,
        "positions": position_rows,
        "pending_signals": pending_count,
        "realized_pnl": realized_pnl,
    }


@router.get("/algo/signals/pending")
async def get_pending_signal(request: Request):
    """Claim and return the next PENDING algo signal.

    Steps:
    1. Reset stale CLAIMED signals (claimed > 5 min ago) back to PENDING.
    2. Return the oldest PENDING signal and mark it CLAIMED.
    3. If none, return {"signal": null}.

    Returns:
        Dict with key "signal": signal dict or null.
    """
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        now = datetime.utcnow()
        stale_threshold = now - timedelta(minutes=5)

        # Step 1: reset stale CLAIMED signals
        stale_result = await session.execute(
            select(AlgoSignal).where(
                AlgoSignal.status == "CLAIMED",
                AlgoSignal.claimed_at <= stale_threshold,
            )
        )
        for sig in stale_result.scalars().all():
            sig.status = "PENDING"
            sig.claimed_at = None

        # Step 1b: expire signals older than 3 hours
        ttl_threshold = now - timedelta(hours=3)
        expired_result = await session.execute(
            select(AlgoSignal).where(
                AlgoSignal.status == "PENDING",
                AlgoSignal.created_at <= ttl_threshold,
            )
        )
        expired_count = 0
        for sig in expired_result.scalars().all():
            sig.status = "EXPIRED"
            expired_count += 1
        if expired_count:
            logger.info("Expired %d stale signals (>3h old)", expired_count)

        # Step 2: find next PENDING signal
        pending_result = await session.execute(
            select(AlgoSignal)
            .where(AlgoSignal.status == "PENDING")
            .order_by(AlgoSignal.created_at)
            .limit(1)
        )
        signal = pending_result.scalar_one_or_none()

        if signal is None:
            await session.commit()
            return {"signal": None}

        # Mark CLAIMED
        signal.status = "CLAIMED"
        signal.claimed_at = now

        # Load player metadata
        player_result = await session.execute(
            select(PlayerRecord).where(PlayerRecord.ea_id == signal.ea_id)
        )
        player = player_result.scalar_one_or_none()

        data = _signal_dict(signal, player)
        await session.commit()

    logger.info(
        "Claimed algo signal id=%d action=%s ea_id=%d",
        data["id"], data["action"], data["ea_id"],
    )
    return {"signal": data}


@router.post("/algo/signals/{signal_id}/complete", status_code=200)
async def complete_signal(signal_id: int, payload: CompletePayload, request: Request):
    """Record the outcome of an algo signal.

    Outcomes:
    - "bought": creates an AlgoPosition, marks signal DONE.
    - "sold": deletes the AlgoPosition for this ea_id, marks signal DONE.
    - "failed" | "skipped": marks signal CANCELLED.

    Args:
        signal_id: ID of the AlgoSignal to complete.
        payload: Contains outcome, price, quantity.
        request: FastAPI request (session_factory on app.state).

    Returns:
        Dict with status "ok".

    Raises:
        HTTPException 404 if signal not found.
        HTTPException 400 if outcome is invalid.
    """
    valid_outcomes = {"bought", "sold", "listed", "failed", "skipped"}
    if payload.outcome not in valid_outcomes:
        raise HTTPException(status_code=400, detail=f"Invalid outcome: {payload.outcome}")

    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        result = await session.execute(
            select(AlgoSignal).where(AlgoSignal.id == signal_id)
        )
        signal = result.scalar_one_or_none()

        if signal is None:
            raise HTTPException(status_code=404, detail="Signal not found")

        now = datetime.utcnow()

        if payload.outcome == "bought":
            pos = AlgoPosition(
                ea_id=signal.ea_id,
                quantity=payload.quantity,
                buy_price=payload.price,
                buy_time=now,
                peak_price=payload.price,
            )
            session.add(pos)
            signal.status = "DONE"
            signal.completed_at = now

        elif payload.outcome == "listed":
            pos_result = await session.execute(
                select(AlgoPosition).where(AlgoPosition.ea_id == signal.ea_id)
            )
            pos = pos_result.scalar_one_or_none()
            if pos is not None:
                pos.listed_price = payload.price
                pos.listed_at = now
            signal.status = "DONE"
            signal.completed_at = now

        elif payload.outcome == "sold":
            pos_result = await session.execute(
                select(AlgoPosition).where(AlgoPosition.ea_id == signal.ea_id)
            )
            pos = pos_result.scalar_one_or_none()
            if pos is not None:
                await session.delete(pos)
            signal.status = "DONE"
            signal.completed_at = now

        else:  # "failed" | "skipped"
            signal.status = "CANCELLED"
            signal.completed_at = now

        await session.commit()

    logger.info(
        "Completed algo signal id=%d outcome=%s ea_id=%d price=%d qty=%d",
        signal_id, payload.outcome, signal.ea_id, payload.price, payload.quantity,
    )
    return {"status": "ok"}


class PositionSoldPayload(BaseModel):
    """Payload for POST /api/v1/algo/positions/{ea_id}/sold."""

    sell_price: int
    quantity: int


@router.post("/algo/positions/{ea_id}/sold", status_code=200)
async def position_sold(ea_id: int, payload: PositionSoldPayload, request: Request):
    """Record that algo cards actually sold on the transfer market.

    Decrements position quantity, writes an AlgoTrade row for PnL tracking.
    Deletes the position entirely when quantity reaches 0.
    """
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        result = await session.execute(
            select(AlgoPosition).where(AlgoPosition.ea_id == ea_id)
        )
        pos = result.scalar_one_or_none()

        if pos is None:
            raise HTTPException(status_code=404, detail=f"No position for ea_id={ea_id}")

        now = datetime.utcnow()
        per_unit_net = int(payload.sell_price * (1 - EA_TAX_RATE)) - pos.buy_price
        pnl = per_unit_net * payload.quantity

        session.add(AlgoTrade(
            ea_id=ea_id,
            quantity=payload.quantity,
            buy_price=pos.buy_price,
            sell_price=payload.sell_price,
            pnl=pnl,
            sold_at=now,
        ))

        pos.quantity -= payload.quantity
        if pos.quantity <= 0:
            await session.delete(pos)

        await session.commit()

    logger.info(
        "Algo position sold: ea_id=%d qty=%d sell_price=%d pnl=%d",
        ea_id, payload.quantity, payload.sell_price, pnl,
    )
    return {"status": "ok", "pnl": pnl}


class PositionRelistPayload(BaseModel):
    """Payload for POST /api/v1/algo/positions/{ea_id}/relist."""

    price: int
    quantity: int


@router.post("/algo/positions/{ea_id}/relist", status_code=200)
async def position_relist(ea_id: int, payload: PositionRelistPayload, request: Request):
    """Record that an expired algo card was relisted at a new price."""
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        result = await session.execute(
            select(AlgoPosition).where(AlgoPosition.ea_id == ea_id)
        )
        pos = result.scalar_one_or_none()

        if pos is None:
            raise HTTPException(status_code=404, detail=f"No position for ea_id={ea_id}")

        now = datetime.utcnow()
        pos.listed_price = payload.price
        pos.listed_at = now
        await session.commit()

    logger.info("Algo position relisted: ea_id=%d price=%d", ea_id, payload.price)
    return {"status": "ok"}
