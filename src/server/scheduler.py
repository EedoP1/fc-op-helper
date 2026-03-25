"""APScheduler configuration for the persistent scanner."""
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from src.config import SCAN_DISPATCH_INTERVAL, SCORING_JOB_INTERVAL_MINUTES

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

    # V2 scoring job: re-score players from accumulated listing data (D-10)
    scheduler.add_job(
        scanner.run_scoring,
        trigger=IntervalTrigger(minutes=SCORING_JOB_INTERVAL_MINUTES),
        id="scoring_v2",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
        name="V2 listing scorer",
    )

    # Daily aggregation: summarize yesterday's listing observations (D-13)
    scheduler.add_job(
        scanner.run_aggregation,
        trigger=IntervalTrigger(hours=24),
        id="aggregation",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
        name="Daily listing aggregation",
    )

    return scheduler
