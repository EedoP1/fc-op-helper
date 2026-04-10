# Algo Trading Lifecycle Bug Fixes — Design Spec

**Date:** 2026-04-10
**Branch:** feat/algo-trading-backtester
**Scope:** Fix 3 bugs found during E2E testing of the algo trading mode

## Problem Statement

E2E testing of the algo trading mode revealed 3 bugs that block live usage (Phase 4):

1. **Bug #1 — Strategy emits duplicate BUYs, runner dedup drops them, SELL qty > BUY qty.** The strong-buy layer in `promo_dip_buy.py` has no guard against buying a player already held. Over multiple ticks the engine internally accumulates positions (e.g., 22 units) but the runner's `(ea_id, action)` dedup keeps only the first BUY signal (5 units). The SELL signal uses the full internal holding (22). In live mode: extension buys 5, later tries to sell 22 it doesn't have.

2. **Bug #2 — Extension reports SELL outcome as `'listed'`, backend rejects with 400.** `algo-automation-loop.ts:158` sends `outcome: 'listed'` but `algo.py:327` only accepts `{bought, sold, failed, skipped}`. Every sell-listing completion fails silently.

3. **Bug #3 — Position deleted when card is listed, not when actually sold.** Even if Bug #2's string were fixed to `'sold'`, the backend would delete `AlgoPosition` the moment the card is placed on the transfer list — before any buyer purchases it. User requirement: "move a player to sold only when he actually sold." Additionally, expired listings need to be relisted at current lowest BIN (not original price) to ensure liquidation.

## Design

### Bug #1 Fix: Strategy Guard

**File:** `src/algo/strategies/promo_dip_buy.py`

Add a single guard in the strong-buy layer (around line 178), after the `promo_ids` check:

```python
if portfolio.holdings(ea_id) > 0:
    continue
```

This matches the snapshot-buy layer which already has this check (line 232). After the fix, BUY is emitted at most once per player per activation → BUY qty equals SELL qty.

### Bug #2 + #3 Fix: Sold Lifecycle Redesign

#### Lifecycle States

```
BUY signal → extension buys card → card in unassigned pile → AlgoPosition created
SELL signal → extension lists card on TL → AlgoPosition updated (listed_at, listed_price)
  ├─ Card sells → AlgoPosition deleted, AlgoTrade written (profit recorded)
  └─ Card expires → relist at current lowest BIN → AlgoPosition.listed_price updated
       ├─ Card sells → (same as above)
       └─ Card expires again → relist again → (loop until sold)
```

The signal lifecycle ends at "listed" (signal status = DONE). Everything after that — relist, sold detection — operates on the **position**, not the signal.

#### Schema Changes

**AlgoPosition** — add 2 nullable columns:

```
listed_at:    DateTime | None   — when the card was placed on the transfer list
listed_price: Integer | None    — the BIN price it was listed at
```

**New table: `algo_trades`** — records realized trades for profit tracking:

```
id:          Integer, PK, autoincrement
ea_id:       Integer, indexed
quantity:    Integer
buy_price:   Integer    — per-unit, copied from AlgoPosition at sale time
sell_price:  Integer    — per-unit, from extension's trade-observer DOM reading
pnl:         Integer    — (sell_price * 0.95 - buy_price) * quantity
sold_at:     DateTime
```

#### Backend Endpoint Changes

**`POST /algo/signals/{id}/complete`** — expand valid outcomes:

| Outcome | Behavior |
|---|---|
| `'bought'` | Create AlgoPosition (unchanged) |
| `'listed'` | Mark signal DONE. Update AlgoPosition with `listed_at` + `listed_price`. Do NOT delete position. |
| `'sold'` | Delete AlgoPosition (unchanged — but now only called on actual sale) |
| `'failed'` / `'skipped'` | Mark signal CANCELLED (unchanged) |

**New: `POST /algo/positions/{ea_id}/sold`** — `{ sell_price: int, quantity: int }`

- Decrements `position.quantity` by `quantity`
- Writes `algo_trades` row: `pnl = (sell_price * 0.95 - buy_price) * quantity`
- If `position.quantity` reaches 0 → delete position entirely
- Returns 404 if no position exists for `ea_id`

**New: `POST /algo/positions/{ea_id}/relist`** — `{ price: int, quantity: int }`

- Updates `position.listed_price` to `price`
- Updates `position.listed_at` to now
- `quantity` is informational (position qty unchanged — cards aren't gone, just relisted)
- Returns 404 if no position exists for `ea_id`

**`GET /algo/status`** — add `realized_pnl` field by summing `algo_trades.pnl`.

#### Partial Fill Handling

When 3 of 8 listed cards sell and 5 expire:

| Step | Position qty | listed_price | algo_trades |
|---|---|---|---|
| Initial buy (8 × 25k) | 8 | null | — |
| Listed at 45k | 8 | 45,000 | — |
| 3 sold at 45k | 5 | 45,000 | +1 row: qty=3, pnl=3×(42,750−25,000) |
| 5 expired, relisted at 42k | 5 | 42,000 | — |
| 5 sold at 42k | 0 (deleted) | — | +1 row: qty=5, pnl=5×(39,900−25,000) |

#### Extension Changes

**New file: `extension/src/algo-transfer-list-sweep.ts`**

Called by the algo automation loop at the start of each iteration. Uses `scanTransferList()` (existing export from `transfer-list-cycle.ts`) for DOM scanning without side effects.

Flow:
1. Call `scanTransferList()` → get `{ listed, expired, sold }` arrays of `DetectedItem`
2. Fetch `/algo/status` → get positions with `player_name` and `ea_id`
3. Match sold/expired items to AlgoPositions by `name` + `rating`
4. For each **sold** match: send `ALGO_POSITION_SOLD` message → backend decrements qty, writes trade
5. For each **expired** match:
   - Discover current lowest BIN via transfer market search (reuse price discovery from `algo-sell-cycle.ts`)
   - Navigate to card on TL, relist at discovered price
   - Send `ALGO_POSITION_RELIST` message → backend updates `listed_price`
6. Clear sold items (click "Clear Sold" button, same pattern as `transfer-list-cycle.ts`)

Key difference from manual relist: does NOT use "Relist All" button (which relists at original locked price). Instead, individually relists each expired card at current lowest BIN to ensure liquidation.

**`extension/src/algo-automation-loop.ts`** — restructured loop:

```
while (!stopped):
  ── Phase A: Transfer List Sweep ──
  if status has positions with listed_at set:
    run algoTransferListSweep()
    jitter 3-5s

  ── Phase B: Poll for signal ──
  poll ALGO_SIGNAL_REQUEST
  if no signal: wait 30-60s → continue

  ── Phase C: Execute signal ──
  if BUY: executeAlgoBuyCycle (unchanged)
  if SELL: executeAlgoSellCycle → report outcome 'listed'
  jitter 3-5s
```

SELL completion on line 158: `'listed'` string is now correct (backend accepts it).

**`extension/src/messages.ts`** — new message types:

| Message | Direction | Payload |
|---|---|---|
| `ALGO_POSITION_SOLD` | content → background → backend | `{ ea_id, sell_price, quantity }` |
| `ALGO_POSITION_RELIST` | content → background → backend | `{ ea_id, price, quantity }` |
| `ALGO_POSITION_SOLD_RESULT` | background → content | `{ success, error? }` |
| `ALGO_POSITION_RELIST_RESULT` | background → content | `{ success, error? }` |

**`extension/entrypoints/background.ts`** — 2 new handler functions (thin proxies, same pattern as existing algo handlers):
- `handleAlgoPositionSold(ea_id, sell_price, quantity)` → `POST /algo/positions/{ea_id}/sold`
- `handleAlgoPositionRelist(ea_id, price, quantity)` → `POST /algo/positions/{ea_id}/relist`

## Files Changed

| File | Change Type | Description |
|---|---|---|
| `src/algo/strategies/promo_dip_buy.py` | Edit | Add holdings guard in strong-buy loop |
| `src/server/models_db.py` | Edit | Add `listed_at`, `listed_price` to AlgoPosition; add AlgoTrade model |
| `src/server/api/algo.py` | Edit | Add 'listed' outcome handling; add `/positions/{ea_id}/sold` and `/relist` endpoints |
| `extension/src/algo-automation-loop.ts` | Edit | Add Phase A (TL sweep) before signal polling |
| `extension/src/algo-transfer-list-sweep.ts` | New | TL scan → match positions → report sold/relist expired at lowest BIN |
| `extension/src/messages.ts` | Edit | Add ALGO_POSITION_SOLD/RELIST message types |
| `extension/entrypoints/background.ts` | Edit | Add 2 handler functions for sold/relist messages |

## Out of Scope

- Parity test fix (tautological test noted during E2E — separate concern)
- Backtester integration test failures (`test_integration.py` — pre-existing, unrelated)
- Algo tab visibility in empty/draft panel states (UX decision, not a bug)
