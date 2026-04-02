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
from typing import Optional

logger = logging.getLogger(__name__)

_FUTGG_BASE = "https://www.fut.gg"
_PRICES_URL = _FUTGG_BASE + "/api/fut/player-prices/26/{ea_id}/"

# Regex to extract JSON from Chrome's raw JSON viewer (<pre> tag)
_PRE_JSON_RE = re.compile(r"<pre>(.*?)</pre>", re.DOTALL)

# Number of browser pages to keep in the pool for concurrent fetches.
# 5 triggers Cloudflare rate limiting; 3 is the sweet spot.
_PAGE_POOL_SIZE = 3


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
        self._browser_context = await self._browser.new_context()
        # Remove webdriver flag to pass Cloudflare bot detection
        await self._browser_context.add_init_script(
            'Object.defineProperty(navigator, "webdriver", {get: () => undefined})'
        )

        # Create page pool
        self._page_pool = asyncio.Queue()
        for _ in range(_PAGE_POOL_SIZE):
            page = await self._browser_context.new_page()
            self._pages.append(page)
            self._page_pool.put_nowait(page)

        # Solve Cloudflare challenge on the first page
        logger.info("PlaywrightPricesClient: solving Cloudflare challenge...")
        await self._resolve_challenge()
        logger.info(
            f"PlaywrightPricesClient: browser started with {_PAGE_POOL_SIZE} pages"
        )

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
            return future.result(timeout=30)
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
        url = _PRICES_URL.format(ea_id=ea_id)
        page = await self._page_pool.get()
        try:
            await page.goto(url, timeout=15000)
            content = await page.content()

            # Check for Cloudflare challenge
            if "Just a moment" in content:
                logger.warning(
                    f"PlaywrightPricesClient: Cloudflare challenge for ea_id={ea_id} "
                    "— waiting for resolution..."
                )
                await asyncio.sleep(5)
                content = await page.content()
                if "Just a moment" in content:
                    logger.error(
                        f"PlaywrightPricesClient: challenge not resolved for ea_id={ea_id}"
                    )
                    return None

            return self._parse_json_response(content, ea_id)
        except Exception as exc:
            logger.error(f"PlaywrightPricesClient: error fetching prices for ea_id={ea_id}: {exc}")
            return None
        finally:
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
