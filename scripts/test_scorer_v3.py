"""Quick simulation: score all active players with v3 using current DB data."""
import asyncio
import sys
import os
os.environ["PYTHONIOENCODING"] = "utf-8"
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, ".")

from rich.console import Console
from rich.table import Table

from sqlalchemy import text
from src.server.db import create_engine, create_session_factory
from src.server.scorer_v3 import score_player_v3


async def main():
    engine = create_engine()
    sf = create_session_factory(engine)

    async with sf() as session:
        result = await session.execute(text("""
            SELECT p.ea_id, p.name, p.card_type, p.listing_count,
                   ps.buy_price, ps.sales_per_hour
            FROM players p
            JOIN LATERAL (
                SELECT buy_price, sales_per_hour
                FROM player_scores
                WHERE ea_id = p.ea_id AND is_viable = TRUE
                ORDER BY scored_at DESC
                LIMIT 1
            ) ps ON TRUE
            WHERE p.is_active = TRUE
              AND p.card_type NOT IN ('Icon', 'UT Heroes')
        """))
        rows = result.mappings().all()

    results = []
    for row in rows:
        v3 = score_player_v3(
            ea_id=row["ea_id"],
            buy_price=row["buy_price"],
            sales_per_hour=row["sales_per_hour"],
            listing_count=row["listing_count"],
        )
        if v3 is not None:
            v3["name"] = row["name"]
            v3["card_type"] = row["card_type"]
            results.append(v3)

    results.sort(key=lambda x: x["weighted_score"], reverse=True)

    console = Console(width=140)
    table = Table(title=f"Scorer V3 — Top 100 of {len(results)} scored players", show_lines=False, expand=True)
    table.add_column("#", justify="right", style="dim", width=4)
    table.add_column("Name", style="bold", width=24)
    table.add_column("Card Type", width=22)
    table.add_column("Buy", justify="right", width=10)
    table.add_column("SPH", justify="right", width=7)
    table.add_column("Listings", justify="right", width=8)
    table.add_column("Sell Ratio", justify="right", width=10)
    table.add_column("Score", justify="right", style="green", width=10)

    for i, r in enumerate(results[:100], 1):
        table.add_row(
            str(i),
            r["name"],
            r["card_type"],
            f"{r['buy_price']:,}",
            f"{r['sales_per_hour']:.1f}",
            f"{r['listing_count']:,}",
            f"{r['sell_ratio']:.4f}",
            f"{r['weighted_score']:,.1f}",
        )

    console.print(table)
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
