"""APScheduler configuration for the persistent scanner."""
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from src.config import SCAN_DISPATCH_INTERVAL

logger = logging.getLogger(__name__)


def create_scheduler(scanner) -> AsyncIOScheduler:
    """Create and configure the APScheduler with scan dispatch and discovery jobs.

    Args:
        scanner: ScannerService instance whose methods will be scheduled.

    Returns:
        Configured AsyncIOScheduler (not yet started).
    """
    scheduler = AsyncIOScheduler(timezone="UTC")

    # Dispatch loop: checks priority queue every SCAN_DISPATCH_INTERVAL seconds
    scheduler.add_job(
        scanner.dispatch_scans,
        trigger=IntervalTrigger(seconds=SCAN_DISPATCH_INTERVAL),
        id="scan_dispatch",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
        name="Scanner dispatch",
    )

    # Hourly rediscovery to catch new players entering the price range
    scheduler.add_job(
        scanner.run_discovery,
        trigger=IntervalTrigger(hours=1),
        id="discovery",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
        name="Player discovery",
    )

    # Daily cleanup of old market data beyond retention period
    scheduler.add_job(
        scanner.run_cleanup,
        trigger=IntervalTrigger(hours=24),
        id="cleanup",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
        name="Data cleanup",
    )

    return scheduler
