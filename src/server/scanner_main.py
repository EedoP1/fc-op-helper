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
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    # Suppress verbose httpx/httpcore logs (same as main.py pattern)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    logger.info("Starting scanner process...")

    engine, session_factory = await create_engine_and_tables()

    cb = CircuitBreaker()
    scanner = ScannerService(session_factory=session_factory, circuit_breaker=cb)
    await scanner.start()

    scheduler = create_scheduler(scanner)
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
