"""Playwright-based client for the fut.gg player-prices endpoint.

Uses a real Chromium browser to solve Cloudflare's JS challenge, then
leverages the browser's cookie jar for subsequent API requests. Bypasses
the managed JS challenge that curl_cffi cannot solve on the prices endpoint.

The prices endpoint uses Playwright's APIRequestContext (browser cookies +
headers), while the definitions endpoint continues to use curl_cffi.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_FUTGG_BASE = "https://www.fut.gg"
_PLAYERS_URL = f"{_FUTGG_BASE}/players/"
_PRICES_PATH = "/api/fut/player-prices/26/{ea_id}/"

_DEFAULT_HEADERS = {
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": _PLAYERS_URL,
}


class PlaywrightPricesClient:
    """Playwright browser client for the player-prices endpoint.

    Lifecycle (async):
        await client.start()  # launch browser, solve Cloudflare challenge
        ...
        await client.stop()   # close browser gracefully

    Thread-safe sync bridge:
        client.set_loop(loop)         # call from async context after start()
        data = client.get_prices_sync(ea_id)  # call from ThreadPoolExecutor

    The sync bridge uses asyncio.run_coroutine_threadsafe to schedule the
    async fetch onto the main event loop where Playwright lives.
    """

    def __init__(self) -> None:
        self._playwright = None
        self._browser = None
        self._browser_context = None
        self._api_context = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Launch Chromium, create browser context, solve Cloudflare challenge.

        Navigates to fut.gg/players/ and waits for the page to fully load so
        the Cloudflare managed challenge is resolved before any API calls are
        made. The resulting cookies are stored in the browser context for all
        subsequent APIRequestContext requests.
        """
        from playwright.async_api import async_playwright

        logger.info("PlaywrightPricesClient: launching Chromium browser...")
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=True)
        self._browser_context = await self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
        )
        self._api_context = self._browser_context.request

        logger.info("PlaywrightPricesClient: solving Cloudflare challenge on fut.gg...")
        await self._resolve_challenge()
        logger.info("PlaywrightPricesClient: browser started and challenge solved")

    async def stop(self) -> None:
        """Close browser context, browser, and Playwright instance."""
        try:
            if self._browser_context:
                await self._browser_context.close()
            if self._browser:
                await self._browser.close()
            if self._playwright:
                await self._playwright.stop()
        except Exception as exc:
            logger.error(f"PlaywrightPricesClient: error during stop: {exc}")
        finally:
            self._api_context = None
            self._browser_context = None
            self._browser = None
            self._playwright = None
        logger.info("PlaywrightPricesClient: stopped")

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Store event loop reference for sync-to-async bridging.

        Must be called from the async context after start() so the loop
        reference is valid when get_prices_sync() is called from threads.

        Args:
            loop: The running asyncio event loop (from asyncio.get_running_loop()).
        """
        self._loop = loop

    # ── Sync bridge ───────────────────────────────────────────────────────────

    def get_prices_sync(self, ea_id: int) -> dict | None:
        """Fetch player-prices data synchronously (thread-pool safe).

        Bridges sync ThreadPoolExecutor threads to the async Playwright
        event loop using asyncio.run_coroutine_threadsafe. Blocks until the
        fetch completes or times out.

        Rate limiting is handled by the caller (futgg_client._get_sync).

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
        """Fetch and parse player-prices data for a single player.

        On 403, re-solves the Cloudflare challenge and retries once.

        Args:
            ea_id: EA resource ID of the player.

        Returns:
            The parsed ``data`` dict from the API response, or None on error.
        """
        url = f"{_FUTGG_BASE}{_PRICES_PATH.format(ea_id=ea_id)}"
        try:
            response = await self._api_context.get(url, headers=_DEFAULT_HEADERS)
            if response.status == 403:
                logger.warning(
                    f"PlaywrightPricesClient: 403 for ea_id={ea_id} "
                    "— re-solving Cloudflare challenge and retrying"
                )
                await self._resolve_challenge()
                response = await self._api_context.get(url, headers=_DEFAULT_HEADERS)

            if response.status != 200:
                logger.error(
                    f"PlaywrightPricesClient: HTTP {response.status} for ea_id={ea_id}"
                )
                return None

            data = await response.json()
            return data.get("data")
        except Exception as exc:
            logger.error(f"PlaywrightPricesClient: request error for ea_id={ea_id}: {exc}")
            return None

    async def _resolve_challenge(self) -> None:
        """Navigate to fut.gg/players/ to solve the Cloudflare JS challenge.

        Creates a temporary page, navigates to the players listing, and waits
        for the page to fully settle. This causes Cloudflare to issue a valid
        cookie set into the browser context, which the APIRequestContext
        automatically inherits for subsequent API calls.
        """
        try:
            page = await self._browser_context.new_page()
            await page.goto(_PLAYERS_URL, wait_until="domcontentloaded", timeout=30000)
            # Give Cloudflare challenge ~3 seconds to complete
            await asyncio.sleep(3)
            await page.close()
            logger.info("PlaywrightPricesClient: Cloudflare challenge resolved")
        except Exception as exc:
            logger.error(f"PlaywrightPricesClient: challenge resolution failed: {exc}")
