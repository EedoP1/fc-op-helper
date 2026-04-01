"""Scanner discovery sub-module: bootstrap, initial scoring, periodic rediscovery.

Extracted from scanner.py to keep the core scan loop isolated.
ScannerService delegates to these functions via thin wrappers.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import Callable

from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.config import (
    SCANNER_MIN_PRICE,
    SCANNER_MAX_PRICE,
    INITIAL_SCORING_CONCURRENCY,
    INITIAL_SCORING_BATCH_SIZE,
)
from src.futgg_client import FutGGClient
from src.server.models_db import PlayerRecord

logger = logging.getLogger(__name__)


async def run_bootstrap(
    session_factory: async_sessionmaker,
    client: FutGGClient,
) -> None:
    """Discover all players in the 11k-200k price range and seed the DB.

    Upserts PlayerRecord rows with scan_tier='normal' and next_scan_at=now
    so the dispatch loop picks them up immediately.

    Uses batched DB writes (chunks of 200) to reduce round-trips vs the
    previous one-insert-per-player approach.

    Args:
        session_factory: Async session factory for DB writes.
        client: FutGG API client for player discovery.
    """
    t_discovery = time.monotonic()
    players = await client.discover_players(
        budget=SCANNER_MAX_PRICE,
        min_price=SCANNER_MIN_PRICE,
        max_price=SCANNER_MAX_PRICE,
    )
    discovery_elapsed = time.monotonic() - t_discovery
    logger.info(
        f"Bootstrap discovered {len(players)} players in {discovery_elapsed:.1f}s"
    )

    before = len(players)
    players = [p for p in players if p.get("rarityName", "") not in ("Icon", "UT Heroes")]
    if before - len(players):
        logger.info(f"Bootstrap: filtered {before - len(players)} base icons/heroes")

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
    async with session_factory() as session:
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


async def run_initial_scoring(
    session_factory: async_sessionmaker,
    scan_player: Callable[[int], asyncio.coroutines],
) -> None:
    """Score all unscored active players with elevated concurrency.

    Called once after bootstrap. Uses INITIAL_SCORING_CONCURRENCY (10)
    instead of normal SCAN_CONCURRENCY (5) to complete faster.
    Processes players in batches of INITIAL_SCORING_BATCH_SIZE to avoid
    overwhelming the event loop.

    Args:
        session_factory: Async session factory for DB queries.
        scan_player: Async callable that scans a single player by ea_id.
    """
    start = time.monotonic()

    async with session_factory() as session:
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
            await scan_player(ea_id)
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


async def run_discovery(
    session_factory: async_sessionmaker,
    client: FutGGClient,
) -> None:
    """Periodic rediscovery: upsert new players and deactivate removed ones.

    Runs hourly to catch new players entering the 11k-200k range.
    Players no longer in the discovery result are marked cold (per Research pitfall 6).

    Args:
        session_factory: Async session factory for DB writes.
        client: FutGG API client for player discovery.
    """
    players = await client.discover_players(
        budget=SCANNER_MAX_PRICE,
        min_price=SCANNER_MIN_PRICE,
        max_price=SCANNER_MAX_PRICE,
    )
    logger.info(f"Discovery found {len(players)} players")

    before = len(players)
    players = [p for p in players if p.get("rarityName", "") not in ("Icon", "UT Heroes")]
    if before - len(players):
        logger.info(f"Discovery: filtered {before - len(players)} base icons/heroes")

    discovered_ids = {p["ea_id"] for p in players}
    now = datetime.utcnow()
    far_future = now + timedelta(hours=24)

    async with session_factory() as session:
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
