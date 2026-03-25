"""Health check endpoint."""
from fastapi import APIRouter, Request

router = APIRouter(prefix="/api/v1")


@router.get("/health")
async def health(request: Request):
    """Return operational health metrics for the scanner.

    Returns:
        Dict with scanner_status, circuit_breaker, scan_success_rate_1h,
        last_scan_at, players_in_db, and queue_depth (per D-09, D-10).
    """
    scanner = request.app.state.scanner
    cb = request.app.state.circuit_breaker
    return {
        "scanner_status": "running" if scanner.is_running else "stopped",  # D-10
        "circuit_breaker": cb.state.value,                                  # D-10
        "scan_success_rate_1h": round(scanner.success_rate_1h(), 3),        # D-10
        "last_scan_at": (
            scanner.last_scan_at.isoformat() if scanner.last_scan_at else None
        ),                                                                   # D-10
        "players_in_db": await scanner.count_players(),                     # D-10
        "queue_depth": scanner.queue_depth(),                                # D-10
    }
