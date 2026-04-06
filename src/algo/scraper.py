"""One-time scraper to fetch full price history from fut.gg into the database."""
import asyncio
import logging
from datetime import datetime

import click
import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from src.config import DATABASE_URL
from src.server.db import Base

logger = logging.getLogger(__name__)

_MIN_REQUEST_INTERVAL = 0.25  # match existing rate limit
BASE_URL = "https://www.fut.gg"


def parse_price_history(ea_id: int, prices_data: dict) -> list[tuple[int, str, int]]:
    """Parse price history from fut.gg API response.

    Returns list of (ea_id, iso_timestamp_str, price) tuples.
    """
    results = []
    for point in prices_data.get("history", []):
        try:
            ts_str = point["date"].replace("Z", "+00:00")
            price = point["price"]
            results.append((ea_id, ts_str, price))
        except (KeyError, TypeError):
            continue
    return results


async def fetch_player_price_history(
    client: httpx.AsyncClient, ea_id: int,
) -> list[tuple[int, str, int]]:
    """Fetch full price history for a single player from fut.gg."""
    try:
        resp = await client.get(f"{BASE_URL}/api/fut/player-prices/26/{ea_id}/")
        resp.raise_for_status()
        data = resp.json()
        prices = data.get("data", {})
        return parse_price_history(ea_id, prices)
    except Exception as e:
        logger.error(f"Failed to fetch price history for {ea_id}: {e}")
        return []


async def scrape_all(db_url: str = DATABASE_URL, concurrency: int = 5):
    """Fetch price history for all known players and insert into price_history table."""
    engine = create_async_engine(db_url)

    from src.algo.models_db import PriceHistory  # noqa: F401
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        result = await session.execute(
            text("SELECT ea_id FROM players WHERE is_active = true")
        )
        ea_ids = [row[0] for row in result.fetchall()]

    logger.info(f"Scraping price history for {len(ea_ids)} players")

    sem = asyncio.Semaphore(concurrency)
    last_request_time = 0.0

    async def fetch_with_rate_limit(client: httpx.AsyncClient, ea_id: int):
        nonlocal last_request_time
        async with sem:
            now = asyncio.get_event_loop().time()
            wait = _MIN_REQUEST_INTERVAL - (now - last_request_time)
            if wait > 0:
                await asyncio.sleep(wait)
            last_request_time = asyncio.get_event_loop().time()
            return await fetch_player_price_history(client, ea_id)

    async with httpx.AsyncClient(timeout=30.0) as client:
        total = len(ea_ids)
        inserted = 0

        for i in range(0, total, concurrency):
            batch_ids = ea_ids[i : i + concurrency]
            tasks = [fetch_with_rate_limit(client, eid) for eid in batch_ids]
            results = await asyncio.gather(*tasks)

            rows = []
            for points in results:
                for ea_id, ts_str, price in points:
                    rows.append({
                        "ea_id": ea_id,
                        "timestamp": ts_str,
                        "price": price,
                    })

            if rows:
                async with session_factory() as session:
                    await session.execute(
                        text(
                            "INSERT INTO price_history (ea_id, timestamp, price) "
                            "VALUES (:ea_id, :timestamp, :price)"
                        ),
                        rows,
                    )
                    await session.commit()
                inserted += len(rows)

            logger.info(
                f"Progress: {min(i + concurrency, total)}/{total} players, "
                f"{inserted} price points inserted"
            )

    await engine.dispose()
    logger.info(f"Scrape complete: {inserted} total price points")


@click.command()
@click.option("--concurrency", default=5, help="Max concurrent API requests")
@click.option("--db-url", default=DATABASE_URL, help="Database URL")
def main(concurrency: int, db_url: str):
    """Scrape full price history from fut.gg for all known players."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    asyncio.run(scrape_all(db_url=db_url, concurrency=concurrency))


if __name__ == "__main__":
    main()
