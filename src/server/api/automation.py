"""Automation endpoints: daily cap tracking and fresh price lookup.

Supports the Chrome extension automation cycle (D-24, D-25, D-32, D-13, D-31):
- Daily cap tracks searches+buys against a configurable per-day limit (default 500).
- Player price returns current buy_price and sell_price from the portfolio for the
  extension to use as a reference before each buy attempt (price guard, D-13/D-31).
"""
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Request, HTTPException
from sqlalchemy import select, text

from src.server.models_db import DailyTransactionCount, PortfolioSlot

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")

_DEFAULT_CAP = 500


def _today_utc() -> str:
    """Return today's date as a YYYY-MM-DD string in UTC."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _read_session_factory(request: Request):
    """Return the read-only session factory if available, else the default one."""
    return getattr(request.app.state, "read_session_factory", None) or request.app.state.session_factory


# ── Daily cap ─────────────────────────────────────────────────────────────────


@router.get("/automation/daily-cap")
async def get_daily_cap(request: Request) -> dict:
    """Return today's transaction count and cap.

    Returns:
        JSON with count, cap, capped flag, and date (UTC).
    """
    today = _today_utc()
    session_factory = _read_session_factory(request)
    async with session_factory() as session:
        row = await session.scalar(
            select(DailyTransactionCount).where(DailyTransactionCount.date == today)
        )

    if row is None:
        return {"count": 0, "cap": _DEFAULT_CAP, "capped": False, "date": today}

    return {
        "count": row.count,
        "cap": row.cap,
        "capped": row.count >= row.cap,
        "date": today,
    }


@router.post("/automation/daily-cap/increment")
async def increment_daily_cap(request: Request) -> dict:
    """Increment today's transaction counter by 1 and return updated state.

    Upserts the daily_transaction_count row for today using a conflict-safe
    INSERT ... ON CONFLICT DO UPDATE so concurrent requests are safe.

    Returns:
        JSON with count, cap, capped flag, and date (UTC).
    """
    today = _today_utc()
    session_factory = request.app.state.session_factory

    async with session_factory() as session:
        # Use raw SQL for the upsert — SQLAlchemy ORM upsert requires dialect-specific
        # on_conflict_do_update which is different between PostgreSQL and SQLite.
        # Raw text works for both and matches the plan's PostgreSQL-ready pattern.
        await session.execute(
            text(
                "INSERT INTO daily_transaction_count (date, count, cap) "
                "VALUES (:date, 1, :cap) "
                "ON CONFLICT (date) DO UPDATE SET count = daily_transaction_count.count + 1"
            ),
            {"date": today, "cap": _DEFAULT_CAP},
        )
        await session.commit()

        row = await session.scalar(
            select(DailyTransactionCount).where(DailyTransactionCount.date == today)
        )

    count = row.count if row else 1
    cap = row.cap if row else _DEFAULT_CAP
    return {"count": count, "cap": cap, "capped": count >= cap, "date": today}


# ── Player price ───────────────────────────────────────────────────────────────


@router.get("/portfolio/player-price/{ea_id}")
async def get_player_price(ea_id: int, request: Request) -> dict:
    """Return current buy_price and sell_price for a portfolio player.

    Used by the extension price guard before each buy attempt (D-13/D-31) to
    ensure the automation uses the latest backend-recommended prices.

    Args:
        ea_id: EA player ID to look up in portfolio_slots.

    Returns:
        JSON with ea_id, buy_price, sell_price.

    Raises:
        404 if the player is not in the active portfolio.
    """
    session_factory = _read_session_factory(request)
    async with session_factory() as session:
        slot = await session.scalar(
            select(PortfolioSlot).where(PortfolioSlot.ea_id == ea_id)
        )

    if slot is None:
        raise HTTPException(status_code=404, detail="Player not in portfolio")

    return {"ea_id": ea_id, "buy_price": slot.buy_price, "sell_price": slot.sell_price}
