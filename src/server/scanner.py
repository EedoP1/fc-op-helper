"""Scanner service: player discovery, scoring, tier management, and dispatch."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy import select, func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from src.config import (
    DATABASE_URL,
    SCAN_INTERVAL_SECONDS,
    SCAN_CONCURRENCY,
    SCAN_DISPATCH_BATCH_SIZE,
    SCANNER_MIN_PRICE,
    SCANNER_MAX_PRICE,
    INITIAL_SCORING_CONCURRENCY,
    INITIAL_SCORING_BATCH_SIZE,
    MARKET_DATA_RETENTION_DAYS,
    LISTING_RETENTION_DAYS,
    MIN_SALES_PER_HOUR,
)
from src.futgg_client import FutGGClient
from src.server.circuit_breaker import CircuitBreaker
from src.server.listing_tracker import record_listings, resolve_outcomes
from src.server.models_db import PlayerRecord, PlayerScore, MarketSnapshot, ListingObservation
from src.server.scorer_v2 import score_player_v2

logger = logging.getLogger(__name__)


def _compute_sales_per_hour(sales) -> float:
    """Compute sales/hour from completedAuctions timestamps."""
    if len(sales) < 2:
        return 0.0
    oldest = min(s.sold_at for s in sales)
    newest = max(s.sold_at for s in sales)
    span_hrs = (newest - oldest).total_seconds() / 3600
    if span_hrs <= 0:
        return 0.0
    return len(sales) / span_hrs


class ScannerService:
    """Orchestrates player discovery, scoring, tier scheduling, and dispatch.

    Scanner HTTP calls (to fut.gg) run in a ThreadPoolExecutor using a
    synchronous httpx.Client. This isolates network I/O from the main FastAPI
    event loop so 40 concurrent outbound connections don't starve API handlers.

    DB writes remain on the main event loop via session_factory. The semaphore
    + fire-and-forget dispatch pattern ensures DB writes are bounded.

    Args:
        session_factory: Async session factory for DB writes (main event loop).
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
        self._active_tasks: set[asyncio.Task] = set()
        self._sync_client: Optional[httpx.Client] = None
        self._scan_executor = None
        # Limit concurrent DB sessions from scanner so API handlers aren't starved.
        # HTTP concurrency is bounded by SCAN_CONCURRENCY (40); DB writes are
        # much tighter to keep the connection pool available for API endpoints.
        self._db_semaphore = asyncio.Semaphore(15)

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the scanner with HTTP calls offloaded to a thread pool.

        Scanner HTTP calls (to fut.gg) run in a ThreadPoolExecutor using a
        synchronous httpx.Client. This isolates scanner network I/O from the
        main FastAPI event loop so 40 concurrent outbound connections don't
        starve API request handlers.

        DB writes remain on the main event loop via session_factory. The
        fire-and-forget dispatch + semaphore ensures DB writes are bounded.

        asyncpg can't create connections from background threads on Windows
        (ProactorEventLoop limitation), so all DB access stays on the main loop.
        """
        from concurrent.futures import ThreadPoolExecutor

        self._sync_client = httpx.Client(
            base_url=self._client.BASE_URL,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json",
                "Referer": f"{self._client.BASE_URL}/players/",
            },
            timeout=30,
            follow_redirects=True,
        )
        await self._client.start()
        self._scan_executor = ThreadPoolExecutor(
            max_workers=SCAN_CONCURRENCY,
            thread_name_prefix="scanner",
        )
        self.is_running = True
        logger.info("ScannerService started (HTTP via thread pool)")

    async def stop(self) -> None:
        """Stop the scanner's HTTP client and thread pool."""
        await self._client.stop()
        if self._sync_client:
            self._sync_client.close()
        if self._scan_executor:
            self._scan_executor.shutdown(wait=False)
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
                name=p.get("commonName") or f"{p.get('firstName', '')} {p.get('lastName', '')}".strip() or str(p["ea_id"]),
                rating=p.get("overall", 0),
                position=p.get("position", "UNK"),
                nation="",
                league="",
                club="",
                card_type=p.get("rarityName", ""),
                scan_tier="",
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
                    stmt = pg_insert(PlayerRecord).values(**row)
                    stmt = stmt.on_conflict_do_update(
                        index_elements=["ea_id"],
                        set_=dict(
                            name=row["name"],
                            rating=row["rating"],
                            position=row["position"],
                            card_type=row["card_type"],
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
                player_name = p.get("commonName") or f"{p.get('firstName', '')} {p.get('lastName', '')}".strip() or str(ea_id)
                stmt = pg_insert(PlayerRecord).values(
                    ea_id=ea_id,
                    name=player_name,
                    rating=p.get("overall", 0),
                    position=p.get("position", "UNK"),
                    nation="",
                    league="",
                    club="",
                    card_type=p.get("rarityName", ""),
                    scan_tier="",
                    next_scan_at=now,
                    is_active=True,
                    listing_count=0,
                    sales_per_hour=0.0,
                )
                stmt = stmt.on_conflict_do_update(
                    index_elements=["ea_id"],
                    set_=dict(
                        name=player_name,
                        rating=p.get("overall", 0),
                        position=p.get("position", "UNK"),
                        card_type=p.get("rarityName", ""),
                        is_active=True,
                    ),
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

        def _fetch_sync():
            """Fetch market data using sync HTTP client (runs in thread pool)."""
            return self._client.get_player_market_data_sync(ea_id, self._sync_client)

        @retry(
            retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.TimeoutException)),
            stop=stop_after_attempt(3),
            wait=wait_exponential_jitter(initial=2, max=60, jitter=10),
            reraise=True,
        )
        async def _fetch_with_retry():
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(self._scan_executor, _fetch_sync)

        now = datetime.utcnow()
        try:
            market_data = await _fetch_with_retry()
            self._circuit_breaker.record_success()
            self._scan_results_1h.append((datetime.now(timezone.utc), True))
        except Exception as exc:
            self._circuit_breaker.record_failure()
            self._scan_results_1h.append((datetime.now(timezone.utc), False))
            logger.error(f"scan_player({ea_id}) failed after retries: {exc}")
            # Reschedule so the player doesn't get stuck in the dispatch queue
            async with self._db_semaphore, self._session_factory() as session:
                record = await session.get(PlayerRecord, ea_id)
                if record is not None:
                    record.next_scan_at = datetime.utcnow() + timedelta(seconds=SCAN_INTERVAL_SECONDS * 2)
                await session.commit()
            return

        async with self._db_semaphore, self._session_factory() as session:
            # --- Listing tracking (D-01, D-02) ---
            listing_result = None
            resolve_result = None
            if market_data is not None and market_data.live_auctions_raw:
                listing_result = await record_listings(
                    ea_id=ea_id,
                    live_auctions_raw=market_data.live_auctions_raw,
                    current_lowest_bin=market_data.current_lowest_bin,
                    completed_sales=market_data.sales,
                    session=session,
                )
                resolve_result = await resolve_outcomes(
                    ea_id=ea_id,
                    current_fingerprints=listing_result["fingerprints"],
                    completed_sales=market_data.sales,
                    session=session,
                )

            # --- Compute real sales/hour from completedAuctions ---
            sph = 0.0
            if market_data is not None and market_data.sales:
                sph = _compute_sales_per_hour(market_data.sales)

            # --- V2 scoring ---
            v2_result = None
            if market_data is not None and market_data.current_lowest_bin > 0:
                v2_result = await score_player_v2(
                    ea_id=ea_id,
                    session=session,
                    buy_price=market_data.current_lowest_bin,
                )

            # Write PlayerScore row built entirely from v2 result
            if v2_result is not None:
                viable = sph >= MIN_SALES_PER_HOUR
                if not viable:
                    logger.debug(
                        "scan_player(%d): sales/hr %.1f below min %d — marking not viable",
                        ea_id, sph, MIN_SALES_PER_HOUR,
                    )
                ps = PlayerScore(
                    ea_id=ea_id,
                    scored_at=now,
                    buy_price=v2_result["buy_price"],
                    sell_price=v2_result["sell_price"],
                    net_profit=v2_result["net_profit"],
                    margin_pct=v2_result["margin_pct"],
                    op_sales=v2_result["op_sold"],
                    total_sales=v2_result["op_total"],
                    op_ratio=v2_result["op_sell_rate"],
                    expected_profit=v2_result["expected_profit_per_hour"],
                    efficiency=v2_result["expected_profit_per_hour"] / v2_result["buy_price"],
                    sales_per_hour=sph,
                    is_viable=viable,
                    expected_profit_per_hour=v2_result["expected_profit_per_hour"],
                    scorer_version="v2",
                )
            else:
                ps = PlayerScore(
                    ea_id=ea_id,
                    scored_at=now,
                    buy_price=market_data.current_lowest_bin if market_data else 0,
                    sell_price=0,
                    net_profit=0,
                    margin_pct=0,
                    op_sales=0,
                    total_sales=0,
                    op_ratio=0.0,
                    expected_profit=0.0,
                    efficiency=0.0,
                    sales_per_hour=sph,
                    is_viable=False,
                    expected_profit_per_hour=None,
                )
            session.add(ps)

            # Persist raw market snapshot
            if market_data is not None:
                snapshot = MarketSnapshot(
                    ea_id=ea_id,
                    captured_at=now,
                    current_lowest_bin=market_data.current_lowest_bin,
                    listing_count=market_data.listing_count,
                    live_auction_prices=json.dumps(market_data.live_auction_prices),
                )
                session.add(snapshot)

            # Update PlayerRecord fields
            record = await session.get(PlayerRecord, ea_id)
            if record is not None:
                record.last_scanned_at = now
                if market_data is not None:
                    record.listing_count = market_data.listing_count
                    # Populate player name from API data
                    if market_data.player and market_data.player.name:
                        record.name = market_data.player.name
                    if market_data.futgg_url:
                        record.futgg_url = market_data.futgg_url

            # Schedule next scan at fixed 5-minute interval
            if record is not None:
                record.next_scan_at = datetime.utcnow() + timedelta(seconds=SCAN_INTERVAL_SECONDS)

            await session.commit()

        self.last_scan_at = datetime.utcnow()

    # ── Dispatch ──────────────────────────────────────────────────────────────

    async def dispatch_scans(self) -> None:
        """Query due players and launch concurrent scan tasks (fire-and-forget).

        Called every SCAN_DISPATCH_INTERVAL seconds by the APScheduler job.
        Returns immediately after creating tasks — does NOT await them.

        HTTP calls are offloaded to the thread pool (via scan_player →
        run_in_executor), so they don't compete with API handlers on the
        main event loop. DB writes stay on the main loop but are bounded
        by the semaphore (max SCAN_CONCURRENCY concurrent scan tasks).
        """
        # Prune completed tasks from the previous cycle(s)
        self._active_tasks = {t for t in self._active_tasks if not t.done()}

        now = datetime.utcnow()
        async with self._session_factory() as session:
            stmt = (
                select(PlayerRecord)
                .where(
                    PlayerRecord.is_active == True,  # noqa: E712
                    PlayerRecord.next_scan_at <= now,
                )
                .order_by(PlayerRecord.next_scan_at.asc())
                .limit(SCAN_DISPATCH_BATCH_SIZE)
            )
            result = await session.execute(stmt)
            due_players = result.scalars().all()

        logger.warning(f"dispatch_scans: found {len(due_players)} due players")
        if not due_players:
            return

        self._queue_depth_cache = len(due_players)
        semaphore = asyncio.Semaphore(SCAN_CONCURRENCY)

        async def _scan_with_sem(ea_id: int) -> None:
            async with semaphore:
                await self.scan_player(ea_id)

        for p in due_players:
            task = asyncio.get_running_loop().create_task(_scan_with_sem(p.ea_id))
            self._active_tasks.add(task)

    # ── Scheduled aggregation and cleanup jobs ──────────────────────────────

    async def run_aggregation(self) -> None:
        """Aggregate yesterday's listing observations into daily summaries (D-13).

        Runs daily. Summarises all resolved ListingObservation rows for the
        previous day into DailyListingSummary rows per margin tier.
        """
        from src.server.listing_tracker import aggregate_daily_summaries
        yesterday = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")

        async with self._session_factory() as session:
            result = await session.execute(
                select(PlayerRecord.ea_id).where(PlayerRecord.is_active == True)  # noqa: E712
            )
            ea_ids = [row[0] for row in result.all()]

        total = 0
        for ea_id in ea_ids:
            try:
                async with self._session_factory() as session:
                    count = await aggregate_daily_summaries(ea_id, yesterday, session)
                    await session.commit()
                    total += count
            except Exception as exc:
                logger.error(f"Aggregation failed for {ea_id}: {exc}")
        logger.info(f"Daily aggregation: {total} summary rows for {yesterday}")

    # ── Cleanup ─────────────────────────────────────────────────────────────

    async def run_cleanup(self) -> None:
        """Delete market snapshots older than MARKET_DATA_RETENTION_DAYS.

        FK cascade on SnapshotSale and SnapshotPricePoint ensures child
        rows are deleted automatically. Also prunes old PlayerScore rows
        beyond retention to keep the DB lean. Additionally purges old
        resolved and orphaned ListingObservation rows (D-12).
        """
        cutoff = datetime.utcnow() - timedelta(days=MARKET_DATA_RETENTION_DAYS)
        listing_cutoff = datetime.utcnow() - timedelta(days=LISTING_RETENTION_DAYS)
        async with self._session_factory() as session:
            from sqlalchemy import delete

            # Delete old snapshots (cascades to sales + price points)
            result = await session.execute(
                delete(MarketSnapshot).where(MarketSnapshot.captured_at < cutoff)
            )
            snapshot_count = result.rowcount

            # Also prune old PlayerScore rows
            result = await session.execute(
                delete(PlayerScore).where(PlayerScore.scored_at < cutoff)
            )
            score_count = result.rowcount

            # Purge old resolved listing observations (D-12)
            result = await session.execute(
                delete(ListingObservation).where(
                    ListingObservation.resolved_at.isnot(None),
                    ListingObservation.resolved_at < listing_cutoff,
                )
            )
            resolved_purged = result.rowcount

            # Purge orphaned unresolved observations (last_seen_at too old)
            result = await session.execute(
                delete(ListingObservation).where(
                    ListingObservation.outcome.is_(None),
                    ListingObservation.last_seen_at < listing_cutoff,
                )
            )
            orphaned_purged = result.rowcount

            await session.commit()
        logger.info(
            f"Cleanup: deleted {snapshot_count} snapshots, {score_count} scores "
            f"older than {MARKET_DATA_RETENTION_DAYS} days; "
            f"purged {resolved_purged} resolved and {orphaned_purged} orphaned "
            f"listing observations older than {LISTING_RETENTION_DAYS} days"
        )

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
