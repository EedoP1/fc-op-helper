"""Scanner service: player discovery, scoring, tier management, and dispatch."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy import select, func
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from src.config import (
    SCAN_INTERVAL_HOT,
    SCAN_INTERVAL_NORMAL,
    SCAN_INTERVAL_COLD,
    SCAN_CONCURRENCY,
    SCANNER_MIN_PRICE,
    SCANNER_MAX_PRICE,
    TIER_PROFIT_THRESHOLD,
    INITIAL_SCORING_CONCURRENCY,
    INITIAL_SCORING_BATCH_SIZE,
)
from src.futgg_client import FutGGClient
from src.scorer import score_player
from src.server.circuit_breaker import CircuitBreaker
from src.server.models_db import PlayerRecord, PlayerScore

logger = logging.getLogger(__name__)


class ScannerService:
    """Orchestrates player discovery, scoring, tier scheduling, and dispatch.

    Args:
        session_factory: Async session factory for DB writes.
        circuit_breaker: Shared circuit breaker for fut.gg API resilience.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker,
        circuit_breaker: CircuitBreaker,
    ):
        self._session_factory = session_factory
        self._circuit_breaker = circuit_breaker
        self._client = FutGGClient()
        self.is_running = False
        self.last_scan_at: datetime | None = None
        self._scan_results_1h: list[tuple[datetime, bool]] = []
        self._queue_depth_cache: int = 0

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the FutGG HTTP client."""
        await self._client.start()
        self.is_running = True
        logger.info("ScannerService started")

    async def stop(self) -> None:
        """Stop the FutGG HTTP client."""
        await self._client.stop()
        self.is_running = False
        logger.info("ScannerService stopped")

    # ── Discovery ─────────────────────────────────────────────────────────────

    async def run_bootstrap(self) -> None:
        """Discover all players in the 11k-200k price range and seed the DB.

        Upserts PlayerRecord rows with scan_tier='normal' and next_scan_at=now
        so the dispatch loop picks them up immediately.

        Uses batched DB writes (chunks of 200) to reduce round-trips vs the
        previous one-insert-per-player approach.
        """
        t_discovery = time.monotonic()
        players = await self._client.discover_players(
            budget=SCANNER_MAX_PRICE,
            min_price=SCANNER_MIN_PRICE,
            max_price=SCANNER_MAX_PRICE,
        )
        discovery_elapsed = time.monotonic() - t_discovery
        logger.info(
            f"Bootstrap discovered {len(players)} players in {discovery_elapsed:.1f}s"
        )
        now = datetime.utcnow()

        # Build values list for bulk upsert
        values_list = [
            dict(
                ea_id=p["ea_id"],
                name=str(p["ea_id"]),
                rating=0,
                position="UNK",
                nation="",
                league="",
                club="",
                card_type="",
                scan_tier="normal",
                next_scan_at=now,
                is_active=True,
                listing_count=0,
                sales_per_hour=0.0,
            )
            for p in players
        ]

        t_db = time.monotonic()
        chunk_size = 200
        async with self._session_factory() as session:
            for i in range(0, len(values_list), chunk_size):
                chunk = values_list[i : i + chunk_size]
                for row in chunk:
                    stmt = sqlite_insert(PlayerRecord).values(**row)
                    stmt = stmt.on_conflict_do_update(
                        index_elements=["ea_id"],
                        set_=dict(
                            is_active=True,
                            next_scan_at=now,
                        ),
                    )
                    await session.execute(stmt)
                await session.commit()
        db_elapsed = time.monotonic() - t_db
        logger.info(
            f"Bootstrap upserted {len(players)} PlayerRecord rows in {db_elapsed:.1f}s"
        )

    async def run_initial_scoring(self) -> None:
        """Score all unscored active players with elevated concurrency.

        Called once after bootstrap. Uses INITIAL_SCORING_CONCURRENCY (10)
        instead of normal SCAN_CONCURRENCY (5) to complete faster.
        Processes players in batches of INITIAL_SCORING_BATCH_SIZE to avoid
        overwhelming the event loop.
        """
        start = time.monotonic()

        async with self._session_factory() as session:
            stmt = (
                select(PlayerRecord.ea_id)
                .where(
                    PlayerRecord.is_active == True,  # noqa: E712
                    PlayerRecord.last_scanned_at == None,  # noqa: E711
                )
                .order_by(PlayerRecord.ea_id)
            )
            result = await session.execute(stmt)
            unscored_ids = [row[0] for row in result.all()]

        total = len(unscored_ids)
        logger.info(f"Initial scoring: {total} unscored players")

        semaphore = asyncio.Semaphore(INITIAL_SCORING_CONCURRENCY)
        scored = 0

        async def _scan_with_sem(ea_id: int) -> None:
            nonlocal scored
            async with semaphore:
                await self.scan_player(ea_id)
                scored += 1
                if scored % 100 == 0:
                    logger.info(f"Initial scoring progress: {scored}/{total}")

        # Process in batches to avoid creating thousands of tasks at once
        for i in range(0, total, INITIAL_SCORING_BATCH_SIZE):
            batch = unscored_ids[i : i + INITIAL_SCORING_BATCH_SIZE]
            tasks = [asyncio.create_task(_scan_with_sem(eid)) for eid in batch]
            await asyncio.gather(*tasks, return_exceptions=True)

        elapsed = time.monotonic() - start
        logger.info(
            f"Initial scoring complete: {scored}/{total} players in {elapsed:.1f}s"
        )

    async def run_bootstrap_and_score(self) -> None:
        """Run bootstrap discovery then immediately score all discovered players.

        Single method for startup chaining — called as one-shot job.
        """
        await self.run_bootstrap()
        await self.run_initial_scoring()

    async def run_discovery(self) -> None:
        """Periodic rediscovery: upsert new players and deactivate removed ones.

        Runs hourly to catch new players entering the 11k-200k range.
        Players no longer in the discovery result are marked cold (per Research pitfall 6).
        """
        players = await self._client.discover_players(
            budget=SCANNER_MAX_PRICE,
            min_price=SCANNER_MIN_PRICE,
            max_price=SCANNER_MAX_PRICE,
        )
        logger.info(f"Discovery found {len(players)} players")
        discovered_ids = {p["ea_id"] for p in players}
        now = datetime.utcnow()
        far_future = now + timedelta(hours=24)

        async with self._session_factory() as session:
            # Upsert all discovered players
            for p in players:
                ea_id = p["ea_id"]
                stmt = sqlite_insert(PlayerRecord).values(
                    ea_id=ea_id,
                    name=str(ea_id),
                    rating=0,
                    position="UNK",
                    nation="",
                    league="",
                    club="",
                    card_type="",
                    scan_tier="normal",
                    next_scan_at=now,
                    is_active=True,
                    listing_count=0,
                    sales_per_hour=0.0,
                )
                stmt = stmt.on_conflict_do_update(
                    index_elements=["ea_id"],
                    set_=dict(is_active=True),
                )
                await session.execute(stmt)

            # Mark players NOT in discovery as cold
            if discovered_ids:
                result = await session.execute(
                    select(PlayerRecord).where(
                        PlayerRecord.is_active == True  # noqa: E712
                    )
                )
                all_active = result.scalars().all()
                for record in all_active:
                    if record.ea_id not in discovered_ids:
                        record.scan_tier = "cold"
                        record.next_scan_at = far_future

            await session.commit()
        logger.info(f"Discovery complete: {len(discovered_ids)} active players")

    # ── Per-player scan ───────────────────────────────────────────────────────

    async def scan_player(self, ea_id: int) -> None:
        """Fetch market data, score player, persist result, update tier.

        Checks circuit breaker before calling the API. Retries up to 3 times
        with exponential backoff on HTTP or timeout errors.

        Args:
            ea_id: The EA resource ID of the player to scan.
        """
        if self._circuit_breaker.is_open:
            logger.debug(f"Circuit breaker OPEN — skipping scan for {ea_id}")
            return

        market_data = None

        @retry(
            retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.TimeoutException)),
            stop=stop_after_attempt(3),
            wait=wait_exponential_jitter(initial=2, max=60, jitter=10),
            reraise=True,
        )
        async def _fetch_with_retry():
            return await self._client.get_player_market_data(ea_id)

        now = datetime.utcnow()
        try:
            market_data = await _fetch_with_retry()
            self._circuit_breaker.record_success()
            self._scan_results_1h.append((datetime.now(timezone.utc), True))
        except Exception as exc:
            self._circuit_breaker.record_failure()
            self._scan_results_1h.append((datetime.now(timezone.utc), False))
            logger.error(f"scan_player({ea_id}) failed after retries: {exc}")
            return

        score_result = score_player(market_data) if market_data is not None else None

        async with self._session_factory() as session:
            # Write PlayerScore row
            if score_result is not None:
                ps = PlayerScore(
                    ea_id=ea_id,
                    scored_at=now,
                    buy_price=score_result["buy_price"],
                    sell_price=score_result["sell_price"],
                    net_profit=score_result["net_profit"],
                    margin_pct=score_result["margin_pct"],
                    op_sales=score_result["op_sales"],
                    total_sales=score_result["total_sales"],
                    op_ratio=score_result["op_ratio"],
                    expected_profit=score_result["expected_profit"],
                    efficiency=score_result["expected_profit"] / score_result["buy_price"],
                    sales_per_hour=score_result["sales_per_hour"],
                    is_viable=True,
                )
            else:
                ps = PlayerScore(
                    ea_id=ea_id,
                    scored_at=now,
                    buy_price=0,
                    sell_price=0,
                    net_profit=0,
                    margin_pct=0,
                    op_sales=0,
                    total_sales=0,
                    op_ratio=0.0,
                    expected_profit=0.0,
                    efficiency=0.0,
                    sales_per_hour=0.0,
                    is_viable=False,
                )
            session.add(ps)

            # Update PlayerRecord fields
            record = await session.get(PlayerRecord, ea_id)
            if record is not None:
                record.last_scanned_at = now
                if market_data is not None:
                    record.listing_count = market_data.listing_count
                    # Compute sales_per_hour from score result if available
                    if score_result is not None:
                        record.sales_per_hour = score_result["sales_per_hour"]

            await session.flush()

            # Classify tier and schedule next scan
            listing_count = record.listing_count if record is not None else 0
            sales_per_hour = record.sales_per_hour if record is not None else 0.0
            last_expected_profit = score_result["expected_profit"] if score_result is not None else 0.0

            await self._classify_and_schedule(
                ea_id, listing_count, sales_per_hour, last_expected_profit, session
            )

        self.last_scan_at = datetime.utcnow()

    # ── Tier classification ───────────────────────────────────────────────────

    def _classify_tier(
        self,
        listing_count: int,
        sales_per_hour: float,
        last_expected_profit: float = 0.0,
    ) -> str:
        """Classify a player into hot/normal/cold scan tier.

        Per D-05, D-06, and API-04:
        - High-value players (profit >= threshold) are always hot regardless of activity.
        - High-activity players (many listings or fast sales) are hot.
        - Moderate-activity players are normal.
        - Low-activity players are cold.

        Args:
            listing_count: Current number of live listings.
            sales_per_hour: Sales velocity over recent history.
            last_expected_profit: Most recent expected_profit from scorer.

        Returns:
            One of "hot", "normal", or "cold".
        """
        if last_expected_profit >= TIER_PROFIT_THRESHOLD:
            return "hot"
        if listing_count >= 50 or sales_per_hour >= 15:
            return "hot"
        if listing_count >= 20 or sales_per_hour >= 7:
            return "normal"
        return "cold"

    async def _classify_and_schedule(
        self,
        ea_id: int,
        listing_count: int,
        sales_per_hour: float,
        last_expected_profit: float,
        session,
    ) -> None:
        """Update PlayerRecord with new tier and next_scan_at.

        Args:
            ea_id: Player EA ID.
            listing_count: Current live listing count.
            sales_per_hour: Sales velocity.
            last_expected_profit: Most recent expected profit for value-based promotion.
            session: Active AsyncSession (will be committed here).
        """
        tier = self._classify_tier(listing_count, sales_per_hour, last_expected_profit)
        interval_map = {
            "hot": SCAN_INTERVAL_HOT,
            "normal": SCAN_INTERVAL_NORMAL,
            "cold": SCAN_INTERVAL_COLD,
        }
        interval = interval_map[tier]
        next_scan = datetime.utcnow() + timedelta(seconds=interval)

        record = await session.get(PlayerRecord, ea_id)
        if record is not None:
            record.scan_tier = tier
            record.next_scan_at = next_scan

        await session.commit()

    # ── Dispatch ──────────────────────────────────────────────────────────────

    async def dispatch_scans(self) -> None:
        """Query due players and launch concurrent scan tasks.

        Called every SCAN_DISPATCH_INTERVAL seconds by the APScheduler job.
        Limits concurrency with an asyncio.Semaphore.
        """
        now = datetime.utcnow()
        async with self._session_factory() as session:
            stmt = (
                select(PlayerRecord)
                .where(
                    PlayerRecord.is_active == True,  # noqa: E712
                    PlayerRecord.next_scan_at <= now,
                )
                .order_by(PlayerRecord.next_scan_at.asc())
                .limit(SCAN_CONCURRENCY * 2)
            )
            result = await session.execute(stmt)
            due_players = result.scalars().all()

        if not due_players:
            return

        self._queue_depth_cache = len(due_players)
        semaphore = asyncio.Semaphore(SCAN_CONCURRENCY)

        async def _scan_with_sem(ea_id: int) -> None:
            async with semaphore:
                await self.scan_player(ea_id)

        tasks = [
            asyncio.create_task(_scan_with_sem(p.ea_id))
            for p in due_players
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

    # ── Metrics ───────────────────────────────────────────────────────────────

    def success_rate_1h(self) -> float:
        """Return fraction of scans that succeeded in the last hour.

        Returns:
            Float between 0.0 and 1.0. Returns 1.0 when no results are recorded.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
        self._scan_results_1h = [
            (ts, ok) for ts, ok in self._scan_results_1h if ts >= cutoff
        ]
        if not self._scan_results_1h:
            return 1.0
        successes = sum(1 for _, ok in self._scan_results_1h if ok)
        return successes / len(self._scan_results_1h)

    async def count_players(self) -> int:
        """Return count of active players in the DB.

        Returns:
            Integer count of PlayerRecord rows with is_active=True.
        """
        async with self._session_factory() as session:
            result = await session.execute(
                select(func.count()).where(PlayerRecord.is_active == True)  # noqa: E712
            )
            return result.scalar() or 0

    def queue_depth(self) -> int:
        """Return cached queue depth from the last dispatch cycle.

        Returns:
            Count of players that were due at last dispatch call.
        """
        return self._queue_depth_cache
