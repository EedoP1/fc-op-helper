# src/algo/report.py
"""CLI for viewing and comparing backtest results."""
import asyncio
import logging

import click
from rich.console import Console
from rich.table import Table
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from src.config import DATABASE_URL

console = Console(force_terminal=True)
logger = logging.getLogger(__name__)


async def show_results(
    db_url: str,
    strategy_name: str | None = None,
    sort_by: str = "total_pnl",
    limit: int = 50,
):
    """Query backtest_results and display a ranked table."""
    engine = create_async_engine(db_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    query = "SELECT * FROM backtest_results"
    params = {}
    if strategy_name:
        query += " WHERE strategy_name = :name"
        params["name"] = strategy_name

    valid_sorts = {"total_pnl", "win_rate", "sharpe_ratio", "max_drawdown", "total_trades"}
    if sort_by not in valid_sorts:
        sort_by = "total_pnl"

    order = "ASC" if sort_by == "max_drawdown" else "DESC"
    query += f" ORDER BY {sort_by} {order} LIMIT :limit"
    params["limit"] = limit

    async with session_factory() as session:
        result = await session.execute(text(query), params)
        rows = result.fetchall()

    await engine.dispose()

    if not rows:
        console.print("[yellow]No backtest results found.[/yellow]")
        return

    table = Table(title="Backtest Results", show_lines=True)
    table.add_column("#", style="dim", width=4)
    table.add_column("Strategy", style="cyan")
    table.add_column("Params", max_width=40)
    table.add_column("P&L", justify="right", style="green")
    table.add_column("Win Rate", justify="right")
    table.add_column("Trades", justify="right")
    table.add_column("Max DD", justify="right", style="red")
    table.add_column("Sharpe", justify="right")
    table.add_column("Final Budget", justify="right")

    for i, row in enumerate(rows, 1):
        pnl_color = "green" if row.total_pnl > 0 else "red"
        table.add_row(
            str(i),
            row.strategy_name,
            row.params[:40] if row.params else "",
            f"[{pnl_color}]{row.total_pnl:>+,}[/{pnl_color}]",
            f"{row.win_rate:.1%}",
            f"{row.total_trades:,}",
            f"{row.max_drawdown:.1%}",
            f"{row.sharpe_ratio:.2f}",
            f"{row.final_budget:>,}",
        )

    console.print(table)


@click.command()
@click.option("--strategy", default=None, help="Filter by strategy name")
@click.option("--sort", "sort_by", default="total_pnl", help="Sort column: total_pnl, win_rate, sharpe_ratio, max_drawdown, total_trades")
@click.option("--limit", default=50, help="Max rows to show")
@click.option("--db-url", default=DATABASE_URL, help="Database URL")
def main(strategy, sort_by, limit, db_url):
    """View and compare backtest results."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    asyncio.run(show_results(db_url, strategy, sort_by, limit))


if __name__ == "__main__":
    main()
