"""Scanner service: player discovery, scoring, tier management, and dispatch."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

from curl_cffi.requests import Session as CffiSession
from curl_cffi.requests.exceptions import HTTPError
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy import select, func, update
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
    MIN_SALES_PER_HOUR,
)
from src.futgg_client import FutGGClient, PricesFetchError
from src.server.circuit_breaker import CircuitBreaker
from src.server.playwright_client import PlaywrightPricesClient
from src.server.listing_tracker import record_listings, resolve_outcomes
from src.server.models_db import PlayerRecord, PlayerScore, MarketSnapshot, ScannerStatus
from src.server.scorer_v2 import score_player_v2
from src.server.scorer_v3 import score_player_v3

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


def _compute_listings_per_hour(live_auctions_raw: list[dict]) -> float:
    """Compute listings/hour from liveAuctions expiry timestamps.

    Each listing has an expiresOn field. Since EA listings last 1 hour,
    listed_at = expiresOn - 1 hour. We compute the time span of all
    listings and normalize to per-hour, same approach as sales_per_hour.
    """
    if len(live_auctions_raw) < 2:
        return 0.0

    listed_times = []
    for entry in live_auctions_raw:
        expires = entry.get("endDate") or entry.get("expiresOn") or entry.get("expires")
        if not expires:
            continue
        try:
            if isinstance(expires, (int, float)):
                expiry_dt = datetime.fromtimestamp(expires, tz=timezone.utc)
            else:
                expiry_dt = datetime.fromisoformat(str(expires).replace("Z", "+00:00"))
            listed_at = expiry_dt - timedelta(hours=1)
            listed_times.append(listed_at)
        except (ValueError, TypeError, OSError):
            continue

    if len(listed_times) < 2:
        return 0.0

    oldest = min(listed_times)
    newest = max(listed_times)
    span_hrs = (newest - oldest).total_seconds() / 3600
    if span_hrs <= 0:
        return 0.0
    return len(listed_times) / span_hrs


class ScannerService:
    """Orchestrates player discovery, scoring, tier scheduling, and dispatch.

    Scanner HTTP calls (to fut.gg) run in a ThreadPoolExecutor using a
    synchronous curl_cffi Session. This isolates network I/O from the main FastAPI
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
        self._sync_client: Optional[CffiSession] = None
        self._scan_executor = None
        self._pw_client: Optional[PlaywrightPricesClient] = None
        # Limit concurrent DB sessions from scanner so API handlers aren't starved.
        # HTTP concurrency is bounded by SCAN_CONCURRENCY (40); DB writes are
        # much tighter to keep the connection pool available for API endpoints.
        self._db_semaphore = asyncio.Semaphore(20)
        # Track ea_ids currently being scanned to prevent duplicate work.
        self._in_flight: set[int] = set()

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the scanner with HTTP calls offloaded to a thread pool.

        Scanner HTTP calls (to fut.gg) run in a ThreadPoolExecutor using a
        synchronous curl_cffi Session. This isolates scanner network I/O from the
        main FastAPI event loop so 40 concurrent outbound connections don't
        starve API request handlers.

        DB writes remain on the main event loop via session_factory. The
        fire-and-forget dispatch + semaphore ensures DB writes are bounded.

        asyncpg can't create connections from background threads on Windows
        (ProactorEventLoop limitation), so all DB access stays on the main loop.
        """
        from concurrent.futures import ThreadPoolExecutor

        self._sync_client = CffiSession(
            impersonate="chrome",
            headers=self._client.DEFAULT_HEADERS,
            timeout=30,
            allow_redirects=True,
        )
        await self._client.start()
        self._scan_executor = ThreadPoolExecutor(
            max_workers=SCAN_CONCURRENCY,
            thread_name_prefix="scanner",
        )
        # Start Playwright browser for player-prices endpoint (Cloudflare bypass)
        self._pw_client = PlaywrightPricesClient()
        await self._pw_client.start()
        self._pw_client.set_loop(asyncio.get_running_loop())
        self.is_running = True
        logger.info("ScannerService started (HTTP via thread pool, prices via Playwright)")

    async def stop(self) -> None:
        """Stop the scanner's HTTP client, Playwright browser, and thread pool."""
        if self._pw_client:
            await self._pw_client.stop()
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

        Delegates to scanner_discovery.run_bootstrap().
        """
        from src.server.scanner_discovery import run_bootstrap
        await run_bootstrap(self._session_factory, self._client)

    async def run_initial_scoring(self) -> None:
        """Score all unscored active players with elevated concurrency.

        Delegates to scanner_discovery.run_initial_scoring().
        """
        from src.server.scanner_discovery import run_initial_scoring
        await run_initial_scoring(self._session_factory, self.scan_player)

    async def run_bootstrap_and_score(self) -> None:
        """Run bootstrap discovery then immediately score all discovered players.

        Single method for startup chaining — called as one-shot job.
        """
        await self.run_bootstrap()
        await self.run_initial_scoring()

    async def run_discovery(self) -> None:
        """Periodic rediscovery: upsert new players and deactivate removed ones.

        Delegates to scanner_discovery.run_discovery().
        """
        from src.server.scanner_discovery import run_discovery
        await run_discovery(self._session_factory, self._client)

    # ── Per-player scan ───────────────────────────────────────────────────────

    async def scan_player(self, ea_id: int) -> None:
        """Fetch market data, score player, persist result, update tier.

        Checks circuit breaker before calling the API. Retries up to 3 times
        with exponential backoff on HTTP or timeout errors.

        Args:
            ea_id: The EA resource ID of the player to scan.
        """
        if ea_id in self._in_flight:
            return
        self._in_flight.add(ea_id)

        try:
            await self._scan_player_inner(ea_id)
        finally:
            self._in_flight.discard(ea_id)

    async def _scan_player_inner(self, ea_id: int) -> None:
        if self._circuit_breaker.is_open:
            logger.debug(f"Circuit breaker OPEN — skipping scan for {ea_id}")
            return

        market_data = None

        def _fetch_sync():
            """Fetch market data using sync clients (runs in thread pool).

            Definitions fetched via curl_cffi; prices via Playwright browser
            (bypasses Cloudflare managed JS challenge on the prices endpoint).
            """
            return self._client.get_player_market_data_sync(
                ea_id, self._sync_client,
                prices_fetcher=self._pw_client.get_prices_sync if self._pw_client else None,
            )

        @retry(
            retry=retry_if_exception_type(
                (HTTPError, ConnectionError, TimeoutError, PricesFetchError)
            ),
            stop=stop_after_attempt(3),
            wait=wait_exponential_jitter(initial=2, max=60, jitter=10),
            reraise=True,
        )
        async def _fetch_with_retry():
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(self._scan_executor, _fetch_sync)

        t_start = time.monotonic()
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

        t_http = time.monotonic()

        t_sem_wait_start = time.monotonic()
        async with self._db_semaphore, self._session_factory() as session:
            t_sem_acquired = time.monotonic()

            # Load PlayerRecord early for last_resolved_at
            record = await session.get(PlayerRecord, ea_id)

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
                    last_resolved_at=record.last_resolved_at if record else None,
                )
                # Store resolution timestamp for next scan
                if resolve_result and resolve_result.get("resolved_at") and record:
                    record.last_resolved_at = resolve_result["resolved_at"]

            t_listing = time.monotonic()

            # --- Compute real sales/hour from completedAuctions ---
            sph = 0.0
            if market_data is not None and market_data.sales:
                sph = _compute_sales_per_hour(market_data.sales)

            # --- Compute listings/hour from liveAuctions ---
            lph = 0.0
            if market_data is not None and market_data.live_auctions_raw:
                lph = _compute_listings_per_hour(market_data.live_auctions_raw)

            # --- V3 scoring (weighted supply/demand) ---
            v3_result = None
            if market_data is not None and market_data.current_lowest_bin > 0:
                v3_result = await score_player_v3(
                    ea_id=ea_id,
                    buy_price=market_data.current_lowest_bin,
                    sales_per_hour=sph,
                    visible_lph=lph,
                    session=session,
                    max_price_range=market_data.max_price_range,
                )

            t_score = time.monotonic()

            # Write PlayerScore row from v3 result
            if v3_result is not None:
                viable = sph >= MIN_SALES_PER_HOUR
                if not viable:
                    logger.debug(
                        "scan_player(%d): sales/hr %.1f below min %d — marking not viable",
                        ea_id, sph, MIN_SALES_PER_HOUR,
                    )
                ps = PlayerScore(
                    ea_id=ea_id,
                    scored_at=now,
                    buy_price=v3_result["buy_price"],
                    sell_price=v3_result["sell_price"],
                    net_profit=v3_result["net_profit"],
                    margin_pct=v3_result["margin_pct"],
                    op_sales=v3_result["op_sold_count"],
                    total_sales=v3_result["op_total_count"],
                    op_ratio=v3_result["op_sell_rate"],
                    expected_profit=v3_result["weighted_score"],
                    efficiency=v3_result["weighted_score"] / v3_result["buy_price"],
                    sales_per_hour=sph,
                    is_viable=viable,
                    expected_profit_per_hour=v3_result["weighted_score"],
                    scorer_version="v3",
                    max_sell_price=market_data.max_price_range,
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
                    )
                session.add(snapshot)

            # Update PlayerRecord fields (record already loaded above)
            if record is not None:
                record.last_scanned_at = now
                if market_data is not None:
                    record.listing_count = market_data.listing_count
                    record.listings_per_hour = lph
                    # Populate player name from API data
                    if market_data.player and market_data.player.name:
                        record.name = market_data.player.name
                    if market_data.futgg_url:
                        record.futgg_url = market_data.futgg_url
                    if market_data.created_at is not None and record.created_at is None:
                        record.created_at = market_data.created_at

            # Schedule next scan at fixed 5-minute interval
            if record is not None:
                record.next_scan_at = datetime.utcnow() + timedelta(seconds=SCAN_INTERVAL_SECONDS)

            t_pre_commit = time.monotonic()
            await session.commit()

        t_end = time.monotonic()
        logger.warning(
            "SCAN_TIMING ea_id=%d total=%.1fs http=%.1fs sem_wait=%.1fs listing=%.1fs score=%.1fs write+commit=%.1fs",
            ea_id,
            t_end - t_start,
            t_http - t_start,
            t_sem_acquired - t_sem_wait_start,
            t_listing - t_sem_acquired,
            t_score - t_listing,
            t_end - t_pre_commit,
        )

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

        # Write scanner metrics to DB for health endpoint (D-01, D-02)
        try:
            async with self._session_factory() as session:
                stmt = pg_insert(ScannerStatus).values(
                    id=1,
                    is_running=self.is_running,
                    last_scan_at=self.last_scan_at,
                    success_rate_1h=self.success_rate_1h(),
                    queue_depth=self._queue_depth_cache,
                    circuit_breaker_state=self._circuit_breaker.state.value,
                    updated_at=now,
                )
                stmt = stmt.on_conflict_do_update(
                    index_elements=["id"],
                    set_=dict(
                        is_running=stmt.excluded.is_running,
                        last_scan_at=stmt.excluded.last_scan_at,
                        success_rate_1h=stmt.excluded.success_rate_1h,
                        queue_depth=stmt.excluded.queue_depth,
                        circuit_breaker_state=stmt.excluded.circuit_breaker_state,
                        updated_at=stmt.excluded.updated_at,
                    ),
                )
                await session.execute(stmt)
                await session.commit()
        except Exception as exc:
            logger.error(f"Failed to upsert scanner_status: {exc}")

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

            if not due_players:
                logger.warning("dispatch_scans: found 0 due players")
                return

            # Immediately push next_scan_at forward so the next dispatch cycle
            # won't pick up the same players while they're still being scanned.
            ea_ids = [p.ea_id for p in due_players]
            await session.execute(
                update(PlayerRecord)
                .where(PlayerRecord.ea_id.in_(ea_ids))
                .values(next_scan_at=now + timedelta(seconds=SCAN_INTERVAL_SECONDS))
            )
            await session.commit()

        logger.warning(f"dispatch_scans: found {len(ea_ids)} due players")

        self._queue_depth_cache = len(ea_ids)
        semaphore = asyncio.Semaphore(SCAN_CONCURRENCY)

        async def _scan_with_sem(ea_id: int) -> None:
            async with semaphore:
                await self.scan_player(ea_id)

        for eid in ea_ids:
            task = asyncio.get_running_loop().create_task(_scan_with_sem(eid))
            self._active_tasks.add(task)

    # ── Scheduled aggregation and cleanup jobs ──────────────────────────────

    # ── Cleanup ─────────────────────────────────────────────────────────────

    async def run_cleanup(self) -> None:
        """Delete market snapshots and stale records beyond retention periods.

        Delegates to scanner_jobs.run_cleanup().
        """
        from src.server.scanner_jobs import run_cleanup
        await run_cleanup(self._session_factory)

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
