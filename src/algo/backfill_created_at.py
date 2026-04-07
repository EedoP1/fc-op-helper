"""One-time backfill: fetch createdAt from fut.gg for all players missing it."""
import asyncio
import logging

import click
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from src.config import DATABASE_URL
from src.futgg_client import FutGGClient

logger = logging.getLogger(__name__)


async def backfill(db_url: str):
    engine = create_async_engine(db_url)
    sf = async_sessionmaker(engine, expire_on_commit=False)

    async with sf() as s:
        r = await s.execute(text("SELECT ea_id FROM players WHERE created_at IS NULL"))
        ea_ids = [row[0] for row in r.fetchall()]

    logger.info(f"Backfilling created_at for {len(ea_ids)} players")

    client = FutGGClient()
    await client.start()

    updated = 0
    for i, ea_id in enumerate(ea_ids):
        defn = await client.get_player_definition(ea_id)
        if defn and defn.get("createdAt"):
            from datetime import datetime
            try:
                created_at = datetime.fromisoformat(defn["createdAt"].replace("Z", "+00:00")).replace(tzinfo=None)
                async with sf() as s:
                    await s.execute(
                        text("UPDATE players SET created_at = :ca WHERE ea_id = :eid"),
                        {"ca": created_at, "eid": ea_id},
                    )
                    await s.commit()
                updated += 1
            except (ValueError, AttributeError):
                pass

        if (i + 1) % 50 == 0:
            logger.info(f"Progress: {i + 1}/{len(ea_ids)} ({updated} updated)")

    await client.stop()
    await engine.dispose()
    logger.info(f"Backfill complete: {updated}/{len(ea_ids)} updated")


@click.command()
@click.option("--db-url", default=DATABASE_URL)
def main(db_url):
    """Backfill created_at from fut.gg for all players."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    asyncio.run(backfill(db_url))


if __name__ == "__main__":
    main()
