"""
Listing tracking module: fingerprint-based upsert, outcome resolution, daily aggregation.

This is the core data collection layer that records individual liveAuctions entries
as ListingObservation rows, resolves their outcome (sold/expired) when they disappear,
and aggregates daily summaries per margin tier.

Public API:
    record_listings(ea_id, live_auctions_raw, current_lowest_bin, completed_sales, session)
    resolve_outcomes(ea_id, current_fingerprints, completed_sales, session)
    aggregate_daily_summaries(ea_id, target_date, session)
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone, timedelta

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from src.config import MARGINS
from src.models import SaleRecord
from src.server.models_db import DailyListingSummary, ListingObservation

logger = logging.getLogger(__name__)

# Bucket size in minutes for fallback fingerprinting (no tradeId available)
_BUCKET_MINUTES = 10


def _utcnow() -> datetime:
    """Return current UTC time as a naive datetime (consistent with DB storage)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _extract_remaining_seconds(entry: dict) -> float:
    """Extract remaining auction time in seconds from a liveAuctions entry.

    Checks fields in order:
    1. ``expiresOn`` / ``expires`` — ISO datetime string; computes delta from now.
    2. ``remainingTime`` / ``timeRemaining`` — numeric seconds value.
    3. Falls back to 3600.0 (1-hour minimum FC26 listing duration).

    Args:
        entry: Raw liveAuctions dict from fut.gg.

    Returns:
        Remaining seconds as float (minimum 0.0).
    """
    now_utc = datetime.now(timezone.utc)

    expires = entry.get("expiresOn") or entry.get("expires")
    if expires:
        try:
            expiry_dt = datetime.fromisoformat(str(expires).replace("Z", "+00:00"))
            remaining = (expiry_dt - now_utc).total_seconds()
            return max(remaining, 0.0)
        except (ValueError, TypeError):
            pass

    rem = entry.get("remainingTime") or entry.get("timeRemaining")
    if rem is not None and isinstance(rem, (int, float)):
        return max(float(rem), 0.0)

    return 3600.0  # default: 1-hour minimum FC26 listing duration


def _make_fingerprint(ea_id: int, entry: dict, first_seen_at: datetime) -> str:
    """Build a deterministic fingerprint for a liveAuctions entry.

    Strategy:
    - If entry has 'tradeId': use ``{ea_id}:{tradeId}`` — globally unique auction ID.
    - Fallback: use ``{ea_id}:{buyNowPrice}:{bucket}`` where bucket rounds
      ``first_seen_at`` down to the nearest 10-minute window.

    Args:
        ea_id: Player EA ID.
        entry: Raw liveAuctions dict from fut.gg.
        first_seen_at: Timestamp when first seen (for bucket calculation).

    Returns:
        String fingerprint, at most 128 characters.
    """
    trade_id = entry.get("tradeId") or entry.get("id")
    buy_now_price = entry.get("buyNowPrice", 0)

    if trade_id is not None:
        fp = f"{ea_id}:{trade_id}"
    else:
        # Round first_seen_at down to 10-minute bucket to group same listing seen
        # in rapid successive scans without a tradeId
        bucket_minutes = (first_seen_at.minute // _BUCKET_MINUTES) * _BUCKET_MINUTES
        bucket = first_seen_at.strftime(f"%Y%m%d%H") + f"{bucket_minutes:02d}"
        fp = f"{ea_id}:{buy_now_price}:{bucket}"

    return fp[:128]


def _is_op_listing(buy_now_price: int, market_price: int, margin_pct: int) -> bool:
    """Check if a listing qualifies as OP at the given margin tier.

    Args:
        buy_now_price: The listing's Buy Now price.
        market_price: Market reference price (current_lowest_bin) at observation time.
        margin_pct: Margin percentage threshold (e.g. 40 for 40%).

    Returns:
        True if buy_now_price >= market_price * (1 + margin_pct / 100).
    """
    return buy_now_price >= int(market_price * (1 + margin_pct / 100.0))


async def record_listings(
    ea_id: int,
    live_auctions_raw: list[dict],
    current_lowest_bin: int,
    completed_sales: list[SaleRecord],
    session: AsyncSession,
) -> dict:
    """Record liveAuctions entries as ListingObservation rows with fingerprint dedup.

    For each entry in ``live_auctions_raw``, builds a deterministic fingerprint and
    upserts a ListingObservation row:
    - INSERT: sets all fields, scan_count=1, outcome=None
    - CONFLICT on fingerprint: updates last_seen_at and increments scan_count

    Args:
        ea_id: Player EA ID.
        live_auctions_raw: List of raw liveAuctions dicts from fut.gg.
        current_lowest_bin: Current market price (stored as market_price_at_obs).
        completed_sales: Unused here; passed for API symmetry with resolve_outcomes.
        session: Active AsyncSession.

    Returns:
        Dict with ``recorded`` (count) and ``fingerprints`` (list of fingerprint strings).
    """
    now = _utcnow()
    fingerprints: list[str] = []
    pending_values: list[dict] = []

    for entry in live_auctions_raw:
        buy_now_price = entry.get("buyNowPrice", 0)
        if not buy_now_price:
            continue

        fp = _make_fingerprint(ea_id, entry, now)
        fingerprints.append(fp)

        remaining = _extract_remaining_seconds(entry)
        expected_expiry_at = now + timedelta(seconds=remaining)

        pending_values.append(dict(
            fingerprint=fp,
            ea_id=ea_id,
            buy_now_price=buy_now_price,
            market_price_at_obs=current_lowest_bin,
            first_seen_at=now,
            last_seen_at=now,
            expected_expiry_at=expected_expiry_at,
            scan_count=1,
            outcome=None,
            resolved_at=None,
        ))

    # Deduplicate within the batch: multiple listings at the same price produce
    # identical fallback fingerprints ({ea_id}:{price}:{bucket}), which Postgres
    # rejects in a single INSERT ... ON CONFLICT statement.
    deduped: dict[str, dict] = {}
    for values in pending_values:
        deduped[values["fingerprint"]] = values
    unique_values = list(deduped.values())

    # Batch upsert: one INSERT ... VALUES (...), (...), ... ON CONFLICT per chunk
    # instead of one round-trip per row.
    chunk_size = 50
    for i in range(0, len(unique_values), chunk_size):
        chunk = unique_values[i:i + chunk_size]
        stmt = pg_insert(ListingObservation).values(chunk)
        stmt = stmt.on_conflict_do_update(
            index_elements=["fingerprint"],
            set_=dict(
                last_seen_at=now,
                expected_expiry_at=stmt.excluded.expected_expiry_at,
                scan_count=ListingObservation.__table__.c.scan_count + 1,
            ),
        )
        await session.execute(stmt)

    return {"recorded": len(fingerprints), "fingerprints": fingerprints}


async def resolve_outcomes(
    ea_id: int,
    current_fingerprints: list[str],
    completed_sales: list[SaleRecord],
    session: AsyncSession,
    last_resolved_at: datetime | None = None,
) -> dict:
    """Resolve outcomes for listings that have disappeared since last scan.

    Identifies ListingObservation rows for ``ea_id`` that are unresolved and
    whose fingerprint is no longer in the current scan (i.e., listing gone).
    Groups by buy_now_price and assigns outcomes proportionally:
    - First M listings marked "sold" (M = matching completed sales at that price)
    - Remaining N-M listings marked "expired"

    Args:
        ea_id: Player EA ID.
        current_fingerprints: Fingerprints still visible in latest scan.
        completed_sales: List of SaleRecord from fut.gg completedAuctions.
        session: Active AsyncSession.

    Returns:
        Dict with ``sold`` and ``expired`` counts.
    """
    import time as _time
    now = _utcnow()

    # Query unresolved observations not in current scan whose expected expiry has passed.
    # Use lightweight column fetch (not full ORM) to avoid loading all columns.
    # Listings with NULL expected_expiry_at (created before migration) are excluded —
    # they will be cleaned up by the retention purge.
    from sqlalchemy import text, update

    fp_filter = (
        ListingObservation.fingerprint.not_in(current_fingerprints)
        if current_fingerprints
        else ListingObservation.fingerprint.isnot(None)
    )
    stmt = (
        select(
            ListingObservation.id,
            ListingObservation.fingerprint,
            ListingObservation.buy_now_price,
            ListingObservation.market_price_at_obs,
            ListingObservation.first_seen_at,
        )
        .where(
            ListingObservation.ea_id == ea_id,
            ListingObservation.outcome.is_(None),
            ListingObservation.expected_expiry_at.isnot(None),
            ListingObservation.expected_expiry_at < now,
            fp_filter,
        )
    )
    _t0 = _time.monotonic()
    result = await session.execute(stmt)
    disappeared = result.all()
    _t_find = _time.monotonic()

    if not disappeared:
        _t_total = _time.monotonic() - _t0
        if _t_total > 1.0:
            logger.warning("RESOLVE_TIMING ea_id=%d find=%.1fs (0 rows) total=%.1fs", ea_id, _t_find - _t0, _t_total)
        return {"sold": 0, "expired": 0}

    # Filter stale sales using last_resolved_at from PlayerRecord (passed in by caller).
    # This avoids the expensive MAX(resolved_at) query on the listing_observations table
    # which took 11-56s under load due to heap scans on a 1.2GB table.
    _t_max = _time.monotonic()
    if last_resolved_at is not None:
        last_resolved_naive = last_resolved_at.replace(tzinfo=None) if last_resolved_at.tzinfo else last_resolved_at
        completed_sales = [
            sale for sale in completed_sales
            if sale.sold_at.replace(tzinfo=None) > last_resolved_naive
        ]

    logger.debug(
        f"resolve_outcomes: ea_id={ea_id} last_resolved_at={last_resolved_at} "
        f"sales_after_filter={len(completed_sales)} disappeared={len(disappeared)}"
    )

    # Group by price — use lightweight tuples instead of ORM objects
    by_price: dict[int, list] = {}
    for row in disappeared:
        by_price.setdefault(row.buy_now_price, []).append(row)

    # Build per-price list of sale timestamps from completedAuctions.
    sale_times_by_price: dict[int, list[datetime]] = {}
    for sale in completed_sales:
        price = sale.sold_price
        sold_at_naive = sale.sold_at.replace(tzinfo=None)
        sale_times_by_price.setdefault(price, []).append(sold_at_naive)
    for times in sale_times_by_price.values():
        times.sort()

    sold_ids: list[int] = []
    expired_ids: list[int] = []

    for price, obs_list in by_price.items():
        earliest_first_seen = min(
            r.first_seen_at.replace(tzinfo=None) if r.first_seen_at.tzinfo else r.first_seen_at
            for r in obs_list
        )
        all_times = sale_times_by_price.get(price, [])
        matching_sales = sum(1 for t in all_times if t >= earliest_first_seen)
        n_sold = min(matching_sales, len(obs_list))

        for i, row in enumerate(obs_list):
            if i < n_sold:
                sold_ids.append(row.id)
            else:
                expired_ids.append(row.id)

    # Aggregate resolved observations into daily summaries, then delete them.
    # This replaces the old batch UPDATE + nightly aggregation approach:
    # observations are classified by margin tier, counts are upserted into
    # daily_listing_summaries, and the raw observations are deleted in-place.

    today_str = now.strftime("%Y-%m-%d")
    n_sold = len(sold_ids)
    n_expired = len(expired_ids)
    n_total = n_sold + n_expired

    # Build price info lookup from disappeared rows
    price_info = {row.id: (row.buy_now_price, row.market_price_at_obs) for row in disappeared}

    from src.config import MAX_OP_MARGIN_PCT
    max_op_factor = 1 + MAX_OP_MARGIN_PCT / 100.0

    # Detect dialect for SQLite vs Postgres upsert strategy
    dialect = session.bind.dialect.name

    for margin_pct in MARGINS:
        op_sold = 0
        op_expired = 0
        op_listed = 0

        for obs_id in sold_ids:
            buy_price, market_price = price_info[obs_id]
            if market_price > 0 and buy_price < int(market_price * max_op_factor):
                if _is_op_listing(buy_price, market_price, margin_pct):
                    op_sold += 1
                    op_listed += 1

        for obs_id in expired_ids:
            buy_price, market_price = price_info[obs_id]
            if market_price > 0 and buy_price < int(market_price * max_op_factor):
                if _is_op_listing(buy_price, market_price, margin_pct):
                    op_expired += 1
                    op_listed += 1

        # Upsert: increment today's summary row
        if dialect == "sqlite":
            # SQLite: SELECT + UPDATE/INSERT to support incremental counts
            # (pg_insert on_conflict_do_update with += doesn't work on SQLite
            # because SQLite's ON CONFLICT replaces instead of updating in place)
            existing = await session.execute(
                select(DailyListingSummary).where(
                    DailyListingSummary.ea_id == ea_id,
                    DailyListingSummary.date == today_str,
                    DailyListingSummary.margin_pct == margin_pct,
                )
            )
            row = existing.scalar_one_or_none()
            if row is not None:
                row.op_listed_count += op_listed
                row.op_sold_count += op_sold
                row.op_expired_count += op_expired
                row.total_listed_count += n_total
                row.total_sold_count += n_sold
                row.total_expired_count += n_expired
            else:
                session.add(DailyListingSummary(
                    ea_id=ea_id,
                    date=today_str,
                    margin_pct=margin_pct,
                    op_listed_count=op_listed,
                    op_sold_count=op_sold,
                    op_expired_count=op_expired,
                    total_listed_count=n_total,
                    total_sold_count=n_sold,
                    total_expired_count=n_expired,
                ))
        else:
            stmt = pg_insert(DailyListingSummary).values(
                ea_id=ea_id,
                date=today_str,
                margin_pct=margin_pct,
                op_listed_count=op_listed,
                op_sold_count=op_sold,
                op_expired_count=op_expired,
                total_listed_count=n_total,
                total_sold_count=n_sold,
                total_expired_count=n_expired,
            ).on_conflict_do_update(
                constraint="uq_daily_summary_ea_id_date_margin",
                set_=dict(
                    op_listed_count=DailyListingSummary.__table__.c.op_listed_count + op_listed,
                    op_sold_count=DailyListingSummary.__table__.c.op_sold_count + op_sold,
                    op_expired_count=DailyListingSummary.__table__.c.op_expired_count + op_expired,
                    total_listed_count=DailyListingSummary.__table__.c.total_listed_count + n_total,
                    total_sold_count=DailyListingSummary.__table__.c.total_sold_count + n_sold,
                    total_expired_count=DailyListingSummary.__table__.c.total_expired_count + n_expired,
                ),
            )
            await session.execute(stmt)

    # Delete resolved observations — they've been aggregated
    from sqlalchemy import delete
    all_resolved_ids = sold_ids + expired_ids
    for i in range(0, len(all_resolved_ids), 500):
        chunk = all_resolved_ids[i:i + 500]
        await session.execute(
            delete(ListingObservation).where(ListingObservation.id.in_(chunk))
        )
    _t_update = _time.monotonic()

    logger.warning(
        "RESOLVE_TIMING ea_id=%d find=%.1fs(%d rows) max_resolved=%.1fs update=%.1fs total=%.1fs",
        ea_id, _t_find - _t0, len(disappeared), _t_max - _t_find, _t_update - _t_max, _t_update - _t0,
    )

    return {"sold": n_sold, "expired": n_expired, "resolved_at": now}


async def aggregate_daily_summaries(
    ea_id: int,
    target_date: str,
    session: AsyncSession,
) -> int:
    """Aggregate resolved ListingObservations into DailyListingSummary rows.

    For each margin in MARGINS, counts OP listings (and sold/expired sub-counts)
    among all resolved observations for ``ea_id`` on ``target_date``.
    Upserts one DailyListingSummary row per (ea_id, date, margin_pct).

    Args:
        ea_id: Player EA ID.
        target_date: Date string in YYYY-MM-DD format.
        session: Active AsyncSession.

    Returns:
        Number of DailyListingSummary rows written.
    """
    # Parse date boundaries
    day_start = datetime.strptime(target_date, "%Y-%m-%d")
    day_end = datetime(day_start.year, day_start.month, day_start.day, 23, 59, 59)

    # Fetch all resolved observations for this player on target_date
    stmt = select(ListingObservation).where(
        ListingObservation.ea_id == ea_id,
        ListingObservation.outcome.isnot(None),
        ListingObservation.first_seen_at >= day_start,
        ListingObservation.first_seen_at <= day_end,
    )
    result = await session.execute(stmt)
    observations = result.scalars().all()

    total_listed = len(observations)
    rows_written = 0

    for margin_pct in MARGINS:
        op_obs = [
            obs for obs in observations
            if _is_op_listing(obs.buy_now_price, obs.market_price_at_obs, margin_pct)
        ]
        op_listed = len(op_obs)
        op_sold = sum(1 for obs in op_obs if obs.outcome == "sold")
        op_expired = sum(1 for obs in op_obs if obs.outcome == "expired")

        stmt = (
            pg_insert(DailyListingSummary)
            .values(
                ea_id=ea_id,
                date=target_date,
                margin_pct=margin_pct,
                op_listed_count=op_listed,
                op_sold_count=op_sold,
                op_expired_count=op_expired,
                total_listed_count=total_listed,
            )
            .on_conflict_do_update(
                constraint="uq_daily_summary_ea_id_date_margin",
                set_=dict(
                    op_listed_count=op_listed,
                    op_sold_count=op_sold,
                    op_expired_count=op_expired,
                    total_listed_count=total_listed,
                ),
            )
        )
        await session.execute(stmt)
        rows_written += 1

    logger.debug(
        f"aggregate_daily_summaries: ea_id={ea_id} date={target_date} "
        f"obs={total_listed} margins={rows_written}"
    )
    return rows_written
