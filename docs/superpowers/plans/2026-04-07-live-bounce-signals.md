# Live Bounce Signals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** CLI tool that generates buy/sell signals for the new_card_bounce strategy using live OP-seller DB data.

**Architecture:** Single CLI module (`src/algo/live.py`) with three click commands (`scan`, `add`, `positions`). All data comes from Postgres (`market_snapshots`, `players` tables). Positions tracked in `positions.json`. A schema migration adds `created_at` to the `players` table, and the scanner stores it on each scan.

**Tech Stack:** Python 3.12, click, SQLAlchemy async, Rich tables for output.

---

### Task 1: Add `created_at` Column to PlayerRecord

**Files:**
- Modify: `src/server/models_db.py:8-30` (PlayerRecord class)

- [ ] **Step 1: Add the column to the ORM model**

In `src/server/models_db.py`, add to the `PlayerRecord` class after the `last_resolved_at` field:

```python
created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
```

- [ ] **Step 2: Run a migration to add the column to Postgres**

Run:
```bash
python -c "
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from src.config import DATABASE_URL

async def main():
    engine = create_async_engine(DATABASE_URL)
    async with engine.begin() as conn:
        await conn.execute(text('ALTER TABLE players ADD COLUMN IF NOT EXISTS created_at TIMESTAMP'))
    await engine.dispose()
    print('Column added')

asyncio.run(main())
"
```
Expected: `Column added`

- [ ] **Step 3: Commit**

```bash
git add src/server/models_db.py
git commit -m "feat(algo): add created_at column to players table"
```

---

### Task 2: Store `createdAt` During Scanner Scans

**Files:**
- Modify: `src/futgg_client.py` (return `createdAt` from definitions)
- Modify: `src/server/scanner.py` (save it to PlayerRecord)

- [ ] **Step 1: Return `createdAt` from `_build_player` context**

The scanner calls `get_player_market_data_sync` which internally calls `_build_player`. The raw `createdAt` field is available in the definition response but gets discarded. Add it to `PlayerMarketData`.

In `src/models.py`, add to `PlayerMarketData` after `max_price_range`:

```python
created_at: Optional[datetime] = None  # fut.gg createdAt — when EA released the card
```

- [ ] **Step 2: Populate `created_at` in `get_player_market_data`**

In `src/futgg_client.py`, in the `get_player_market_data` async method (around line 138), add parsing of `createdAt` from the definition and pass it to the `PlayerMarketData` constructor:

```python
created_at_raw = defn.get("createdAt")
created_at = None
if created_at_raw:
    try:
        created_at = datetime.fromisoformat(created_at_raw.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        pass
```

Then add `created_at=created_at` to the `PlayerMarketData(...)` constructor call.

- [ ] **Step 3: Populate `created_at` in `get_player_market_data_sync`**

Same change in the sync version (around line 220). Parse `createdAt` from `defn` the same way and pass to the `PlayerMarketData(...)` constructor.

- [ ] **Step 4: Save `created_at` to PlayerRecord in scanner**

In `src/server/scanner.py`, in the `_persist_scan_result` section where it updates the `PlayerRecord` fields (around line 386-391 where `record.last_scanned_at = now` etc.), add:

```python
if market_data is not None and market_data.created_at is not None and record.created_at is None:
    record.created_at = market_data.created_at
```

This only writes once (when `record.created_at is None`) so it doesn't overwrite on every scan.

- [ ] **Step 5: Verify by checking a scanned player**

Run the scanner briefly or check a player:
```bash
python -c "
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import text
from src.config import DATABASE_URL

async def main():
    engine = create_async_engine(DATABASE_URL)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    async with sf() as s:
        r = await s.execute(text('SELECT ea_id, name, created_at FROM players WHERE created_at IS NOT NULL LIMIT 5'))
        rows = r.fetchall()
        print(f'Players with created_at: {len(rows)}')
        for row in rows:
            print(f'  {row[0]} | {row[1]} | {row[2]}')
    await engine.dispose()

asyncio.run(main())
"
```

If none yet (scanner hasn't run since the change), that's expected. The backfill in Task 3 will populate them.

- [ ] **Step 6: Commit**

```bash
git add src/models.py src/futgg_client.py src/server/scanner.py
git commit -m "feat(algo): store createdAt from fut.gg in players table"
```

---

### Task 3: Backfill `created_at` for Existing Players

**Files:**
- Create: `src/algo/backfill_created_at.py`

- [ ] **Step 1: Write the backfill script**

Create `src/algo/backfill_created_at.py`:

```python
"""One-time backfill: fetch createdAt from fut.gg for all players missing it."""
import asyncio
import logging

import click
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from src.config import DATABASE_URL
from src.futgg_client import FutGGClient

logger = logging.getLogger(__name__)


async def backfill(db_url: str):
    engine = create_async_engine(db_url)
    sf = async_sessionmaker(engine, expire_on_commit=False)

    async with sf() as s:
        r = await s.execute(text("SELECT ea_id FROM players WHERE created_at IS NULL"))
        ea_ids = [row[0] for row in r.fetchall()]

    logger.info(f"Backfilling created_at for {len(ea_ids)} players")

    client = FutGGClient()
    await client.start()

    updated = 0
    for i, ea_id in enumerate(ea_ids):
        defn = await client.get_player_definition(ea_id)
        if defn and defn.get("createdAt"):
            from datetime import datetime
            try:
                created_at = datetime.fromisoformat(defn["createdAt"].replace("Z", "+00:00"))
                async with sf() as s:
                    await s.execute(
                        text("UPDATE players SET created_at = :ca WHERE ea_id = :eid"),
                        {"ca": created_at, "eid": ea_id},
                    )
                    await s.commit()
                updated += 1
            except (ValueError, AttributeError):
                pass

        if (i + 1) % 50 == 0:
            logger.info(f"Progress: {i + 1}/{len(ea_ids)} ({updated} updated)")

    await client.stop()
    await engine.dispose()
    logger.info(f"Backfill complete: {updated}/{len(ea_ids)} updated")


@click.command()
@click.option("--db-url", default=DATABASE_URL)
def main(db_url):
    """Backfill created_at from fut.gg for all players."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    asyncio.run(backfill(db_url))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it**

```bash
python -m src.algo.backfill_created_at
```

Expected: logs showing progress, should take a few minutes for ~2100 players at 4 req/s.

- [ ] **Step 3: Verify**

```bash
python -c "
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import text
from src.config import DATABASE_URL

async def main():
    engine = create_async_engine(DATABASE_URL)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    async with sf() as s:
        r = await s.execute(text('SELECT COUNT(*) FROM players WHERE created_at IS NOT NULL'))
        print(f'Players with created_at: {r.scalar()}')
        r = await s.execute(text('SELECT COUNT(*) FROM players WHERE created_at IS NULL'))
        print(f'Players still NULL: {r.scalar()}')
    await engine.dispose()

asyncio.run(main())
"
```

- [ ] **Step 4: Commit**

```bash
git add src/algo/backfill_created_at.py
git commit -m "feat(algo): backfill script for players.created_at"
```

---

### Task 4: Build the Live Signal Generator CLI

**Files:**
- Create: `src/algo/live.py`

- [ ] **Step 1: Create the CLI skeleton with click group and three commands**

Create `src/algo/live.py`:

```python
"""Live new_card_bounce signal generator.

Generates buy/sell signals from the OP-seller DB using the backtested
new_card_bounce strategy (13K-61K, 5-18% bounce, 5% trailing stop).

Usage:
    python -m src.algo.live scan --budget 5000000
    python -m src.algo.live add <ea_id> <buy_price> <quantity>
    python -m src.algo.live positions
"""
import asyncio
import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from src.config import DATABASE_URL

logger = logging.getLogger(__name__)
console = Console()

POSITIONS_FILE = Path("positions.json")

# Strategy params (from backtesting: 13K-61K best all-rounder)
MIN_BOUNCE = 0.05
MAX_BOUNCE = 0.18
MIN_DAY = 3
MAX_DAY = 10
MIN_PRICE = 13_000
MAX_PRICE = 61_000
TRAILING_STOP = 0.05
MAX_HOLD_DAYS = 14
EA_TAX = 0.05


def _load_positions() -> list[dict]:
    if not POSITIONS_FILE.exists():
        return []
    with open(POSITIONS_FILE) as f:
        return json.load(f)


def _save_positions(positions: list[dict]):
    with open(POSITIONS_FILE, "w") as f:
        json.dump(positions, f, indent=2, default=str)


# ── Scan command ─────────────────────────────────────────────


async def _scan(budget: int, db_url: str):
    engine = create_async_engine(db_url)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    positions = _load_positions()
    now = datetime.now(timezone.utc)

    # ── Step 1: Sell signals ──
    sell_signals = []
    held_ea_ids = {p["ea_id"] for p in positions}

    if positions:
        async with sf() as s:
            for pos in positions:
                r = await s.execute(text(
                    "SELECT current_lowest_bin, captured_at FROM market_snapshots "
                    "WHERE ea_id = :eid ORDER BY captured_at DESC LIMIT 1"
                ), {"eid": pos["ea_id"]})
                row = r.fetchone()
                if not row:
                    continue
                current_price, last_snapshot = row

                # Warn if stale
                if isinstance(last_snapshot, str):
                    last_snapshot = datetime.fromisoformat(last_snapshot)
                if last_snapshot.tzinfo is None:
                    last_snapshot = last_snapshot.replace(tzinfo=timezone.utc)

                # Update peak
                if current_price > pos.get("peak_price", 0):
                    pos["peak_price"] = current_price

                peak = pos.get("peak_price", pos["buy_price"])
                drop_from_peak = (peak - current_price) / peak if peak > 0 else 0

                buy_time = datetime.fromisoformat(pos["buy_time"])
                if buy_time.tzinfo is None:
                    buy_time = buy_time.replace(tzinfo=timezone.utc)
                days_held = (now - buy_time).days

                reason = None
                if drop_from_peak >= TRAILING_STOP:
                    reason = f"trailing stop ({drop_from_peak:.1%} from peak {peak:,})"
                elif days_held >= MAX_HOLD_DAYS:
                    reason = f"max hold ({days_held}d)"

                if reason:
                    sell_signals.append({
                        "ea_id": pos["ea_id"],
                        "name": pos.get("name", str(pos["ea_id"])),
                        "quantity": pos["quantity"],
                        "buy_price": pos["buy_price"],
                        "current_price": current_price,
                        "reason": reason,
                    })

    # Print sell signals
    if sell_signals:
        table = Table(title="SELL Signals")
        table.add_column("EA ID", style="red")
        table.add_column("Name")
        table.add_column("Qty", justify="right")
        table.add_column("Buy", justify="right")
        table.add_column("Current", justify="right")
        table.add_column("P&L", justify="right")
        table.add_column("Reason")
        for sig in sell_signals:
            pnl = (sig["current_price"] * sig["quantity"] * (1 - EA_TAX)) - (sig["buy_price"] * sig["quantity"])
            table.add_row(
                str(sig["ea_id"]),
                sig["name"],
                f"{sig['quantity']:,}",
                f"{sig['buy_price']:,}",
                f"{sig['current_price']:,}",
                f"{int(pnl):,}",
                sig["reason"],
            )
        console.print(table)
    else:
        console.print("[dim]No sell signals.[/dim]")

    # ── Step 2: Available cash ──
    sell_ea_ids = {s["ea_id"] for s in sell_signals}
    held_cost = sum(
        p["buy_price"] * p["quantity"]
        for p in positions
        if p["ea_id"] not in sell_ea_ids
    )
    sell_revenue = sum(
        int(s["current_price"] * s["quantity"] * (1 - EA_TAX))
        for s in sell_signals
    )
    available = budget - held_cost + sell_revenue
    console.print(f"\nBudget: {budget:,} | Held: {held_cost:,} | Sell revenue: {sell_revenue:,} | [bold green]Available: {available:,}[/bold green]")

    if available <= 0:
        console.print("[yellow]No cash available for buys.[/yellow]")
        _save_positions(positions)
        await engine.dispose()
        return

    # ── Step 3: Buy signals ──
    async with sf() as s:
        # Get new cards (created_at within last 10 days)
        cutoff = now - timedelta(days=MAX_DAY)
        r = await s.execute(text(
            "SELECT ea_id, name, created_at FROM players "
            "WHERE created_at IS NOT NULL AND created_at >= :cutoff"
        ), {"cutoff": cutoff})
        new_cards = r.fetchall()

    if not new_cards:
        console.print("[dim]No new cards (created in last 10 days).[/dim]")
        _save_positions(positions)
        await engine.dispose()
        return

    console.print(f"\nFound {len(new_cards)} cards created in last {MAX_DAY} days")

    buy_signals = []
    async with sf() as s:
        for ea_id, name, created_at in new_cards:
            if ea_id in held_ea_ids:
                continue

            if isinstance(created_at, str):
                created_at = datetime.fromisoformat(created_at)
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)

            card_age = (now - created_at).days
            if card_age < MIN_DAY or card_age > MAX_DAY:
                continue

            # Get daily closing prices (last snapshot per UTC day)
            r = await s.execute(text(
                "SELECT DISTINCT ON (DATE(captured_at)) "
                "DATE(captured_at) as day, current_lowest_bin "
                "FROM market_snapshots "
                "WHERE ea_id = :eid "
                "ORDER BY DATE(captured_at) DESC, captured_at DESC "
                "LIMIT 2"
            ), {"eid": ea_id})
            rows = r.fetchall()

            if len(rows) < 2:
                continue

            today_price = rows[0][1]
            yesterday_price = rows[1][1]

            if yesterday_price <= 0:
                continue

            bounce = (today_price - yesterday_price) / yesterday_price

            if bounce < MIN_BOUNCE or bounce > MAX_BOUNCE:
                continue

            if today_price < MIN_PRICE or today_price > MAX_PRICE:
                continue

            buy_signals.append({
                "ea_id": ea_id,
                "name": name or str(ea_id),
                "price": today_price,
                "bounce": bounce,
                "card_age": card_age,
            })

    if not buy_signals:
        console.print("[dim]No buy signals today.[/dim]")
        _save_positions(positions)
        await engine.dispose()
        return

    # ── Step 4: Size positions ──
    per_card = available // len(buy_signals)
    table = Table(title="BUY Signals")
    table.add_column("EA ID", style="green")
    table.add_column("Name")
    table.add_column("Price", justify="right")
    table.add_column("Bounce", justify="right")
    table.add_column("Age", justify="right")
    table.add_column("Qty", justify="right")
    table.add_column("Cost", justify="right")

    for sig in buy_signals:
        qty = per_card // sig["price"] if sig["price"] > 0 else 0
        if qty < 1:
            continue
        cost = qty * sig["price"]
        sig["quantity"] = qty
        sig["cost"] = cost
        table.add_row(
            str(sig["ea_id"]),
            sig["name"],
            f"{sig['price']:,}",
            f"{sig['bounce']:.1%}",
            f"{sig['card_age']}d",
            f"{qty:,}",
            f"{cost:,}",
        )

    console.print(table)

    # Save updated peaks
    _save_positions(positions)

    # Check staleness
    async with sf() as s:
        r = await s.execute(text("SELECT MAX(captured_at) FROM market_snapshots"))
        latest = r.scalar()
        if latest:
            if isinstance(latest, str):
                latest = datetime.fromisoformat(latest)
            if latest.tzinfo is None:
                latest = latest.replace(tzinfo=timezone.utc)
            age_mins = (now - latest).total_seconds() / 60
            if age_mins > 60:
                console.print(f"\n[bold yellow]WARNING: Latest snapshot is {age_mins:.0f} min old. Scanner may not be running.[/bold yellow]")

    await engine.dispose()


# ── Add command ──────────────────────────────────────────────


async def _add(ea_id: int, buy_price: int, quantity: int, db_url: str):
    positions = _load_positions()

    if any(p["ea_id"] == ea_id for p in positions):
        console.print(f"[red]EA ID {ea_id} already in positions.json[/red]")
        return

    # Look up player name
    engine = create_async_engine(db_url)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    name = str(ea_id)
    async with sf() as s:
        r = await s.execute(text("SELECT name FROM players WHERE ea_id = :eid"), {"eid": ea_id})
        row = r.fetchone()
        if row:
            name = row[0]
    await engine.dispose()

    positions.append({
        "ea_id": ea_id,
        "name": name,
        "buy_price": buy_price,
        "quantity": quantity,
        "buy_time": datetime.now(timezone.utc).isoformat(),
        "peak_price": buy_price,
    })
    _save_positions(positions)
    console.print(f"[green]Added {name} ({ea_id}): {quantity}x @ {buy_price:,}[/green]")


# ── Positions command ────────────────────────────────────────


async def _positions(db_url: str):
    positions = _load_positions()
    if not positions:
        console.print("[dim]No positions.[/dim]")
        return

    engine = create_async_engine(db_url)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)

    table = Table(title="Current Positions")
    table.add_column("EA ID")
    table.add_column("Name")
    table.add_column("Qty", justify="right")
    table.add_column("Buy", justify="right")
    table.add_column("Current", justify="right")
    table.add_column("P&L", justify="right")
    table.add_column("Days", justify="right")
    table.add_column("Peak", justify="right")
    table.add_column("Stop Dist", justify="right")

    total_cost = 0
    total_value = 0

    async with sf() as s:
        for pos in positions:
            r = await s.execute(text(
                "SELECT current_lowest_bin FROM market_snapshots "
                "WHERE ea_id = :eid ORDER BY captured_at DESC LIMIT 1"
            ), {"eid": pos["ea_id"]})
            row = r.fetchone()
            current = row[0] if row else pos["buy_price"]

            buy_time = datetime.fromisoformat(pos["buy_time"])
            if buy_time.tzinfo is None:
                buy_time = buy_time.replace(tzinfo=timezone.utc)
            days = (now - buy_time).days

            peak = max(pos.get("peak_price", pos["buy_price"]), current)
            stop_dist = (peak - current) / peak if peak > 0 else 0
            pnl = int(current * pos["quantity"] * (1 - EA_TAX)) - (pos["buy_price"] * pos["quantity"])

            cost = pos["buy_price"] * pos["quantity"]
            value = int(current * pos["quantity"] * (1 - EA_TAX))
            total_cost += cost
            total_value += value

            pnl_style = "green" if pnl >= 0 else "red"
            stop_style = "yellow" if stop_dist >= TRAILING_STOP * 0.8 else "dim"

            table.add_row(
                str(pos["ea_id"]),
                pos.get("name", "?"),
                f"{pos['quantity']:,}",
                f"{pos['buy_price']:,}",
                f"{current:,}",
                f"[{pnl_style}]{pnl:,}[/{pnl_style}]",
                str(days),
                f"{peak:,}",
                f"[{stop_style}]{stop_dist:.1%}[/{stop_style}]",
            )

    console.print(table)
    total_pnl = total_value - total_cost
    pnl_style = "green" if total_pnl >= 0 else "red"
    console.print(f"Total cost: {total_cost:,} | Total value: {total_value:,} | [{pnl_style}]P&L: {total_pnl:,}[/{pnl_style}]")

    await engine.dispose()


# ── CLI ──────────────────────────────────────────────────────


@click.group()
def cli():
    """Live new_card_bounce signal generator."""
    pass


@cli.command()
@click.option("--budget", required=True, type=int, help="Total budget in coins")
@click.option("--db-url", default=DATABASE_URL)
def scan(budget, db_url):
    """Generate buy/sell signals."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    asyncio.run(_scan(budget, db_url))


@cli.command()
@click.argument("ea_id", type=int)
@click.argument("buy_price", type=int)
@click.argument("quantity", type=int)
@click.option("--db-url", default=DATABASE_URL)
def add(ea_id, buy_price, quantity, db_url):
    """Log a buy to positions.json."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    asyncio.run(_add(ea_id, buy_price, quantity, db_url))


@cli.command()
@click.option("--db-url", default=DATABASE_URL)
def positions(db_url):
    """Show current positions with live prices."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    asyncio.run(_positions(db_url))


if __name__ == "__main__":
    cli()
```

- [ ] **Step 2: Test the scan command (dry run)**

```bash
python -m src.algo.live scan --budget 5000000
```

Expected: prints sell signals (none if no positions), available cash, new cards found, and buy signals (if any cards match the bounce criteria).

- [ ] **Step 3: Test the add command**

```bash
python -m src.algo.live add 50332736 45000 10
```

Expected: `Added Alessandro Nesta (50332736): 10x @ 45,000`

- [ ] **Step 4: Test the positions command**

```bash
python -m src.algo.live positions
```

Expected: table showing the position just added with current price from DB.

- [ ] **Step 5: Clean up test position**

Delete or edit `positions.json` to remove the test entry.

- [ ] **Step 6: Commit**

```bash
git add src/algo/live.py
git commit -m "feat(algo): live new_card_bounce signal generator CLI"
```

---

### Task 5: Add `__main__` Entry Point

**Files:**
- Modify: `src/algo/__init__.py` (or confirm it exists)

- [ ] **Step 1: Ensure `src/algo/live.py` is runnable as module**

The file already has `if __name__ == "__main__": cli()` at the bottom. Verify it works:

```bash
python -m src.algo.live --help
```

Expected:
```
Usage: python -m src.algo.live [OPTIONS] COMMAND [ARGS]...

  Live new_card_bounce signal generator.

Commands:
  add        Log a buy to positions.json.
  positions  Show current positions with live prices.
  scan       Generate buy/sell signals.
```

- [ ] **Step 2: Commit (only if changes needed)**

If `src/algo/__init__.py` needed to be created:
```bash
git add src/algo/__init__.py
git commit -m "chore: add algo package init"
```
