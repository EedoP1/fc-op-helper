# Phase 4: Algo Trading End-to-End Test — Real Coins

**Date:** 2026-04-10
**Branch:** `feat/algo-trading-backtester`
**Risk:** REAL COINS — use 50,000-100,000 budget max

## What Was Built (and already tested in Phases 1-3)

9 commits on `feat/algo-trading-backtester` implement a live algo trading mode:

### Server (Python/FastAPI)
- `src/server/models_db.py` — AlgoConfig, AlgoSignal, AlgoPosition (+listed_at, listed_price), AlgoTrade (PnL tracking)
- `src/server/algo_engine.py` — AlgoSignalEngine wraps PromoDipBuyStrategy for live tick processing
- `src/server/algo_runner.py` — run_signal_engine() loads market_snapshots → runs engine → writes signals
- `src/server/api/algo.py` — 7 endpoints:
  - `POST /api/v1/algo/start` — activate with budget
  - `POST /api/v1/algo/stop` — deactivate, cancel pending signals
  - `GET /api/v1/algo/status` — status with positions, realized_pnl, pending_signals
  - `GET /api/v1/algo/signals/pending` — claim next pending signal
  - `POST /api/v1/algo/signals/{id}/complete` — outcomes: bought, listed, sold, failed, skipped
  - `POST /api/v1/algo/positions/{ea_id}/sold` — partial/full sale with PnL
  - `POST /api/v1/algo/positions/{ea_id}/relist` — update listed price after relist
- `src/server/scheduler.py` — signal engine runs every 10 min in scanner process

### Extension (TypeScript/WXT) — uses EA internal APIs, NOT DOM automation
All extension automation uses EA's internal JavaScript APIs via `extension/src/ea-services.ts` (merged from `feature/services-item-migration`). No DOM clicking, no CSS selectors for automation — direct calls to `services.Item.bid()`, `services.Item.searchTransferMarket()`, etc.

- `extension/src/ea-services.ts` — wraps all EA globals (`services.Item`, `repositories.Item`, `ItemPile`, `UTSearchCriteriaDTO`). Provides: `searchMarket()`, `buyItem()`, `listItem()`, `getTransferList()`, `getUnassigned()`, `moveItem()`, `clearSold()`, `refreshAuctions()`, `performUnassignedGlitch()`, etc.
- `extension/src/algo-buy-cycle.ts` — `searchMarket(buildCriteria(ea_id, maxBuy))` → price guard (reference_price × 1.10) → `buyItem()` → stays in unassigned. On error 473 (unassigned full): performs FUT Enhancer-style glitch (dummy bid → refresh cache → swap duplicates to club → retry).
- `extension/src/algo-sell-cycle.ts` — `getUnassigned()` → find card by definitionId → `searchMarket()` for price discovery → `listItem()` at lowest BIN
- `extension/src/algo-transfer-list-sweep.ts` — `getTransferList()` → detect sold → report to backend → relist expired individually via `listItem()` at current lowest BIN (NOT `relistAll()`)
- `extension/src/algo-automation-loop.ts` — 3-phase loop:
  - Phase A: TL sweep (if listed positions exist)
  - Phase B: poll for signal
  - Phase C: execute BUY/SELL
- `extension/entrypoints/ea-webapp-main.content.ts` — runs in MAIN world (EA's JS context), hosts the algo automation engine with direct EA API access
- `extension/entrypoints/ea-webapp.content.ts` — runs in ISOLATED world, relays start/stop commands via bridge
- `extension/entrypoints/background.ts` — 7 message handlers proxying to backend
- `extension/src/messages.ts` — types for all algo messages
- `extension/src/overlay/panel.ts` — "Algo" tab (visible only in confirmed-portfolio state) with budget input, start/stop, status display

### Signal Lifecycle (Bug #3 fix)
```
BUY signal → extension buys card → unassigned pile → AlgoPosition created
SELL signal → extension lists card on TL → AlgoPosition updated (listed_at, listed_price), signal DONE
  ├─ Card sells → TL sweep detects sale → /positions/{ea_id}/sold → AlgoTrade written, position decremented/deleted
  └─ Card expires → TL sweep detects expired → relist at current lowest BIN → /positions/{ea_id}/relist
```

### Bugs Fixed
1. **Strategy duplicate BUY** — strong-buy layer now skips players already held (holdings guard)
2. **SELL outcome string** — backend now accepts `'listed'` as valid outcome
3. **Premature position deletion** — position stays alive after listing, only removed on actual sale

## Prerequisites

### 1. Server running on port 8000

The production server on :8000 needs to be running the LATEST code (with algo endpoints). If the server is running an older build:

```bash
# Kill old server
taskkill /PID <pid> /F

# Start with latest code
python -m uvicorn src.server.main:app --port 8000 --reload
```

### 2. DB migration (one-time)

The `algo_positions` table needs 2 new columns that `create_all` doesn't add to existing tables:

```bash
python -c "
import asyncio
from sqlalchemy import text
from src.server.db import create_engine_and_tables
async def migrate():
    e, sf = await create_engine_and_tables()
    async with e.begin() as conn:
        # Check if columns exist first
        from sqlalchemy import inspect as sa_inspect
        def check(connection):
            insp = sa_inspect(connection)
            cols = [c['name'] for c in insp.get_columns('algo_positions')]
            return 'listed_at' in cols
        exists = await conn.run_sync(check)
        if not exists:
            await conn.execute(text('ALTER TABLE algo_positions ADD COLUMN listed_at TIMESTAMP'))
            await conn.execute(text('ALTER TABLE algo_positions ADD COLUMN listed_price INTEGER'))
            print('Migration done')
        else:
            print('Columns already exist')
    await e.dispose()
asyncio.run(migrate())
"
```

### 3. Extension rebuilt and loaded

```bash
cd extension && npm run build
```

Then reload the extension in `chrome://extensions/` (click the refresh icon on the OP Seller extension card).

### 4. Clean algo state

Clear any test signals from Phases 1-3:

```bash
python -c "
import asyncio
from sqlalchemy import delete
from src.server.db import create_engine_and_tables
from src.server.models_db import AlgoSignal, AlgoPosition, AlgoTrade, AlgoConfig
async def clean():
    e, sf = await create_engine_and_tables()
    async with sf() as s:
        await s.execute(delete(AlgoSignal))
        await s.execute(delete(AlgoPosition))
        await s.execute(delete(AlgoTrade))
        await s.execute(delete(AlgoConfig))
        await s.commit()
        print('Algo state cleared')
    await e.dispose()
asyncio.run(clean())
"
```

### 5. Confirmed portfolio exists

The Algo tab only appears in the confirmed-portfolio panel state. You need a confirmed portfolio to access it. If you don't have one, generate and confirm one via the existing OP seller flow first.

## Test 4.1: Start/Stop Wiring (No Signals)

**Goal:** Verify the UI ↔ backend start/stop works without any signals being generated.

1. Navigate to https://www.ea.com/ea-sports-fc/ultimate-team/web-app/ and log in
2. Open the OP Seller overlay panel (right side)
3. Click the **Algo** tab
4. Enter budget: **50000**
5. Click **Start Algo**

**Verify:**
- Status display updates to show `Active`, `Budget: 50,000`, `Cash: 50,000`
- Backend: `curl http://localhost:8000/api/v1/algo/status` shows `is_active: true, budget: 50000`
- Wait 30s — the loop should poll for signals and find none (status shows "No pending signals — waiting")
- Check Chrome DevTools → service worker console for errors

6. Click **Stop Algo**

**Verify:**
- Status shows `Inactive`
- Backend: `curl http://localhost:8000/api/v1/algo/status` shows `is_active: false`

## Test 4.2: Synthetic BUY Signal (Real Coins!)

**Goal:** Verify the extension can buy a cheap card from a synthetic signal.

**IMPORTANT:** Pick a very cheap card (under 2,000 coins). The buy cycle uses `reference_price * 1.10` as the max price guard — so set reference_price accordingly.

### Inject a test BUY signal:

```bash
python -c "
import asyncio
from datetime import datetime
from sqlalchemy import select
from src.server.db import create_engine_and_tables
from src.server.models_db import AlgoSignal, AlgoConfig, PlayerRecord

async def main():
    e, sf = await create_engine_and_tables()
    async with sf() as s:
        # Ensure algo is active
        config = (await s.execute(select(AlgoConfig))).scalar_one_or_none()
        if not config or not config.is_active:
            print('ERROR: Start algo from the UI first!')
            await e.dispose()
            return

        # Find a cheap rare card that should be on the market
        player = (await s.execute(
            select(PlayerRecord)
            .where(PlayerRecord.card_type == 'Rare')
            .where(PlayerRecord.rating <= 80)
            .limit(1)
        )).scalar_one_or_none()

        if not player:
            print('No cheap rare player found — pick one manually')
            await e.dispose()
            return

        ref_price = 750  # Very cheap — price guard allows up to 825
        print(f'Inserting BUY signal for {player.name} (ea_id={player.ea_id}, rating={player.rating}, pos={player.position})')
        print(f'  reference_price={ref_price}, quantity=1')
        print(f'  Price guard: buy up to {int(ref_price * 1.10):,} coins')

        s.add(AlgoSignal(
            ea_id=player.ea_id,
            action='BUY',
            quantity=1,
            reference_price=ref_price,
            status='PENDING',
            created_at=datetime.utcnow(),
        ))
        await s.commit()
        print('Signal inserted. Extension should pick it up within 30-60s.')
    await e.dispose()

asyncio.run(main())
"
```

### Watch the extension:
1. With Algo mode active, watch the overlay status display
2. The automation loop should pick up the signal within 30-60 seconds
3. Status should show: `Buying: [player name] (1/1)`
4. Extension navigates to transfer market, searches, attempts to buy

### After buy attempt:
- **If bought:** Check that the card is in the **unassigned pile** (NOT on the transfer list). Check backend: `curl http://localhost:8000/api/v1/algo/status` — should show a position with `listed_price: null`
- **If skipped (price too high):** Status shows `Skipped: [reason]`. This is OK — the reference_price guard prevented overpaying. Try with a higher reference_price or a different card.
- **If error:** Check DevTools console for the exact error. Common issues:
  - `Card not found` — player name/rating mismatch between DB and EA web app
  - `Navigation error` — EA web app wasn't on the right page
  - `Buy failed` — card was already bought by someone else (race condition)

## Test 4.3: Synthetic SELL Signal

**Goal:** Verify the extension can list the card from the unassigned pile at market price.

**Only do this if Test 4.2 successfully bought a card.**

### Inject a SELL signal:

```bash
python -c "
import asyncio
from datetime import datetime
from sqlalchemy import select
from src.server.db import create_engine_and_tables
from src.server.models_db import AlgoSignal, AlgoPosition

async def main():
    e, sf = await create_engine_and_tables()
    async with sf() as s:
        pos = (await s.execute(select(AlgoPosition))).scalar_one_or_none()
        if not pos:
            print('No position to sell — did the BUY succeed?')
            await e.dispose()
            return

        print(f'Inserting SELL signal for ea_id={pos.ea_id}, qty={pos.quantity}')
        s.add(AlgoSignal(
            ea_id=pos.ea_id,
            action='SELL',
            quantity=pos.quantity,
            reference_price=pos.buy_price * 2,  # Doesn't matter — price is discovered live
            status='PENDING',
            created_at=datetime.utcnow(),
        ))
        await s.commit()
        print('SELL signal inserted. Extension should pick it up within 30-60s.')
    await e.dispose()

asyncio.run(main())
"
```

### Watch the extension:
1. Status shows: `Listing: [player name] (1/1)`
2. Extension navigates to unassigned pile → finds the card
3. Navigates to transfer market → discovers cheapest BIN
4. Navigates back to unassigned → lists the card at discovered BIN

### After listing:
- **If listed:** Card should now be on the **transfer list** (not unassigned). Check backend:
  ```bash
  curl -s http://localhost:8000/api/v1/algo/status | python -m json.tool
  ```
  Position should show `listed_price` and `listed_at` set (not null). Signal should be DONE.
- **If skipped/error:** Check DevTools console for details.

## Test 4.4: Transfer List Sweep (Sale Detection)

**Goal:** Verify the TL sweep detects sold/expired cards and handles them correctly.

This test requires waiting for the listed card to either sell or expire. Two approaches:

### Option A: Wait for natural sale
If the card was listed at a competitive price, it may sell within minutes. Watch the Algo tab status — the next loop iteration will sweep the TL and detect the sale.

When the TL sweep finds a sold card:
- Status briefly shows: `Sweeping transfer list`
- Event log: `TL sweep: 1 sold, 0 relisted`
- Backend: `curl http://localhost:8000/api/v1/algo/status` — position should be **gone**, `realized_pnl` should show the profit

### Option B: Wait for expiry (1 hour)
If the card doesn't sell within the EA listing duration (1 hour), it will expire. The TL sweep should:
1. Detect the expired card
2. Discover the current lowest BIN via transfer market search
3. **Individually relist** at that price (NOT using "Relist All")
4. Report relist to backend

Watch for:
- Status: `Sweeping transfer list`
- Extension navigates to TL → detects expired → searches market → relists
- Backend: position's `listed_price` updates to the new price

## What to Watch For (Known Risks)

1. **Name matching:** The TL sweep matches cards by `playerName` substring + `rating`. If the EA web app displays a different name format than what's in the DB, matching will fail silently. Check DevTools console for `[algo-tl-sweep]` warnings.

2. **Transfer list full:** If the transfer list has 100 items (EA cap), the sell cycle can't list. The quick-list panel stays visible and the cycle reports an error: `Listing failed — panel still visible (TL full?)`.

3. **Unassigned pile full:** When `buyItem()` returns error 473, the algo buy cycle now handles it automatically via the unassigned glitch (same algorithm as FUT Enhancer): dummy bid → refresh cache → swap duplicates to club → retry buy. This ONLY works if there are **duplicate items** in the unassigned pile (cards that also exist in the club). If no duplicates, the buy fails with `"Unassigned pile full (no duplicates to swap)"`.

   **To test the glitch:** Fill your unassigned pile to 50+ items (some of which are duplicates of club cards), then try a buy. Watch the console for `[algo-buy] Unassigned pile full, performing glitch...` and `Glitch freed N slots`.

   **If no duplicates available:** Manually clear some unassigned items before testing (send to club or quick-sell).

4. **EA rate limiting:** Rapid navigation between pages may trigger EA's soft rate limits (empty search results, delayed responses). The jitter helpers should prevent this, but watch for it.

5. **Price guard too tight:** The buy cycle rejects cards priced above `reference_price * 1.10`. If the market price is above this, the buy is skipped. Use a reference_price that gives enough headroom.

6. **EA API changes:** Since the extension uses EA's internal JS APIs directly (not DOM), if EA updates their internal API surface (`services.Item`, `repositories.Item`), calls may fail. Check the console for `[ea-services]` log lines to verify API responses.

7. **Daily transaction cap:** The extension tracks daily transactions. If you've been testing all day, you might hit the cap. Check: `curl http://localhost:8000/api/v1/daily-cap`.

## Reporting

After each test step, report:
- **Worked?** Yes/No
- **What happened** (exact behavior observed)
- **Errors** (exact messages from DevTools console or backend logs)
- **Screenshots** (if possible, especially for UI state)

For failures:
- Copy the exact error from DevTools service worker console
- Copy the exact error from the server terminal
- Identify: extension bug? backend bug? EA web app issue? configuration issue?
- Note the file:line if possible

## Quick Reference: Backend Endpoints

```bash
# Status
curl -s http://localhost:8000/api/v1/algo/status | python -m json.tool

# Pending signals
curl -s http://localhost:8000/api/v1/algo/signals/pending

# Stop
curl -s -X POST http://localhost:8000/api/v1/algo/stop

# Check positions in DB directly
python -c "
import asyncio
from sqlalchemy import select
from src.server.db import create_engine_and_tables
from src.server.models_db import AlgoPosition, AlgoSignal, AlgoTrade
async def main():
    e, sf = await create_engine_and_tables()
    async with sf() as s:
        for p in (await s.execute(select(AlgoPosition))).scalars().all():
            print(f'POS: ea_id={p.ea_id} qty={p.quantity} buy={p.buy_price} listed_price={p.listed_price} listed_at={p.listed_at}')
        for s2 in (await s.execute(select(AlgoSignal).order_by(AlgoSignal.id.desc()).limit(5))).scalars().all():
            print(f'SIG: id={s2.id} {s2.action} ea_id={s2.ea_id} qty={s2.quantity} status={s2.status}')
        for t in (await s.execute(select(AlgoTrade))).scalars().all():
            print(f'TRADE: ea_id={t.ea_id} qty={t.quantity} buy={t.buy_price} sell={t.sell_price} pnl={t.pnl}')
    await e.dispose()
asyncio.run(main())
"
```
