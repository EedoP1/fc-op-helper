"""Health check endpoint — reads scanner metrics from DB (D-01)."""
from fastapi import APIRouter, Request
from sqlalchemy import select, func

from src.server.models_db import ScannerStatus, PlayerRecord

router = APIRouter(prefix="/api/v1")


@router.get("/health")
async def health(request: Request):
    """Return operational health metrics.

    Scanner metrics come from the scanner_status DB table (written by
    the scanner process every dispatch cycle). Players count is a direct
    DB query. If scanner hasn't written yet (startup race), returns
    degraded 'unknown' state per Pitfall 2.

    Returns:
        Dict with scanner_status, circuit_breaker, scan_success_rate_1h,
        last_scan_at, players_in_db, and queue_depth.
    """
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        # Scanner status from DB (written by scanner process)
        result = await session.execute(
            select(ScannerStatus).where(ScannerStatus.id == 1)
        )
        status = result.scalar_one_or_none()

        # players_in_db is a direct DB query (unchanged)
        count_result = await session.execute(
            select(func.count()).select_from(PlayerRecord).where(
                PlayerRecord.is_active == True  # noqa: E712
            )
        )
        players_in_db = count_result.scalar() or 0

    if status is None:
        # Scanner hasn't written yet (startup race) — degraded state
        return {
            "scanner_status": "unknown",
            "circuit_breaker": "unknown",
            "scan_success_rate_1h": None,
            "last_scan_at": None,
            "players_in_db": players_in_db,
            "queue_depth": 0,
        }

    return {
        "scanner_status": "running" if status.is_running else "stopped",
        "circuit_breaker": status.circuit_breaker_state,
        "scan_success_rate_1h": round(status.success_rate_1h, 3),
        "last_scan_at": (
            status.last_scan_at.isoformat() if status.last_scan_at else None
        ),
        "players_in_db": players_in_db,
        "queue_depth": status.queue_depth,
    }
