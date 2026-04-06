"""FUTBIN price history scraper using Playwright with stealth.

Scrapes daily price history for all 75+ rated players from FUTBIN.
Uses the same Playwright + stealth pattern as the existing scanner.

Data is extracted from the `data-ps-data` attribute on `.highcharts-graph-wrapper`
elements — FUTBIN embeds the full price history (game launch to now) as
[[timestamp_ms, price], ...] in the server-rendered HTML.

EA resource IDs are extracted from player image URLs which follow the pattern
`players/{ea_id}.png`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone

import click
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from src.config import DATABASE_URL
from src.server.db import Base

logger = logging.getLogger(__name__)

_FUTBIN_BASE = "https://www.futbin.com"
_PLAYERS_LIST_URL = _FUTBIN_BASE + "/players?player_rating=75-99&page={page}"
_PLAYER_MARKET_URL = _FUTBIN_BASE + "/26/player/{futbin_id}/{slug}/market"

# Delay between page loads to avoid detection (seconds)
_PAGE_DELAY = 1.0

# Number of browser pages for concurrent scraping
_PAGE_POOL_SIZE = 3

# Regex to extract EA base ID from player image URL
_EA_ID_RE = re.compile(r"players/p?(\d{4,})")

# Regex to extract futbin_id and slug from player links
_PLAYER_LINK_RE = re.compile(r'/26/player/(\d+)/([a-z0-9-]+)')


def parse_futbin_price_data(data_attr: str) -> list[tuple[int, int]]:
    """Parse FUTBIN's data-ps-data attribute into (timestamp_ms, price) tuples."""
    try:
        points = json.loads(data_attr)
        return [(int(ts), int(price)) for ts, price in points]
    except (json.JSONDecodeError, TypeError, ValueError):
        return []


def extract_ea_id(page_html: str) -> int | None:
    """Extract EA resource ID from player image URL in page HTML."""
    match = _EA_ID_RE.search(page_html)
    return int(match.group(1)) if match else None


async def _create_browser(pool_size: int = _PAGE_POOL_SIZE):
    """Launch Chrome with stealth flags, create page pool.

    Returns (pw, browser, context, page_pool) where page_pool is an
    asyncio.Queue of browser pages for concurrent scraping.
    """
    from playwright.async_api import async_playwright

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(
        channel="chrome",
        headless=False,
        args=["--disable-blink-features=AutomationControlled"],
    )
    context = await browser.new_context()
    await context.add_init_script(
        'Object.defineProperty(navigator, "webdriver", {get: () => undefined})'
    )

    page_pool: asyncio.Queue = asyncio.Queue()
    pages = []
    for _ in range(pool_size):
        page = await context.new_page()
        pages.append(page)
        page_pool.put_nowait(page)

    return pw, browser, context, pages, page_pool


async def scrape_player_list(page, max_pages: int = 999, max_players: int = 0) -> list[dict]:
    """Scrape FUTBIN player list to get all 75+ rated player URLs.

    Args:
        page: Playwright page instance.
        max_pages: Max list pages to crawl.
        max_players: Stop after collecting this many players (0 = no limit).

    Returns list of {futbin_id, slug} dicts.
    """
    players = []
    seen_ids = set()

    for page_num in range(1, max_pages + 1):
        url = _PLAYERS_LIST_URL.format(page=page_num)
        logger.info(f"Fetching player list page {page_num}...")

        await page.goto(url, timeout=30000)
        await asyncio.sleep(_PAGE_DELAY)

        content = await page.content()

        # Check for Cloudflare challenge
        if "Just a moment" in content:
            logger.warning("Cloudflare challenge — waiting...")
            await asyncio.sleep(10)
            content = await page.content()
            if "Just a moment" in content:
                logger.error("Cloudflare challenge not resolved, stopping")
                break

        # Extract player links
        links = _PLAYER_LINK_RE.findall(content)
        if not links:
            logger.info(f"No player links found on page {page_num}, done")
            break

        new_count = 0
        for futbin_id, slug in links:
            futbin_id = int(futbin_id)
            if futbin_id not in seen_ids:
                seen_ids.add(futbin_id)
                players.append({
                    "futbin_id": futbin_id,
                    "slug": slug,
                })
                new_count += 1

        logger.info(f"Page {page_num}: {new_count} new players, {len(players)} total")

        if new_count == 0:
            break

        if max_players > 0 and len(players) >= max_players:
            players = players[:max_players]
            logger.info(f"Reached {max_players} player limit, stopping list scrape")
            break

    return players


async def scrape_player_prices(page, futbin_id: int, slug: str) -> dict | None:
    """Scrape a single player's market page for price history.

    Returns dict with ea_id, futbin_id, name, prices or None on failure.
    """
    url = f"{_FUTBIN_BASE}/26/player/{futbin_id}/{slug}/market"

    try:
        await page.goto(url, timeout=30000)
        await asyncio.sleep(_PAGE_DELAY)

        content = await page.content()

        if "Just a moment" in content:
            logger.warning(f"Cloudflare challenge for {slug} — waiting...")
            await asyncio.sleep(10)
            content = await page.content()
            if "Just a moment" in content:
                logger.error(f"Challenge not resolved for {slug}")
                return None

        # Extract EA resource ID from player image
        ea_id = extract_ea_id(content)
        if not ea_id:
            logger.warning(f"Could not extract ea_id for {slug} (futbin_id={futbin_id})")
            return None

        # Extract player name from page title
        name_match = re.search(r"<h1[^>]*>([^<]+)", content)
        name = name_match.group(1).strip() if name_match else slug

        # Extract price history from the first data-ps-data attribute
        # (the main daily graph for PS/Cross platform)
        ps_match = re.search(r'data-ps-data="(\[\[.*?\]\])"', content)
        if not ps_match:
            logger.warning(f"No price data found for {slug} (ea_id={ea_id})")
            return None

        # HTML-decode the attribute value
        raw = ps_match.group(1).replace("&quot;", '"')
        prices = parse_futbin_price_data(raw)

        if not prices:
            logger.warning(f"Empty price data for {slug} (ea_id={ea_id})")
            return None

        logger.info(
            f"  {name}: ea_id={ea_id}, {len(prices)} price points "
            f"({datetime.fromtimestamp(prices[0][0]/1000, tz=timezone.utc).date()} → "
            f"{datetime.fromtimestamp(prices[-1][0]/1000, tz=timezone.utc).date()})"
        )

        return {
            "ea_id": ea_id,
            "futbin_id": futbin_id,
            "name": name,
            "prices": prices,
        }

    except Exception as exc:
        logger.error(f"Error scraping {slug} (futbin_id={futbin_id}): {exc}")
        return None


async def save_prices(session_factory, player_data: dict):
    """Insert price history rows into the database."""
    rows = []
    for ts_ms, price in player_data["prices"]:
        rows.append({
            "ea_id": player_data["ea_id"],
            "futbin_id": player_data["futbin_id"],
            "timestamp": datetime.utcfromtimestamp(ts_ms / 1000),
            "price": price,
        })

    if rows:
        async with session_factory() as session:
            await session.execute(
                text(
                    "INSERT INTO price_history (ea_id, futbin_id, timestamp, price) "
                    "VALUES (:ea_id, :futbin_id, :timestamp, :price)"
                ),
                rows,
            )
            await session.commit()

    return len(rows)


async def scrape_all(
    db_url: str = DATABASE_URL,
    limit: int = 0,
    concurrency: int = _PAGE_POOL_SIZE,
):
    """Full scrape pipeline: list players → visit each market page → store prices.

    Args:
        db_url: Database connection URL.
        limit: Max players to scrape (0 = all). Use 10 for POC.
        concurrency: Number of browser pages for parallel scraping.
    """
    engine = create_async_engine(db_url)

    # Ensure tables exist
    from src.algo.models_db import PriceHistory  # noqa: F401
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    pw, browser, context, pages, page_pool = await _create_browser(pool_size=concurrency)

    try:
        # Phase 1: Get player list (use first page from pool)
        list_page = await page_pool.get()
        logger.info("Phase 1: Scraping player list from FUTBIN...")
        players = await scrape_player_list(list_page, max_pages=999, max_players=limit)
        logger.info(f"Found {len(players)} players")
        page_pool.put_nowait(list_page)

        if limit > 0 and len(players) > limit:
            players = players[:limit]

        if limit > 0:
            logger.info(f"POC mode: scraping {len(players)} players")

        # Phase 2: Scrape each player's market page using page pool
        logger.info(
            f"Phase 2: Scraping price history for {len(players)} players "
            f"({concurrency} concurrent pages)..."
        )
        total_inserted = 0
        success = 0
        failed = 0
        counter = 0

        async def scrape_worker(player: dict):
            """Checkout a page from pool, scrape, return page."""
            nonlocal total_inserted, success, failed, counter
            page = await page_pool.get()
            try:
                counter += 1
                idx = counter
                logger.info(
                    f"[{idx}/{len(players)}] Scraping {player['slug']}..."
                )
                data = await scrape_player_prices(
                    page, player["futbin_id"], player["slug"],
                )

                if data:
                    inserted = await save_prices(session_factory, data)
                    total_inserted += inserted
                    success += 1
                else:
                    failed += 1
            finally:
                page_pool.put_nowait(page)

        # Process players in batches of concurrency size
        for i in range(0, len(players), concurrency):
            batch = players[i : i + concurrency]
            await asyncio.gather(*[scrape_worker(p) for p in batch])

        logger.info(
            f"Scrape complete: {success} players, {failed} failed, "
            f"{total_inserted} price points inserted"
        )

    finally:
        for p in pages:
            await p.close()
        await context.close()
        await browser.close()
        await pw.stop()
        await engine.dispose()


@click.command()
@click.option("--limit", default=0, help="Max players to scrape (0 = all, 10 = POC)")
@click.option("--concurrency", default=_PAGE_POOL_SIZE, help="Number of browser pages for parallel scraping")
@click.option("--db-url", default=DATABASE_URL, help="Database URL")
def main(limit: int, concurrency: int, db_url: str):
    """Scrape full price history from FUTBIN for all 75+ rated players."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    asyncio.run(scrape_all(db_url=db_url, limit=limit, concurrency=concurrency))


if __name__ == "__main__":
    main()
