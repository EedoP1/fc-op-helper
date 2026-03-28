"""FUTBIN health monitor CLI — validates our DB data against FUTBIN reality.

Picks random scored players from the database, fetches their FUTBIN sales/listing
data, and compares listing counts by price in the overlapping time window.

Usage:
    python -m src.health_check --count 10 --verbose
"""

import asyncio
import logging
from collections import Counter
from datetime import datetime, timedelta, timezone

import click
from rich.console import Console
from rich.table import Table
from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.futbin_client import FutbinClient
from src.server.db import create_engine, create_session_factory
from src.server.models_db import PlayerRecord, ListingObservation
from src.config import DATABASE_URL

logger = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────

def _to_db_fmt(ts: str) -> str:
    """Normalise any ISO or DB timestamp to 'YYYY-MM-DD HH:MM:SS' format."""
    return ts.replace("T", " ").split("+")[0].split("Z")[0]


# ── Database helpers ──────────────────────────────────────────────────

async def _select_players(session: AsyncSession, count: int) -> list[dict]:
    """Select active players randomly for health check."""
    stmt = (
        select(PlayerRecord.ea_id, PlayerRecord.name)
        .where(PlayerRecord.is_active == True)  # noqa: E712
        .order_by(func.random())
        .limit(count)
    )
    result = await session.execute(stmt)
    return [{"ea_id": row.ea_id, "name": row.name, "futbin_id": None} for row in result]


async def _get_our_listings(session: AsyncSession, ea_id: int, cutoff: str) -> list[dict]:
    """Get resolved listing observations for a player after cutoff.

    Filters by resolved_at (when the listing disappeared / auction ended)
    rather than first_seen_at (when we first observed it). This aligns with
    FUTBIN's date field which records when the auction completed, not when
    it was first listed.

    Returns list of dicts with price and outcome (sold/expired).
    """
    stmt = (
        select(ListingObservation.buy_now_price, ListingObservation.outcome)
        .where(
            ListingObservation.ea_id == ea_id,
            ListingObservation.resolved_at >= cutoff,
            ListingObservation.outcome != None,  # noqa: E711
        )
    )
    result = await session.execute(stmt)
    return [{"price": row.buy_now_price, "outcome": row.outcome} for row in result]


def _get_futbin_listings(sales: list[dict], cutoff_dt: datetime | None) -> list[dict]:
    """Filter FUTBIN sales to the overlap window.

    Returns list of dicts with price (listed_for) and outcome.
    """
    result = []
    for s in sales:
        if cutoff_dt and s.get("date") is not None and s["date"] < cutoff_dt:
            continue
        outcome = "sold" if s["sold_for"] > 0 else "expired"
        result.append({"price": s["listed_for"], "outcome": outcome})
    return result


def _price_bucket(price: int) -> int:
    """Round a price to a bucket for fuzzy matching.

    Rounds to nearest 2% of the price value. This accounts for the fact that
    FUTBIN and our DB may record slightly different prices for the same listing
    (e.g. 17,750 vs 18,000).
    """
    if price <= 0:
        return 0
    step = max(500, int(price * 0.02 / 500) * 500)  # 2% rounded to 500s, min 500
    return (price // step) * step


def _compare_listings(our: list[dict], futbin: list[dict]) -> dict:
    """Compare listings by price bucket between our DB and FUTBIN.

    Prices are bucketed (rounded to ~2%) for fuzzy matching since the same
    listing can show slightly different prices across sources. OP listings
    far above FUTBIN's price range are excluded.

    Returns dict with match counts, per-bucket breakdown, and excluded count.
    """
    # Derive market-price ceiling from FUTBIN's price range.
    fb_prices = [item["price"] for item in futbin if item["price"] > 0]
    if fb_prices:
        price_ceiling = int(max(fb_prices) * 1.3)
        our_filtered = [item for item in our if item["price"] <= price_ceiling]
    else:
        our_filtered = our
    our_excluded = len(our) - len(our_filtered)

    # Bucket both sides
    our_buckets = Counter(_price_bucket(item["price"]) for item in our_filtered)
    fb_buckets = Counter(_price_bucket(item["price"]) for item in futbin)

    our_outcome_by_bucket: dict[int, Counter] = {}
    for item in our_filtered:
        b = _price_bucket(item["price"])
        our_outcome_by_bucket.setdefault(b, Counter())[item["outcome"]] += 1

    fb_outcome_by_bucket: dict[int, Counter] = {}
    for item in futbin:
        b = _price_bucket(item["price"])
        fb_outcome_by_bucket.setdefault(b, Counter())[item["outcome"]] += 1

    all_buckets = sorted(set(our_buckets) | set(fb_buckets))

    rows = []
    for bucket in all_buckets:
        ours_count = our_buckets.get(bucket, 0)
        fb_count = fb_buckets.get(bucket, 0)
        our_oc = our_outcome_by_bucket.get(bucket, Counter())
        fb_oc = fb_outcome_by_bucket.get(bucket, Counter())

        if ours_count > 0 and fb_count > 0:
            where = "both"
        elif ours_count > 0:
            where = "only_ours"
        else:
            where = "only_futbin"

        rows.append({
            "price": bucket,
            "ours": ours_count,
            "futbin": fb_count,
            "our_sold": our_oc.get("sold", 0),
            "our_expired": our_oc.get("expired", 0),
            "fb_sold": fb_oc.get("sold", 0),
            "fb_expired": fb_oc.get("expired", 0),
            "where": where,
        })

    total_ours = sum(our_buckets.values())
    total_fb = sum(fb_buckets.values())
    both_buckets = set(our_buckets) & set(fb_buckets)
    matched = sum(min(our_buckets[b], fb_buckets[b]) for b in both_buckets)

    return {
        "rows": rows,
        "total_ours": total_ours,
        "total_futbin": total_fb,
        "matched": matched,
        "only_ours": total_ours - matched,
        "only_futbin": total_fb - matched,
        "our_excluded_op": our_excluded,
        "our_sold": sum(1 for item in our_filtered if item["outcome"] == "sold"),
        "our_expired": sum(1 for item in our_filtered if item["outcome"] == "expired"),
        "fb_sold": sum(1 for item in futbin if item["outcome"] == "sold"),
        "fb_expired": sum(1 for item in futbin if item["outcome"] == "expired"),
    }


# ── CLI entry point ──────────────────────────────────────────────────

@click.command()
@click.option("--count", default=10, help="Number of players to check (default 10).")
@click.option("--verbose", is_flag=True, help="Print per-price listing breakdown.")
def main(count: int, verbose: bool) -> None:
    """Run FUTBIN health check against our DB data."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    asyncio.run(_async_main(count, verbose))


async def _async_main(count: int, verbose: bool) -> None:
    """Async health check implementation."""
    import sys, io
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    console = Console()

    engine = create_engine()
    session_factory = create_session_factory(engine)

    try:
        async with session_factory() as session:
            players = await _select_players(session, count)

        if not players:
            console.print("[red]No active players found in database.[/red]")
            return

        console.print(f"\n[bold]FUTBIN Health Check[/bold] — checking {len(players)} players\n")

        client = FutbinClient()
        results: list[dict] = []

        try:
            for i, player in enumerate(players, 1):
                ea_id = player["ea_id"]
                name = player["name"]

                if not name or name.isdigit():
                    continue

                safe_name = name.encode("ascii", errors="replace").decode("ascii")
                console.print(f"  [{i}/{len(players)}] {safe_name} (EA {ea_id})...", end=" ")

                # Resolve futbin_id via search (no column to cache to)
                futbin_id = client.search_player(name, ea_id=ea_id)
                if futbin_id is None:
                    console.print("[yellow]not found on FUTBIN[/yellow]")
                    continue

                # Fetch FUTBIN sales
                sales = client.fetch_sales_page(futbin_id, name)
                if not sales:
                    console.print("[yellow]no sales data[/yellow]")
                    continue

                # Find FUTBIN time range
                fb_dates = [s["date"] for s in sales if s.get("date") is not None]
                fb_earliest = min(fb_dates) if fb_dates else None

                # Find our earliest resolved observation by resolved_at — this is
                # when the listing disappeared (auction ended), which aligns with
                # FUTBIN's date field. Using first_seen_at here would be wrong
                # because a listing could be observed hours before it ends.
                async with session_factory() as session:
                    earliest_stmt = (
                        select(func.min(ListingObservation.resolved_at))
                        .where(
                            ListingObservation.ea_id == ea_id,
                            ListingObservation.outcome != None,  # noqa: E711
                            ListingObservation.resolved_at != None,  # noqa: E711
                        )
                    )
                    earliest_result = await session.execute(earliest_stmt)
                    our_earliest = earliest_result.scalar()

                # Overlap cutoff: whichever started later
                overlap_cutoff = None
                cutoff_dt = None
                if fb_earliest and our_earliest:
                    fb_db = _to_db_fmt(fb_earliest.isoformat())
                    our_db = _to_db_fmt(str(our_earliest))
                    overlap_cutoff = max(fb_db, our_db)
                elif fb_earliest:
                    overlap_cutoff = _to_db_fmt(fb_earliest.isoformat())
                elif our_earliest:
                    overlap_cutoff = _to_db_fmt(str(our_earliest))

                if overlap_cutoff:
                    try:
                        cutoff_dt = datetime.fromisoformat(overlap_cutoff)
                    except (ValueError, TypeError):
                        cutoff_dt = None

                # Get listings from both sources in the overlap window
                async with session_factory() as session:
                    our_listings = await _get_our_listings(session, ea_id, overlap_cutoff or "2000-01-01")

                if not our_listings:
                    console.print("[yellow]no resolved observations yet[/yellow]")
                    continue
                fb_listings = _get_futbin_listings(sales, cutoff_dt)

                comparison = _compare_listings(our_listings, fb_listings)
                comparison["overlap_cutoff"] = overlap_cutoff

                result = {
                    "name": name,
                    "ea_id": ea_id,
                    "futbin_id": futbin_id,
                    "comparison": comparison,
                }
                results.append(result)

                # Quick status
                c = comparison
                console.print(
                    f"ours={c['total_ours']} fb={c['total_futbin']} "
                    f"matched={c['matched']}"
                )

        finally:
            client.close()

        if results:
            _display_results(console, results, verbose)
        else:
            console.print("[yellow]No players could be checked.[/yellow]")

    finally:
        await engine.dispose()


def _display_results(console: Console, results: list[dict], verbose: bool) -> None:
    """Display listing comparison results."""
    # Summary table
    table = Table(title="\nListing Comparison (overlap window)")
    table.add_column("Player", style="cyan")
    table.add_column("Ours\n(sold/exp)", justify="right")
    table.add_column("FUTBIN\n(sold/exp)", justify="right")
    table.add_column("Matched", justify="right", style="green")
    table.add_column("Only Ours", justify="right", style="yellow")
    table.add_column("Only FUTBIN", justify="right", style="red")
    table.add_column("OP excl.", justify="right", style="dim")
    table.add_column("Overlap", justify="right")

    for r in results:
        c = r["comparison"]
        ours_str = f"{c['total_ours']} ({c['our_sold']}/{c['our_expired']})"
        fb_str = f"{c['total_futbin']} ({c['fb_sold']}/{c['fb_expired']})"

        total = max(c["total_ours"], c["total_futbin"])
        overlap_pct = f"{c['matched'] / total:.0%}" if total > 0 else "—"

        table.add_row(
            r["name"],
            ours_str,
            fb_str,
            str(c["matched"]),
            str(c["only_ours"]),
            str(c["only_futbin"]),
            str(c["our_excluded_op"]),
            overlap_pct,
        )

    console.print(table)

    # Totals
    total_ours = sum(r["comparison"]["total_ours"] for r in results)
    total_fb = sum(r["comparison"]["total_futbin"] for r in results)
    total_matched = sum(r["comparison"]["matched"] for r in results)
    total_max = max(total_ours, total_fb)
    overall_pct = f"{total_matched / total_max:.0%}" if total_max > 0 else "—"
    console.print(
        f"\n[bold]Totals:[/bold] ours={total_ours} futbin={total_fb} "
        f"matched={total_matched} ({overall_pct})"
    )

    # Per-price breakdown in verbose mode
    if verbose:
        console.print("\n[bold]Per-Price Breakdown:[/bold]")
        for r in results:
            c = r["comparison"]
            console.print(
                f"\n  [cyan]{r['name']}[/cyan] "
                f"(EA {r['ea_id']}, overlap from {c['overlap_cutoff'] or '—'})"
            )

            price_table = Table(show_header=True, box=None, padding=(0, 1))
            price_table.add_column("Bucket", justify="right", style="bold")
            price_table.add_column("Ours", justify="right")
            price_table.add_column("(S/E)", justify="left")
            price_table.add_column("FUTBIN", justify="right")
            price_table.add_column("(S/E)", justify="left")
            price_table.add_column("Where", justify="left")

            for row in c["rows"]:
                if row["where"] == "both":
                    where_str = "[green]both[/green]"
                elif row["where"] == "only_ours":
                    where_str = "[yellow]only ours[/yellow]"
                else:
                    where_str = "[red]only FUTBIN[/red]"

                price_table.add_row(
                    f"{row['price']:,}",
                    str(row["ours"]),
                    f"({row['our_sold']}/{row['our_expired']})",
                    str(row["futbin"]),
                    f"({row['fb_sold']}/{row['fb_expired']})",
                    where_str,
                )

            console.print(price_table)


if __name__ == "__main__":
    main()
