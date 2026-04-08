# Algo Trading Mode (Promo Dip Buy)

## Overview

A new "Algo" mode in the Chrome extension that executes the `promo_dip_buy` strategy live. The server runs the strategy against real-time `market_snapshots`, emits BUY/SELL signals with quantities, and the extension executes them — buying into the unassigned pile and selling at live market price when signaled.

Separate from the existing OP sell mode. Both modes share DOM helpers and the buy cycle infrastructure, but have independent automation loops, UI tabs, and backend endpoints.

## Server Side

### Signal Engine

A background task (`algo_signal_engine`) that runs on a schedule (every time new snapshots land, or every ~10 minutes). It:

1. Loads `algo_config` to check if algo mode is active and read the budget.
2. Loads `algo_portfolio` to know current held positions and cash.
3. Instantiates `PromoDipBuyStrategy` with the winning params from `param_grid_hourly()`.
4. Calls `set_created_at_map()` with `players.created_at` data to identify promo batches.
5. Replays `market_snapshots` from the last 14 days (max_hold_hours=336h) to rebuild internal state (price history, batch tracking, sell stall counters). This ensures held positions have full sell-signal history and new promo batches within the 13-day buy window are detected.
6. Processes the latest tick via `on_tick_batch()` using a `Portfolio` object constructed from `algo_portfolio` + `algo_config.budget`.
7. Writes resulting BUY/SELL signals to `algo_signals` table. Deduplicates: if a PENDING signal already exists for the same ea_id + action, skip.

The engine maintains the same internal state as `PromoDipBuyStrategy`:

- Identifies promo batches: 10+ cards created in the same hour on a Friday.
- Tracks price history per card from `market_snapshots`.
- **BUY Layer 1 (Strong signal)**: For any promo card within 13 days of release in the 12k-61k range, if 12h median trend >= 21%, emit BUY.
- **BUY Layer 2 (Snapshot)**: At exactly 176h after batch release, rank all remaining unbought promo cards by trend, buy top 3. Fires once per batch.
- **SELL**: After 48h hold delay, when 24h median trend <= 5% for 3 consecutive hours, emit SELL.
- **Force sell** at 336h (14 days).

### Position Sizing (exact match to strategy file)

Position sizing uses the exact logic from `PromoDipBuyStrategy.on_tick_batch`:

1. Calculate `sell_revenue`: sum of (price * quantity * 0.95) for all SELL signals in the same tick.
2. Calculate `available_cash`: portfolio cash + sell_revenue.
3. Calculate `per_card`: available_cash // number_of_buys_in_tick.
4. If `max_position_pct > 0`: calculate total portfolio value (cash + sell_revenue + sum of held positions at current price), then `max_spend = portfolio_value * 0.10`. Cap `per_card = min(per_card, max_spend)`.
5. For each buy: `quantity = per_card // price`.

### Database Tables

#### `algo_config`

| Column | Type | Notes |
|--------|------|-------|
| id | int PK | Single row (id=1) |
| budget | int | Total budget in coins |
| is_active | bool | Whether signal engine should run |
| strategy_params | JSON | Override params (nullable, defaults to winning params) |
| created_at | datetime | |
| updated_at | datetime | |

#### `algo_signals`

| Column | Type | Notes |
|--------|------|-------|
| id | int PK | Auto-increment |
| ea_id | int | Player EA ID |
| action | str | "BUY" or "SELL" |
| quantity | int | Number of cards |
| reference_price | int | Market price at signal time (informational) |
| status | str | PENDING / CLAIMED / DONE / CANCELLED |
| created_at | datetime | |
| claimed_at | datetime | Nullable, set when extension claims it |
| completed_at | datetime | Nullable, set on completion |

#### `algo_portfolio`

| Column | Type | Notes |
|--------|------|-------|
| id | int PK | Auto-increment |
| ea_id | int | Player EA ID |
| quantity | int | Cards held |
| buy_price | int | Price paid per card |
| buy_time | datetime | When bought |
| peak_price | int | Highest price seen since buy |

### API Endpoints

All under `/api/v1/algo/`.

#### `POST /algo/start`

Activate algo trading mode.

**Request body:**
```json
{
  "budget": 5000000
}
```

**Behavior:** Creates or updates `algo_config` row with `is_active=true`. If positions already exist from a previous session, they are preserved (resume).

**Response:**
```json
{
  "status": "ok",
  "budget": 5000000,
  "cash": 5000000,
  "positions": 0
}
```

#### `POST /algo/stop`

Deactivate algo trading mode. Pending signals are cancelled.

**Response:**
```json
{
  "status": "ok"
}
```

#### `GET /algo/status`

Current state of the algo trading system.

**Response:**
```json
{
  "is_active": true,
  "budget": 5000000,
  "cash": 3200000,
  "positions": [
    {
      "ea_id": 12345,
      "name": "Player Name",
      "quantity": 3,
      "buy_price": 15000,
      "buy_time": "2026-04-08T12:00:00",
      "current_price": 18000,
      "peak_price": 19000,
      "unrealized_pnl": 5550
    }
  ],
  "pending_signals": 2,
  "total_pnl": 125000
}
```

#### `GET /algo/signals/pending`

Claim the next pending signal for the extension to execute.

**Behavior:** Same claim pattern as `/actions/pending` — resets stale CLAIMED signals (>5 min), returns one signal, marks it CLAIMED.

**Response:**
```json
{
  "signal": {
    "id": 42,
    "ea_id": 12345,
    "action": "BUY",
    "quantity": 3,
    "reference_price": 15000,
    "player_name": "Player Name",
    "rating": 88,
    "position": "CM",
    "card_type": "TOTS"
  }
}
```

Returns `{"signal": null}` when nothing to do.

#### `POST /algo/signals/{id}/complete`

Record signal execution outcome.

**Request body:**
```json
{
  "outcome": "bought",
  "price": 14800,
  "quantity": 3
}
```

Valid outcomes: `bought`, `sold`, `failed`, `skipped`.

**Behavior:**
- `bought`: Creates `algo_portfolio` row, deducts from cash.
- `sold`: Removes `algo_portfolio` row, adds revenue (after 5% tax) to cash, records P&L.
- `failed` / `skipped`: Resets signal to PENDING for retry (or CANCELLED after 3 failures).

## Extension Side

### New "Algo" Tab

Added to the overlay panel tab bar alongside Portfolio / Dashboard / Automation.

**Contents:**
- Budget input field
- Start / Stop button
- Status display: cash remaining, number of held positions, pending signals count
- Positions list: ea_id, name, quantity, buy_price, current unrealized P&L
- Total P&L (realized + unrealized)

### New Message Types

Added to `ExtensionMessage` discriminated union:

```typescript
| { type: 'ALGO_START'; budget: number }
| { type: 'ALGO_START_RESULT'; success: boolean; error?: string }
| { type: 'ALGO_STOP' }
| { type: 'ALGO_STOP_RESULT'; success: boolean }
| { type: 'ALGO_STATUS_REQUEST' }
| { type: 'ALGO_STATUS_RESULT'; data: AlgoStatusData | null; error?: string }
| { type: 'ALGO_SIGNAL_REQUEST' }
| { type: 'ALGO_SIGNAL_RESULT'; signal: AlgoSignal | null; error?: string }
| { type: 'ALGO_SIGNAL_COMPLETE'; signal_id: number; outcome: string; price: number; quantity: number }
| { type: 'ALGO_SIGNAL_COMPLETE_RESULT'; success: boolean; error?: string }
```

### Algo Automation Loop

Separate from the OP sell `runAutomationLoop`. Runs when algo mode is active.

```
while (!stopped) {
  // 1. Poll for next signal
  signal = GET /algo/signals/pending

  if (signal == null) {
    wait 30-60 seconds
    continue
  }

  if (signal.action == "BUY") {
    // 2a. Execute buy cycle (reuse executeBuyCycle but SKIP the listing step)
    result = executeBuyCycleAlgo(signal)
    // Card stays in unassigned pile
    POST /algo/signals/{id}/complete (outcome, price, quantity)
  }

  if (signal.action == "SELL") {
    // 2b. Execute sell cycle
    result = executeAlgoSellCycle(signal)
    POST /algo/signals/{id}/complete (outcome, price, quantity)
  }
}
```

### Buy Cycle (Algo variant)

A modified version of `executeBuyCycle` that:
- Searches for the player by name + rarity filter (same as OP sell)
- Does price discovery via binary search (same as OP sell)
- Buys the card (same as OP sell)
- **Skips the listing step** — does NOT open "List on Transfer Market" accordion
- Navigates back to search page for the next buy
- Reports the actual price paid

The price guard uses the signal's `reference_price` (not a portfolio slot's `buy_price`).

### Sell Cycle (New)

`executeAlgoSellCycle(signal)` — a new function:

1. Navigate to unassigned pile (Transfers hub > Unassigned tile).
2. Find the matching card by name + rating verification (same `verifyCard` logic).
3. For each card to sell (up to `signal.quantity`):
   a. Click the card.
   b. Search the transfer market for that player to discover the current cheapest BIN.
   c. Navigate back to unassigned pile.
   d. Click the card again, open "List on Transfer Market" accordion.
   e. Set BIN price to discovered market price (or slightly under to sell fast).
   f. Set start price to BIN - 100.
   g. Click "List for Transfer".
4. If transfer list is full: wait for listed cards to sell/expire, then continue.
5. After listing, monitor: poll transfer list for sold status. When sold, report completion.

**Price discovery detail:** The sell cycle searches the transfer market for the same player name + rarity, reads the cheapest BIN from results (same as buy cycle's price reading logic), and uses that as the list price.

## Data Flow

```
Scanner writes market_snapshots (hourly)
        |
        v
Signal engine runs (~every 10 min or on new snapshots)
  - Loads algo_config (budget, is_active)
  - Loads algo_portfolio (held positions, cash)
  - Instantiates PromoDipBuyStrategy
  - Replays market_snapshots to rebuild state
  - Processes latest tick -> BUY/SELL signals
  - Writes to algo_signals table
        |
        v
Extension polls GET /algo/signals/pending
        |
        v
  BUY signal:
    executeBuyCycleAlgo -> buy card -> stays in unassigned pile
    POST /algo/signals/{id}/complete (bought, price, qty)
    Server creates algo_portfolio row, deducts cash
        |
  SELL signal:
    executeAlgoSellCycle -> find in unassigned -> price discover -> list at market BIN
    Monitor until sold
    POST /algo/signals/{id}/complete (sold, price, qty)
    Server removes algo_portfolio row, adds revenue, records P&L
```

## Not In Scope (Future)

- OP selling held positions while waiting for sell signal
- Multiple strategies running simultaneously
- Strategy parameter tuning from the UI
- Cloud deployment of signal engine
