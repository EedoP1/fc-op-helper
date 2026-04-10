# Phase 4: Architecture Refactor — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Aggressive restructuring of every module for cleaner boundaries and maintainability. No behavioral changes — same functionality, better organization.

**Architecture:** Split large files (portfolio.py 981L, scanner.py 732L, actions.py 524L) into focused modules. Extract inline DB migrations to Alembic. Standardize error handling. Keep raw SQL in scorer_v2 (it's already optimal). Leave models.py and models_db.py separate (they serve different purposes: API DTOs vs ORM).

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy async, Alembic, pytest

---

### Task 1: Extract inline migrations to Alembic

**Files:**
- Modify: `src/server/main.py:48-91` (remove migration blocks)
- Create: `alembic.ini`
- Create: `alembic/env.py`
- Create: `alembic/versions/001_add_is_leftover_to_portfolio_slots.py`
- Create: `alembic/versions/002_add_max_sell_price_to_player_scores.py`
- Create: `alembic/versions/003_purge_v1_scores.py`

- [ ] **Step 1: Install Alembic**

```bash
pip install alembic && pip freeze | grep -i alembic >> requirements.txt
```

- [ ] **Step 2: Initialize Alembic**

```bash
alembic init alembic
```

- [ ] **Step 3: Configure alembic/env.py**

Replace the generated `alembic/env.py` with an async-compatible version that imports our models:

```python
"""Alembic env — async PostgreSQL with SQLAlchemy."""
import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine

from src.server.models_db import Base
from src.config import DATABASE_URL

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in offline mode."""
    context.configure(
        url=DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations():
    connectable = create_async_engine(DATABASE_URL, poolclass=pool.NullPool)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in online mode."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

- [ ] **Step 4: Update alembic.ini**

Set `sqlalchemy.url` to empty (we use DATABASE_URL from config):

```ini
sqlalchemy.url =
```

- [ ] **Step 5: Create migration 001 — add is_leftover column**

Create `alembic/versions/001_add_is_leftover_to_portfolio_slots.py`:

```python
"""Add is_leftover column to portfolio_slots.

Revision ID: 001
"""
from alembic import op
import sqlalchemy as sa

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Idempotent: only add if column doesn't exist
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [c["name"] for c in inspector.get_columns("portfolio_slots")]
    if "is_leftover" not in columns:
        op.add_column(
            "portfolio_slots",
            sa.Column("is_leftover", sa.Boolean(), nullable=False, server_default="false"),
        )


def downgrade() -> None:
    op.drop_column("portfolio_slots", "is_leftover")
```

- [ ] **Step 6: Create migration 002 — add max_sell_price column**

Create `alembic/versions/002_add_max_sell_price_to_player_scores.py`:

```python
"""Add max_sell_price column to player_scores.

Revision ID: 002
"""
from alembic import op
import sqlalchemy as sa

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [c["name"] for c in inspector.get_columns("player_scores")]
    if "max_sell_price" not in columns:
        op.add_column(
            "player_scores",
            sa.Column("max_sell_price", sa.Integer(), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("player_scores", "max_sell_price")
```

- [ ] **Step 7: Create migration 003 — purge v1 scores**

Create `alembic/versions/003_purge_v1_scores.py`:

```python
"""Purge stale v1 scores lacking expected_profit_per_hour.

Revision ID: 003
"""
from alembic import op

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DELETE FROM player_scores WHERE expected_profit_per_hour IS NULL")


def downgrade() -> None:
    pass  # Cannot restore deleted data
```

- [ ] **Step 8: Remove inline migrations from src/server/main.py**

Remove lines 48-91 (the three migration blocks) from `src/server/main.py`. The lifespan function should go straight from `create_engine_and_tables()` to the scanner/scheduler setup.

- [ ] **Step 9: Add Alembic upgrade to lifespan**

In `src/server/main.py` lifespan, after `create_engine_and_tables()`, add:

```python
from alembic.config import Config
from alembic import command

alembic_cfg = Config("alembic.ini")
command.upgrade(alembic_cfg, "head")
```

- [ ] **Step 10: Run tests**

```bash
python -m pytest tests/ --ignore=tests/integration -v
```

Expected: All tests pass.

- [ ] **Step 11: Commit**

```bash
git add alembic/ alembic.ini src/server/main.py requirements.txt
git commit -m "refactor: extract inline migrations to Alembic

Three inline migration blocks in server startup replaced with versioned
Alembic migrations. Supports rollback, dry-run, and independent testing.
Migrations are idempotent (check column existence before ALTER)."
```

---

### Task 2: Split portfolio.py into focused modules

**Files:**
- Modify: `src/server/api/portfolio.py` (981 lines → ~120 lines as router wiring)
- Create: `src/server/api/_helpers.py` (shared helpers)
- Create: `src/server/api/portfolio_query.py` (data fetching + optimization logic)
- Create: `src/server/api/portfolio_read.py` (GET endpoints)
- Create: `src/server/api/portfolio_write.py` (POST/DELETE endpoints)

**Split strategy:**

| New file | Contains | Line count |
|----------|----------|-----------|
| `_helpers.py` | `_read_session_factory()`, Pydantic request/response models | ~70 |
| `portfolio_query.py` | `_PlayerProxy`, `_build_scored_entry()`, `_fetch_latest_viable_scores()` | ~130 |
| `portfolio_read.py` | `get_portfolio()`, `generate_portfolio()`, `get_confirmed_portfolio()`, `swap_preview()`, `get_actions_needed()` | ~400 |
| `portfolio_write.py` | `confirm_portfolio()`, `rebalance_portfolio()`, `delete_portfolio_player()` | ~380 |
| `portfolio.py` | Router definition + includes sub-routers | ~20 |

- [ ] **Step 1: Create `src/server/api/_helpers.py`**

Extract from portfolio.py:
- `_read_session_factory()` (lines 25-28)
- All Pydantic request models: `GenerateRequest`, `ConfirmPlayer`, `ConfirmRequest`, `SwapPreviewRequest`, `RebalanceRequest`

```python
"""Shared helpers and request models for portfolio API."""
from fastapi import Request
from pydantic import BaseModel, Field


def _read_session_factory(request: Request):
    """Return the read-only session factory if available, else the default one."""
    return getattr(request.app.state, "read_session_factory", None) or request.app.state.session_factory


class GenerateRequest(BaseModel):
    """Request body for POST /portfolio/generate."""
    budget: int = Field(..., gt=0, description="Total budget in coins")


class ConfirmPlayer(BaseModel):
    """A single player entry in a confirm request."""
    ea_id: int
    buy_price: int
    sell_price: int


class ConfirmRequest(BaseModel):
    """Request body for POST /portfolio/confirm."""
    players: list[ConfirmPlayer]


class SwapPreviewRequest(BaseModel):
    """Request body for POST /portfolio/swap-preview."""
    budget: int = Field(..., gt=0)
    excluded_ea_ids: list[int] = Field(default_factory=list)


class RebalanceRequest(BaseModel):
    """Request body for POST /portfolio/rebalance."""
    budget: int = Field(..., gt=0)
```

- [ ] **Step 2: Create `src/server/api/portfolio_query.py`**

Extract from portfolio.py:
- `_PlayerProxy` class (lines 70-76)
- `_build_scored_entry()` (lines 79-105)
- `_fetch_latest_viable_scores()` (lines 108-193)

```python
"""Portfolio data fetching and score preparation."""
import logging
from datetime import datetime, timedelta

from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.server.models_db import PlayerRecord, PlayerScore
from src.config import STALE_THRESHOLD_HOURS

logger = logging.getLogger(__name__)


class _PlayerProxy:
    """Minimal adapter so optimize_portfolio() sees .buy_price."""

    def __init__(self, buy_price: int):
        self.buy_price = buy_price


def _build_scored_entry(row, player_rec) -> dict:
    """Build a scored-player dict from a PlayerScore row + PlayerRecord."""
    # Copy lines 79-105 from portfolio.py exactly
    ...


async def _fetch_latest_viable_scores(session: AsyncSession, *, budget: int | None = None) -> list[dict]:
    """Fetch latest viable score per player using ROW_NUMBER."""
    # Copy lines 108-193 from portfolio.py exactly
    ...
```

- [ ] **Step 3: Create `src/server/api/portfolio_read.py`**

Extract GET and preview endpoints:

```python
"""Portfolio read endpoints — GET operations and previews."""
import logging

from fastapi import APIRouter, Query, Request, HTTPException
from sqlalchemy import select, func

from src.server.models_db import PlayerRecord, PlayerScore, PortfolioSlot, TradeAction, TradeRecord
from src.config import STALE_THRESHOLD_HOURS, TARGET_PLAYER_COUNT
from src.optimizer import optimize_portfolio

from src.server.api._helpers import _read_session_factory, GenerateRequest, SwapPreviewRequest
from src.server.api.portfolio_query import _fetch_latest_viable_scores, _build_scored_entry, _PlayerProxy

logger = logging.getLogger(__name__)
router = APIRouter()

# Copy these functions from portfolio.py:
# get_portfolio() (lines 196-276)
# generate_portfolio() (lines 279-351)
# swap_preview() (lines 469-535)
# get_confirmed_portfolio() (lines 538-586)
# get_actions_needed() (lines 589-713)
```

- [ ] **Step 4: Create `src/server/api/portfolio_write.py`**

Extract mutation endpoints:

```python
"""Portfolio write endpoints — POST/DELETE mutations."""
import logging

from fastapi import APIRouter, Request, Path, HTTPException
from sqlalchemy import select, func, update, delete

from src.server.models_db import PlayerRecord, PlayerScore, PortfolioSlot, TradeAction, TradeRecord
from src.config import TARGET_PLAYER_COUNT
from src.optimizer import optimize_portfolio

from src.server.api._helpers import _read_session_factory, ConfirmRequest, RebalanceRequest
from src.server.api.portfolio_query import _fetch_latest_viable_scores, _build_scored_entry, _PlayerProxy

logger = logging.getLogger(__name__)
router = APIRouter()

# Copy these functions from portfolio.py:
# confirm_portfolio() (lines 354-466)
# rebalance_portfolio() (lines 722-850)
# delete_portfolio_player() (lines 853-981)
```

- [ ] **Step 5: Rewrite `src/server/api/portfolio.py` as router aggregator**

```python
"""Portfolio API — aggregates read and write sub-routers."""
from fastapi import APIRouter

from src.server.api.portfolio_read import router as read_router
from src.server.api.portfolio_write import router as write_router

router = APIRouter(prefix="/api/v1")
router.include_router(read_router)
router.include_router(write_router)
```

- [ ] **Step 6: Update `src/server/main.py` import**

The main.py import `from src.server.api.portfolio import router as portfolio_router` should still work since `portfolio.py` still exports `router`.

- [ ] **Step 7: Run tests**

```bash
python -m pytest tests/ --ignore=tests/integration -v
```

Expected: All tests pass. The router paths are unchanged.

- [ ] **Step 8: Commit**

```bash
git add src/server/api/
git commit -m "refactor: split portfolio.py (981L) into focused modules

portfolio_query.py: data fetching + score preparation (130L)
portfolio_read.py: GET endpoints + previews (400L)
portfolio_write.py: POST/DELETE mutations (380L)
_helpers.py: shared session helper + Pydantic models (70L)
portfolio.py: router aggregator (20L)

No behavioral changes — all endpoints retain same paths and logic."
```

---

### Task 3: Extract scanner sub-modules

**Files:**
- Modify: `src/server/scanner.py` (732 lines → ~350 lines core)
- Create: `src/server/scanner_discovery.py` (~200 lines)
- Create: `src/server/scanner_jobs.py` (~80 lines)

**Split strategy:**

| New file | Contains | Functions |
|----------|----------|-----------|
| `scanner_discovery.py` | Bootstrap + discovery logic | `run_bootstrap()`, `run_initial_scoring()`, `run_discovery()` |
| `scanner_jobs.py` | Scheduled maintenance jobs | `run_aggregation()`, `run_cleanup()` |
| `scanner.py` (remaining) | Core scan loop + dispatch | `ScannerService`, `scan_player()`, `_scan_player_inner()`, `dispatch_scans()`, metrics |

- [ ] **Step 1: Create `src/server/scanner_discovery.py`**

Extract discovery functions as standalone async functions that accept the dependencies they need (session_factory, client, circuit_breaker) as parameters:

```python
"""Scanner discovery — bootstrap and periodic player discovery."""
import logging
from datetime import datetime, timedelta

from sqlalchemy import select, func, update

from src.server.models_db import PlayerRecord, PlayerScore
from src.config import SCANNER_MIN_PRICE, SCANNER_MAX_PRICE

logger = logging.getLogger(__name__)


async def run_bootstrap(session_factory, client, *, min_price=SCANNER_MIN_PRICE, max_price=SCANNER_MAX_PRICE):
    """Discover all players in price range and seed the players table."""
    # Copy lines 138-207 from scanner.py
    ...


async def run_initial_scoring(scanner_service, session_factory, *, concurrency=10, batch_size=50):
    """Score all unscored players with high concurrency."""
    # Copy lines 210-255 from scanner.py
    ...


async def run_discovery(session_factory, client, circuit_breaker, *, min_price=SCANNER_MIN_PRICE, max_price=SCANNER_MAX_PRICE):
    """Periodic rediscovery — find new players, mark removed ones cold."""
    # Copy lines 265-332 from scanner.py
    ...
```

- [ ] **Step 2: Create `src/server/scanner_jobs.py`**

Extract scheduled maintenance jobs:

```python
"""Scanner scheduled jobs — aggregation and cleanup."""
import logging
from datetime import datetime, timedelta

from sqlalchemy import select, delete

from src.server.models_db import MarketSnapshot, ListingObservation, DailyListingSummary
from src.config import MARKET_DATA_RETENTION_DAYS, LISTING_RETENTION_DAYS, AGGREGATION_HOUR_UTC

logger = logging.getLogger(__name__)


async def run_aggregation(session_factory, listing_tracker):
    """Daily aggregation of listing observations into daily summaries."""
    # Copy lines 619-643 from scanner.py
    ...


async def run_cleanup(session_factory):
    """Purge old market snapshots and listing observations past retention."""
    # Copy lines 647-695 from scanner.py
    ...
```

- [ ] **Step 3: Update `src/server/scanner.py`**

Replace extracted functions with imports and delegation:

```python
from src.server.scanner_discovery import run_bootstrap, run_initial_scoring, run_discovery
from src.server.scanner_jobs import run_aggregation, run_cleanup
```

Update `ScannerService` methods to delegate:

```python
async def run_bootstrap(self):
    await run_bootstrap(self._session_factory, self._client)

async def run_initial_scoring(self):
    await run_initial_scoring(self, self._session_factory)

# etc.
```

- [ ] **Step 4: Update scheduler.py if it references scanner methods directly**

Check and update any imports in `scheduler.py` or `scanner_main.py`.

- [ ] **Step 5: Run tests**

```bash
python -m pytest tests/test_scanner.py -v
python -m pytest tests/ --ignore=tests/integration -v
```

Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/server/scanner.py src/server/scanner_discovery.py src/server/scanner_jobs.py src/server/scheduler.py src/server/scanner_main.py
git commit -m "refactor: extract scanner discovery and jobs into separate modules

scanner_discovery.py: bootstrap, initial scoring, periodic discovery (200L)
scanner_jobs.py: aggregation and cleanup scheduled tasks (80L)
scanner.py: core scan loop + dispatch (350L, down from 732L)

No behavioral changes — ScannerService delegates to extracted functions."
```

---

### Task 4: Extract actions.py lifecycle logic

**Files:**
- Modify: `src/server/api/actions.py` (524 lines → ~350 lines)
- Create: `src/server/lifecycle.py` (~180 lines)

- [ ] **Step 1: Create `src/server/lifecycle.py`**

Extract lifecycle derivation and trade record validation:

```python
"""Portfolio lifecycle logic — derives next actions and validates trade records."""
import logging
from datetime import datetime

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from src.server.models_db import PortfolioSlot, TradeAction, TradeRecord

logger = logging.getLogger(__name__)

# Outcome → action type mapping
OUTCOME_TO_ACTION_TYPE = {
    "bought": "LIST",
    "listed": None,    # waiting for outcome
    "sold": "BUY",     # rebuy
    "expired": "RELIST",
}


async def derive_next_action(session: AsyncSession, slot: PortfolioSlot) -> TradeAction | None:
    """Determine the next action for a portfolio slot based on trade history.

    Examines the most recent trade record for this slot and derives
    what action should happen next in the buy→list→sell cycle.
    """
    # Extract lines 90-176 from actions.py
    ...


async def validate_trade_record(session: AsyncSession, ea_id: int, outcome: str, price: int) -> tuple[PortfolioSlot | None, str | None]:
    """Validate a trade record against portfolio state.

    Returns (slot, error_message). If error_message is not None, the record is invalid.
    """
    # Extract the common validation logic from lines 379-405 and 456-480
    ...
```

- [ ] **Step 2: Update `src/server/api/actions.py`**

Import from lifecycle.py and simplify the endpoint functions:

```python
from src.server.lifecycle import derive_next_action, validate_trade_record, OUTCOME_TO_ACTION_TYPE
```

Replace inline derivation calls with `await derive_next_action(session, slot)`.
Replace duplicated validation with `await validate_trade_record(session, ea_id, outcome, price)`.

- [ ] **Step 3: Run tests**

```bash
python -m pytest tests/test_actions.py -v
python -m pytest tests/ --ignore=tests/integration -v
```

Expected: All tests pass.

- [ ] **Step 4: Commit**

```bash
git add src/server/lifecycle.py src/server/api/actions.py
git commit -m "refactor: extract lifecycle derivation from actions.py

lifecycle.py: derive_next_action(), validate_trade_record() (180L)
actions.py: endpoint orchestration only (350L, down from 524L)

Eliminates duplicated validation between direct and batch trade records."
```

---

### Task 5: Standardize error handling

**Files:**
- Create: `src/server/exceptions.py` (~30 lines)
- Modify: `src/server/scorer_v2.py` (add logging for insufficient data)
- Modify: `src/server/api/portfolio_read.py` (use exceptions instead of error dicts)

- [ ] **Step 1: Create `src/server/exceptions.py`**

```python
"""Application-specific exceptions for the server."""


class InsufficientDataError(Exception):
    """Raised when scoring has insufficient observations for a player."""
    pass


class ScoringError(Exception):
    """Raised when the scoring pipeline encounters a fatal error."""
    pass
```

- [ ] **Step 2: Add logging in scorer_v2.py for insufficient data**

Where `score_player_v2()` currently returns `None` silently on insufficient data, add a debug log:

```python
logger.debug("ea_id=%d: insufficient data (%d observations, need %d)", ea_id, total_obs, MIN_TOTAL_RESOLVED_OBSERVATIONS)
```

Note: Don't raise an exception here — the caller (scanner) handles None returns gracefully and this is a normal case (new players with few observations).

- [ ] **Step 3: Run tests**

```bash
python -m pytest tests/ --ignore=tests/integration -v
```

Expected: All tests pass.

- [ ] **Step 4: Commit**

```bash
git add src/server/exceptions.py src/server/scorer_v2.py
git commit -m "refactor: add exceptions module and scorer logging

exceptions.py: InsufficientDataError, ScoringError for future use
scorer_v2.py: debug logging when player has insufficient observations"
```

---

### Task 6: Final verification

- [ ] **Step 1: Run full unit test suite**

```bash
python -m pytest tests/ --ignore=tests/integration -v
```

Expected: All tests pass.

- [ ] **Step 2: Run integration tests**

```bash
python -m pytest tests/integration/ -v
```

Expected: All integration tests pass.

- [ ] **Step 3: Verify all imports resolve**

```bash
python -c "
from src.server.api.portfolio import router
from src.server.api.portfolio_read import router as r1
from src.server.api.portfolio_write import router as r2
from src.server.api.portfolio_query import _fetch_latest_viable_scores
from src.server.api._helpers import _read_session_factory
from src.server.scanner_discovery import run_bootstrap
from src.server.scanner_jobs import run_aggregation, run_cleanup
from src.server.lifecycle import derive_next_action
from src.server.exceptions import InsufficientDataError, ScoringError
print('All new module imports OK')
"
```

Expected: "All new module imports OK"

- [ ] **Step 4: Check no files exceed 400 lines**

```bash
find src/ -name "*.py" -exec wc -l {} + | sort -rn | head -20
```

Expected: No file over ~400 lines (scanner.py should be ~350, portfolio_read.py ~400).

- [ ] **Step 5: Final commit if any cleanup needed**

```bash
git add -u
git commit -m "refactor: final cleanup after architecture refactor"
```

Only if needed.
