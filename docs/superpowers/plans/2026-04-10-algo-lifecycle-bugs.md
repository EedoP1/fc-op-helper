# Algo Lifecycle Bug Fixes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix 3 bugs blocking live algo trading: strategy duplicate-buy qty mismatch, SELL outcome string rejection, and premature position deletion before actual sale.

**Architecture:** Bug #1 is a one-line strategy guard. Bugs #2+#3 are a lifecycle redesign: the signal lifecycle ends at "listed" (DONE), and two new position-level endpoints handle relist and sold. A new TL sweep phase in the algo automation loop detects sold/expired cards and reports to the backend.

**Tech Stack:** Python/FastAPI (backend), TypeScript/WXT (extension), SQLAlchemy (ORM), pytest (backend tests)

---

### Task 1: Fix strategy duplicate-buy guard (Bug #1)

**Files:**
- Modify: `src/algo/strategies/promo_dip_buy.py:175-210`
- Test: `tests/algo/test_signal_parity.py` (existing, run to verify no regression)

- [ ] **Step 1: Add holdings guard to strong-buy layer**

In `src/algo/strategies/promo_dip_buy.py`, add a guard after line 179 (`if ea_id not in self._promo_ids: continue`):

```python
            if portfolio.holdings(ea_id) > 0:
                continue
```

This prevents the strategy from emitting duplicate BUY signals for a player already held. The snapshot-buy layer already has this check at line 232.

- [ ] **Step 2: Run parity and strategy tests**

Run: `python -m pytest tests/algo/test_signal_parity.py tests/algo/test_signal_parity_db.py tests/algo/test_strategies.py -v`
Expected: all pass (the strategy now produces BUY qty == SELL qty for every player)

- [ ] **Step 3: Run signal engine against real DB to verify qty alignment**

```bash
python -c "
import asyncio
from datetime import datetime
from sqlalchemy import select, delete
from src.server.db import create_engine_and_tables
from src.server.models_db import AlgoConfig, AlgoSignal, AlgoPosition
from src.server.algo_runner import run_signal_engine
import logging
logging.basicConfig(level=logging.INFO)

async def main():
    engine, sf = await create_engine_and_tables()
    # Clean slate: remove old signals
    async with sf() as s:
        await s.execute(delete(AlgoSignal))
        await s.execute(delete(AlgoPosition))
        config = (await s.execute(select(AlgoConfig))).scalar_one_or_none()
        if not config:
            s.add(AlgoConfig(budget=500000, is_active=True, strategy_params=None,
                             created_at=datetime.utcnow(), updated_at=datetime.utcnow()))
        else:
            config.is_active = True
        await s.commit()
    count = await run_signal_engine(sf)
    print(f'Generated {count} signals')
    async with sf() as s:
        sigs = (await s.execute(select(AlgoSignal).order_by(AlgoSignal.ea_id, AlgoSignal.created_at))).scalars().all()
        # Group by ea_id and check BUY qty == SELL qty
        from collections import defaultdict
        by_player = defaultdict(list)
        for sig in sigs:
            by_player[sig.ea_id].append(sig)
        for ea_id, player_sigs in by_player.items():
            buys = [s for s in player_sigs if s.action == 'BUY']
            sells = [s for s in player_sigs if s.action == 'SELL']
            buy_qty = sum(s.quantity for s in buys)
            sell_qty = sum(s.quantity for s in sells)
            status = 'OK' if buy_qty == sell_qty else 'MISMATCH'
            print(f'  ea_id={ea_id}: BUY qty={buy_qty}, SELL qty={sell_qty} [{status}]')
    await engine.dispose()
asyncio.run(main())
"
```

Expected: every player shows `[OK]` — no mismatches.

- [ ] **Step 4: Commit**

```bash
git add src/algo/strategies/promo_dip_buy.py
git commit -m "fix(algo): add holdings guard in strong-buy layer to prevent duplicate BUYs"
```

---

### Task 2: Add AlgoTrade model and extend AlgoPosition schema

**Files:**
- Modify: `src/server/models_db.py:232-244`
- Test: `tests/algo/test_algo_api.py` (existing fixtures use these models)

- [ ] **Step 1: Add listed_at and listed_price to AlgoPosition**

In `src/server/models_db.py`, after the `peak_price` column on line 243, add:

```python
    listed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    listed_price: Mapped[int | None] = mapped_column(Integer, nullable=True)
```

- [ ] **Step 2: Add AlgoTrade model**

After the `AlgoPosition` class (after line 244), add:

```python


class AlgoTrade(Base):
    """Realized algo trade — one row per partial or full sale."""

    __tablename__ = "algo_trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ea_id: Mapped[int] = mapped_column(Integer, index=True)
    quantity: Mapped[int] = mapped_column(Integer)
    buy_price: Mapped[int] = mapped_column(Integer)
    sell_price: Mapped[int] = mapped_column(Integer)
    pnl: Mapped[int] = mapped_column(Integer)
    sold_at: Mapped[datetime] = mapped_column(DateTime)
```

- [ ] **Step 3: Run existing algo model tests to verify no regression**

Run: `python -m pytest tests/algo/test_algo_api.py tests/algo/test_models_db.py -v`
Expected: all pass (new nullable columns don't break existing tests)

- [ ] **Step 4: Commit**

```bash
git add src/server/models_db.py
git commit -m "feat(algo): add AlgoTrade model and listed_at/listed_price to AlgoPosition"
```

---

### Task 3: Add 'listed' outcome handling to complete_signal endpoint

**Files:**
- Modify: `src/server/api/algo.py:327-375`
- Test: `tests/algo/test_algo_api.py`

- [ ] **Step 1: Write the failing test for 'listed' outcome**

Add to `tests/algo/test_algo_api.py`:

```python
@pytest.mark.asyncio
async def test_signal_complete_listed(client, db):
    """POST /algo/signals/{id}/complete with outcome=listed updates position, keeps it."""
    async with db() as session:
        now = datetime.utcnow()
        session.add(AlgoPosition(
            ea_id=9001, quantity=5, buy_price=25000,
            buy_time=now, peak_price=30000,
        ))
        session.add(AlgoSignal(
            ea_id=9001, action="SELL", quantity=5, reference_price=45000,
            status="CLAIMED", created_at=now, claimed_at=now,
        ))
        await session.commit()
        signal_id = (await session.execute(select(AlgoSignal))).scalar_one().id

    resp = await client.post(
        f"/api/v1/algo/signals/{signal_id}/complete",
        json={"outcome": "listed", "price": 45000, "quantity": 5},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

    async with db() as session:
        sig = (await session.execute(select(AlgoSignal))).scalar_one()
        positions = (await session.execute(select(AlgoPosition))).scalars().all()

    # Signal is DONE
    assert sig.status == "DONE"
    # Position is NOT deleted — still present with listed_at and listed_price
    assert len(positions) == 1
    pos = positions[0]
    assert pos.ea_id == 9001
    assert pos.quantity == 5
    assert pos.listed_price == 45000
    assert pos.listed_at is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/algo/test_algo_api.py::test_signal_complete_listed -v`
Expected: FAIL — `'listed'` not in valid_outcomes → 400

- [ ] **Step 3: Implement 'listed' outcome in complete_signal**

In `src/server/api/algo.py`, make these changes:

Change line 327 to add 'listed':
```python
    valid_outcomes = {"bought", "sold", "listed", "failed", "skipped"}
```

Add a new `elif` block after the `"bought"` block (after line 353), before the `"sold"` block:

```python
        elif payload.outcome == "listed":
            pos_result = await session.execute(
                select(AlgoPosition).where(AlgoPosition.ea_id == signal.ea_id)
            )
            pos = pos_result.scalar_one_or_none()
            if pos is not None:
                pos.listed_price = payload.price
                pos.listed_at = now
            signal.status = "DONE"
            signal.completed_at = now
```

Also add the import for AlgoTrade at the top of `algo.py` (line 17):
```python
from src.server.models_db import AlgoConfig, AlgoPosition, AlgoSignal, AlgoTrade, PlayerRecord
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/algo/test_algo_api.py::test_signal_complete_listed -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/server/api/algo.py tests/algo/test_algo_api.py
git commit -m "feat(algo): accept 'listed' outcome — update position, keep it alive"
```

---

### Task 4: Add /algo/positions/{ea_id}/sold endpoint

**Files:**
- Modify: `src/server/api/algo.py`
- Test: `tests/algo/test_algo_api.py`

- [ ] **Step 1: Write failing tests for sold endpoint**

Add to `tests/algo/test_algo_api.py`:

```python
from src.server.models_db import AlgoTrade


@pytest.mark.asyncio
async def test_position_sold_full(client, db):
    """POST /algo/positions/{ea_id}/sold with full quantity deletes position and writes trade."""
    async with db() as session:
        now = datetime.utcnow()
        session.add(AlgoPosition(
            ea_id=10001, quantity=5, buy_price=25000,
            buy_time=now, peak_price=30000,
            listed_at=now, listed_price=45000,
        ))
        await session.commit()

    resp = await client.post(
        "/api/v1/algo/positions/10001/sold",
        json={"sell_price": 45000, "quantity": 5},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["pnl"] == 5 * (int(45000 * 0.95) - 25000)  # 5 * (42750 - 25000) = 88750

    async with db() as session:
        positions = (await session.execute(select(AlgoPosition))).scalars().all()
        trades = (await session.execute(select(AlgoTrade))).scalars().all()

    assert len(positions) == 0  # fully sold — deleted
    assert len(trades) == 1
    trade = trades[0]
    assert trade.ea_id == 10001
    assert trade.quantity == 5
    assert trade.buy_price == 25000
    assert trade.sell_price == 45000
    assert trade.pnl == 5 * (int(45000 * 0.95) - 25000)


@pytest.mark.asyncio
async def test_position_sold_partial(client, db):
    """POST /algo/positions/{ea_id}/sold with partial quantity decrements position."""
    async with db() as session:
        now = datetime.utcnow()
        session.add(AlgoPosition(
            ea_id=10002, quantity=8, buy_price=25000,
            buy_time=now, peak_price=30000,
            listed_at=now, listed_price=45000,
        ))
        await session.commit()

    resp = await client.post(
        "/api/v1/algo/positions/10002/sold",
        json={"sell_price": 45000, "quantity": 3},
    )
    assert resp.status_code == 200

    async with db() as session:
        pos = (await session.execute(select(AlgoPosition))).scalar_one()
        trades = (await session.execute(select(AlgoTrade))).scalars().all()

    assert pos.quantity == 5  # 8 - 3
    assert len(trades) == 1
    assert trades[0].quantity == 3


@pytest.mark.asyncio
async def test_position_sold_not_found(client, db):
    """POST /algo/positions/{ea_id}/sold returns 404 if no position."""
    resp = await client.post(
        "/api/v1/algo/positions/99999/sold",
        json={"sell_price": 45000, "quantity": 1},
    )
    assert resp.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/algo/test_algo_api.py::test_position_sold_full tests/algo/test_algo_api.py::test_position_sold_partial tests/algo/test_algo_api.py::test_position_sold_not_found -v`
Expected: FAIL — endpoint doesn't exist (404 from router, not our 404)

- [ ] **Step 3: Implement the endpoint**

Add to `src/server/api/algo.py`, after the `complete_signal` function:

```python
class PositionSoldPayload(BaseModel):
    """Payload for POST /api/v1/algo/positions/{ea_id}/sold."""

    sell_price: int
    quantity: int


@router.post("/algo/positions/{ea_id}/sold", status_code=200)
async def position_sold(ea_id: int, payload: PositionSoldPayload, request: Request):
    """Record that algo cards actually sold on the transfer market.

    Decrements position quantity, writes an AlgoTrade row for PnL tracking.
    Deletes the position entirely when quantity reaches 0.

    Args:
        ea_id: Player EA ID.
        payload: Contains sell_price and quantity.
        request: FastAPI request (session_factory on app.state).

    Returns:
        Dict with status "ok" and pnl for this sale.

    Raises:
        HTTPException 404 if no position exists for ea_id.
    """
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        result = await session.execute(
            select(AlgoPosition).where(AlgoPosition.ea_id == ea_id)
        )
        pos = result.scalar_one_or_none()

        if pos is None:
            raise HTTPException(status_code=404, detail=f"No position for ea_id={ea_id}")

        now = datetime.utcnow()
        per_unit_net = int(payload.sell_price * (1 - EA_TAX_RATE)) - pos.buy_price
        pnl = per_unit_net * payload.quantity

        session.add(AlgoTrade(
            ea_id=ea_id,
            quantity=payload.quantity,
            buy_price=pos.buy_price,
            sell_price=payload.sell_price,
            pnl=pnl,
            sold_at=now,
        ))

        pos.quantity -= payload.quantity
        if pos.quantity <= 0:
            await session.delete(pos)

        await session.commit()

    logger.info(
        "Algo position sold: ea_id=%d qty=%d sell_price=%d pnl=%d",
        ea_id, payload.quantity, payload.sell_price, pnl,
    )
    return {"status": "ok", "pnl": pnl}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/algo/test_algo_api.py::test_position_sold_full tests/algo/test_algo_api.py::test_position_sold_partial tests/algo/test_algo_api.py::test_position_sold_not_found -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/server/api/algo.py tests/algo/test_algo_api.py
git commit -m "feat(algo): add /positions/{ea_id}/sold endpoint with PnL tracking"
```

---

### Task 5: Add /algo/positions/{ea_id}/relist endpoint

**Files:**
- Modify: `src/server/api/algo.py`
- Test: `tests/algo/test_algo_api.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/algo/test_algo_api.py`:

```python
@pytest.mark.asyncio
async def test_position_relist(client, db):
    """POST /algo/positions/{ea_id}/relist updates listed_price and listed_at."""
    async with db() as session:
        now = datetime.utcnow()
        session.add(AlgoPosition(
            ea_id=11001, quantity=5, buy_price=25000,
            buy_time=now, peak_price=30000,
            listed_at=now, listed_price=45000,
        ))
        await session.commit()

    resp = await client.post(
        "/api/v1/algo/positions/11001/relist",
        json={"price": 42000, "quantity": 5},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

    async with db() as session:
        pos = (await session.execute(select(AlgoPosition))).scalar_one()

    assert pos.listed_price == 42000
    assert pos.listed_at is not None
    assert pos.quantity == 5  # unchanged


@pytest.mark.asyncio
async def test_position_relist_not_found(client, db):
    """POST /algo/positions/{ea_id}/relist returns 404 if no position."""
    resp = await client.post(
        "/api/v1/algo/positions/99999/relist",
        json={"price": 42000, "quantity": 5},
    )
    assert resp.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/algo/test_algo_api.py::test_position_relist tests/algo/test_algo_api.py::test_position_relist_not_found -v`
Expected: FAIL — endpoint doesn't exist

- [ ] **Step 3: Implement the endpoint**

Add to `src/server/api/algo.py`, after the `position_sold` function:

```python
class PositionRelistPayload(BaseModel):
    """Payload for POST /api/v1/algo/positions/{ea_id}/relist."""

    price: int
    quantity: int


@router.post("/algo/positions/{ea_id}/relist", status_code=200)
async def position_relist(ea_id: int, payload: PositionRelistPayload, request: Request):
    """Record that an expired algo card was relisted at a new price.

    Updates the position's listed_price and listed_at. Quantity is informational
    (position quantity does not change — cards are still held, just relisted).

    Args:
        ea_id: Player EA ID.
        payload: Contains price (new BIN) and quantity.
        request: FastAPI request (session_factory on app.state).

    Returns:
        Dict with status "ok".

    Raises:
        HTTPException 404 if no position exists for ea_id.
    """
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        result = await session.execute(
            select(AlgoPosition).where(AlgoPosition.ea_id == ea_id)
        )
        pos = result.scalar_one_or_none()

        if pos is None:
            raise HTTPException(status_code=404, detail=f"No position for ea_id={ea_id}")

        now = datetime.utcnow()
        pos.listed_price = payload.price
        pos.listed_at = now
        await session.commit()

    logger.info("Algo position relisted: ea_id=%d price=%d", ea_id, payload.price)
    return {"status": "ok"}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/algo/test_algo_api.py::test_position_relist tests/algo/test_algo_api.py::test_position_relist_not_found -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/server/api/algo.py tests/algo/test_algo_api.py
git commit -m "feat(algo): add /positions/{ea_id}/relist endpoint"
```

---

### Task 6: Add realized_pnl to /algo/status response

**Files:**
- Modify: `src/server/api/algo.py` (the `algo_status` function)
- Test: `tests/algo/test_algo_api.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/algo/test_algo_api.py`:

```python
@pytest.mark.asyncio
async def test_status_includes_realized_pnl(client, db):
    """GET /algo/status includes realized_pnl from algo_trades."""
    async with db() as session:
        now = datetime.utcnow()
        session.add(AlgoConfig(
            budget=500000, is_active=True, strategy_params=None,
            created_at=now, updated_at=now,
        ))
        session.add(AlgoTrade(
            ea_id=12001, quantity=3, buy_price=25000,
            sell_price=45000, pnl=53250, sold_at=now,
        ))
        session.add(AlgoTrade(
            ea_id=12002, quantity=2, buy_price=15000,
            sell_price=20000, pnl=8000, sold_at=now,
        ))
        await session.commit()

    resp = await client.get("/api/v1/algo/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["realized_pnl"] == 53250 + 8000
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/algo/test_algo_api.py::test_status_includes_realized_pnl -v`
Expected: FAIL — `realized_pnl` key not in response

- [ ] **Step 3: Implement realized_pnl in algo_status**

In `src/server/api/algo.py`, in the `algo_status` function:

After the `pending_count` query (around line 233), add:

```python
        # Sum realized PnL from algo_trades
        from sqlalchemy import func as sa_func
        pnl_result = await session.execute(
            select(sa_func.coalesce(sa_func.sum(AlgoTrade.pnl), 0))
        )
        realized_pnl = pnl_result.scalar_one()
```

In the return dict (around line 236), add the new field:

```python
    return {
        "is_active": config.is_active,
        "budget": config.budget,
        "cash": cash,
        "positions": position_rows,
        "pending_signals": pending_count,
        "realized_pnl": realized_pnl,
    }
```

Also add `realized_pnl` to the early-return for missing config (around line 165):

```python
        if config is None:
            return {
                "is_active": False,
                "budget": 0,
                "cash": 0,
                "positions": [],
                "pending_signals": 0,
                "realized_pnl": 0,
            }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/algo/test_algo_api.py::test_status_includes_realized_pnl -v`
Expected: PASS

- [ ] **Step 5: Run all algo API tests to check for regressions**

Run: `python -m pytest tests/algo/test_algo_api.py -v`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add src/server/api/algo.py tests/algo/test_algo_api.py
git commit -m "feat(algo): add realized_pnl to /algo/status response"
```

---

### Task 7: Add extension message types for position sold/relist

**Files:**
- Modify: `extension/src/messages.ts`
- Modify: `extension/entrypoints/background.ts`

- [ ] **Step 1: Update AlgoPosition and AlgoStatusData types**

In `extension/src/messages.ts`, update the `AlgoPosition` type (around line 83) to add listed fields:

```typescript
/** Algo position from GET /algo/status. */
export type AlgoPosition = {
  ea_id: number;
  player_name: string;
  quantity: number;
  buy_price: number;
  buy_time: string;
  current_price: number;
  peak_price: number;
  unrealized_pnl: number;
  listed_price: number | null;
  listed_at: string | null;
};
```

Update `AlgoStatusData` to replace `total_pnl` with `realized_pnl`:

```typescript
/** Full response shape from GET /algo/status. */
export type AlgoStatusData = {
  is_active: boolean;
  budget: number;
  cash: number;
  positions: AlgoPosition[];
  pending_signals: number;
  realized_pnl: number;
};
```

- [ ] **Step 2: Add message types to ExtensionMessage union**

In `extension/src/messages.ts`, add to the `ExtensionMessage` union (before the closing semicolon at line 152):

```typescript
  // Algo position lifecycle (TL sweep → backend)
  | { type: 'ALGO_POSITION_SOLD'; ea_id: number; sell_price: number; quantity: number }
  | { type: 'ALGO_POSITION_SOLD_RESULT'; success: boolean; pnl?: number; error?: string }
  | { type: 'ALGO_POSITION_RELIST'; ea_id: number; price: number; quantity: number }
  | { type: 'ALGO_POSITION_RELIST_RESULT'; success: boolean; error?: string };
```

Note: the existing final line ending with `| { type: 'ALGO_SIGNAL_COMPLETE_RESULT'; ... };` should drop its semicolon and the new types chain after it.

- [ ] **Step 3: Add handler functions in background.ts**

In `extension/entrypoints/background.ts`, add after the `handleAlgoSignalComplete` function (around line 495):

```typescript
async function handleAlgoPositionSold(
  ea_id: number, sell_price: number, quantity: number,
): Promise<ExtensionMessage> {
  try {
    const res = await fetch(`${BACKEND_URL}/api/v1/algo/positions/${ea_id}/sold`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sell_price, quantity }),
    });
    if (!res.ok) {
      return { type: 'ALGO_POSITION_SOLD_RESULT', success: false, error: `Backend ${res.status}` };
    }
    const data = await res.json();
    return { type: 'ALGO_POSITION_SOLD_RESULT', success: true, pnl: data.pnl };
  } catch (e) {
    return { type: 'ALGO_POSITION_SOLD_RESULT', success: false, error: String(e) };
  }
}

async function handleAlgoPositionRelist(
  ea_id: number, price: number, quantity: number,
): Promise<ExtensionMessage> {
  try {
    const res = await fetch(`${BACKEND_URL}/api/v1/algo/positions/${ea_id}/relist`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ price, quantity }),
    });
    if (!res.ok) {
      return { type: 'ALGO_POSITION_RELIST_RESULT', success: false, error: `Backend ${res.status}` };
    }
    return { type: 'ALGO_POSITION_RELIST_RESULT', success: true };
  } catch (e) {
    return { type: 'ALGO_POSITION_RELIST_RESULT', success: false, error: String(e) };
  }
}
```

- [ ] **Step 4: Wire handlers in the message switch**

In the `chrome.runtime.onMessage.addListener` switch block (around line 91), add after the `ALGO_SIGNAL_COMPLETE` case:

```typescript
        case 'ALGO_POSITION_SOLD':
          handleAlgoPositionSold(msg.ea_id, msg.sell_price, msg.quantity).then(sendResponse);
          return true;
        case 'ALGO_POSITION_RELIST':
          handleAlgoPositionRelist(msg.ea_id, msg.price, msg.quantity).then(sendResponse);
          return true;
        case 'ALGO_POSITION_SOLD_RESULT':
        case 'ALGO_POSITION_RELIST_RESULT':
          return false;
```

- [ ] **Step 5: Build extension to verify TypeScript compiles**

Run: `cd extension && npm run build`
Expected: builds without errors

- [ ] **Step 6: Commit**

```bash
git add extension/src/messages.ts extension/entrypoints/background.ts
git commit -m "feat(algo): add ALGO_POSITION_SOLD/RELIST message types and handlers"
```

---

### Task 8: Create algo transfer list sweep

**Files:**
- Create: `extension/src/algo-transfer-list-sweep.ts`

- [ ] **Step 1: Create the sweep module**

Create `extension/src/algo-transfer-list-sweep.ts`:

```typescript
/**
 * Algo transfer list sweep — scan the TL for sold/expired algo cards.
 *
 * Called by the algo automation loop at the start of each iteration.
 * Uses scanTransferList() for DOM reading, then:
 *   - Sold items matching algo positions → report to backend via ALGO_POSITION_SOLD
 *   - Expired items matching algo positions → discover current lowest BIN, relist individually
 *   - Clear sold items from the TL
 *
 * Does NOT use "Relist All" button (which relists at original locked price).
 * Instead, individually relists each expired card at current lowest BIN.
 */
import * as SELECTORS from './selectors';
import {
  clickElement,
  waitForElement,
  waitForSearchResults,
  requireElement,
  typePrice,
  jitter,
  AutomationError,
} from './automation';
import { scanTransferList, type TransferListScanResult } from './transfer-list-cycle';
import type { DetectedItem } from './trade-observer';
import type { AlgoStatusData, ExtensionMessage } from './messages';

export type AlgoSweepResult = {
  soldCount: number;
  relistedCount: number;
  clearedCount: number;
};

type PositionMatch = {
  ea_id: number;
  player_name: string;
  quantity: number;
  buy_price: number;
  listed_price: number | null;
};

/**
 * Match a DetectedItem from the transfer list to an algo position by name.
 * Returns the matched position or null.
 */
function matchItemToPosition(
  item: DetectedItem,
  positions: PositionMatch[],
): PositionMatch | null {
  const itemName = item.name.toLowerCase();
  for (const pos of positions) {
    const posName = pos.player_name.toLowerCase();
    if (posName.includes(itemName) || itemName.includes(posName)) {
      return pos;
    }
  }
  return null;
}

/**
 * Discover current lowest BIN for a player via transfer market search.
 * Reuses the same search flow as algo-sell-cycle.ts.
 *
 * @param playerName  Player name to search for
 * @param rating      Player rating for result verification
 * @param fallbackPrice  Price to return if search fails
 * @returns Discovered lowest BIN price
 */
async function discoverLowestBin(
  playerName: string,
  rating: number,
  fallbackPrice: number,
): Promise<number> {
  // Navigate to transfer market search
  const transfersBtn = requireElement<HTMLElement>(
    'NAV_TRANSFERS',
    SELECTORS.NAV_TRANSFERS,
  );
  await clickElement(transfersBtn);
  await jitter(1000, 2000);

  const searchTile = await waitForElement<HTMLElement>(
    'TILE_SEARCH_MARKET',
    '.ut-tile-transfer-market',
    document,
    10_000,
  );
  await clickElement(searchTile);
  await jitter();

  // Wait for search page
  await waitForElement(
    'SEARCH_PLAYER_NAME_INPUT',
    SELECTORS.SEARCH_PLAYER_NAME_INPUT,
    document,
    10_000,
  );

  // Fill player name
  const nameInput = requireElement<HTMLInputElement>(
    'SEARCH_PLAYER_NAME_INPUT',
    SELECTORS.SEARCH_PLAYER_NAME_INPUT,
  );
  nameInput.focus();
  nameInput.value = '';
  nameInput.dispatchEvent(new Event('input', { bubbles: true }));
  await jitter(300, 600);

  nameInput.value = playerName;
  nameInput.dispatchEvent(new Event('input', { bubbles: true }));
  nameInput.dispatchEvent(new Event('change', { bubbles: true }));

  // Wait for autocomplete and click match
  await jitter(1000, 2000);
  try {
    const suggestionList = await waitForElement(
      'SEARCH_PLAYER_SUGGESTIONS',
      SELECTORS.SEARCH_PLAYER_SUGGESTIONS,
      document,
      5_000,
    );
    const buttons = Array.from(suggestionList.querySelectorAll('button'));
    const match = buttons.find(
      btn => btn.textContent?.trim().toLowerCase().includes(playerName.toLowerCase()),
    ) ?? buttons[0];
    if (match) {
      await clickElement(match);
      await jitter();
    }
  } catch {
    // No suggestions — continue with search
  }

  // Click search
  const searchBtn = requireElement<HTMLElement>(
    'SEARCH_SUBMIT_BUTTON',
    SELECTORS.SEARCH_SUBMIT_BUTTON,
  );
  await clickElement(searchBtn);

  const searchResult = await waitForSearchResults();
  let discoveredPrice = fallbackPrice;

  if (searchResult.outcome === 'results') {
    const resultsList = document.querySelector(SELECTORS.SEARCH_RESULTS_LIST)!;
    const resultItems = Array.from(
      resultsList.querySelectorAll<Element>(SELECTORS.TRANSFER_LIST_ITEM),
    );

    let cheapestBin = Infinity;
    for (const item of resultItems) {
      const binEl = item.querySelector(SELECTORS.ITEM_BIN_PRICE);
      const itemBin = parseInt(binEl?.textContent?.replace(/,/g, '') ?? '', 10);
      if (isNaN(itemBin)) continue;
      const ratingEl = item.querySelector(SELECTORS.ITEM_RATING);
      const itemRating = parseInt(ratingEl?.textContent?.trim() ?? '', 10);
      if (itemRating === rating && itemBin < cheapestBin) {
        cheapestBin = itemBin;
      }
    }
    if (cheapestBin < Infinity) {
      discoveredPrice = cheapestBin;
    }
  }

  // Navigate back from search results
  const backBtn = document.querySelector<HTMLElement>(SELECTORS.NAV_BACK_BUTTON);
  if (backBtn) {
    await clickElement(backBtn);
    await jitter(1000, 2000);
  }

  return discoveredPrice;
}

/**
 * Navigate back to the first page of the transfer list.
 */
async function goToFirstPage(): Promise<void> {
  let hasPrev = true;
  while (hasPrev) {
    const prevBtn = document.querySelector<HTMLButtonElement>(SELECTORS.PAGINATION_PREV);
    if (prevBtn && !prevBtn.disabled && !prevBtn.classList.contains('disabled')) {
      await clickElement(prevBtn);
      await jitter(400, 800);
      await new Promise(r => setTimeout(r, 300));
    } else {
      hasPrev = false;
    }
  }
}

/**
 * Find the Clear Sold button and click it (with confirmation dialog).
 */
async function clearSoldItems(): Promise<number> {
  await goToFirstPage();
  await jitter();

  const container = document.querySelector(SELECTORS.TRANSFER_LIST_CONTAINER);
  if (!container) return 0;

  const buttons = container.querySelectorAll<HTMLElement>('.section-header-btn');
  let clearBtn: HTMLElement | null = null;
  for (const btn of buttons) {
    const text = btn.textContent?.trim().toLowerCase() ?? '';
    if (text.includes('clear sold')) {
      clearBtn = btn;
      break;
    }
  }

  if (!clearBtn) return 0;

  await clickElement(clearBtn);
  await jitter();

  try {
    const confirmBtn = await waitForElement<HTMLElement>(
      'EA_DIALOG_PRIMARY_BUTTON',
      SELECTORS.EA_DIALOG_PRIMARY_BUTTON,
      document,
      3_000,
    );
    await clickElement(confirmBtn);
    await jitter();
  } catch {
    // No confirmation dialog — clear may execute immediately
  }

  return 1; // cleared
}

/**
 * Run the full algo transfer list sweep.
 *
 * @param sendMessage  Callback to relay messages to the service worker
 * @param positions    Current algo positions from /algo/status
 * @param stopped      Callback to check if automation was stopped
 * @returns Sweep result with counts
 */
export async function runAlgoTransferListSweep(
  sendMessage: (msg: any) => Promise<any>,
  positions: PositionMatch[],
  stopped: () => boolean,
): Promise<AlgoSweepResult> {
  const result: AlgoSweepResult = { soldCount: 0, relistedCount: 0, clearedCount: 0 };

  // Step 1: Scan the transfer list
  const scan = await scanTransferList();

  // Handle processing items — wait and rescan
  const hasProcessing = scan.listed.some(item => item.status === 'processing');
  let finalScan: TransferListScanResult = scan;
  if (hasProcessing) {
    await new Promise(r => setTimeout(r, 5_000));
    finalScan = await scanTransferList();
  }

  if (stopped()) return result;

  // Step 2: Match sold items to algo positions and report
  const soldByPosition = new Map<number, { count: number; price: number }>();
  for (const item of finalScan.sold) {
    const match = matchItemToPosition(item, positions);
    if (!match) continue;
    const existing = soldByPosition.get(match.ea_id) ?? { count: 0, price: item.price };
    existing.count += 1;
    soldByPosition.set(match.ea_id, existing);
  }

  for (const [ea_id, { count, price }] of soldByPosition) {
    if (stopped()) return result;
    try {
      await sendMessage({
        type: 'ALGO_POSITION_SOLD',
        ea_id,
        sell_price: price,
        quantity: count,
      } satisfies ExtensionMessage);
      result.soldCount += count;
    } catch (err) {
      console.warn(`[algo-tl-sweep] ALGO_POSITION_SOLD failed for ea_id=${ea_id}:`, err);
    }
  }

  if (stopped()) return result;

  // Step 3: Match expired items to algo positions and relist at current lowest BIN
  const expiredByPosition = new Map<number, { count: number; match: PositionMatch }>();
  for (const item of finalScan.expired) {
    const match = matchItemToPosition(item, positions);
    if (!match) continue;
    const existing = expiredByPosition.get(match.ea_id);
    if (!existing) {
      expiredByPosition.set(match.ea_id, { count: 1, match });
    } else {
      existing.count += 1;
    }
  }

  for (const [ea_id, { count, match }] of expiredByPosition) {
    if (stopped()) return result;

    // Discover current lowest BIN for this player
    const fallback = match.listed_price ?? match.buy_price;
    // We need the rating for search verification — get it from the expired item
    const expiredItem = finalScan.expired.find(
      item => matchItemToPosition(item, [match]) !== null,
    );
    const rating = expiredItem?.rating ?? 0;

    const lowestBin = await discoverLowestBin(match.player_name, rating, fallback);

    if (stopped()) return result;

    // Navigate back to transfer list to relist
    // The individual relist needs to click each expired card and set price.
    // For simplicity, we use the Relist All approach but at the new price:
    // Navigate to TL, find expired items, click each, relist with new price.
    // However, EA only supports individual relist via the card detail panel,
    // or "Relist All" at original price. For individual relist:
    //   1. Click the expired card on TL
    //   2. Click "Re-list" button → opens quick-list panel with original price
    //   3. Change BIN price to lowestBin
    //   4. Click "List for Transfer"

    // Navigate to transfer list
    const { navigateToTransferList } = await import('./navigation');
    await navigateToTransferList();
    await jitter();

    // Rescan to find expired items for this player
    const rescan = await scanTransferList();
    const playerExpired = rescan.expired.filter(
      item => matchItemToPosition(item, [match]) !== null,
    );

    for (const expItem of playerExpired) {
      if (stopped()) return result;

      // Navigate to TL again (DOM may have changed)
      await navigateToTransferList();
      await jitter();

      // Find and click the expired card element
      // The trade observer reads items from .ut-item-view elements;
      // we need to click the matching one on the current TL page
      const tlItems = Array.from(
        document.querySelectorAll<Element>(SELECTORS.TRANSFER_LIST_ITEM),
      );
      const itemNameLower = expItem.name.toLowerCase();
      let targetItem: Element | null = null;
      for (const el of tlItems) {
        const nameEl = el.querySelector(SELECTORS.ITEM_PLAYER_NAME);
        const name = nameEl?.textContent?.trim().toLowerCase() ?? '';
        const ratingEl = el.querySelector(SELECTORS.ITEM_RATING);
        const itemRating = parseInt(ratingEl?.textContent?.trim() ?? '', 10);
        if ((name.includes(itemNameLower) || itemNameLower.includes(name)) && itemRating === expItem.rating) {
          // Verify it's expired (has expired status indicator)
          const statusEl = el.querySelector('.auction-state');
          const statusText = statusEl?.textContent?.trim().toLowerCase() ?? '';
          if (statusText === 'expired') {
            targetItem = el;
            break;
          }
        }
      }

      if (!targetItem) continue;

      await clickElement(targetItem);
      await jitter();

      // Wait for and click the "Re-list" accordion/button
      try {
        const relistBtn = await waitForElement<HTMLElement>(
          'LIST_ON_MARKET_ACCORDION',
          SELECTORS.LIST_ON_MARKET_ACCORDION,
          document,
          8_000,
        );
        await clickElement(relistBtn);
        await jitter();
      } catch {
        continue; // Can't relist this card — skip
      }

      // Wait for quick list panel
      try {
        await waitForElement(
          'QUICK_LIST_PANEL',
          SELECTORS.QUICK_LIST_PANEL,
          document,
          8_000,
        );
      } catch {
        continue;
      }

      // Set prices
      const listInputs = Array.from(
        document.querySelectorAll<HTMLInputElement>(SELECTORS.QUICK_LIST_PRICE_INPUTS),
      );
      if (listInputs.length < 2) continue;

      const startPrice = Math.max(lowestBin - 100, 200);
      await typePrice(listInputs[0], startPrice);
      await jitter();
      await typePrice(listInputs[1], lowestBin);
      await jitter();

      // Click "List for Transfer"
      const buttons = document.querySelectorAll<HTMLButtonElement>(
        `${SELECTORS.QUICK_LIST_PANEL} button.btn-standard.primary`,
      );
      let listBtn: HTMLButtonElement | null = null;
      for (const btn of Array.from(buttons)) {
        const text = btn.textContent?.trim() ?? '';
        if (text.includes('List for Transfer') || text.includes('List on Transfer Market')) {
          listBtn = btn;
          break;
        }
      }
      if (!listBtn) {
        listBtn = document.querySelector<HTMLButtonElement>(
          `${SELECTORS.QUICK_LIST_PANEL} button.btn-standard.primary`,
        );
      }

      if (listBtn) {
        await clickElement(listBtn);
        await jitter(1500, 3000);
        result.relistedCount += 1;
      }
    }

    // Report relist to backend
    if (result.relistedCount > 0) {
      try {
        await sendMessage({
          type: 'ALGO_POSITION_RELIST',
          ea_id,
          price: lowestBin,
          quantity: count,
        } satisfies ExtensionMessage);
      } catch (err) {
        console.warn(`[algo-tl-sweep] ALGO_POSITION_RELIST failed for ea_id=${ea_id}:`, err);
      }
    }
  }

  // Step 4: Clear sold items from the TL
  if (soldByPosition.size > 0) {
    await navigateToTransferList();
    await jitter();
    const cleared = await clearSoldItems();
    result.clearedCount = cleared > 0 ? result.soldCount : 0;
  }

  return result;
}
```

- [ ] **Step 2: Build extension to verify TypeScript compiles**

Run: `cd extension && npm run build`
Expected: builds without errors. If `navigateToTransferList` import causes issues, verify the export exists in `navigation.ts`.

- [ ] **Step 3: Commit**

```bash
git add extension/src/algo-transfer-list-sweep.ts
git commit -m "feat(algo): add transfer list sweep — sold detection and relist at lowest BIN"
```

---

### Task 9: Wire TL sweep into algo automation loop

**Files:**
- Modify: `extension/src/algo-automation-loop.ts`

- [ ] **Step 1: Add Phase A (TL sweep) to the loop**

Replace the contents of `extension/src/algo-automation-loop.ts` with:

```typescript
/**
 * Algo trading automation loop.
 *
 * Polls the backend for pending signals and executes buy/sell cycles.
 * Runs continuously until stopped via the AutomationEngine abort signal.
 *
 * Loop:
 *   Phase A: If positions are listed, sweep TL for sold/expired
 *   Phase B: Poll for signal via ALGO_SIGNAL_REQUEST
 *     - If null: wait 30-60s, continue
 *   Phase C: Execute signal
 *     - BUY: executeAlgoBuyCycle, report 'bought'
 *     - SELL: executeAlgoSellCycle, report 'listed' (position stays alive)
 *   Jitter 3-5s between signals
 */
import { AutomationEngine, AutomationError, jitter } from './automation';
import { executeAlgoBuyCycle, type AlgoBuyCycleResult } from './algo-buy-cycle';
import { executeAlgoSellCycle, type AlgoSellCycleResult } from './algo-sell-cycle';
import { runAlgoTransferListSweep } from './algo-transfer-list-sweep';
import type { ExtensionMessage, AlgoSignal, AlgoStatusData } from './messages';

/**
 * Run the algo trading automation loop until stopped or error.
 *
 * @param engine       AutomationEngine for state tracking and abort signal
 * @param sendMessage  Callback to relay messages to the service worker / backend
 */
export async function runAlgoAutomationLoop(
  engine: AutomationEngine,
  sendMessage: (msg: any) => Promise<any>,
): Promise<void> {
  const signal = engine.getAbortSignal();
  const stopped = () => signal?.aborted ?? false;

  try {
    while (!stopped()) {
      // ── Phase A: Transfer List Sweep ────────────────────────────────────
      // Check if any positions are listed (have listed_at set).
      // If so, sweep the TL for sold/expired cards.
      try {
        const statusRes = await sendMessage({ type: 'ALGO_STATUS_REQUEST' } satisfies ExtensionMessage);
        if (statusRes?.type === 'ALGO_STATUS_RESULT' && statusRes.data) {
          const statusData: AlgoStatusData = statusRes.data;
          // Positions with listed_price set are on the transfer list
          const listedPositions = statusData.positions.filter(p => p.listed_price != null);
          if (listedPositions.length > 0 && !stopped()) {
            await engine.setState('SCANNING', 'Sweeping transfer list');
            const positions = listedPositions.map(p => ({
              ea_id: p.ea_id,
              player_name: p.player_name,
              quantity: p.quantity,
              buy_price: p.buy_price,
              listed_price: p.listed_price,
            }));
            const sweepResult = await runAlgoTransferListSweep(sendMessage, positions, stopped);
            if (sweepResult.soldCount > 0) {
              await engine.setLastEvent(
                `TL sweep: ${sweepResult.soldCount} sold, ${sweepResult.relistedCount} relisted`,
              );
            }
          }
        }
      } catch (err) {
        await engine.log(`TL sweep error: ${err instanceof Error ? err.message : String(err)}`);
      }

      if (stopped()) return;

      // ── Phase B: Poll for next signal ───────────────────────────────────
      await engine.setState('SCANNING', 'Polling for algo signal');

      let algoSignal: AlgoSignal | null = null;
      try {
        const res = await sendMessage({ type: 'ALGO_SIGNAL_REQUEST' } satisfies ExtensionMessage);
        if (res && res.type === 'ALGO_SIGNAL_RESULT') {
          if (res.error) {
            await engine.log(`Signal poll error: ${res.error}`);
          }
          algoSignal = res.signal ?? null;
        }
      } catch (err) {
        await engine.log(`Signal poll failed: ${err instanceof Error ? err.message : String(err)}`);
      }

      if (stopped()) return;

      // No signal available — wait 30-60s before next poll
      if (!algoSignal) {
        await engine.setState('IDLE', 'No pending signals — waiting');
        const waitMs = 30_000 + Math.floor(Math.random() * 30_000);
        let remaining = waitMs;
        while (remaining > 0 && !stopped()) {
          const chunk = Math.min(remaining, 5_000);
          await new Promise(r => setTimeout(r, chunk));
          remaining -= chunk;
        }
        continue;
      }

      // ── Phase C: Execute signal ─────────────────────────────────────────
      if (algoSignal.action === 'BUY') {
        await engine.setState('BUYING', `Buying: ${algoSignal.player_name} x${algoSignal.quantity}`);

        let totalBought = 0;
        let lastPrice = 0;

        for (let i = 0; i < algoSignal.quantity; i++) {
          if (stopped()) return;

          await engine.setState('BUYING', `Buying: ${algoSignal.player_name} (${i + 1}/${algoSignal.quantity})`);
          const result: AlgoBuyCycleResult = await executeAlgoBuyCycle(algoSignal, sendMessage);

          if (result.outcome === 'bought') {
            totalBought += result.quantity;
            lastPrice = result.buyPrice;
            await engine.setLastEvent(
              `Bought ${algoSignal.player_name} for ${result.buyPrice.toLocaleString()}`,
            );
          } else if (result.outcome === 'skipped') {
            await engine.setLastEvent(
              `Skipped ${algoSignal.player_name}: ${result.reason}`,
            );
            break;
          } else {
            await engine.setLastEvent(
              `Error buying ${algoSignal.player_name}: ${result.reason}`,
            );
            break;
          }

          if (i < algoSignal.quantity - 1 && !stopped()) {
            await jitter(3000, 5000);
          }
        }

        // Report completion to backend
        if (!stopped()) {
          const outcome = totalBought > 0 ? 'bought' : 'skipped';
          try {
            await sendMessage({
              type: 'ALGO_SIGNAL_COMPLETE',
              signal_id: algoSignal.id,
              outcome,
              price: lastPrice,
              quantity: totalBought,
            } satisfies ExtensionMessage);
          } catch (err) {
            await engine.log(`Signal complete report failed: ${err instanceof Error ? err.message : String(err)}`);
          }
        }

      } else if (algoSignal.action === 'SELL') {
        await engine.setState('LISTING', `Listing: ${algoSignal.player_name} x${algoSignal.quantity}`);

        let totalListed = 0;
        let lastPrice = 0;

        for (let i = 0; i < algoSignal.quantity; i++) {
          if (stopped()) return;

          await engine.setState('LISTING', `Listing: ${algoSignal.player_name} (${i + 1}/${algoSignal.quantity})`);
          const result: AlgoSellCycleResult = await executeAlgoSellCycle(algoSignal, sendMessage);

          if (result.outcome === 'listed') {
            totalListed += result.quantity;
            lastPrice = result.sellPrice;
            await engine.setLastEvent(
              `Listed ${algoSignal.player_name} for ${result.sellPrice.toLocaleString()}`,
            );
          } else if (result.outcome === 'skipped') {
            await engine.setLastEvent(
              `Skipped sell ${algoSignal.player_name}: ${result.reason}`,
            );
            break;
          } else {
            await engine.setLastEvent(
              `Error selling ${algoSignal.player_name}: ${result.reason}`,
            );
            break;
          }

          if (i < algoSignal.quantity - 1 && !stopped()) {
            await jitter(3000, 5000);
          }
        }

        // Report listing to backend — outcome is 'listed', NOT 'sold'
        // Position stays alive until the TL sweep detects actual sale
        if (!stopped()) {
          const outcome = totalListed > 0 ? 'listed' : 'skipped';
          try {
            await sendMessage({
              type: 'ALGO_SIGNAL_COMPLETE',
              signal_id: algoSignal.id,
              outcome,
              price: lastPrice,
              quantity: totalListed,
            } satisfies ExtensionMessage);
          } catch (err) {
            await engine.log(`Signal complete report failed: ${err instanceof Error ? err.message : String(err)}`);
          }
        }
      }

      if (stopped()) return;

      // Jitter between signals
      await jitter(3000, 5000);
    }
  } catch (err) {
    if (err instanceof AutomationError) {
      await engine.setError(err.message);
      return;
    }
    const msg = err instanceof Error ? err.message : String(err);
    await engine.setError(`Unexpected error: ${msg}`);
  }
}
```

- [ ] **Step 2: Update /algo/status response to include listed fields on positions**

In `src/server/api/algo.py`, in the `algo_status` function, update the position_rows building (around line 219-226) to include the new fields:

```python
            position_rows.append({
                "ea_id": pos.ea_id,
                "player_name": player.name if player else None,
                "quantity": pos.quantity,
                "buy_price": pos.buy_price,
                "current_price": current_price,
                "unrealized_pnl": unrealized_pnl,
                "listed_price": pos.listed_price,
                "listed_at": pos.listed_at.isoformat() if pos.listed_at else None,
            })
```

- [ ] **Step 3: Build extension to verify TypeScript compiles**

Run: `cd extension && npm run build`
Expected: builds without errors

- [ ] **Step 4: Run all backend tests**

Run: `python -m pytest tests/algo/test_algo_api.py -v`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add extension/src/algo-automation-loop.ts src/server/api/algo.py
git commit -m "feat(algo): wire TL sweep into automation loop, add listed fields to status"
```

---

### Task 10: Final integration verification

**Files:** None (verification only)

- [ ] **Step 1: Run all algo tests**

Run: `python -m pytest tests/algo/ --ignore=tests/algo/test_integration.py -v`
Expected: all pass (test_integration.py excluded — pre-existing failures unrelated to this work)

- [ ] **Step 2: Build extension**

Run: `cd extension && npm run build`
Expected: clean build, no errors or warnings

- [ ] **Step 3: Start server and verify new endpoints**

```bash
# Start server (use port 8050 if 8000 is occupied)
python -m uvicorn src.server.main:app --port 8050 &

# Test new endpoints
curl -s -X POST http://localhost:8050/api/v1/algo/start -H "Content-Type: application/json" -d '{"budget":500000}'

# Create a position via signal completion
curl -s http://localhost:8050/api/v1/algo/signals/pending
# (use the claimed signal_id)
# Complete with 'listed' outcome
curl -s -X POST http://localhost:8050/api/v1/algo/signals/1/complete \
  -H "Content-Type: application/json" \
  -d '{"outcome":"listed","price":45000,"quantity":5}'

# Test relist
curl -s -X POST http://localhost:8050/api/v1/algo/positions/67333022/relist \
  -H "Content-Type: application/json" \
  -d '{"price":42000,"quantity":5}'

# Test sold
curl -s -X POST http://localhost:8050/api/v1/algo/positions/67333022/sold \
  -H "Content-Type: application/json" \
  -d '{"sell_price":42000,"quantity":3}'

# Check status shows realized_pnl
curl -s http://localhost:8050/api/v1/algo/status
```

Expected: all endpoints return 200 with correct responses. Status includes `realized_pnl`.

- [ ] **Step 4: Verify Bug #1 fix with signal engine**

Run the signal engine and confirm BUY qty == SELL qty for all players (same script as Task 1 Step 3).

- [ ] **Step 5: Commit any remaining fixes**

If any fixes were needed during verification, commit them with descriptive messages.
