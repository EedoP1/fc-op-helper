"""Portfolio data fetching and score preparation utilities."""
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.server.models_db import PlayerRecord, PlayerScore


class _PlayerProxy:
    """Minimal proxy satisfying optimize_portfolio()'s entry['player'].resource_id access."""

    __slots__ = ("resource_id",)

    def __init__(self, ea_id: int):
        self.resource_id = ea_id


def _build_scored_entry(score: PlayerScore, record: PlayerRecord) -> dict:
    """Build a scored-entry dict from DB rows, matching optimize_portfolio()'s expected format.

    A fresh dict is built per request to avoid mutation issues
    (optimize_portfolio mutates input dicts).
    """
    return {
        "player": _PlayerProxy(score.ea_id),
        "buy_price": score.buy_price,
        "sell_price": score.sell_price,
        "net_profit": score.net_profit,
        "margin_pct": score.margin_pct,
        "op_sales": score.op_sales,
        "total_sales": score.total_sales,
        "op_ratio": score.op_ratio,
        "expected_profit": score.expected_profit,
        "efficiency": score.efficiency,
        "sales_per_hour": score.sales_per_hour,
        "ea_id": record.ea_id,
        "name": record.name,
        "rating": record.rating,
        "position": record.position,
        "card_type": record.card_type,
        "scan_tier": record.scan_tier,
        "last_scanned_at": record.last_scanned_at,
        "expected_profit_per_hour": score.expected_profit_per_hour,
        "futgg_url": record.futgg_url,
    }


async def _fetch_latest_viable_scores(session: AsyncSession) -> list[tuple]:
    """Fetch the latest viable PlayerScore + PlayerRecord for every active player.

    Replaces the subquery+nested-loop ORM pattern
    (JOIN (SELECT ea_id, MAX(scored_at) ... GROUP BY ea_id) ...) which degrades
    to O(N) random index lookups on cold cache with 500k+ rows (~33s).

    ROW_NUMBER() OVER (PARTITION BY ea_id ORDER BY scored_at DESC) lets
    PostgreSQL use an incremental sort on the (ea_id, scored_at) index in a
    single forward pass (~4s cold, ~1s warm).

    Returns:
        List of (PlayerScore, PlayerRecord) tuples, one per active+viable player.
    """
    cutoff = datetime.utcnow() - timedelta(hours=4)
    sql = text("""
        SELECT
            ps.id, ps.ea_id, ps.scored_at,
            ps.buy_price, ps.sell_price, ps.net_profit, ps.margin_pct,
            ps.op_sales, ps.total_sales, ps.op_ratio, ps.expected_profit,
            ps.efficiency, ps.sales_per_hour, ps.is_viable,
            ps.expected_profit_per_hour, ps.scorer_version, ps.max_sell_price,
            pr.ea_id   AS pr_ea_id, pr.name, pr.rating, pr.position,
            pr.nation, pr.league, pr.club, pr.card_type, pr.scan_tier,
            pr.last_scanned_at, pr.next_scan_at, pr.is_active,
            pr.listing_count, pr.sales_per_hour AS pr_sales_per_hour,
            pr.futgg_url
        FROM (
            SELECT *,
                   ROW_NUMBER() OVER (
                       PARTITION BY ea_id
                       ORDER BY scored_at DESC
                   ) AS rn
            FROM player_scores
            WHERE is_viable = TRUE
              AND scored_at >= :cutoff
        ) ps
        JOIN players pr ON pr.ea_id = ps.ea_id
        WHERE ps.rn = 1
          AND pr.is_active = TRUE
          AND pr.card_type NOT IN ('Icon', 'UT Heroes')
          AND (ps.max_sell_price IS NULL OR ps.sell_price <= ps.max_sell_price)
    """)
    result = await session.execute(sql, {"cutoff": cutoff})
    raw_rows = result.mappings().all()

    # Reconstruct ORM-like objects so callers can use score.field / record.field
    pairs = []
    for row in raw_rows:
        score = PlayerScore(
            id=row["id"],
            ea_id=row["ea_id"],
            scored_at=row["scored_at"],
            buy_price=row["buy_price"],
            sell_price=row["sell_price"],
            net_profit=row["net_profit"],
            margin_pct=row["margin_pct"],
            op_sales=row["op_sales"],
            total_sales=row["total_sales"],
            op_ratio=row["op_ratio"],
            expected_profit=row["expected_profit"],
            efficiency=row["efficiency"],
            sales_per_hour=row["sales_per_hour"],
            is_viable=row["is_viable"],
            expected_profit_per_hour=row["expected_profit_per_hour"],
            scorer_version=row["scorer_version"],
            max_sell_price=row["max_sell_price"],
        )
        record = PlayerRecord(
            ea_id=row["pr_ea_id"],
            name=row["name"],
            rating=row["rating"],
            position=row["position"],
            nation=row["nation"],
            league=row["league"],
            club=row["club"],
            card_type=row["card_type"],
            scan_tier=row["scan_tier"],
            last_scanned_at=row["last_scanned_at"],
            next_scan_at=row["next_scan_at"],
            is_active=row["is_active"],
            listing_count=row["listing_count"],
            sales_per_hour=row["pr_sales_per_hour"],
            futgg_url=row["futgg_url"],
        )
        pairs.append((score, record))
    return pairs
