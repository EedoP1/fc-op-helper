"""
FC26 OP Sell List Generator

All data from fut.gg. Sell rate estimated from:
- OP/normal ratio in live listings
- Normal sales per hour (= normal listings per hour)
- OP sales per hour from completed auctions

Usage:
    python -m src.main --budget 1000000
"""

from __future__ import annotations

import asyncio
import csv
import logging
import sys
import io
from datetime import datetime
import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

from src.config import EA_TAX_RATE, TARGET_PLAYER_COUNT
from src.futgg_client import FutGGClient
from src.models import PlayerMarketData

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

console = Console(force_terminal=True)
logger = logging.getLogger("op-seller")


def score_player(md: PlayerMarketData) -> dict | None:
    """
    Score a player for OP selling using only fut.gg data.

    Key metric: how many OP sales in the data window, extrapolated to 24h.
    """
    buy_price = md.current_lowest_bin
    if buy_price <= 0:
        return None

    sales = md.sales
    live_prices = md.live_auction_prices

    if len(sales) < 5 or len(live_prices) < 20:
        return None

    # Time span of sales data
    sorted_sales = sorted(sales, key=lambda s: s.sold_at)
    time_span_hrs = (sorted_sales[-1].sold_at - sorted_sales[0].sold_at).total_seconds() / 3600
    if time_span_hrs < 0.5:
        time_span_hrs = 0.5

    total_sales = len(sales)
    sales_per_hour = total_sales / time_span_hrs
    if sales_per_hour < 7:
        return None

    # Build price-at-time lookup from history
    price_by_hour = {}
    for point in md.price_history:
        hour_key = point.recorded_at.strftime("%Y-%m-%dT%H")
        price_by_hour[hour_key] = point.lowest_bin

    def get_price_at_time(sale_time):
        """Get price at sale time, falling back to nearest available hour."""
        from datetime import timedelta
        hour_key = sale_time.strftime("%Y-%m-%dT%H")
        if hour_key in price_by_hour:
            return price_by_hour[hour_key]
        # Try nearby hours (±1, ±2)
        for delta in [-1, 1, -2, 2]:
            nearby = (sale_time + timedelta(hours=delta)).strftime("%Y-%m-%dT%H")
            if nearby in price_by_hour:
                return price_by_hour[nearby]
        return buy_price  # last resort

    # Try each margin — pick the one with the highest net profit
    # that still has at least 3 REAL OP sales (vs price at time of sale)
    best = None

    for margin_pct in [40, 35, 30, 25, 20, 15, 10, 8, 5, 3]:
        margin = margin_pct / 100.0

        # Count OP sales using price AT THE TIME, not current BIN
        op_sales = 0
        for s in sales:
            price_at_time = get_price_at_time(s.sold_at)
            threshold = int(price_at_time * (1 + margin))
            if s.sold_price >= threshold:
                op_sales += 1

        if op_sales < 3:
            continue

        # Calculate profit based on CURRENT price (what we'd buy/sell at now)
        sell_price = int(buy_price * (1 + margin))
        ea_tax = int(sell_price * EA_TAX_RATE)
        net_profit = sell_price - ea_tax - buy_price
        if net_profit <= 0:
            continue

        op_sales_per_24h = op_sales / time_span_hrs * 24
        op_ratio = op_sales / total_sales

        # Pick highest net profit (we iterate from highest margin down)
        if best is None:
            best = {
                "margin": margin,
                "margin_pct": margin_pct,
                "sell_price": sell_price,
                "net_profit": net_profit,
                "op_sales": op_sales,
                "total_sales": total_sales,
                "op_ratio": op_ratio,
                "op_sales_24h": round(op_sales_per_24h, 1),
                "time_span_hrs": round(time_span_hrs, 1),
                "sales_per_hour": round(sales_per_hour, 1),
            }
            break  # highest margin with 3+ OP sales = best profit

    if not best:
        return None

    return {
        "player": md.player,
        "buy_price": buy_price,
        **best,
    }


async def run(budget: int, verbose: bool) -> None:
    """discover → fetch → score → optimize → display."""
    client = FutGGClient()
    await client.start()

    try:
        # ── Step 1: Discover all players in price range ──────────────
        max_price = int(budget * 0.10)
        min_price = int(budget * 0.005)
        console.print(
            f"\n[bold]Discovering ALL players {min_price:,}–{max_price:,} "
            f"(budget: {budget:,})...[/bold]"
        )
        candidates = await client.discover_players(
            budget, min_price=min_price, max_price=max_price,
        )
        console.print(f"Found [green]{len(candidates)}[/green] candidates\n")
        if not candidates:
            console.print("[red]No candidates found.[/red]")
            return

        # ── Step 2: Fetch market data (batched) ──────────────────────
        console.print("[bold]Fetching market data...[/bold]")
        ea_ids = [c.get("ea_id", 0) for c in candidates if c.get("ea_id")]
        all_md = []
        for i in range(0, len(ea_ids), 10):
            batch = ea_ids[i:i + 10]
            if (i // 10) % 10 == 0 or i == 0:
                console.print(f"  [{min(i+10, len(ea_ids))}/{len(ea_ids)}]")
            results = await client.get_batch_market_data(batch, concurrency=10)
            all_md.extend(results)

        valid = [(eid, md) for eid, md in zip(ea_ids, all_md)
                 if md and md.current_lowest_bin > 0]
        console.print(f"Got data for [green]{len(valid)}[/green] players\n")

        # ── Step 3: Score all players ────────────────────────────────
        console.print("[bold]Scoring players...[/bold]")
        scored = []
        for ea_id, md in valid:
            result = score_player(md)
            if result:
                scored.append(result)

        console.print(f"Scored [green]{len(scored)}[/green] viable players\n")

        # ── Step 4: Sort by OP sales per 24h ────────────────────────
        console.print("[bold]Optimizing portfolio...[/bold]")
        # Sort by expected profit per coin invested
        # = (net_profit × op_ratio) / buy_price
        # This favors cards where you get the most expected return per coin
        for s in scored:
            s["expected_profit"] = s["net_profit"] * s["op_ratio"]
            s["efficiency"] = s["expected_profit"] / s["buy_price"] if s["buy_price"] > 0 else 0
        scored.sort(key=lambda s: s["efficiency"], reverse=True)

        selected = []
        total_used = 0
        used_ids = set()

        for entry in scored:
            if len(selected) >= TARGET_PLAYER_COUNT:
                break
            pid = entry["player"].resource_id
            if pid in used_ids:
                continue
            cost = entry["buy_price"]
            if total_used + cost > budget:
                continue
            selected.append(entry)
            used_ids.add(pid)
            total_used += cost

        # Swap loop
        swaps = 0
        while len(selected) < TARGET_PLAYER_COUNT and swaps < 100:
            if not selected:
                break
            exp_idx = max(range(len(selected)), key=lambda i: selected[i]["buy_price"])
            expensive = selected[exp_idx]
            freed = expensive["buy_price"]
            removed_ep = expensive["expected_profit"]

            replacements = []
            repl_ep = 0
            repl_cost = 0
            temp_used = {s["player"].resource_id for s in selected} - {expensive["player"].resource_id}

            for s in scored:
                pid = s["player"].resource_id
                if pid in temp_used:
                    continue
                if repl_cost + s["buy_price"] <= freed:
                    replacements.append(s)
                    repl_ep += s["expected_profit"]
                    repl_cost += s["buy_price"]
                    temp_used.add(pid)

            if len(replacements) >= 2 and repl_ep > removed_ep:
                used_ids.discard(expensive["player"].resource_id)
                selected.pop(exp_idx)
                total_used -= freed
                for r in replacements:
                    selected.append(r)
                    used_ids.add(r["player"].resource_id)
                    total_used += r["buy_price"]
                swaps += 1
            else:
                break

        # Backfill
        remaining = budget - total_used
        for s in scored:
            if len(selected) >= TARGET_PLAYER_COUNT:
                break
            pid = s["player"].resource_id
            if pid in used_ids:
                continue
            if s["buy_price"] <= remaining:
                selected.append(s)
                used_ids.add(pid)
                total_used += s["buy_price"]
                remaining -= s["buy_price"]

        selected.sort(key=lambda s: s["expected_profit"], reverse=True)

        # ── Step 5: Display + Export ─────────────────────────────────
        display_results(selected, budget, total_used)
        csv_path = export_csv(selected, budget, total_used)
        console.print(f"\n[bold green]Exported:[/bold green] {csv_path}")

    finally:
        await client.stop()


def display_results(selected, budget, total_used):
    if not selected:
        console.print("[red]No players selected.[/red]")
        return

    total_profit = sum(s["net_profit"] for s in selected)
    total_expected = sum(s["expected_profit"] for s in selected)

    summary = Text()
    summary.append(f"Budget: {budget:,}", style="bold")
    summary.append(f"  |  Used: {total_used:,}")
    summary.append(f"  |  Profit/sell: {total_profit:,}", style="bold green")
    summary.append(f" ({total_profit/total_used:.1%})" if total_used else "")
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


def export_csv(selected, budget, total_used):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = f"op_sell_list_{ts}.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "Rank", "Player", "Rating", "Position", "League", "Club",
            "Buy", "Sell", "Profit", "Profit%", "Margin",
            "OP Sales/24h", "OP Sales", "Total Sales", "OP Ratio",
            "Sales/hr", "Data Span",
        ])
        for i, s in enumerate(selected):
            p = s["player"]
            w.writerow([
                i+1, p.name, p.rating, p.position, p.league, p.club,
                s["buy_price"], s["sell_price"], s["net_profit"],
                f"{s['net_profit']/s['buy_price']:.2%}", f"{s['margin_pct']}%",
                f"{s['op_sales_24h']:.0f}", s["op_sales"], s["total_sales"],
                f"{s['op_ratio']:.2%}", s["sales_per_hour"],
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
