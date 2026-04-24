"""Playwright-based client for the fut.gg player-prices endpoint.

Uses a real Chrome browser with stealth flags to bypass Cloudflare's managed
JS challenge. Maintains a pool of browser pages for concurrent fetches —
Cloudflare solves the challenge on the first request, and subsequent
navigations return JSON directly.

The definitions endpoint continues to use curl_cffi (no challenge there).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Optional

logger = logging.getLogger(__name__)

_FUTGG_BASE = "https://www.fut.gg"
_PRICES_URL = _FUTGG_BASE + "/api/fut/player-prices/26/{ea_id}/"

# Regex to extract JSON from Chrome's raw JSON viewer (<pre> tag)
_PRE_JSON_RE = re.compile(r"<pre>(.*?)</pre>", re.DOTALL)

# Number of browser pages to keep in the pool for concurrent fetches.
# 5 triggers Cloudflare rate limiting; 3 is the sweet spot.
_PAGE_POOL_SIZE = 3

# How often to recycle the browser context (pages + cookies + cache).
# Cloudflare's clearance cookie has a short lifetime, and a long-lived
# browser context accumulates stale cache/state that causes fut.gg to
# start returning responses with `liveAuctions: []` while keeping
# `currentPrice` populated — a silent, differentially-throttled shape.
# Recycling the context every ~30min re-solves the Cloudflare challenge
# with fresh cookies and clears any accumulated session state.
_CONTEXT_RECYCLE_SECONDS = 1800.0

# If we see this many consecutive suspicious empty-liveAuctions responses,
# recycle the context sooner than the timed interval. Catches the common
# case where fut.gg's rate limiter kicks in earlier than the interval.
_EMPTY_STREAK_TRIGGER = 20


class PlaywrightPricesClient:
    """Playwright browser client for the player-prices endpoint.

    Lifecycle (async):
        await client.start()  # launch browser, solve Cloudflare challenge
        ...
        await client.stop()   # close browser gracefully

    Thread-safe sync bridge:
        client.set_loop(loop)         # call from async context after start()
        data = client.get_prices_sync(ea_id)  # call from ThreadPoolExecutor

    Uses a pool of browser pages so multiple scanner threads can fetch
    concurrently without blocking each other. Pages are checked out from
    an asyncio.Queue and returned after use.
    """

    def __init__(self) -> None:
        self._playwright = None
        self._browser = None
        self._browser_context = None
        self._pages: list = []
        self._page_pool: Optional[asyncio.Queue] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        # Health tracking for automatic context recycle
        self._context_started_at: float = 0.0
        self._empty_la_streak: int = 0
        self._recycle_lock: Optional[asyncio.Lock] = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Launch Chrome with stealth flags, create page pool, solve challenge.

        Uses the system-installed Chrome (channel='chrome') with
        --disable-blink-features=AutomationControlled to avoid Cloudflare's
        bot detection. Creates a pool of pages for concurrent price fetches.
        """
        from playwright.async_api import async_playwright

        logger.info("PlaywrightPricesClient: launching Chrome browser...")
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            channel="chrome",
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        self._recycle_lock = asyncio.Lock()
        await self._build_context()
        logger.info(
            f"PlaywrightPricesClient: browser started with {_PAGE_POOL_SIZE} pages"
        )

    async def _build_context(self) -> None:
        """Create a fresh browser_context + page pool and solve Cloudflare challenge.

        Called from start() and _recycle_context(). Caller must ensure any
        previous context/pages have been torn down.
        """
        self._browser_context = await self._browser.new_context()
        # Remove webdriver flag to pass Cloudflare bot detection
        await self._browser_context.add_init_script(
            'Object.defineProperty(navigator, "webdriver", {get: () => undefined})'
        )

        # Create page pool
        self._page_pool = asyncio.Queue()
        self._pages = []
        for _ in range(_PAGE_POOL_SIZE):
            page = await self._browser_context.new_page()
            self._pages.append(page)
            self._page_pool.put_nowait(page)

        # Solve Cloudflare challenge on the first page
        logger.info("PlaywrightPricesClient: solving Cloudflare challenge...")
        await self._resolve_challenge()
        self._context_started_at = time.monotonic()
        self._empty_la_streak = 0

    async def _recycle_context(self, reason: str) -> None:
        """Tear down and rebuild browser_context + pages.

        Serialized via self._recycle_lock so only one coroutine recycles at
        a time; the rest skip (their trigger has been satisfied by the
        in-progress rebuild). After rebuild, the challenge is freshly solved
        with new cookies — the most effective counter to fut.gg/Cloudflare
        throttling the long-lived session into empty-liveAuctions responses.
        """
        if self._recycle_lock is None:
            return
        if self._recycle_lock.locked():
            # Another coroutine is already recycling; skip.
            return
        async with self._recycle_lock:
            logger.warning(
                f"PlaywrightPricesClient: recycling browser context (reason={reason})"
            )
            try:
                for page in self._pages:
                    try:
                        await page.close()
                    except Exception:
                        pass
                if self._browser_context is not None:
                    try:
                        await self._browser_context.close()
                    except Exception:
                        pass
            finally:
                self._pages = []
                self._page_pool = None
                self._browser_context = None
            await self._build_context()
            logger.info("PlaywrightPricesClient: context recycle complete")

    async def stop(self) -> None:
        """Close all pages, browser context, browser, and Playwright."""
        try:
            for page in self._pages:
                await page.close()
            if self._browser_context:
                await self._browser_context.close()
            if self._browser:
                await self._browser.close()
            if self._playwright:
                await self._playwright.stop()
        except Exception as exc:
            logger.error(f"PlaywrightPricesClient: error during stop: {exc}")
        finally:
            self._pages.clear()
            self._page_pool = None
            self._browser_context = None
            self._browser = None
            self._playwright = None
        logger.info("PlaywrightPricesClient: stopped")

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Store event loop reference for sync-to-async bridging.

        Args:
            loop: The running asyncio event loop (from asyncio.get_running_loop()).
        """
        self._loop = loop

    # ── Sync bridge ───────────────────────────────────────────────────────────

    def get_prices_sync(self, ea_id: int) -> dict | None:
        """Fetch player-prices data synchronously (thread-pool safe).

        Bridges sync ThreadPoolExecutor threads to the async Playwright
        event loop using asyncio.run_coroutine_threadsafe.

        Rate limiting is handled by the caller (futgg_client).

        Args:
            ea_id: EA resource ID of the player.

        Returns:
            The parsed ``data`` dict from the prices API, or None on error.
        """
        if self._loop is None:
            logger.error("PlaywrightPricesClient: event loop not set — call set_loop() first")
            return None

        try:
            future = asyncio.run_coroutine_threadsafe(
                self._fetch_prices(ea_id), self._loop
            )
            # Outer timeout > inner Cloudflare poll budget (30s) + a 15s slack
            # for navigation, parsing, and queue contention.
            return future.result(timeout=45)
        except TimeoutError:
            logger.error(f"PlaywrightPricesClient: timeout fetching prices for {ea_id}")
            return None
        except Exception as exc:
            logger.error(f"PlaywrightPricesClient: error fetching prices for {ea_id}: {exc}")
            return None

    # ── Internal async helpers ─────────────────────────────────────────────────

    async def _fetch_prices(self, ea_id: int) -> dict | None:
        """Fetch player-prices by checking out a page and navigating to the URL.

        Acquires a page from the pool, navigates to the price endpoint,
        parses the JSON response, and returns the page to the pool.

        On Cloudflare challenge, waits for resolution and retries.

        Args:
            ea_id: EA resource ID of the player.

        Returns:
            The parsed ``data`` dict from the API response, or None on error.
        """
        # Time-based context recycling: Cloudflare's clearance cookie expires
        # and the long-lived browser context has been observed to start
        # returning liveAuctions=[] responses. Force a periodic refresh.
        if (
            self._context_started_at > 0
            and time.monotonic() - self._context_started_at > _CONTEXT_RECYCLE_SECONDS
        ):
            await self._recycle_context(reason="age")

        url = _PRICES_URL.format(ea_id=ea_id)
        # Page pool may be torn down mid-recycle; loop until we get one.
        while True:
            if self._page_pool is None:
                await asyncio.sleep(0.1)
                continue
            page = await self._page_pool.get()
            break
        try:
            await page.goto(url, timeout=30000)
            content = await page.content()

            # Check for Cloudflare challenge — poll every 0.5s for up to 30s
            # rather than guessing a single 5s sleep. Cloudflare's managed
            # JS challenge can take anywhere from <1s to ~25s to clear.
            if "Just a moment" in content:
                logger.warning(
                    f"PlaywrightPricesClient: Cloudflare challenge for ea_id={ea_id} "
                    "— polling up to 30s..."
                )
                deadline = time.monotonic() + 30.0
                while time.monotonic() < deadline:
                    await asyncio.sleep(0.5)
                    content = await page.content()
                    if "Just a moment" not in content:
                        break
                if "Just a moment" in content:
                    logger.error(
                        f"PlaywrightPricesClient: challenge not resolved in 30s for ea_id={ea_id}"
                    )
                    return None

            data = self._parse_json_response(content, ea_id)
            # Streak-based recycle trigger: if we see many consecutive
            # responses that have a priced card but zero liveAuctions, the
            # session is almost certainly being throttled into stripped
            # responses. Reset on any good response.
            if data is not None:
                cp = data.get("currentPrice") or {}
                has_price = bool(cp.get("price"))
                live_count = len(data.get("liveAuctions") or [])
                if has_price and live_count == 0:
                    self._empty_la_streak += 1
                else:
                    self._empty_la_streak = 0
                if self._empty_la_streak >= _EMPTY_STREAK_TRIGGER:
                    # Release this page first so recycle can close it cleanly.
                    try:
                        self._page_pool.put_nowait(page)
                    except Exception:
                        pass
                    page = None  # prevent double-release in finally
                    await self._recycle_context(reason="empty-la-streak")
            return data
        except Exception as exc:
            logger.error(f"PlaywrightPricesClient: error fetching prices for ea_id={ea_id}: {exc}")
            return None
        finally:
            if page is not None and self._page_pool is not None:
                self._page_pool.put_nowait(page)

    def _parse_json_response(self, content: str, ea_id: int) -> dict | None:
        """Extract and parse JSON data from the browser's rendered response.

        Chrome renders raw JSON inside a <pre> tag. Extract that and parse it.

        Args:
            content: The page HTML content.
            ea_id: EA resource ID (for logging).

        Returns:
            The parsed ``data`` dict, or None on parse failure.
        """
        match = _PRE_JSON_RE.search(content)
        if not match:
            logger.error(f"PlaywrightPricesClient: no JSON found in response for ea_id={ea_id}")
            return None

        try:
            data = json.loads(match.group(1))
            return data.get("data")
        except json.JSONDecodeError as exc:
            logger.error(f"PlaywrightPricesClient: JSON parse error for ea_id={ea_id}: {exc}")
            return None

    async def _resolve_challenge(self) -> None:
        """Navigate to a prices endpoint to trigger and solve the Cloudflare challenge.

        Uses the first page in the pool. The challenge is solved once and
        cookies are shared across all pages in the browser context.
        """
        test_url = _PRICES_URL.format(ea_id=50563721)
        page = self._pages[0]
        try:
            await page.goto(test_url, timeout=30000)
            content = await page.content()
            if "Just a moment" in content:
                logger.info("PlaywrightPricesClient: waiting for Cloudflare challenge...")
                await asyncio.sleep(5)
                content = await page.content()
                if "Just a moment" in content:
                    logger.warning("PlaywrightPricesClient: challenge may not have resolved")
                else:
                    logger.info("PlaywrightPricesClient: Cloudflare challenge resolved")
            else:
                logger.info("PlaywrightPricesClient: no challenge needed")
        except Exception as exc:
            logger.error(f"PlaywrightPricesClient: challenge resolution failed: {exc}")
