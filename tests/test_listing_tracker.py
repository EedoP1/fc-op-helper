"""Tests for listing_tracker: fingerprint upsert, outcome resolution, daily aggregation."""
import pytest
from datetime import datetime, timezone, timedelta
from sqlalchemy import select

from src.server.db import create_engine_and_tables
from src.server.models_db import ListingObservation, DailyListingSummary
from src.models import SaleRecord


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
async def db():
    """In-memory SQLite engine + session factory."""
    engine, session_factory = await create_engine_and_tables("sqlite+aiosqlite:///:memory:")
    yield engine, session_factory
    await engine.dispose()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _make_live_auction(
    buy_now_price: int,
    trade_id: int | None = None,
    remaining_seconds: float | None = None,
) -> dict:
    """Create a minimal liveAuctions entry dict.

    Args:
        buy_now_price: The listing's Buy Now price.
        trade_id: Optional trade ID for fingerprinting.
        remaining_seconds: Remaining auction time in seconds. If None, no expiry
            field is set (defaults to 3600s in _extract_remaining_seconds).
    """
    entry = {"buyNowPrice": buy_now_price}
    if trade_id is not None:
        entry["tradeId"] = trade_id
    if remaining_seconds is not None:
        entry["remainingTime"] = remaining_seconds
    return entry


def _make_sale(sold_price: int, sold_at: datetime, resource_id: int = 1) -> SaleRecord:
    return SaleRecord(resource_id=resource_id, sold_at=sold_at, sold_price=sold_price)


# ── Tests ─────────────────────────────────────────────────────────────────────

async def test_fingerprint_upsert(db):
    """Given 2 scans with overlapping liveAuctions entries, the second scan
    updates last_seen_at and scan_count=2 (no duplicate created)."""
    from src.server.listing_tracker import record_listings

    _, session_factory = db
    ea_id = 12345
    live_auctions = [_make_live_auction(buy_now_price=15000, trade_id=999)]

    # First scan
    async with session_factory() as session:
        result1 = await record_listings(
            ea_id=ea_id,
            live_auctions_raw=live_auctions,
            current_lowest_bin=10000,
            completed_sales=[],
            session=session,
        )
        await session.commit()

    assert result1["recorded"] == 1
    assert len(result1["fingerprints"]) == 1

    # Second scan with same entry
    async with session_factory() as session:
        result2 = await record_listings(
            ea_id=ea_id,
            live_auctions_raw=live_auctions,
            current_lowest_bin=10000,
            completed_sales=[],
            session=session,
        )
        await session.commit()

    assert result2["recorded"] == 1

    # Verify only one row exists with scan_count=2
    async with session_factory() as session:
        rows = (await session.execute(select(ListingObservation).where(
            ListingObservation.ea_id == ea_id
        ))).scalars().all()
    assert len(rows) == 1
    assert rows[0].scan_count == 2
    assert rows[0].fingerprint == result1["fingerprints"][0]


async def test_fingerprint_without_auction_id(db):
    """When liveAuctions entries have no id/tradeId field, fingerprint falls back
    to (ea_id, buyNowPrice, bucket) with temporal dedup."""
    from src.server.listing_tracker import record_listings

    _, session_factory = db
    ea_id = 99999
    live_auctions = [_make_live_auction(buy_now_price=20000)]  # no tradeId

    # Two scans without tradeId — same entry should be deduped via bucket
    async with session_factory() as session:
        result1 = await record_listings(
            ea_id=ea_id,
            live_auctions_raw=live_auctions,
            current_lowest_bin=15000,
            completed_sales=[],
            session=session,
        )
        await session.commit()

    async with session_factory() as session:
        result2 = await record_listings(
            ea_id=ea_id,
            live_auctions_raw=live_auctions,
            current_lowest_bin=15000,
            completed_sales=[],
            session=session,
        )
        await session.commit()

    # Should still only have 1 row
    async with session_factory() as session:
        rows = (await session.execute(select(ListingObservation).where(
            ListingObservation.ea_id == ea_id
        ))).scalars().all()
    assert len(rows) == 1
    assert rows[0].scan_count == 2
    fp = rows[0].fingerprint
    # Fingerprint should NOT contain 'tradeId' prefix pattern — it's a bucket fallback
    assert fp.startswith(f"{ea_id}:")


async def test_outcome_sold(db):
    """Given listing A disappeared and a completedAuctions entry matches
    (same price, within time window), outcome='sold' and resolved_at is set."""
    from src.server.listing_tracker import record_listings, resolve_outcomes

    _, session_factory = db
    ea_id = 11111
    now = _utcnow()
    # Use remaining_seconds=-60 so expected_expiry_at is set in the past (already expired)
    live_auctions = [_make_live_auction(buy_now_price=15000, trade_id=1001, remaining_seconds=-60)]

    # First scan records the listing
    async with session_factory() as session:
        result = await record_listings(
            ea_id=ea_id,
            live_auctions_raw=live_auctions,
            current_lowest_bin=10000,
            completed_sales=[],
            session=session,
        )
        await session.commit()

    fp = result["fingerprints"][0]

    # Completed sale matching the price
    sale = _make_sale(sold_price=15000, sold_at=now, resource_id=ea_id)

    # Resolve outcomes: listing disappeared (empty current fingerprints)
    async with session_factory() as session:
        counts = await resolve_outcomes(
            ea_id=ea_id,
            current_fingerprints=[],
            completed_sales=[sale],
            session=session,
        )
        await session.commit()

    assert counts["sold"] == 1
    assert counts["expired"] == 0

    # Verify the row was updated
    async with session_factory() as session:
        row = (await session.execute(select(ListingObservation).where(
            ListingObservation.fingerprint == fp
        ))).scalar_one()
    assert row.outcome == "sold"
    assert row.resolved_at is not None


async def test_outcome_expired(db):
    """Given listing B disappeared and no completedAuctions match found,
    outcome='expired' and resolved_at is set."""
    from src.server.listing_tracker import record_listings, resolve_outcomes

    _, session_factory = db
    ea_id = 22222
    # Use remaining_seconds=-60 so expected_expiry_at is set in the past (already expired)
    live_auctions = [_make_live_auction(buy_now_price=15000, trade_id=2001, remaining_seconds=-60)]

    # Record the listing
    async with session_factory() as session:
        result = await record_listings(
            ea_id=ea_id,
            live_auctions_raw=live_auctions,
            current_lowest_bin=10000,
            completed_sales=[],
            session=session,
        )
        await session.commit()

    fp = result["fingerprints"][0]

    # No matching sale — listing expired
    async with session_factory() as session:
        counts = await resolve_outcomes(
            ea_id=ea_id,
            current_fingerprints=[],
            completed_sales=[],
            session=session,
        )
        await session.commit()

    assert counts["sold"] == 0
    assert counts["expired"] == 1

    async with session_factory() as session:
        row = (await session.execute(select(ListingObservation).where(
            ListingObservation.fingerprint == fp
        ))).scalar_one()
    assert row.outcome == "expired"
    assert row.resolved_at is not None


async def test_outcome_proportional(db):
    """Given 3 listings at price 15000 disappeared and only 1 completedAuctions
    sale at 15000, 1 marked 'sold' and 2 marked 'expired'."""
    from src.server.listing_tracker import record_listings, resolve_outcomes

    _, session_factory = db
    ea_id = 33333
    now = _utcnow()

    # Use remaining_seconds=-60 so expected_expiry_at is in the past for all three
    live_auctions = [
        _make_live_auction(buy_now_price=15000, trade_id=3001, remaining_seconds=-60),
        _make_live_auction(buy_now_price=15000, trade_id=3002, remaining_seconds=-60),
        _make_live_auction(buy_now_price=15000, trade_id=3003, remaining_seconds=-60),
    ]

    async with session_factory() as session:
        await record_listings(
            ea_id=ea_id,
            live_auctions_raw=live_auctions,
            current_lowest_bin=10000,
            completed_sales=[],
            session=session,
        )
        await session.commit()

    # Only 1 sale at that price
    sale = _make_sale(sold_price=15000, sold_at=now, resource_id=ea_id)

    async with session_factory() as session:
        counts = await resolve_outcomes(
            ea_id=ea_id,
            current_fingerprints=[],
            completed_sales=[sale],
            session=session,
        )
        await session.commit()

    assert counts["sold"] == 1
    assert counts["expired"] == 2

    # Verify outcomes in DB
    async with session_factory() as session:
        rows = (await session.execute(select(ListingObservation).where(
            ListingObservation.ea_id == ea_id
        ))).scalars().all()

    sold = [r for r in rows if r.outcome == "sold"]
    expired = [r for r in rows if r.outcome == "expired"]
    assert len(sold) == 1
    assert len(expired) == 2


async def test_daily_summary(db):
    """Given 10 resolved observations for a player across 2 margin tiers,
    aggregate_daily_summaries produces correct counts per (ea_id, date, margin_pct)."""
    from src.server.listing_tracker import aggregate_daily_summaries

    _, session_factory = db
    ea_id = 44444
    today_str = "2026-03-25"
    today = datetime(2026, 3, 25, 12, 0, 0)

    # Insert resolved observations directly:
    # 5 at buyNowPrice=15000 (market=10000, margin=50% => OP at 40% and 35% and 30% and 25% and 20% and 15%)
    # 5 at buyNowPrice=11000 (market=10000, margin=10% => OP at 10% and 8%)
    observations = []
    for i in range(5):
        obs = ListingObservation(
            fingerprint=f"fp-high-{ea_id}-{i}",
            ea_id=ea_id,
            buy_now_price=15000,
            market_price_at_obs=10000,
            first_seen_at=today,
            last_seen_at=today,
            scan_count=1,
            outcome="sold" if i < 3 else "expired",
            resolved_at=today,
        )
        observations.append(obs)
    for i in range(5):
        obs = ListingObservation(
            fingerprint=f"fp-low-{ea_id}-{i}",
            ea_id=ea_id,
            buy_now_price=11000,
            market_price_at_obs=10000,
            first_seen_at=today,
            last_seen_at=today,
            scan_count=1,
            outcome="sold" if i < 2 else "expired",
            resolved_at=today,
        )
        observations.append(obs)

    async with session_factory() as session:
        session.add_all(observations)
        await session.commit()

    async with session_factory() as session:
        written = await aggregate_daily_summaries(
            ea_id=ea_id,
            target_date=today_str,
            session=session,
        )
        await session.commit()

    # Should write multiple margin summary rows
    assert written > 0

    async with session_factory() as session:
        summaries = (await session.execute(select(DailyListingSummary).where(
            DailyListingSummary.ea_id == ea_id,
            DailyListingSummary.date == today_str,
        ))).scalars().all()

    # Margin 40%: 15000 >= 10000 * 1.40 = 14000 -> yes (5 listings)
    # 11000 < 14000 -> no
    margin_40 = next((s for s in summaries if s.margin_pct == 40), None)
    assert margin_40 is not None
    assert margin_40.op_listed_count == 5
    assert margin_40.op_sold_count == 3
    assert margin_40.op_expired_count == 2
    assert margin_40.total_listed_count == 10

    # Margin 10%: 15000 >= 11000 -> yes, 11000 >= 11000 -> yes (all 10)
    margin_10 = next((s for s in summaries if s.margin_pct == 10), None)
    assert margin_10 is not None
    assert margin_10.op_listed_count == 10
    assert margin_10.total_listed_count == 10


async def test_record_listings_op_classification(db):
    """A listing with buyNowPrice=15000 and market_price_at_obs=10000 is correctly
    classified as OP at margin 40% (15000 >= 10000 * 1.40)."""
    from src.server.listing_tracker import record_listings, _is_op_listing

    _, session_factory = db
    ea_id = 55555

    live_auctions = [_make_live_auction(buy_now_price=15000, trade_id=5001)]

    async with session_factory() as session:
        await record_listings(
            ea_id=ea_id,
            live_auctions_raw=live_auctions,
            current_lowest_bin=10000,
            completed_sales=[],
            session=session,
        )
        await session.commit()

    async with session_factory() as session:
        row = (await session.execute(select(ListingObservation).where(
            ListingObservation.ea_id == ea_id
        ))).scalar_one()

    # Verify OP classification using the helper
    # 15000 >= 10000 * 1.40 = 14000 -> OP at 40%
    assert _is_op_listing(row.buy_now_price, row.market_price_at_obs, 40) is True
    # 15000 >= 10000 * 1.50 = 15000 -> still OP at 50% (boundary equals)
    assert _is_op_listing(row.buy_now_price, row.market_price_at_obs, 50) is True
    # 15000 < 10000 * 1.51 = 15100 -> not OP at 51%
    assert _is_op_listing(row.buy_now_price, row.market_price_at_obs, 51) is False
    # 15000 >= 10000 * 1.40 = 14000 -> OP at 40%
    assert row.buy_now_price == 15000
    assert row.market_price_at_obs == 10000


async def test_resolve_outcomes_no_double_counting(db):
    """Consecutive resolution batches with the same completedAuctions must NOT
    double-count sales. Batch 2 should only count sales that occurred AFTER
    batch 1's resolved_at timestamp."""
    from src.server.listing_tracker import record_listings, resolve_outcomes

    _, session_factory = db
    ea_id = 66666
    now = _utcnow()

    # Batch 1: 5 listings at 160k disappear
    batch1_auctions = [
        _make_live_auction(buy_now_price=160000, trade_id=7001 + i, remaining_seconds=-60)
        for i in range(5)
    ]

    async with session_factory() as session:
        await record_listings(
            ea_id=ea_id,
            live_auctions_raw=batch1_auctions,
            current_lowest_bin=100000,
            completed_sales=[],
            session=session,
        )
        await session.commit()

    # 10 completed sales at 160k (the sliding window from fut.gg)
    sales = [
        _make_sale(sold_price=160000, sold_at=now, resource_id=ea_id)
        for _ in range(10)
    ]

    # Resolve batch 1: all 5 disappeared listings should be matched as sold
    async with session_factory() as session:
        counts1 = await resolve_outcomes(
            ea_id=ea_id,
            current_fingerprints=[],
            completed_sales=sales,
            session=session,
        )
        await session.commit()

    assert counts1["sold"] == 5
    assert counts1["expired"] == 0

    # Batch 2: 3 NEW listings at 160k disappear
    batch2_auctions = [
        _make_live_auction(buy_now_price=160000, trade_id=7006 + i, remaining_seconds=-60)
        for i in range(3)
    ]

    async with session_factory() as session:
        await record_listings(
            ea_id=ea_id,
            live_auctions_raw=batch2_auctions,
            current_lowest_bin=100000,
            completed_sales=[],
            session=session,
        )
        await session.commit()

    # Resolve batch 2 with the SAME 10 sales (no new sales occurred)
    # Bug: without timestamp filtering, all 10 sales count again -> 3 sold
    # Fixed: only sales with sold_at > batch 1's resolved_at count -> 0 sold, 3 expired
    async with session_factory() as session:
        counts2 = await resolve_outcomes(
            ea_id=ea_id,
            current_fingerprints=[],
            completed_sales=sales,
            session=session,
        )
        await session.commit()

    assert counts2["sold"] == 0, (
        f"Expected 0 sold (no new sales after first resolution) but got {counts2['sold']}. "
        "Double-counting bug: same completedAuctions re-counted across batches."
    )
    assert counts2["expired"] == 3


async def test_resolve_outcomes_first_resolution_counts_all(db):
    """First-ever resolution for a player (no prior resolved_at) should count
    all available completedAuctions -- bootstrap correctness."""
    from src.server.listing_tracker import record_listings, resolve_outcomes

    _, session_factory = db
    ea_id = 77777
    now = _utcnow()

    # 2 listings at 100k, already expired
    auctions = [
        _make_live_auction(buy_now_price=100000, trade_id=8001 + i, remaining_seconds=-60)
        for i in range(2)
    ]

    async with session_factory() as session:
        await record_listings(
            ea_id=ea_id,
            live_auctions_raw=auctions,
            current_lowest_bin=80000,
            completed_sales=[],
            session=session,
        )
        await session.commit()

    # 5 completed sales at 100k
    sales = [
        _make_sale(sold_price=100000, sold_at=now, resource_id=ea_id)
        for _ in range(5)
    ]

    # First resolution: no prior resolved_at, all sales available
    async with session_factory() as session:
        counts = await resolve_outcomes(
            ea_id=ea_id,
            current_fingerprints=[],
            completed_sales=sales,
            session=session,
        )
        await session.commit()

    assert counts["sold"] == 2
    assert counts["expired"] == 0
