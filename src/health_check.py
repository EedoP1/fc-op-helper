"""FUTBIN health monitor CLI — validates our DB data against FUTBIN reality.

Picks random scored players from the database, fetches their FUTBIN sales/listing
data, and compares against our stored metrics to produce an audit report.

Usage:
    python -m src.health_check --count 10 --verbose
"""

import logging
import os
import sqlite3
import statistics
from datetime import datetime, timedelta, timezone

import click
from rich.console import Console
from rich.table import Table

from src.futbin_client import FutbinClient

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────

DB_PATH = "D:/op-seller/op_seller.db"

# Health score weights
WEIGHT_SELL_THROUGH = 0.40
WEIGHT_PRICE_ACCURACY = 0.30
WEIGHT_LISTING_COUNT = 0.15
WEIGHT_PRICE_RANGE = 0.15


# ── Database helpers ──────────────────────────────────────────────────

def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Add futbin_id column to players table if not present, and create health_checks table."""
    try:
        conn.execute("ALTER TABLE players ADD COLUMN futbin_id INTEGER")
        conn.commit()
        logger.info("Added futbin_id column to players table")
    except sqlite3.OperationalError:
        # Column already exists
        pass

    conn.execute("""
        CREATE TABLE IF NOT EXISTS health_checks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ea_id INTEGER,
            checked_at TEXT,
            our_sell_rate REAL,
            futbin_sell_rate REAL,
            our_median_price INTEGER,
            futbin_median_price INTEGER,
            health_score REAL
        )
    """)
    conn.commit()


def _select_players(conn: sqlite3.Connection, count: int) -> list[dict]:
    """Select a mix of players with and without cached futbin_id.

    Tries 50/50 split: half with known futbin_id (fast), half without (to build cache).

    Args:
        conn: SQLite connection.
        count: Total number of players to select.

    Returns:
        List of dicts with ea_id, name, futbin_id.
    """
    half = count // 2
    rest = count - half

    # Players with cached futbin_id
    with_id = conn.execute(
        "SELECT ea_id, name, futbin_id FROM players "
        "WHERE is_active = 1 AND futbin_id IS NOT NULL "
        "ORDER BY RANDOM() LIMIT ?",
        (half,),
    ).fetchall()

    # Players without futbin_id
    without_id = conn.execute(
        "SELECT ea_id, name, futbin_id FROM players "
        "WHERE is_active = 1 AND futbin_id IS NULL "
        "ORDER BY RANDOM() LIMIT ?",
        (rest,),
    ).fetchall()

    # If one group has fewer, take more from the other
    combined = with_id + without_id
    if len(combined) < count:
        existing_ids = {r[0] for r in combined}
        extra = conn.execute(
            "SELECT ea_id, name, futbin_id FROM players "
            "WHERE is_active = 1 AND ea_id NOT IN ({}) "
            "ORDER BY RANDOM() LIMIT ?".format(
                ",".join(str(eid) for eid in existing_ids) or "0"
            ),
            (count - len(combined),),
        ).fetchall()
        combined.extend(extra)

    return [
        {"ea_id": r[0], "name": r[1], "futbin_id": r[2]}
        for r in combined
    ]


def _get_our_data(conn: sqlite3.Connection, ea_id: int, cutoff_iso: str | None = None) -> dict:
    """Query our DB for comparison data within a time window.

    Args:
        conn: SQLite connection.
        ea_id: Player EA ID.
        cutoff_iso: ISO timestamp cutoff. Only data after this time is included.
            If None, defaults to 48 hours ago.

    Returns:
        Dict with our_sold, our_expired, our_sell_rate, our_median_price,
        our_listing_count, our_min_price, our_max_price.
    """
    if cutoff_iso is None:
        cutoff_iso = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    cutoff = cutoff_iso

    # Listing observations: sold vs expired
    sold_count = conn.execute(
        "SELECT COUNT(*) FROM listing_observations "
        "WHERE ea_id = ? AND first_seen_at > ? AND outcome = 'sold'",
        (ea_id, cutoff),
    ).fetchone()[0]

    expired_count = conn.execute(
        "SELECT COUNT(*) FROM listing_observations "
        "WHERE ea_id = ? AND first_seen_at > ? AND outcome = 'expired'",
        (ea_id, cutoff),
    ).fetchone()[0]

    total = sold_count + expired_count
    sell_rate = sold_count / total if total > 0 else 0.0

    # Listing prices for range
    listing_prices = [
        r[0] for r in conn.execute(
            "SELECT buy_now_price FROM listing_observations "
            "WHERE ea_id = ? AND first_seen_at > ?",
            (ea_id, cutoff),
        ).fetchall()
    ]

    # Snapshot sales prices (via market_snapshots join)
    sale_prices = [
        r[0] for r in conn.execute(
            "SELECT ss.sold_price FROM snapshot_sales ss "
            "JOIN market_snapshots ms ON ss.snapshot_id = ms.id "
            "WHERE ms.ea_id = ? AND ss.sold_at > ?",
            (ea_id, cutoff),
        ).fetchall()
    ]

    median_price = int(statistics.median(sale_prices)) if sale_prices else 0

    # Latest market snapshot
    latest = conn.execute(
        "SELECT current_lowest_bin, listing_count FROM market_snapshots "
        "WHERE ea_id = ? ORDER BY captured_at DESC LIMIT 1",
        (ea_id,),
    ).fetchone()

    listing_count = latest[1] if latest else 0

    return {
        "our_sold": sold_count,
        "our_expired": expired_count,
        "our_sell_rate": sell_rate,
        "our_median_price": median_price,
        "our_listing_count": listing_count,
        "our_min_price": min(listing_prices) if listing_prices else 0,
        "our_max_price": max(listing_prices) if listing_prices else 0,
    }


def _get_futbin_data(sales: list[dict]) -> dict:
    """Compute FUTBIN metrics from parsed sales data.

    Args:
        sales: List of sale dicts from FutbinClient.

    Returns:
        Dict with futbin_sold, futbin_expired, futbin_sell_rate,
        futbin_median_price, futbin_total, futbin_min_listed, futbin_max_listed,
        futbin_earliest, futbin_latest (datetime or None).
    """
    if not sales:
        return {
            "futbin_sold": 0,
            "futbin_expired": 0,
            "futbin_sell_rate": 0.0,
            "futbin_median_price": 0,
            "futbin_total": 0,
            "futbin_min_listed": 0,
            "futbin_max_listed": 0,
            "futbin_earliest": None,
            "futbin_latest": None,
        }

    sold = [s for s in sales if s["sold_for"] > 0]
    expired = [s for s in sales if s["sold_for"] == 0]
    total = len(sold) + len(expired)
    sell_rate = len(sold) / total if total > 0 else 0.0

    sold_prices = [s["sold_for"] for s in sold]
    median_price = int(statistics.median(sold_prices)) if sold_prices else 0

    listed_prices = [s["listed_for"] for s in sales if s["listed_for"] > 0]

    # Extract time range from FUTBIN data
    dates = [s["date"] for s in sales if s.get("date") is not None]
    futbin_earliest = min(dates) if dates else None
    futbin_latest = max(dates) if dates else None

    return {
        "futbin_sold": len(sold),
        "futbin_expired": len(expired),
        "futbin_sell_rate": sell_rate,
        "futbin_median_price": median_price,
        "futbin_total": total,
        "futbin_min_listed": min(listed_prices) if listed_prices else 0,
        "futbin_max_listed": max(listed_prices) if listed_prices else 0,
        "futbin_earliest": futbin_earliest,
        "futbin_latest": futbin_latest,
    }


# ── Health score computation ─────────────────────────────────────────

def compute_health_score(our: dict, futbin: dict) -> float:
    """Compute a 0-100 health score comparing our data against FUTBIN.

    Weights: sell-through 40%, price accuracy 30%, listing count 15%, price range 15%.

    Args:
        our: Dict from _get_our_data.
        futbin: Dict from _get_futbin_data.

    Returns:
        Health score from 0 to 100.
    """
    # Sell-through rate accuracy (40%)
    if futbin["futbin_total"] == 0:
        sell_score = 100.0 if (our["our_sold"] + our["our_expired"]) == 0 else 50.0
    else:
        delta = abs(our["our_sell_rate"] - futbin["futbin_sell_rate"])
        sell_score = max(0.0, 100.0 - (delta * 500.0))  # 20% delta = 0 score

    # Price accuracy (30%)
    if futbin["futbin_median_price"] == 0:
        price_score = 100.0 if our["our_median_price"] == 0 else 50.0
    else:
        price_delta = abs(our["our_median_price"] - futbin["futbin_median_price"])
        price_pct = price_delta / futbin["futbin_median_price"]
        price_score = max(0.0, 100.0 - (price_pct * 1000.0))  # 10% = 0 score

    # Listing count ratio (15%)
    if futbin["futbin_total"] == 0:
        count_score = 100.0 if our["our_listing_count"] == 0 else 50.0
    else:
        # Compare our listing count to FUTBIN total listings
        if our["our_listing_count"] == 0:
            count_score = 0.0
        else:
            ratio = min(our["our_listing_count"], futbin["futbin_total"]) / max(
                our["our_listing_count"], futbin["futbin_total"]
            )
            count_score = ratio * 100.0

    # Price range match (15%)
    if futbin["futbin_min_listed"] == 0 and futbin["futbin_max_listed"] == 0:
        range_score = 100.0 if our["our_min_price"] == 0 else 50.0
    else:
        min_delta = abs(our["our_min_price"] - futbin["futbin_min_listed"])
        max_delta = abs(our["our_max_price"] - futbin["futbin_max_listed"])
        avg_ref = (futbin["futbin_min_listed"] + futbin["futbin_max_listed"]) / 2
        if avg_ref > 0:
            range_pct = ((min_delta + max_delta) / 2) / avg_ref
            range_score = max(0.0, 100.0 - (range_pct * 500.0))
        else:
            range_score = 50.0

    score = (
        sell_score * WEIGHT_SELL_THROUGH
        + price_score * WEIGHT_PRICE_ACCURACY
        + count_score * WEIGHT_LISTING_COUNT
        + range_score * WEIGHT_PRICE_RANGE
    )
    return round(min(100.0, max(0.0, score)), 1)


# ── CLI entry point ──────────────────────────────────────────────────

@click.command()
@click.option("--count", default=10, help="Number of players to check (default 10).")
@click.option("--verbose", is_flag=True, help="Print detailed per-player breakdown.")
def main(count: int, verbose: bool) -> None:
    """Run FUTBIN health check against our DB data."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )
    # Suppress noisy loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    import sys, io
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    console = Console()

    # Check DB exists
    if not os.path.exists(DB_PATH):
        console.print(
            f"[red]Database not found at {DB_PATH}[/red]\n"
            "Start the server first to create the database: "
            "python -m src.server.main",
        )
        return

    conn = sqlite3.connect(DB_PATH, timeout=30)
    try:
        _ensure_schema(conn)
        players = _select_players(conn, count)

        if not players:
            console.print("[red]No active players found in database.[/red]")
            return

        console.print(f"\n[bold]FUTBIN Health Check[/bold] - checking {len(players)} players\n")

        client = FutbinClient()
        results: list[dict] = []

        try:
            for i, player in enumerate(players, 1):
                ea_id = player["ea_id"]
                name = player["name"]
                futbin_id = player["futbin_id"]

                # Skip players with no real name (ea_id as string or empty)
                if not name or name.isdigit():
                    logger.warning(
                        "Player %d has no real name (name='%s'), skipping",
                        ea_id, name,
                    )
                    continue

                safe_name = name.encode("ascii", errors="replace").decode("ascii")
                console.print(
                    f"  [{i}/{len(players)}] {safe_name} (EA {ea_id})...",
                    end=" ",
                )

                # Resolve futbin_id if not cached
                if futbin_id is None:
                    futbin_id = client.search_player(name)
                    if futbin_id is not None:
                        conn.execute(
                            "UPDATE players SET futbin_id = ? WHERE ea_id = ?",
                            (futbin_id, ea_id),
                        )
                        conn.commit()
                    else:
                        console.print("[yellow]not found on FUTBIN[/yellow]")
                        continue

                # Fetch FUTBIN sales
                sales = client.fetch_sales_page(futbin_id, name)

                # Find overlapping time window between FUTBIN and our DB
                futbin_all = _get_futbin_data(sales)

                # Get our DB's earliest observation for this player
                our_earliest_row = conn.execute(
                    "SELECT MIN(first_seen_at) FROM listing_observations "
                    "WHERE ea_id = ? AND outcome IS NOT NULL",
                    (ea_id,),
                ).fetchone()
                our_earliest = our_earliest_row[0] if our_earliest_row and our_earliest_row[0] else None

                # Determine overlap cutoff: whichever started later
                overlap_cutoff = None
                if futbin_all["futbin_earliest"] and our_earliest:
                    fb_iso = futbin_all["futbin_earliest"].isoformat()
                    overlap_cutoff = max(fb_iso, our_earliest)
                elif futbin_all["futbin_earliest"]:
                    overlap_cutoff = futbin_all["futbin_earliest"].isoformat()
                elif our_earliest:
                    overlap_cutoff = our_earliest

                # Filter FUTBIN sales to overlap window
                if overlap_cutoff and futbin_all["futbin_earliest"]:
                    from datetime import datetime as dt
                    try:
                        cutoff_dt = dt.fromisoformat(overlap_cutoff)
                    except (ValueError, TypeError):
                        cutoff_dt = None
                    if cutoff_dt:
                        filtered_sales = [
                            s for s in sales
                            if s.get("date") is not None and s["date"] >= cutoff_dt
                        ]
                        futbin = _get_futbin_data(filtered_sales)
                    else:
                        futbin = futbin_all
                else:
                    futbin = futbin_all

                our = _get_our_data(conn, ea_id, cutoff_iso=overlap_cutoff)

                # Compute health score
                score = compute_health_score(our, futbin)

                result = {
                    "name": name,
                    "ea_id": ea_id,
                    "futbin_id": futbin_id,
                    "our": our,
                    "futbin": futbin,
                    "health_score": score,
                }
                results.append(result)

                # Store in DB
                conn.execute(
                    "INSERT INTO health_checks "
                    "(ea_id, checked_at, our_sell_rate, futbin_sell_rate, "
                    "our_median_price, futbin_median_price, health_score) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        ea_id,
                        datetime.now(timezone.utc).isoformat(),
                        our["our_sell_rate"],
                        futbin["futbin_sell_rate"],
                        our["our_median_price"],
                        futbin["futbin_median_price"],
                        score,
                    ),
                )
                conn.commit()

                # Color-code score
                if score >= 80:
                    color = "green"
                elif score >= 50:
                    color = "yellow"
                else:
                    color = "red"
                console.print(f"[{color}]{score:.0f}[/{color}]")

        finally:
            client.close()

        # Display results table
        if results:
            _display_results(console, results, verbose)
        else:
            console.print("[yellow]No players could be checked.[/yellow]")

    finally:
        conn.close()


def _display_results(console: Console, results: list[dict], verbose: bool) -> None:
    """Display health check results as a rich table.

    Args:
        console: Rich console for output.
        results: List of result dicts from the health check.
        verbose: If True, show detailed per-player breakdown.
    """
    table = Table(title="\nHealth Check Results")
    table.add_column("Player", style="cyan")
    table.add_column("EA ID", justify="right")
    table.add_column("FUTBIN ID", justify="right")
    table.add_column("Sell-Through\n(Ours / FUTBIN)", justify="center")
    table.add_column("Price Accuracy\n(Ours / FUTBIN)", justify="center")
    table.add_column("Listing Count", justify="right")
    table.add_column("Health Score", justify="right")

    for r in results:
        our = r["our"]
        fb = r["futbin"]
        score = r["health_score"]

        # Color-code score
        if score >= 80:
            score_str = f"[green]{score:.0f}[/green]"
        elif score >= 50:
            score_str = f"[yellow]{score:.0f}[/yellow]"
        else:
            score_str = f"[red]{score:.0f}[/red]"

        sell_through = f"{our['our_sell_rate']:.0%} / {fb['futbin_sell_rate']:.0%}"
        price_acc = f"{our['our_median_price']:,} / {fb['futbin_median_price']:,}"

        table.add_row(
            r["name"],
            str(r["ea_id"]),
            str(r["futbin_id"]),
            sell_through,
            price_acc,
            str(our["our_listing_count"]),
            score_str,
        )

    console.print(table)

    # Overall health score
    avg_score = sum(r["health_score"] for r in results) / len(results)
    if avg_score >= 80:
        color = "green"
    elif avg_score >= 50:
        color = "yellow"
    else:
        color = "red"

    console.print(
        f"\n[bold]Overall Health Score: [{color}]{avg_score:.1f} / 100[/{color}][/bold]"
    )
    console.print(f"Players checked: {len(results)}\n")

    if verbose:
        console.print("[bold]Detailed Breakdown:[/bold]\n")
        for r in results:
            our = r["our"]
            fb = r["futbin"]
            console.print(f"  [cyan]{r['name']}[/cyan] (EA {r['ea_id']}, FUTBIN {r['futbin_id']})")
            console.print(f"    Sell-through: ours={our['our_sell_rate']:.1%} "
                          f"({our['our_sold']} sold, {our['our_expired']} expired) "
                          f"vs FUTBIN={fb['futbin_sell_rate']:.1%} "
                          f"({fb['futbin_sold']} sold, {fb['futbin_expired']} expired)")
            console.print(f"    Median price: ours={our['our_median_price']:,} "
                          f"vs FUTBIN={fb['futbin_median_price']:,}")
            console.print(f"    Price range: ours={our['our_min_price']:,}-{our['our_max_price']:,} "
                          f"vs FUTBIN={fb['futbin_min_listed']:,}-{fb['futbin_max_listed']:,}")
            console.print(f"    Listing count: ours={our['our_listing_count']}")
            console.print(f"    Health score: {r['health_score']:.1f}\n")


if __name__ == "__main__":
    main()
