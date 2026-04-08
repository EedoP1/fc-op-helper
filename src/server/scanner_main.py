"""Scanner process entry point (D-05).

Runs the scanner as a standalone process: creates DB engine, ScannerService,
CircuitBreaker, APScheduler, and blocks indefinitely. Docker handles restart
on failure via restart policies (D-04).

Usage:
    python -m src.server.scanner_main
"""
import asyncio
import logging

from src.server.db import create_engine_and_tables
from src.server.scanner import ScannerService
from src.server.scheduler import create_scheduler
from src.server.circuit_breaker import CircuitBreaker

logger = logging.getLogger(__name__)


async def main():
    """Start scanner with all dependencies and block forever."""
    import subprocess
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    # Suppress verbose httpx/httpcore logs (same as main.py pattern)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    # Ensure Playwright Chromium binary is present (idempotent on subsequent runs)
    logger.info("Ensuring Playwright Chromium is installed...")
    subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        check=True,
    )
    logger.info("Playwright Chromium ready.")

    logger.info("Starting scanner process...")

    engine, session_factory = await create_engine_and_tables()

    # Migrate: add last_resolved_at column to players if missing
    async with engine.begin() as conn:
        from sqlalchemy import text, inspect as sa_inspect
        def _check_resolved_col(connection):
            insp = sa_inspect(connection)
            cols = [c["name"] for c in insp.get_columns("players")]
            return "last_resolved_at" in cols
        has_resolved = await conn.run_sync(_check_resolved_col)
        if not has_resolved:
            await conn.execute(text(
                "ALTER TABLE players ADD COLUMN last_resolved_at TIMESTAMP"
            ))
            logger.info("Migrated players: added last_resolved_at column")

        # Migrate: add listings_per_hour column for v3 scorer
        def _check_lph_col(connection):
            insp = sa_inspect(connection)
            cols = [c["name"] for c in insp.get_columns("players")]
            return "listings_per_hour" in cols
        has_lph = await conn.run_sync(_check_lph_col)
        if not has_lph:
            await conn.execute(text(
                "ALTER TABLE players ADD COLUMN listings_per_hour FLOAT DEFAULT 0.0"
            ))
            logger.info("Migrated players: added listings_per_hour column")

    from src.server.algo_runner import run_signal_engine
    async def _run_algo():
        await run_signal_engine(session_factory)

    cb = CircuitBreaker()
    scanner = ScannerService(session_factory=session_factory, circuit_breaker=cb)
    await scanner.start()

    scheduler = create_scheduler(scanner, algo_runner=_run_algo)
    scheduler.start()

    # Queue bootstrap + initial scoring as one-shot job (same as old main.py)
    scheduler.add_job(
        scanner.run_bootstrap_and_score,
        id="bootstrap",
        replace_existing=True,
    )
    logger.info("Scanner process started. Bootstrap + initial scoring queued.")

    try:
        # Block indefinitely — Docker SIGTERM triggers finally block
        await asyncio.Event().wait()
    finally:
        logger.info("Scanner process shutting down...")
        scheduler.shutdown(wait=False)
        await scanner.stop()
        await engine.dispose()
        logger.info("Scanner process stopped.")


if __name__ == "__main__":
    asyncio.run(main())
