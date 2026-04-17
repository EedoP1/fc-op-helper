"""
FC26 OP Sell List Generator — API Client.

Queries the running backend for portfolio and player detail data,
then displays results using Rich tables and CSV export.

Usage:
    python -m src.main --budget 1000000
    python -m src.main --player 12345
"""

from __future__ import annotations

import asyncio
import csv
import logging
import sys
from datetime import datetime

import click
import httpx
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

DEFAULT_SERVER_URL = "http://localhost:8000"  # per D-01

console = Console(force_terminal=True)
logger = logging.getLogger("op-seller")


# ── Portfolio mode ─────────────────────────────────────────────────────────────

async def run_portfolio(url: str, budget: int) -> None:
    """Fetch optimized portfolio from the backend and display it.

    Args:
        url: Backend server base URL.
        budget: Coin budget for portfolio optimization.
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.get(f"{url}/api/v1/portfolio", params={"budget": budget})
        except httpx.ConnectError:
            console.print(
                f"[red]Error: Cannot reach server at {url}. "
                f"Start the backend with: uvicorn src.server.main:app[/red]"
            )
            sys.exit(1)

        if resp.status_code != 200:
            snippet = resp.text[:200] if resp.text else ""
            console.print(
                f"[red]Error: Server returned {resp.status_code}[/red]\n{snippet}"
            )
            sys.exit(1)

        data = resp.json()

    mapped = [
        {
            "player_name": item["name"],
            "rating": item["rating"],
            "position": item["position"],
            "card_type": item.get("card_type"),
            "buy_price": item["price"],       # API key is "price"
            "sell_price": item["sell_price"],
            "net_profit": item["net_profit"],
            "margin_pct": item["margin_pct"],
            "op_sold": item["op_sales"],
            "op_total": item["total_sales"],
            "op_ratio": item["op_ratio"],
            "expected_profit": item["expected_profit"],            # per-flip
            "rank_score": item.get("expected_profit_per_hour"),    # composite — optimizer input
            "coins_per_hour": item.get("coins_per_hour"),          # real coins/hr — for display
            "sales_per_hour": item.get("sales_per_hour"),
            "is_stale": item.get("is_stale", False),
        }
        for item in data["data"]
    ]

    budget_used = data["budget_used"]
    display_results(mapped, budget, budget_used)

    csv_path = export_csv(mapped)
    console.print(f"\n[bold green]Exported:[/bold green] {csv_path}")


# ── Player detail mode ─────────────────────────────────────────────────────────

async def run_player_detail(url: str, ea_id: int) -> None:
    """Fetch player detail from the backend and display it.

    Args:
        url: Backend server base URL.
        ea_id: EA resource ID of the player.
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.get(f"{url}/api/v1/players/{ea_id}")
        except httpx.ConnectError:
            console.print(
                f"[red]Error: Cannot reach server at {url}. "
                f"Start the backend with: uvicorn src.server.main:app[/red]"
            )
            sys.exit(1)

        if resp.status_code == 404:
            console.print(f"[red]Player {ea_id} not found on server[/red]")
            sys.exit(1)

        if resp.status_code != 200:
            snippet = resp.text[:200] if resp.text else ""
            console.print(
                f"[red]Error: Server returned {resp.status_code}[/red]\n{snippet}"
            )
            sys.exit(1)

        data = resp.json()

    # Header panel (per D-05)
    header = Text()
    header.append(f"{data['name']}", style="bold")
    header.append(f"  {data['rating']} {data['position']}")
    header.append(f"\n{data['club']} | {data['league']} | {data['nation']}")
    if data.get("card_type"):
        header.append(f" | {data['card_type']}")
    console.print(Panel(header, title="Player Detail", border_style="cyan"))

    # Score breakdown table
    score = data.get("current_score")
    if score:
        table = Table(show_header=False, show_lines=True, title="Current Score")
        table.add_column("Field", style="bold", min_width=14)
        table.add_column("Value", justify="right")
        table.add_row("Buy Price", f"{score['buy_price']:,}")
        table.add_row("Sell Price", f"{score['sell_price']:,}")
        table.add_row("Net Profit", f"+{score['net_profit']:,}")
        table.add_row("Margin", f"{score['margin_pct']}%")
        table.add_row("OP Sales", f"{score['op_sales']}/{score['total_sales']}")
        table.add_row("OP Ratio", f"{score['op_ratio']:.1%}")
        table.add_row("Expected Prof", f"{score['expected_profit']:.1f}")
        table.add_row("Efficiency", f"{score['efficiency']:.4f}")
        table.add_row("Sales/hr", f"{score['sales_per_hour']:.1f}")
        table.add_row("Scored At", score["scored_at"])
        console.print(table)
    else:
        console.print("[yellow]No score data available for this player.[/yellow]")

    # Trend summary line (per D-05)
    trend = data["trend"]
    arrow = {
        "up": "[green]trending up[/green]",
        "down": "[red]trending down[/red]",
        "stable": "stable",
    }[trend["direction"]]
    console.print(
        f"\nTrend: {arrow}  |  "
        f"Price change: {trend['price_change']:+,}  |  "
        f"Efficiency change: {trend['efficiency_change']:+.4f}"
    )


# ── Display and export ─────────────────────────────────────────────────────────

def display_results(selected: list[dict], budget: int, total_used: int) -> None:
    """Display portfolio summary panel and player table.

    Args:
        selected: List of mapped player dicts from the portfolio API.
        budget: Original coin budget.
        total_used: Total budget used by selected players.
    """
    if not selected:
        console.print("[red]No players selected.[/red]")
        return

    total_cph = sum((s.get("coins_per_hour") or 0) for s in selected)

    summary = Text()
    summary.append(f"Budget: {budget:,}", style="bold")
    summary.append(f"  |  Used: {total_used:,}")
    if total_used:
        summary.append(f"  ({total_used / budget:.1%})")
    summary.append(f"\nExpected profit/hr: {total_cph:,.0f}", style="bold cyan")
    summary.append(f"  |  Players: {len(selected)}")
    console.print(Panel(summary, title="OP Sell Portfolio", border_style="green"))

    table = Table(title=f"Top {len(selected)} OP Sell Targets", show_lines=True)
    table.add_column("#", style="dim", width=3)
    table.add_column("Player", style="bold", min_width=16, max_width=20)
    table.add_column("OVR", justify="center", width=3)
    table.add_column("Pos", justify="center", width=3)
    table.add_column("Buy", justify="right")
    table.add_column("Sell", justify="right")
    table.add_column("Profit", justify="right")
    table.add_column("Margin", justify="right", width=6)
    table.add_column("EP/hr", justify="right", style="cyan")
    table.add_column("Win%", justify="right", width=5)
    table.add_column("OP Sales", justify="right", width=7)
    table.add_column("Sales/hr", justify="right", width=7)

    for i, s in enumerate(selected):
        stale = bool(s.get("is_stale"))
        row_style = "dim" if stale else None
        name_cell = f"*{s['player_name'][:19]}" if stale else s["player_name"][:20]
        cph = s.get("coins_per_hour")
        sph = s.get("sales_per_hour")
        table.add_row(
            str(i + 1),
            name_cell,
            str(s["rating"]),
            s["position"],
            f"{s['buy_price']:,}",
            f"{s['sell_price']:,}",
            f"{s['net_profit']:,}",
            f"{s['margin_pct']}%",
            f"{cph:,.0f}" if cph is not None else "-",
            f"{s['op_ratio']:.0%}",
            f"{s['op_sold']}/{s['op_total']}",
            f"{sph:.1f}" if sph is not None else "-",
            style=row_style,
        )
    console.print(table)


def export_csv(selected: list[dict]) -> str:
    """Export portfolio to a timestamped CSV file.

    Args:
        selected: List of mapped player dicts from the portfolio API.

    Returns:
        Path to the created CSV file.
    """
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = f"op_sell_list_{ts}.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "Rank", "Player", "Rating", "Position",
            "Buy", "Sell", "Profit", "Margin",
            "EP/hr", "Win%", "OP Sales", "Sales/hr", "Stale",
        ])
        for i, s in enumerate(selected):
            cph = s.get("coins_per_hour")
            sph = s.get("sales_per_hour")
            w.writerow([
                i + 1, s["player_name"], s["rating"], s["position"],
                s["buy_price"], s["sell_price"], s["net_profit"],
                f"{s['margin_pct']}%",
                f"{cph:.0f}" if cph is not None else "",
                f"{s['op_ratio']:.1%}",
                f"{s['op_sold']}/{s['op_total']}",
                f"{sph:.1f}" if sph is not None else "",
                1 if s.get("is_stale") else 0,
            ])
    return path


# ── CLI entry point ────────────────────────────────────────────────────────────

@click.command()
@click.option("--budget", "-b", type=int, default=None, help="Coin budget")
@click.option("--player", "-p", type=int, default=None, help="Player EA ID for detail view")
@click.option("--url", type=str, default=DEFAULT_SERVER_URL, help="Server URL")
@click.option("--verbose", "-v", is_flag=True, help="Verbose logging")
def main(budget: int | None, player: int | None, url: str, verbose: bool) -> None:
    """FC26 OP Sell List Generator — queries the backend API."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )
    if not verbose:
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)

    # Validate: exactly one of --budget or --player required
    if budget is None and player is None:
        console.print(
            "[red]Error: Provide --budget for portfolio mode or --player for player detail.[/red]"
        )
        sys.exit(1)
    if budget is not None and player is not None:
        console.print(
            "[red]Error: --budget and --player are mutually exclusive.[/red]"
        )
        sys.exit(1)

    if budget is not None:
        asyncio.run(run_portfolio(url, budget))
    else:
        asyncio.run(run_player_detail(url, player))


if __name__ == "__main__":
    main()
