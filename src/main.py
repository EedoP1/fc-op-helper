"""
FC26 OP Sell List Generator.

Discovers players from fut.gg, scores them for OP selling potential,
and outputs the optimal list for a given budget.

Usage:
    python -m src.main --budget 1000000
"""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import sys
from datetime import datetime

import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

from src.futgg_client import FutGGClient
from src.protocols import MarketDataClient
from src.scorer import score_player
from src.optimizer import optimize_portfolio

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

console = Console(force_terminal=True)
logger = logging.getLogger("op-seller")


async def run(budget: int, verbose: bool, client: MarketDataClient | None = None) -> None:
    """Main pipeline: discover → fetch → score → optimize → display."""
    if client is None:
        client = FutGGClient()
    await client.start()

    try:
        # ── Step 1: Discover players in price range ──────────────
        max_price = int(budget * 0.10)
        min_price = int(budget * 0.005)
        console.print(
            f"\n[bold]Discovering players {min_price:,}–{max_price:,} "
            f"(budget: {budget:,})...[/bold]"
        )
        candidates = await client.discover_players(
            budget, min_price=min_price, max_price=max_price,
        )
        console.print(f"Found [green]{len(candidates)}[/green] candidates\n")
        if not candidates:
            console.print("[red]No candidates found.[/red]")
            return

        # ── Step 2: Fetch market data ────────────────────────────
        console.print("[bold]Fetching market data...[/bold]")
        ea_ids = [c["ea_id"] for c in candidates if c.get("ea_id")]
        all_md = await client.get_batch_market_data(ea_ids, concurrency=10)

        valid_md = [md for md in all_md if md and md.current_lowest_bin > 0]
        console.print(f"Got data for [green]{len(valid_md)}[/green] players\n")

        # ── Step 3: Score ────────────────────────────────────────
        console.print("[bold]Scoring players...[/bold]")
        scored = [s for md in valid_md if (s := score_player(md))]
        console.print(f"Scored [green]{len(scored)}[/green] viable players\n")

        # ── Step 4: Optimize portfolio ───────────────────────────
        console.print("[bold]Optimizing portfolio...[/bold]")
        selected = optimize_portfolio(scored, budget)

        # ── Step 5: Display + Export ─────────────────────────────
        total_used = sum(s["buy_price"] for s in selected)
        display_results(selected, budget, total_used)

        csv_path = export_csv(selected)
        console.print(f"\n[bold green]Exported:[/bold green] {csv_path}")

    finally:
        await client.stop()


def display_results(selected: list[dict], budget: int, total_used: int) -> None:
    """Display portfolio summary and player table."""
    if not selected:
        console.print("[red]No players selected.[/red]")
        return

    total_profit = sum(s["net_profit"] for s in selected)
    total_expected = sum(s["expected_profit"] for s in selected)

    summary = Text()
    summary.append(f"Budget: {budget:,}", style="bold")
    summary.append(f"  |  Used: {total_used:,}")
    summary.append(f"  |  Profit/sell: {total_profit:,}", style="bold green")
    if total_used:
        summary.append(f" ({total_profit / total_used:.1%})")
    summary.append(f"\nExpected profit: {total_expected:,.0f}", style="bold cyan")
    summary.append(f"  |  Players: {len(selected)}")
    console.print(Panel(summary, title="OP Sell Portfolio", border_style="green"))

    table = Table(title=f"Top {len(selected)} OP Sell Targets", show_lines=True)
    table.add_column("#", style="dim", width=3)
    table.add_column("Player", style="bold", min_width=16, max_width=20)
    table.add_column("OVR", justify="center", width=3)
    table.add_column("Pos", justify="center", width=3)
    table.add_column("Buy", justify="right")
    table.add_column("Sell", justify="right")
    table.add_column("Profit", justify="right", style="green")
    table.add_column("Margin", justify="right", width=5)
    table.add_column("ExpProf", justify="right", style="cyan")
    table.add_column("OP%", justify="right", width=4)
    table.add_column("OP/Tot", justify="right", width=6)

    for i, s in enumerate(selected):
        p = s["player"]
        table.add_row(
            str(i + 1),
            p.name[:20],
            str(p.rating),
            p.position,
            f"{s['buy_price']:,}",
            f"{s['sell_price']:,}",
            f"+{s['net_profit']:,}",
            f"{s['margin_pct']}%",
            f"{s['expected_profit']:,.0f}",
            f"{s['op_ratio']:.0%}",
            f"{s['op_sales']}/{s['total_sales']}",
        )
    console.print(table)


def export_csv(selected: list[dict]) -> str:
    """Export portfolio to CSV file. Returns the file path."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = f"op_sell_list_{ts}.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "Rank", "Player", "Rating", "Position", "League", "Club",
            "Buy", "Sell", "Profit", "Profit%", "Margin",
            "Expected Profit", "OP Sales", "Total Sales", "OP Ratio",
            "OP Sales/24h", "Sales/hr", "Data Span",
        ])
        for i, s in enumerate(selected):
            p = s["player"]
            w.writerow([
                i + 1, p.name, p.rating, p.position, p.league, p.club,
                s["buy_price"], s["sell_price"], s["net_profit"],
                f"{s['net_profit'] / s['buy_price']:.2%}", f"{s['margin_pct']}%",
                f"{s['expected_profit']:.0f}",
                s["op_sales"], s["total_sales"], f"{s['op_ratio']:.2%}",
                f"{s['op_sales_24h']:.0f}", s["sales_per_hour"],
                f"{s['time_span_hrs']:.1f}h",
            ])
    return path


@click.command()
@click.option("--budget", "-b", required=True, type=int, help="Coin budget")
@click.option("--verbose", "-v", is_flag=True, help="Detailed progress")
def main(budget: int, verbose: bool):
    """FC26 OP Sell List Generator."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )
    if not verbose:
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
    asyncio.run(run(budget, verbose))


if __name__ == "__main__":
    main()
