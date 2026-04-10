# Dashboard Rebuild Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the web dashboard from scratch with Alpine.js, adding time filtering, profit rankings, stale card tracking, and sortable tables across 4 tabbed sections.

**Architecture:** Single HTML file with Alpine.js for reactivity. Backend gets 2 changes: `?since=` query param on profit/portfolio endpoints, and a new `/api/v1/portfolio/stale` endpoint. All sorting is client-side. Each tab section has its own independent time filter.

**Tech Stack:** Alpine.js 3.x (CDN), Chart.js 4.x (CDN), FastAPI, SQLAlchemy, PostgreSQL.

---

## File Map

| Action | File | Responsibility |
|--------|------|---------------|
| Modify | `src/server/api/profit.py` | Add `?since=` time filter to profit summary |
| Modify | `src/server/api/portfolio_status.py` | Add `?since=` time filter to portfolio status |
| Create | `src/server/api/stale.py` | New endpoint: `/api/v1/portfolio/stale` |
| Modify | `src/server/main.py` | Register stale router |
| Rewrite | `dashboard.html` | Complete rewrite with Alpine.js |
| Create | `tests/integration/test_stale_endpoint.py` | Integration tests for stale endpoint |
| Create | `tests/integration/test_time_filters.py` | Integration tests for `?since=` param |

---

## Task 1: Add `?since=` Time Filter to Profit Summary

**Files:**
- Modify: `src/server/api/profit.py:22-43`
- Create: `tests/integration/test_time_filters.py`

- [ ] **Step 1: Write the failing test for `?since=` on profit summary**

Create `tests/integration/test_time_filters.py`:

```python
"""Integration tests for ?since= time filtering on profit and portfolio endpoints."""
import pytest
import httpx


@pytest.mark.asyncio
async def test_profit_summary_since_param_accepted(client: httpx.AsyncClient):
    """GET /api/v1/profit/summary?since=24h returns 200."""
    resp = await client.get("/api/v1/profit/summary?since=24h")
    assert resp.status_code == 200
    data = resp.json()
    assert "totals" in data
    assert "per_player" in data


@pytest.mark.asyncio
async def test_profit_summary_since_all(client: httpx.AsyncClient):
    """GET /api/v1/profit/summary?since=all returns same as no param."""
    resp_all = await client.get("/api/v1/profit/summary?since=all")
    resp_none = await client.get("/api/v1/profit/summary")
    assert resp_all.status_code == 200
    assert resp_none.status_code == 200
    assert resp_all.json() == resp_none.json()


@pytest.mark.asyncio
async def test_profit_summary_invalid_since(client: httpx.AsyncClient):
    """GET /api/v1/profit/summary?since=bogus returns 422."""
    resp = await client.get("/api/v1/profit/summary?since=bogus")
    assert resp.status_code == 422
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker compose -f docker-compose.yml -f docker-compose.test.yml -p op_seller_test exec api echo "containers running" && pytest tests/integration/test_time_filters.py -v`

Expected: FAIL — `since` param not recognized or endpoint doesn't validate it.

- [ ] **Step 3: Implement `?since=` on profit summary**

Edit `src/server/api/profit.py`. Replace the function signature and add time filtering:

```python
"""Profit summary endpoint.

Buy-anchored FIFO profit calculation:
- Each buy record is matched to the next chronological sell for that ea_id.
- Matched pairs -> realized P&L.  Unmatched buys -> unrealized P&L.
- Sells without buys are ignored (ghost / pre-bot events).
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Query, Request, HTTPException
from sqlalchemy import select, func

from src.server.models_db import TradeRecord, PlayerRecord, MarketSnapshot
from src.config import EA_TAX_RATE

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")

VALID_SINCE_VALUES = {"1h", "24h", "7d", "30d", "all"}


def _parse_since(since: Optional[str]) -> Optional[datetime]:
    """Convert a since string to a UTC cutoff datetime.

    Args:
        since: One of '1h', '24h', '7d', '30d', 'all', or None.

    Returns:
        UTC datetime cutoff, or None for no filter.

    Raises:
        HTTPException: 422 if since value is invalid.
    """
    if since is None or since == "all":
        return None
    if since not in VALID_SINCE_VALUES:
        raise HTTPException(status_code=422, detail=f"Invalid since value: {since}. Must be one of {VALID_SINCE_VALUES}")
    deltas = {"1h": timedelta(hours=1), "24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30)}
    return datetime.now(timezone.utc).replace(tzinfo=None) - deltas[since]


@router.get("/profit/summary")
async def get_profit_summary(
    request: Request,
    since: Optional[str] = Query(default=None),
):
    """Return total and per-player profit breakdown using FIFO buy->sell matching.

    Args:
        request: FastAPI Request (app.state carries session_factory).
        since: Time filter — '1h', '24h', '7d', '30d', 'all', or omit for all data.

    Returns:
        Dict with totals and per_player keys.
    """
    cutoff = _parse_since(since)

    session_factory = request.app.state.read_session_factory
    async with session_factory() as session:
        # All trade records ordered chronologically
        stmt = select(
            TradeRecord.ea_id, TradeRecord.outcome, TradeRecord.price, TradeRecord.recorded_at
        ).order_by(TradeRecord.recorded_at, TradeRecord.id)

        if cutoff is not None:
            stmt = stmt.where(TradeRecord.recorded_at >= cutoff)

        trades_result = await session.execute(stmt)
        trades = trades_result.all()

        # Player names
        name_result = await session.execute(
            select(PlayerRecord.ea_id, PlayerRecord.name)
        )
        name_map: dict[int, str] = {row.ea_id: row.name for row in name_result.all()}

        # Latest market snapshot per ea_id for unrealized P&L
        latest_snap_subq = (
            select(MarketSnapshot.ea_id, func.max(MarketSnapshot.id).label("max_id"))
            .group_by(MarketSnapshot.ea_id)
            .subquery()
        )
        snap_result = await session.execute(
            select(MarketSnapshot.ea_id, MarketSnapshot.current_lowest_bin)
            .join(
                latest_snap_subq,
                (MarketSnapshot.ea_id == latest_snap_subq.c.ea_id)
                & (MarketSnapshot.id == latest_snap_subq.c.max_id),
            )
        )
        bin_map: dict[int, int] = {row.ea_id: row.current_lowest_bin for row in snap_result.all()}

    # Group trades by ea_id
    trades_by_player: dict[int, list] = {}
    for row in trades:
        trades_by_player.setdefault(row.ea_id, []).append(row)

    # FIFO matching per player
    per_player = []
    for ea_id, player_trades in trades_by_player.items():
        buy_queue: list[tuple[int, datetime]] = []  # (price, recorded_at)
        total_spent = 0
        total_earned = 0
        realized_profit = 0
        sell_count = 0
        first_buy_at: datetime | None = None
        last_sell_at: datetime | None = None

        for trade in player_trades:
            if trade.outcome == "bought":
                buy_queue.append((trade.price, trade.recorded_at))
                total_spent += trade.price
                if first_buy_at is None:
                    first_buy_at = trade.recorded_at
            elif trade.outcome == "sold" and buy_queue:
                buy_price, _buy_time = buy_queue.pop(0)
                earned = int(trade.price * (1 - EA_TAX_RATE))
                total_earned += earned
                realized_profit += earned - buy_price
                sell_count += 1
                last_sell_at = trade.recorded_at
            # else: ghost sell (no buy to match) — ignored

        buy_count = sell_count + len(buy_queue)
        held_count = len(buy_queue)

        # Skip players with no buys (only ghost sells)
        if buy_count == 0:
            continue

        # Unrealized P&L for held cards
        unrealized_pnl = 0
        current_bin = bin_map.get(ea_id)
        if held_count > 0 and current_bin is not None:
            for held_buy_price, _held_time in buy_queue:
                unrealized_pnl += int(current_bin * (1 - EA_TAX_RATE)) - held_buy_price

        # Profit rate: realized_profit / hours between first buy and last sell
        profit_per_hour = None
        active_hours = None
        if sell_count > 0 and first_buy_at is not None and last_sell_at is not None:
            delta = (last_sell_at - first_buy_at).total_seconds() / 3600
            active_hours = round(delta, 1)
            if delta > 0:
                profit_per_hour = round(realized_profit / delta, 1)

        per_player.append({
            "ea_id": ea_id,
            "name": name_map.get(ea_id, f"Player {ea_id}"),
            "total_spent": total_spent,
            "total_earned": total_earned,
            "realized_profit": realized_profit,
            "unrealized_pnl": unrealized_pnl,
            "buy_count": buy_count,
            "sell_count": sell_count,
            "held_count": held_count,
            "profit_per_hour": profit_per_hour,
            "first_buy_at": first_buy_at.isoformat() if first_buy_at else None,
            "last_sell_at": last_sell_at.isoformat() if last_sell_at else None,
            "active_hours": active_hours,
        })

    # Totals
    total_spent = sum(p["total_spent"] for p in per_player)
    total_earned = sum(p["total_earned"] for p in per_player)
    realized_profit = sum(p["realized_profit"] for p in per_player)
    unrealized_pnl = sum(p["unrealized_pnl"] for p in per_player)
    buy_count = sum(p["buy_count"] for p in per_player)
    sell_count = sum(p["sell_count"] for p in per_player)
    held_count = sum(p["held_count"] for p in per_player)

    return {
        "totals": {
            "total_spent": total_spent,
            "total_earned": total_earned,
            "realized_profit": realized_profit,
            "unrealized_pnl": unrealized_pnl,
            "total_profit": realized_profit + unrealized_pnl,
            "buy_count": buy_count,
            "sell_count": sell_count,
            "held_count": held_count,
        },
        "per_player": per_player,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/integration/test_time_filters.py -v`

Expected: All 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/server/api/profit.py tests/integration/test_time_filters.py
git commit -m "feat(api): add ?since= time filter to profit summary endpoint"
```

---

## Task 2: Add `?since=` Time Filter to Portfolio Status

**Files:**
- Modify: `src/server/api/portfolio_status.py:30-46`
- Modify: `tests/integration/test_time_filters.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/integration/test_time_filters.py`:

```python
@pytest.mark.asyncio
async def test_portfolio_status_since_param_accepted(client: httpx.AsyncClient):
    """GET /api/v1/portfolio/status?since=7d returns 200."""
    resp = await client.get("/api/v1/portfolio/status?since=7d")
    assert resp.status_code == 200
    data = resp.json()
    assert "summary" in data
    assert "players" in data


@pytest.mark.asyncio
async def test_portfolio_status_invalid_since(client: httpx.AsyncClient):
    """GET /api/v1/portfolio/status?since=bogus returns 422."""
    resp = await client.get("/api/v1/portfolio/status?since=bogus")
    assert resp.status_code == 422
```

- [ ] **Step 2: Run tests to verify new tests fail**

Run: `pytest tests/integration/test_time_filters.py::test_portfolio_status_since_param_accepted tests/integration/test_time_filters.py::test_portfolio_status_invalid_since -v`

Expected: FAIL — `since` param not implemented on portfolio status.

- [ ] **Step 3: Implement `?since=` on portfolio status**

Edit `src/server/api/portfolio_status.py`. Import `_parse_since` from profit module and add `since` param:

```python
"""Portfolio status endpoint for dashboard.

Returns per-player trade status, cumulative stats, unrealized P&L,
and summary totals in a single call (D-07).
"""
import logging
from typing import Optional

from fastapi import APIRouter, Query, Request
from sqlalchemy import select, func, case

from src.server.models_db import PortfolioSlot, TradeRecord, MarketSnapshot, PlayerRecord
from src.server.api.profit import _parse_since
from src.config import EA_TAX_RATE

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")

# Maps TradeRecord.outcome values to dashboard status strings
OUTCOME_TO_STATUS = {
    "bought": "BOUGHT",
    "listed": "LISTED",
    "sold": "SOLD",
    "expired": "EXPIRED",
}

# Statuses where the player is considered "held" (unrealized P&L applies)
HELD_STATUSES = {"BOUGHT", "LISTED"}


@router.get("/portfolio/status")
async def get_portfolio_status(
    request: Request,
    since: Optional[str] = Query(default=None),
):
    """Return per-player trade status, cumulative stats, unrealized P&L, and summary totals.

    Args:
        request: FastAPI Request (app.state carries session_factory).
        since: Time filter — '1h', '24h', '7d', '30d', 'all', or omit for all data.

    Returns:
        Dict with summary and players keys.
    """
    cutoff = _parse_since(since)

    session_factory = request.app.state.read_session_factory
    async with session_factory() as session:
        # Query 1 — All active portfolio slots
        slots_result = await session.execute(
            select(PortfolioSlot.ea_id, PortfolioSlot.buy_price, PortfolioSlot.sell_price, PortfolioSlot.is_leftover)
        )
        slots = slots_result.all()

        if not slots:
            return {
                "summary": {
                    "realized_profit": 0,
                    "unrealized_pnl": 0,
                    "trade_counts": {"bought": 0, "sold": 0, "expired": 0},
                },
                "players": [],
            }

        ea_ids = [row.ea_id for row in slots]

        # Query 2 — Trade record aggregation per ea_id (with optional time filter)
        agg_base = (
            select(
                TradeRecord.ea_id,
                func.sum(
                    case((TradeRecord.outcome == "sold", TradeRecord.price), else_=0)
                ).label("total_sold_gross"),
                func.sum(
                    case((TradeRecord.outcome == "bought", TradeRecord.price), else_=0)
                ).label("total_bought"),
                func.count(
                    case((TradeRecord.outcome == "sold", 1))
                ).label("times_sold"),
                func.count(
                    case((TradeRecord.outcome == "bought", 1))
                ).label("bought_count"),
                func.count(
                    case((TradeRecord.outcome == "expired", 1))
                ).label("expired_count"),
            )
            .where(TradeRecord.ea_id.in_(ea_ids))
        )
        if cutoff is not None:
            agg_base = agg_base.where(TradeRecord.recorded_at >= cutoff)
        agg_stmt = agg_base.group_by(TradeRecord.ea_id)

        agg_result = await session.execute(agg_stmt)
        agg_rows = {row.ea_id: row for row in agg_result.all()}

        # Query 3 — Most recent outcome per ea_id (always unfiltered — status is current state)
        latest_record_subq = (
            select(TradeRecord.ea_id, func.max(TradeRecord.id).label("max_id"))
            .where(TradeRecord.ea_id.in_(ea_ids))
            .group_by(TradeRecord.ea_id)
            .subquery()
        )
        status_stmt = (
            select(TradeRecord.ea_id, TradeRecord.outcome)
            .join(
                latest_record_subq,
                (TradeRecord.ea_id == latest_record_subq.c.ea_id)
                & (TradeRecord.id == latest_record_subq.c.max_id),
            )
        )
        status_result = await session.execute(status_stmt)
        latest_outcome_map: dict[int, str] = {
            row.ea_id: row.outcome for row in status_result.all()
        }

        # Query 4 — Latest MarketSnapshot per ea_id using MAX(id)
        latest_snapshot_subq = (
            select(MarketSnapshot.ea_id, func.max(MarketSnapshot.id).label("max_id"))
            .where(MarketSnapshot.ea_id.in_(ea_ids))
            .group_by(MarketSnapshot.ea_id)
            .subquery()
        )
        bin_stmt = (
            select(MarketSnapshot.ea_id, MarketSnapshot.current_lowest_bin)
            .join(
                latest_snapshot_subq,
                (MarketSnapshot.ea_id == latest_snapshot_subq.c.ea_id)
                & (MarketSnapshot.id == latest_snapshot_subq.c.max_id),
            )
        )
        bin_result = await session.execute(bin_stmt)
        bin_map: dict[int, int] = {
            row.ea_id: row.current_lowest_bin for row in bin_result.all()
        }

        # Query 5 — Player names from PlayerRecord
        name_result = await session.execute(
            select(PlayerRecord.ea_id, PlayerRecord.name, PlayerRecord.futgg_url).where(
                PlayerRecord.ea_id.in_(ea_ids)
            )
        )
        player_info: dict[int, tuple[str, str | None]] = {
            row.ea_id: (row.name, row.futgg_url) for row in name_result.all()
        }

    # ── Assembly ────────────────────────────────────────────────────────────────

    players = []
    total_realized_profit = 0
    total_unrealized_pnl = 0
    total_bought_count = 0
    total_sold_count = 0
    total_expired_count = 0

    for slot in slots:
        ea_id = slot.ea_id
        buy_price = slot.buy_price

        # Derive status from latest trade outcome, default to PENDING
        latest_outcome = latest_outcome_map.get(ea_id)
        status = OUTCOME_TO_STATUS.get(latest_outcome, "PENDING") if latest_outcome else "PENDING"

        # Aggregated trade stats
        agg = agg_rows.get(ea_id)
        if agg:
            total_sold_gross = int(agg.total_sold_gross or 0)
            total_bought_cost = int(agg.total_bought or 0)
            times_sold = int(agg.times_sold or 0)
            bought_count = int(agg.bought_count or 0)
            expired_count = int(agg.expired_count or 0)
        else:
            total_sold_gross = 0
            total_bought_cost = 0
            times_sold = 0
            bought_count = 0
            expired_count = 0

        # Realized profit
        realized_profit = int(total_sold_gross * (1 - EA_TAX_RATE)) - (times_sold * buy_price)

        # Unrealized P&L: only for held (BOUGHT or LISTED) players with a market snapshot
        current_bin = bin_map.get(ea_id)
        if status in HELD_STATUSES and current_bin is not None:
            unrealized_pnl: int | None = current_bin - buy_price
        else:
            unrealized_pnl = None

        # Accumulate summary totals
        total_realized_profit += realized_profit
        if unrealized_pnl is not None:
            total_unrealized_pnl += unrealized_pnl
        total_bought_count += bought_count
        total_sold_count += times_sold
        total_expired_count += expired_count

        info = player_info.get(ea_id)
        name = info[0] if info else f"Player {ea_id}"
        futgg_url = info[1] if info else None

        players.append({
            "ea_id": ea_id,
            "name": name,
            "futgg_url": futgg_url,
            "status": status,
            "times_sold": times_sold,
            "realized_profit": realized_profit,
            "unrealized_pnl": unrealized_pnl,
            "buy_price": buy_price,
            "sell_price": slot.sell_price,
            "current_bin": current_bin if status in HELD_STATUSES else None,
            "is_leftover": slot.is_leftover,
        })

    return {
        "summary": {
            "realized_profit": total_realized_profit,
            "unrealized_pnl": total_unrealized_pnl,
            "trade_counts": {
                "bought": total_bought_count,
                "sold": total_sold_count,
                "expired": total_expired_count,
            },
        },
        "players": players,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/integration/test_time_filters.py -v`

Expected: All 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/server/api/portfolio_status.py tests/integration/test_time_filters.py
git commit -m "feat(api): add ?since= time filter to portfolio status endpoint"
```

---

## Task 3: Create Stale Cards Endpoint

**Files:**
- Create: `src/server/api/stale.py`
- Modify: `src/server/main.py:19` (add import + register router)
- Create: `tests/integration/test_stale_endpoint.py`

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_stale_endpoint.py`:

```python
"""Integration tests for /api/v1/portfolio/stale endpoint."""
import pytest
import httpx


@pytest.mark.asyncio
async def test_stale_endpoint_returns_200(client: httpx.AsyncClient):
    """GET /api/v1/portfolio/stale returns 200 with both views."""
    resp = await client.get("/api/v1/portfolio/stale")
    assert resp.status_code == 200
    data = resp.json()
    assert "longest_unsold" in data
    assert "avg_sale_time" in data
    assert isinstance(data["longest_unsold"], list)
    assert isinstance(data["avg_sale_time"], list)


@pytest.mark.asyncio
async def test_stale_endpoint_with_since(client: httpx.AsyncClient):
    """GET /api/v1/portfolio/stale?since=7d returns 200."""
    resp = await client.get("/api/v1/portfolio/stale?since=7d")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_stale_endpoint_invalid_since(client: httpx.AsyncClient):
    """GET /api/v1/portfolio/stale?since=bogus returns 422."""
    resp = await client.get("/api/v1/portfolio/stale?since=bogus")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_stale_longest_unsold_shape(client: httpx.AsyncClient, seed_real_portfolio_slot):
    """Longest unsold entries have the expected fields."""
    if seed_real_portfolio_slot is None:
        pytest.skip("No real ea_id available")

    # Record a buy trade for the seeded player
    resp = await client.get("/api/v1/portfolio/stale")
    assert resp.status_code == 200
    data = resp.json()
    # With a fresh portfolio slot but no sell, should appear in longest_unsold
    for entry in data["longest_unsold"]:
        assert "ea_id" in entry
        assert "name" in entry
        assert "buy_price" in entry
        assert "bought_at" in entry
        assert "time_since_buy_hours" in entry
        assert "status" in entry


@pytest.mark.asyncio
async def test_stale_avg_sale_time_shape(client: httpx.AsyncClient):
    """Avg sale time entries have the expected fields."""
    resp = await client.get("/api/v1/portfolio/stale")
    assert resp.status_code == 200
    data = resp.json()
    for entry in data["avg_sale_time"]:
        assert "ea_id" in entry
        assert "name" in entry
        assert "total_sales" in entry
        assert "first_activity" in entry
        assert "last_activity" in entry
        assert "time_period_hours" in entry
        # avg_hours_between_sales can be null for 0-sale cards
        assert "avg_hours_between_sales" in entry
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/integration/test_stale_endpoint.py -v`

Expected: FAIL — 404 (endpoint doesn't exist).

- [ ] **Step 3: Create `src/server/api/stale.py`**

```python
"""Stale cards endpoint — longest unsold and avg sale time views.

Provides two ranked views for the dashboard Stale Cards tab:
1. longest_unsold: cards bought but not yet sold, ranked by hold time.
2. avg_sale_time: per-card sale frequency, unsold cards penalized to bottom.
"""
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Query, Request
from sqlalchemy import select, func, case

from src.server.models_db import TradeRecord, PlayerRecord
from src.server.api.profit import _parse_since

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")


@router.get("/portfolio/stale")
async def get_stale_cards(
    request: Request,
    since: Optional[str] = Query(default=None),
):
    """Return stale card views: longest unsold and avg sale time.

    Args:
        request: FastAPI Request (app.state carries session_factory).
        since: Time filter — '1h', '24h', '7d', '30d', 'all', or omit for all data.

    Returns:
        Dict with longest_unsold and avg_sale_time lists.
    """
    cutoff = _parse_since(since)
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    session_factory = request.app.state.read_session_factory
    async with session_factory() as session:
        # All trade records, optionally filtered by time
        stmt = select(
            TradeRecord.ea_id,
            TradeRecord.outcome,
            TradeRecord.price,
            TradeRecord.recorded_at,
        ).order_by(TradeRecord.recorded_at, TradeRecord.id)

        if cutoff is not None:
            stmt = stmt.where(TradeRecord.recorded_at >= cutoff)

        trades_result = await session.execute(stmt)
        trades = trades_result.all()

        # Player names
        name_result = await session.execute(
            select(PlayerRecord.ea_id, PlayerRecord.name)
        )
        name_map: dict[int, str] = {row.ea_id: row.name for row in name_result.all()}

        # Latest outcome per ea_id (unfiltered — reflects current state)
        latest_subq = (
            select(TradeRecord.ea_id, func.max(TradeRecord.id).label("max_id"))
            .group_by(TradeRecord.ea_id)
            .subquery()
        )
        status_result = await session.execute(
            select(TradeRecord.ea_id, TradeRecord.outcome)
            .join(
                latest_subq,
                (TradeRecord.ea_id == latest_subq.c.ea_id)
                & (TradeRecord.id == latest_subq.c.max_id),
            )
        )
        status_map: dict[int, str] = {row.ea_id: row.outcome for row in status_result.all()}

    # Group trades by ea_id
    trades_by_player: dict[int, list] = {}
    for row in trades:
        trades_by_player.setdefault(row.ea_id, []).append(row)

    # ── View 1: Longest Unsold ──────────────────────────────────────────────
    # FIFO match buys to sells. Unmatched buys = unsold.
    longest_unsold = []
    for ea_id, player_trades in trades_by_player.items():
        buy_queue: list[tuple[int, datetime]] = []  # (price, recorded_at)
        for trade in player_trades:
            if trade.outcome == "bought":
                buy_queue.append((trade.price, trade.recorded_at))
            elif trade.outcome == "sold" and buy_queue:
                buy_queue.pop(0)

        # Each remaining buy in the queue is unsold
        for buy_price, bought_at in buy_queue:
            hours = (now - bought_at).total_seconds() / 3600
            current_outcome = status_map.get(ea_id, "bought")
            status_str = {"bought": "BOUGHT", "listed": "LISTED"}.get(current_outcome, "BOUGHT")
            longest_unsold.append({
                "ea_id": ea_id,
                "name": name_map.get(ea_id, f"Player {ea_id}"),
                "buy_price": buy_price,
                "bought_at": bought_at.isoformat(),
                "time_since_buy_hours": round(hours, 1),
                "status": status_str,
            })

    # Sort by time_since_buy_hours desc (longest held first)
    longest_unsold.sort(key=lambda x: x["time_since_buy_hours"], reverse=True)

    # ── View 2: Avg Sale Time ──────────────────────────────────────────────
    avg_sale_time = []
    for ea_id, player_trades in trades_by_player.items():
        sell_count = sum(1 for t in player_trades if t.outcome == "sold")
        all_times = [t.recorded_at for t in player_trades]
        if not all_times:
            continue
        first_activity = min(all_times)
        last_activity = max(all_times)
        time_period_hours = max((last_activity - first_activity).total_seconds() / 3600, 0.1)

        avg_hours = None
        if sell_count > 0:
            avg_hours = round(time_period_hours / sell_count, 1)

        avg_sale_time.append({
            "ea_id": ea_id,
            "name": name_map.get(ea_id, f"Player {ea_id}"),
            "total_sales": sell_count,
            "first_activity": first_activity.isoformat(),
            "last_activity": last_activity.isoformat(),
            "time_period_hours": round(time_period_hours, 1),
            "avg_hours_between_sales": avg_hours,
        })

    # Sort: cards with sales by avg_hours desc (slowest first), then no-sales at bottom
    with_sales = [x for x in avg_sale_time if x["avg_hours_between_sales"] is not None]
    without_sales = [x for x in avg_sale_time if x["avg_hours_between_sales"] is None]
    with_sales.sort(key=lambda x: x["avg_hours_between_sales"], reverse=True)
    avg_sale_time = with_sales + without_sales

    return {
        "longest_unsold": longest_unsold,
        "avg_sale_time": avg_sale_time,
    }
```

- [ ] **Step 4: Register the router in `src/server/main.py`**

Add import after line 18 (`from src.server.api.portfolio_status import router as status_router`):

```python
from src.server.api.stale import router as stale_router
```

Add router registration after line 78 (`app.include_router(automation.router)`):

```python
app.include_router(stale_router)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/integration/test_stale_endpoint.py -v`

Expected: All 5 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/server/api/stale.py src/server/main.py tests/integration/test_stale_endpoint.py
git commit -m "feat(api): add /portfolio/stale endpoint for stale card views"
```

---

## Task 4: Rewrite Dashboard HTML — Shell and Header

**Files:**
- Rewrite: `dashboard.html` (complete replacement — this task writes the shell, header, and tab navigation; subsequent tasks fill in tab content)

- [ ] **Step 1: Write the dashboard shell with Alpine.js**

Replace entire `dashboard.html` with:

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>OP Seller Dashboard</title>
<script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3.x.x/dist/cdn.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
<style>
  :root {
    --bg: #1a1a2e;
    --surface: #16213e;
    --surface2: #0f3460;
    --accent: #6c5ce7;
    --text: #e0e0e0;
    --text-dim: #888;
    --green: #00b894;
    --red: #d63031;
    --yellow: #fdcb6e;
    --blue: #74b9ff;
    --border: #2d3748;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
  }

  /* ── Header ── */
  .header {
    position: sticky;
    top: 0;
    z-index: 100;
    background: var(--surface);
    border-bottom: 1px solid var(--border);
    padding: 12px 24px;
    display: flex;
    align-items: center;
    gap: 16px;
    flex-wrap: wrap;
  }
  .header h1 { font-size: 1.2rem; color: var(--accent); white-space: nowrap; }
  .status-pill {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px 12px;
    border-radius: 99px;
    font-size: 0.75rem;
    font-weight: 600;
    text-transform: uppercase;
  }
  .status-pill.running { background: rgba(0,184,148,0.15); color: var(--green); }
  .status-pill.stopped { background: rgba(214,48,49,0.15); color: var(--red); }
  .status-pill.unknown { background: rgba(253,203,110,0.15); color: var(--yellow); }
  .status-dot {
    width: 8px; height: 8px; border-radius: 50%;
    background: currentColor;
  }
  .status-pill.running .status-dot { animation: pulse 1.5s infinite; }
  @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.3; } }
  .header input[type="text"] {
    background: var(--bg);
    border: 1px solid var(--border);
    color: var(--text);
    padding: 6px 12px;
    border-radius: 6px;
    font-size: 0.85rem;
    width: 280px;
  }
  .header button {
    background: var(--accent);
    color: white;
    border: none;
    padding: 6px 16px;
    border-radius: 6px;
    cursor: pointer;
    font-size: 0.85rem;
    font-weight: 600;
  }
  .header button:hover { opacity: 0.85; }
  .header .updated {
    font-size: 0.75rem;
    color: var(--text-dim);
    margin-left: auto;
  }

  /* ── Tabs ── */
  .tab-bar {
    display: flex;
    gap: 0;
    background: var(--surface);
    border-bottom: 2px solid var(--border);
    padding: 0 24px;
  }
  .tab-btn {
    padding: 12px 24px;
    background: none;
    border: none;
    color: var(--text-dim);
    font-size: 0.9rem;
    font-weight: 600;
    cursor: pointer;
    border-bottom: 2px solid transparent;
    margin-bottom: -2px;
    transition: color 0.15s, border-color 0.15s;
  }
  .tab-btn:hover { color: var(--text); }
  .tab-btn.active { color: var(--accent); border-bottom-color: var(--accent); }

  /* ── Content ── */
  .content { padding: 24px; max-width: 1400px; margin: 0 auto; }

  /* ── Time Filter ── */
  .time-filter {
    display: flex;
    gap: 4px;
    margin-bottom: 20px;
  }
  .time-filter button {
    padding: 6px 14px;
    border-radius: 6px;
    border: 1px solid var(--border);
    background: var(--surface);
    color: var(--text-dim);
    font-size: 0.8rem;
    cursor: pointer;
    transition: all 0.15s;
  }
  .time-filter button:hover { border-color: var(--accent); color: var(--text); }
  .time-filter button.active { background: var(--accent); color: white; border-color: var(--accent); }

  /* ── Stats Row ── */
  .stats-row {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 16px;
    margin-bottom: 24px;
  }
  .stat-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 16px;
  }
  .stat-card .label { font-size: 0.75rem; color: var(--text-dim); text-transform: uppercase; margin-bottom: 4px; }
  .stat-card .value { font-size: 1.4rem; font-weight: 700; }
  .stat-card .value.positive { color: var(--green); }
  .stat-card .value.negative { color: var(--red); }

  /* ── Tables ── */
  .section-title {
    font-size: 1rem;
    font-weight: 700;
    margin-bottom: 12px;
    color: var(--text);
  }
  .table-wrap {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    overflow-x: auto;
    margin-bottom: 24px;
  }
  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.85rem;
  }
  th {
    text-align: left;
    padding: 10px 14px;
    border-bottom: 1px solid var(--border);
    color: var(--text-dim);
    font-weight: 600;
    font-size: 0.75rem;
    text-transform: uppercase;
    white-space: nowrap;
    cursor: pointer;
    user-select: none;
  }
  th:hover { color: var(--accent); }
  th .sort-arrow { font-size: 0.65rem; margin-left: 4px; }
  td {
    padding: 10px 14px;
    border-bottom: 1px solid rgba(45,55,72,0.5);
    white-space: nowrap;
  }
  tr:hover td { background: rgba(108,92,231,0.05); }
  .text-green { color: var(--green); }
  .text-red { color: var(--red); }
  .text-dim { color: var(--text-dim); }

  /* ── Scanner Stats Grid ── */
  .stats-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 16px;
    margin-bottom: 24px;
  }

  /* ── Progress Bar ── */
  .progress-bar {
    height: 6px;
    background: var(--border);
    border-radius: 3px;
    margin-top: 8px;
    overflow: hidden;
  }
  .progress-bar .fill {
    height: 100%;
    border-radius: 3px;
    background: var(--accent);
    transition: width 0.3s;
  }

  /* ── Responsive ── */
  @media (max-width: 900px) {
    .header { padding: 10px 12px; }
    .header input[type="text"] { width: 100%; }
    .content { padding: 12px; }
    .tab-btn { padding: 10px 14px; font-size: 0.8rem; }
  }
</style>
</head>
<body x-data="dashboard()" x-init="init()">

<!-- Header -->
<header class="header">
  <h1>OP Seller Dashboard</h1>
  <span class="status-pill"
        :class="health.scanner_status || 'unknown'">
    <span class="status-dot"></span>
    <span x-text="health.scanner_status || 'unknown'"></span>
  </span>
  <input type="text" placeholder="API URL (e.g. http://localhost:8000)"
         x-model="apiUrl" @change="saveApiUrl()">
  <button @click="refreshAll()">Refresh</button>
  <span class="updated" x-text="lastUpdated ? 'Updated ' + lastUpdated : ''"></span>
</header>

<!-- Tab Bar -->
<nav class="tab-bar">
  <template x-for="tab in tabs" :key="tab.id">
    <button class="tab-btn"
            :class="{ active: activeTab === tab.id }"
            @click="activeTab = tab.id"
            x-text="tab.label"></button>
  </template>
</nav>

<!-- Content -->
<div class="content">

  <!-- ═══ Tab 1: Profit ═══ -->
  <div x-show="activeTab === 'profit'" x-cloak>
    <!-- Time filter -->
    <div class="time-filter">
      <template x-for="opt in sinceOptions" :key="opt.value">
        <button :class="{ active: filters.profit === opt.value }"
                @click="filters.profit = opt.value; fetchProfit()"
                x-text="opt.label"></button>
      </template>
    </div>

    <!-- Stats row -->
    <div class="stats-row">
      <div class="stat-card">
        <div class="label">Total Spent</div>
        <div class="value" x-text="fmt(profitData.totals.total_spent)"></div>
      </div>
      <div class="stat-card">
        <div class="label">Total Earned</div>
        <div class="value" x-text="fmt(profitData.totals.total_earned)"></div>
      </div>
      <div class="stat-card">
        <div class="label">Realized Profit</div>
        <div class="value" :class="profitData.totals.realized_profit >= 0 ? 'positive' : 'negative'"
             x-text="fmt(profitData.totals.realized_profit)"></div>
      </div>
      <div class="stat-card">
        <div class="label">Unrealized P&amp;L</div>
        <div class="value" :class="profitData.totals.unrealized_pnl >= 0 ? 'positive' : 'negative'"
             x-text="fmt(profitData.totals.unrealized_pnl)"></div>
      </div>
    </div>

    <!-- Top Profitters Table -->
    <h3 class="section-title">Top Profitters</h3>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th @click="sortTable('profitters', 'name')">Name <span class="sort-arrow" x-text="sortArrow('profitters', 'name')"></span></th>
            <th @click="sortTable('profitters', 'sell_count')">Times Sold <span class="sort-arrow" x-text="sortArrow('profitters', 'sell_count')"></span></th>
            <th @click="sortTable('profitters', 'total_spent')">Total Spent <span class="sort-arrow" x-text="sortArrow('profitters', 'total_spent')"></span></th>
            <th @click="sortTable('profitters', 'total_earned')">Total Earned <span class="sort-arrow" x-text="sortArrow('profitters', 'total_earned')"></span></th>
            <th @click="sortTable('profitters', 'realized_profit')">Realized Profit <span class="sort-arrow" x-text="sortArrow('profitters', 'realized_profit')"></span></th>
          </tr>
        </thead>
        <tbody>
          <template x-for="p in sorted('profitters')" :key="p.ea_id">
            <tr>
              <td x-text="p.name"></td>
              <td x-text="p.sell_count"></td>
              <td x-text="fmt(p.total_spent)"></td>
              <td x-text="fmt(p.total_earned)"></td>
              <td :class="p.realized_profit >= 0 ? 'text-green' : 'text-red'"
                  x-text="fmt(p.realized_profit)"></td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>

    <!-- Profit Rate Table -->
    <h3 class="section-title">Profit Rate</h3>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th @click="sortTable('profitRate', 'name')">Name <span class="sort-arrow" x-text="sortArrow('profitRate', 'name')"></span></th>
            <th @click="sortTable('profitRate', 'profit_per_hour')">Profit/hr <span class="sort-arrow" x-text="sortArrow('profitRate', 'profit_per_hour')"></span></th>
            <th @click="sortTable('profitRate', 'realized_profit')">Total Profit <span class="sort-arrow" x-text="sortArrow('profitRate', 'realized_profit')"></span></th>
            <th @click="sortTable('profitRate', 'active_hours')">Time Active <span class="sort-arrow" x-text="sortArrow('profitRate', 'active_hours')"></span></th>
          </tr>
        </thead>
        <tbody>
          <template x-for="p in sorted('profitRate')" :key="p.ea_id">
            <tr>
              <td x-text="p.name"></td>
              <td x-text="p.profit_per_hour != null ? fmt(p.profit_per_hour) + '/hr' : '-'"></td>
              <td :class="p.realized_profit >= 0 ? 'text-green' : 'text-red'"
                  x-text="fmt(p.realized_profit)"></td>
              <td x-text="p.active_hours != null ? fmtHours(p.active_hours) : '-'"></td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>
  </div>

  <!-- ═══ Tab 2: Stale Cards ═══ -->
  <div x-show="activeTab === 'stale'" x-cloak>

    <!-- Longest Unsold -->
    <h3 class="section-title">Longest Unsold</h3>
    <div class="time-filter">
      <template x-for="opt in sinceOptions" :key="opt.value">
        <button :class="{ active: filters.staleUnsold === opt.value }"
                @click="filters.staleUnsold = opt.value; fetchStale()"
                x-text="opt.label"></button>
      </template>
    </div>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th @click="sortTable('unsold', 'name')">Name <span class="sort-arrow" x-text="sortArrow('unsold', 'name')"></span></th>
            <th @click="sortTable('unsold', 'buy_price')">Buy Price <span class="sort-arrow" x-text="sortArrow('unsold', 'buy_price')"></span></th>
            <th @click="sortTable('unsold', 'time_since_buy_hours')">Time Since Buy <span class="sort-arrow" x-text="sortArrow('unsold', 'time_since_buy_hours')"></span></th>
            <th @click="sortTable('unsold', 'status')">Status <span class="sort-arrow" x-text="sortArrow('unsold', 'status')"></span></th>
          </tr>
        </thead>
        <tbody>
          <template x-for="p in sorted('unsold')" :key="p.ea_id + '-' + p.bought_at">
            <tr>
              <td x-text="p.name"></td>
              <td x-text="fmt(p.buy_price)"></td>
              <td x-text="fmtHours(p.time_since_buy_hours)"></td>
              <td x-text="p.status"></td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>

    <!-- Avg Sale Time -->
    <h3 class="section-title">Avg Sale Time</h3>
    <div class="time-filter">
      <template x-for="opt in sinceOptions" :key="opt.value">
        <button :class="{ active: filters.staleAvg === opt.value }"
                @click="filters.staleAvg = opt.value; fetchStale()"
                x-text="opt.label"></button>
      </template>
    </div>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th @click="sortTable('avgSale', 'name')">Name <span class="sort-arrow" x-text="sortArrow('avgSale', 'name')"></span></th>
            <th @click="sortTable('avgSale', 'total_sales')">Total Sales <span class="sort-arrow" x-text="sortArrow('avgSale', 'total_sales')"></span></th>
            <th @click="sortTable('avgSale', 'time_period_hours')">Time Period <span class="sort-arrow" x-text="sortArrow('avgSale', 'time_period_hours')"></span></th>
            <th @click="sortTable('avgSale', 'avg_hours_between_sales')">Avg Time Between Sales <span class="sort-arrow" x-text="sortArrow('avgSale', 'avg_hours_between_sales')"></span></th>
          </tr>
        </thead>
        <tbody>
          <template x-for="p in sorted('avgSale')" :key="p.ea_id">
            <tr>
              <td x-text="p.name"></td>
              <td x-text="p.total_sales"></td>
              <td x-text="fmtHours(p.time_period_hours)"></td>
              <td x-text="p.avg_hours_between_sales != null ? fmtHours(p.avg_hours_between_sales) : 'No sales'"
                  :class="p.avg_hours_between_sales == null ? 'text-dim' : ''"></td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>
  </div>

  <!-- ═══ Tab 3: Portfolio ═══ -->
  <div x-show="activeTab === 'portfolio'" x-cloak>
    <div class="time-filter">
      <template x-for="opt in sinceOptions" :key="opt.value">
        <button :class="{ active: filters.portfolio === opt.value }"
                @click="filters.portfolio = opt.value; fetchPortfolio()"
                x-text="opt.label"></button>
      </template>
    </div>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th @click="sortTable('portfolio', 'name')">Name <span class="sort-arrow" x-text="sortArrow('portfolio', 'name')"></span></th>
            <th @click="sortTable('portfolio', 'status')">Status <span class="sort-arrow" x-text="sortArrow('portfolio', 'status')"></span></th>
            <th @click="sortTable('portfolio', 'buy_price')">Buy Price <span class="sort-arrow" x-text="sortArrow('portfolio', 'buy_price')"></span></th>
            <th @click="sortTable('portfolio', 'sell_price')">Sell Price <span class="sort-arrow" x-text="sortArrow('portfolio', 'sell_price')"></span></th>
            <th @click="sortTable('portfolio', 'times_sold')">Times Sold <span class="sort-arrow" x-text="sortArrow('portfolio', 'times_sold')"></span></th>
            <th @click="sortTable('portfolio', 'realized_profit')">Realized P&amp;L <span class="sort-arrow" x-text="sortArrow('portfolio', 'realized_profit')"></span></th>
            <th @click="sortTable('portfolio', 'unrealized_pnl')">Unrealized P&amp;L <span class="sort-arrow" x-text="sortArrow('portfolio', 'unrealized_pnl')"></span></th>
            <th @click="sortTable('portfolio', 'current_bin')">Current BIN <span class="sort-arrow" x-text="sortArrow('portfolio', 'current_bin')"></span></th>
          </tr>
        </thead>
        <tbody>
          <template x-for="p in sorted('portfolio')" :key="p.ea_id">
            <tr>
              <td x-text="p.name"></td>
              <td x-text="p.status"></td>
              <td x-text="fmt(p.buy_price)"></td>
              <td x-text="fmt(p.sell_price)"></td>
              <td x-text="p.times_sold"></td>
              <td :class="(p.realized_profit || 0) >= 0 ? 'text-green' : 'text-red'"
                  x-text="fmt(p.realized_profit)"></td>
              <td :class="(p.unrealized_pnl || 0) >= 0 ? 'text-green' : 'text-red'"
                  x-text="p.unrealized_pnl != null ? fmt(p.unrealized_pnl) : '-'"></td>
              <td x-text="p.current_bin != null ? fmt(p.current_bin) : '-'"></td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>
  </div>

  <!-- ═══ Tab 4: Scanner ═══ -->
  <div x-show="activeTab === 'scanner'" x-cloak>
    <!-- Stats Grid -->
    <div class="stats-grid">
      <div class="stat-card">
        <div class="label">Success Rate (1h)</div>
        <div class="value" x-text="health.scan_success_rate_1h != null ? (health.scan_success_rate_1h * 100).toFixed(1) + '%' : '-'"></div>
      </div>
      <div class="stat-card">
        <div class="label">Players in DB</div>
        <div class="value" x-text="fmt(health.players_in_db)"></div>
      </div>
      <div class="stat-card">
        <div class="label">Queue Depth</div>
        <div class="value" x-text="health.queue_depth ?? '-'"></div>
      </div>
      <div class="stat-card">
        <div class="label">Circuit Breaker</div>
        <div class="value" x-text="health.circuit_breaker || '-'"></div>
      </div>
      <div class="stat-card">
        <div class="label">Last Scan</div>
        <div class="value" x-text="health.last_scan_at ? timeAgo(health.last_scan_at) : '-'"></div>
      </div>
      <div class="stat-card">
        <div class="label">Daily Cap</div>
        <div class="value" x-text="dailyCap.count + ' / ' + dailyCap.cap"></div>
        <div class="progress-bar">
          <div class="fill" :style="'width:' + Math.min(100, (dailyCap.count / dailyCap.cap) * 100) + '%'"></div>
        </div>
      </div>
    </div>

    <!-- Top Scored Players Table -->
    <h3 class="section-title">Top Scored Players</h3>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th @click="sortTable('scored', 'name')">Name <span class="sort-arrow" x-text="sortArrow('scored', 'name')"></span></th>
            <th @click="sortTable('scored', 'rating')">Rating <span class="sort-arrow" x-text="sortArrow('scored', 'rating')"></span></th>
            <th @click="sortTable('scored', 'position')">Position <span class="sort-arrow" x-text="sortArrow('scored', 'position')"></span></th>
            <th @click="sortTable('scored', 'price')">Buy Price <span class="sort-arrow" x-text="sortArrow('scored', 'price')"></span></th>
            <th @click="sortTable('scored', 'margin_pct')">Margin <span class="sort-arrow" x-text="sortArrow('scored', 'margin_pct')"></span></th>
            <th @click="sortTable('scored', 'op_ratio')">OP Ratio <span class="sort-arrow" x-text="sortArrow('scored', 'op_ratio')"></span></th>
            <th @click="sortTable('scored', 'expected_profit_per_hour')">Profit/hr <span class="sort-arrow" x-text="sortArrow('scored', 'expected_profit_per_hour')"></span></th>
            <th @click="sortTable('scored', 'efficiency')">Efficiency <span class="sort-arrow" x-text="sortArrow('scored', 'efficiency')"></span></th>
          </tr>
        </thead>
        <tbody>
          <template x-for="p in sorted('scored')" :key="p.ea_id">
            <tr>
              <td x-text="p.name"></td>
              <td x-text="p.rating"></td>
              <td x-text="p.position"></td>
              <td x-text="fmt(p.price)"></td>
              <td x-text="p.margin_pct + '%'"></td>
              <td x-text="(p.op_ratio * 100).toFixed(1) + '%'"></td>
              <td x-text="p.expected_profit_per_hour != null ? fmt(p.expected_profit_per_hour) : '-'"></td>
              <td x-text="p.efficiency.toFixed(4)"></td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>
  </div>

</div>

<script>
function dashboard() {
  return {
    // ── State ──
    apiUrl: localStorage.getItem('op_api_url') || '',
    activeTab: 'profit',
    lastUpdated: null,
    tabs: [
      { id: 'profit', label: 'Profit' },
      { id: 'stale', label: 'Stale Cards' },
      { id: 'portfolio', label: 'Portfolio' },
      { id: 'scanner', label: 'Scanner' },
    ],
    sinceOptions: [
      { value: '1h', label: '1h' },
      { value: '24h', label: '24h' },
      { value: '7d', label: '7d' },
      { value: '30d', label: '30d' },
      { value: null, label: 'All' },
    ],
    filters: {
      profit: null,
      staleUnsold: null,
      staleAvg: null,
      portfolio: null,
    },

    // ── Data ──
    profitData: { totals: {}, per_player: [] },
    staleData: { longest_unsold: [], avg_sale_time: [] },
    portfolioData: { summary: {}, players: [] },
    health: {},
    dailyCap: { count: 0, cap: 500 },
    scoredPlayers: [],

    // ── Sort state per table ──
    sortState: {
      profitters: { col: 'realized_profit', dir: 'desc' },
      profitRate: { col: 'profit_per_hour', dir: 'desc' },
      unsold: { col: 'time_since_buy_hours', dir: 'desc' },
      avgSale: { col: 'avg_hours_between_sales', dir: 'desc' },
      portfolio: { col: 'name', dir: 'asc' },
      scored: { col: 'efficiency', dir: 'desc' },
    },

    // ── Init ──
    init() {
      if (this.apiUrl) this.refreshAll();
    },

    saveApiUrl() {
      localStorage.setItem('op_api_url', this.apiUrl);
    },

    // ── Fetchers ──
    async _fetch(path) {
      if (!this.apiUrl) return null;
      try {
        const url = this.apiUrl.replace(/\/+$/, '') + path;
        const res = await fetch(url);
        if (!res.ok) return null;
        return await res.json();
      } catch (e) {
        console.error('Fetch error:', path, e);
        return null;
      }
    },

    _sinceParam(filterValue) {
      return filterValue ? `?since=${filterValue}` : '';
    },

    async fetchProfit() {
      const data = await this._fetch(`/api/v1/profit/summary${this._sinceParam(this.filters.profit)}`);
      if (data) this.profitData = data;
    },

    async fetchStale() {
      // Stale endpoint uses whichever filter is more restrictive — we just pass one
      // Both views share the same endpoint but have per-section filters.
      // For simplicity, use the unsold filter (both sections refetch on any filter change).
      const sinceParam = this.filters.staleUnsold || this.filters.staleAvg;
      const data = await this._fetch(`/api/v1/portfolio/stale${this._sinceParam(sinceParam)}`);
      if (data) this.staleData = data;
    },

    async fetchPortfolio() {
      const data = await this._fetch(`/api/v1/portfolio/status${this._sinceParam(this.filters.portfolio)}`);
      if (data) this.portfolioData = data;
    },

    async fetchHealth() {
      const data = await this._fetch('/api/v1/health');
      if (data) this.health = data;
    },

    async fetchDailyCap() {
      const data = await this._fetch('/api/v1/automation/daily-cap');
      if (data) this.dailyCap = data;
    },

    async fetchScored() {
      const data = await this._fetch('/api/v1/players/top?limit=100');
      if (data) this.scoredPlayers = data.data || [];
    },

    async refreshAll() {
      await Promise.all([
        this.fetchProfit(),
        this.fetchStale(),
        this.fetchPortfolio(),
        this.fetchHealth(),
        this.fetchDailyCap(),
        this.fetchScored(),
      ]);
      this.lastUpdated = new Date().toLocaleTimeString();
    },

    // ── Sorting ──
    sortTable(tableName, col) {
      const s = this.sortState[tableName];
      if (s.col === col) {
        s.dir = s.dir === 'asc' ? 'desc' : 'asc';
      } else {
        s.col = col;
        s.dir = typeof this._getTableData(tableName)[0]?.[col] === 'string' ? 'asc' : 'desc';
      }
    },

    sortArrow(tableName, col) {
      const s = this.sortState[tableName];
      if (s.col !== col) return '';
      return s.dir === 'asc' ? '\u25B2' : '\u25BC';
    },

    _getTableData(tableName) {
      switch (tableName) {
        case 'profitters': return this.profitData.per_player;
        case 'profitRate': return this.profitData.per_player.filter(p => p.sell_count > 0);
        case 'unsold': return this.staleData.longest_unsold;
        case 'avgSale': return this.staleData.avg_sale_time;
        case 'portfolio': return this.portfolioData.players;
        case 'scored': return this.scoredPlayers;
        default: return [];
      }
    },

    sorted(tableName) {
      const data = [...this._getTableData(tableName)];
      const { col, dir } = this.sortState[tableName];

      // Special handling: null values always sort to bottom
      data.sort((a, b) => {
        let va = a[col], vb = b[col];
        if (va == null && vb == null) return 0;
        if (va == null) return 1;
        if (vb == null) return -1;
        if (typeof va === 'string') {
          return dir === 'asc' ? va.localeCompare(vb) : vb.localeCompare(va);
        }
        return dir === 'asc' ? va - vb : vb - va;
      });
      return data;
    },

    // ── Formatters ──
    fmt(n) {
      if (n == null) return '-';
      return Math.round(n).toLocaleString();
    },

    fmtHours(h) {
      if (h == null) return '-';
      if (h < 1) return Math.round(h * 60) + 'm';
      if (h < 24) return h.toFixed(1) + 'h';
      const days = Math.floor(h / 24);
      const hrs = Math.round(h % 24);
      return days + 'd ' + hrs + 'h';
    },

    timeAgo(iso) {
      if (!iso) return '-';
      const diff = (Date.now() - new Date(iso).getTime()) / 1000;
      if (diff < 60) return Math.round(diff) + 's ago';
      if (diff < 3600) return Math.round(diff / 60) + 'm ago';
      if (diff < 86400) return Math.round(diff / 3600) + 'h ago';
      return Math.round(diff / 86400) + 'd ago';
    },
  };
}
</script>

</body>
</html>
```

- [ ] **Step 2: Verify the dashboard serves**

Run: `curl -s http://localhost:8000/dashboard | head -5`

Expected: Should see `<!DOCTYPE html>` and Alpine.js script tag.

- [ ] **Step 3: Commit**

```bash
git add dashboard.html
git commit -m "feat(dashboard): complete rewrite with Alpine.js, tabs, sortable tables, time filters"
```

---

## Task 5: Integration Test — Dashboard Serves and Endpoints Work Together

**Files:**
- Modify: `tests/integration/test_time_filters.py`

- [ ] **Step 1: Add end-to-end test for dashboard serving**

Append to `tests/integration/test_time_filters.py`:

```python
@pytest.mark.asyncio
async def test_dashboard_serves_html(client: httpx.AsyncClient):
    """GET /dashboard returns HTML with Alpine.js."""
    resp = await client.get("/dashboard")
    assert resp.status_code == 200
    assert "alpinejs" in resp.text
    assert "OP Seller Dashboard" in resp.text


@pytest.mark.asyncio
async def test_profit_summary_includes_profit_rate_fields(client: httpx.AsyncClient):
    """Profit summary per_player entries include profit rate fields."""
    resp = await client.get("/api/v1/profit/summary")
    assert resp.status_code == 200
    data = resp.json()
    for player in data["per_player"]:
        assert "profit_per_hour" in player
        assert "first_buy_at" in player
        assert "last_sell_at" in player
        assert "active_hours" in player
```

- [ ] **Step 2: Run all integration tests**

Run: `pytest tests/integration/test_time_filters.py tests/integration/test_stale_endpoint.py -v`

Expected: All tests PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_time_filters.py
git commit -m "test: add integration tests for dashboard serving and profit rate fields"
```

---

## Task 6: Manual Smoke Test

- [ ] **Step 1: Start the server and open dashboard**

Run: `docker compose up -d api` (or however you start locally).

Open `http://localhost:8000/dashboard` in Chrome.

- [ ] **Step 2: Verify all 4 tabs render**

1. Enter API URL `http://localhost:8000`, click Refresh
2. Profit tab: stats row shows numbers, both tables populate, time filter buttons work
3. Stale Cards tab: both tables populate, time filters work per section
4. Portfolio tab: table populates, time filter works
5. Scanner tab: stats grid shows values, top scored table populates

- [ ] **Step 3: Verify sorting**

Click each column header in any table — verify arrow appears and data reorders. Click again to reverse.

- [ ] **Step 4: Verify time filters**

On Profit tab, click "24h" — numbers should change (fewer trades). Click "All" — back to full data. Repeat for each tab's filter.

- [ ] **Step 5: Final commit if any fixes needed**

```bash
git add -A
git commit -m "fix(dashboard): smoke test fixes"
```
