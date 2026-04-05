"""Show top 100 v3 scored players in a pretty table."""
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


async def main():
    engine = create_engine()
    sf = create_session_factory(engine)

    async with sf() as session:
        result = await session.execute(text("""
            WITH latest AS (
                SELECT *,
                       ROW_NUMBER() OVER (PARTITION BY ea_id ORDER BY scored_at DESC) AS rn
                FROM player_scores
                WHERE scorer_version = 'v3'
                  AND scored_at >= NOW() - INTERVAL '15 minutes'
            )
            SELECT l.ea_id, p.name, p.card_type, l.buy_price, l.margin_pct,
                   l.net_profit, l.sales_per_hour, l.op_ratio AS sell_ratio,
                   l.expected_profit AS score
            FROM latest l
            JOIN players p ON p.ea_id = l.ea_id
            WHERE l.rn = 1 AND l.expected_profit > 0 AND l.is_viable = TRUE
            ORDER BY l.expected_profit DESC
            LIMIT 100
        """))
        rows = result.mappings().all()

    await engine.dispose()

    console = Console(width=150)
    table = Table(title="V3 Scorer — Top 100", show_lines=False, expand=True, padding=(0, 1))
    table.add_column("#", justify="right", style="dim", width=4)
    table.add_column("Name", style="bold cyan", width=24)
    table.add_column("Card Type", style="dim", width=20)
    table.add_column("Buy", justify="right", width=10)
    table.add_column("Margin", justify="right", style="yellow", width=7)
    table.add_column("Profit", justify="right", style="green", width=9)
    table.add_column("SPH", justify="right", width=8)
    table.add_column("Sell %", justify="right", style="magenta", width=7)
    table.add_column("Score", justify="right", style="bold green", width=12)

    for i, r in enumerate(rows, 1):
        sell_pct = f"{r['sell_ratio'] * 100:.1f}%"
        table.add_row(
            str(i),
            r["name"],
            r.get("card_type", ""),
            f"{r['buy_price']:,}",
            f"{r['margin_pct']}%",
            f"{r['net_profit']:,}",
            f"{r['sales_per_hour']:.0f}",
            sell_pct,
            f"{r['score']:,.0f}",
        )

    console.print(table)


if __name__ == "__main__":
    asyncio.run(main())
