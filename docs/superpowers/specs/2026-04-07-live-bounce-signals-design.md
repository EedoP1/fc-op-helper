# Live New Card Bounce Signal Generator

## Purpose

CLI tool that generates buy and sell signals for the new_card_bounce strategy using live data from the OP-seller Postgres DB. Outputs which EA IDs to buy (with quantities) and which held cards to sell.

## CLI Commands

```
python -m src.algo.live scan --budget 5000000
python -m src.algo.live add <ea_id> <buy_price> <quantity>
python -m src.algo.live positions
```

## Strategy Parameters (hardcoded, from backtesting)

- min_bounce: 0.05 (5%)
- max_bounce: 0.18 (18%)
- min_day: 3
- max_day: 10
- min_price: 13,000
- max_price: 61,000
- trailing_stop: 0.05 (5% from peak)
- max_hold_days: 14

## Data Source

Everything from the OP-seller Postgres DB. No fut.gg API calls at runtime.

- `market_snapshots` table: ~200 price snapshots per player per day (every ~7 min). Aggregated to daily closing price for bounce detection.
- `players` table: player metadata including `created_at` (from fut.gg `createdAt` field) to determine card release date.

## Schema Change: `players.created_at`

Add a `created_at` column (DateTime, nullable) to `PlayerRecord` in `src/server/models_db.py`. This stores the fut.gg `createdAt` timestamp — the exact date EA released the card.

- Populated automatically during scanner discovery (already fetches card definition which contains `createdAt`).
- Backfill existing rows by fetching definitions from fut.gg for players where `created_at IS NULL`.
- "New card" = `created_at` within the last 10 days. No promo name arg needed — works for any promo automatically.

## `scan --budget N` Flow

### Step 1: Load positions

Read `positions.json` from project root. Contains current holdings with buy_price, quantity, buy_time, peak_price per ea_id.

### Step 2: Sell signals

For each position in `positions.json`:
1. Query the latest `market_snapshots` row for that ea_id to get current price.
2. Update peak_price if current > peak.
3. **Trailing stop**: if current price is 5%+ below peak_price, emit SELL signal.
4. **Max hold**: if today minus buy_time >= 14 days, emit SELL signal.

Output: table of sell signals (ea_id, name, qty, buy_price, current_price, reason).

### Step 3: Calculate available cash

`available = budget - sum(pos.buy_price * pos.quantity for all non-sell positions) + sum(sell_revenue)`

Where sell_revenue = current_price * quantity * 0.95 (5% EA tax) for each sell signal.

### Step 4: Buy signals

1. Query `players` table for cards where `created_at` is within the last 10 days (new cards).
2. For new cards in 13K–61K price range, query `market_snapshots` aggregated to daily closing price (last snapshot per UTC day per ea_id).
3. For each candidate:
   - **Card age**: days since `created_at`.
   - **In window**: skip if card_age < 3 or card_age > 10.
   - **Price range**: skip if latest price < 13,000 or > 61,000.
   - **Already holding**: skip if ea_id is in positions.json.
   - **Bounce check**: compare the two most recent daily closing prices. If daily return is between 5% and 18%, it's a buy signal. If the bounce was not between the two most recent days, skip — it's stale.
4. Collect all buy signals.

### Step 5: Size positions

Split `available` cash equally across all buy candidates:
- `per_card = available // num_candidates`
- `quantity = per_card // price` for each card
- Skip cards where quantity < 1.

### Step 6: Output

Print buy signals table: ea_id, player name, current price, bounce %, quantity to buy, total cost.

### Step 7: Save peaks

Update peak_price in `positions.json` for all held positions (even those not being sold).

## `add <ea_id> <buy_price> <quantity>` Flow

1. Read `positions.json` (create if missing).
2. Query `players` table for player name.
3. Append: `{ea_id, name, buy_price, quantity, buy_time: now(), peak_price: buy_price}`.
4. Write back.
5. Print confirmation.

## `positions` Flow

1. Read `positions.json`.
2. For each position, query latest snapshot for current price.
3. Print table: ea_id, name, qty, buy_price, current_price, P&L, days_held, peak, trailing_stop_distance.

## positions.json Schema

```json
[
  {
    "ea_id": 267384,
    "name": "Player Name",
    "buy_price": 19769,
    "quantity": 50,
    "buy_time": "2026-04-07T18:00:00",
    "peak_price": 21000
  }
]
```

## "Today" Definition

The bounce must be between the two most recent daily closing prices. Since the scanner runs continuously, "today" is the current UTC date. If it's early in the day and there's no snapshot for today yet, use yesterday vs day-before-yesterday.

## Edge Cases

- **No snapshots for today yet**: use the two most recent days available.
- **Card has < 2 days of data**: skip (can't compute bounce).
- **positions.json doesn't exist**: create empty `[]` on first `add`.
- **ea_id already in positions**: `add` rejects with error message.
- **Scanner not running**: signals are based on whatever the latest snapshot is. Output warns if latest snapshot is older than 1 hour.
- **players.created_at is NULL**: skip card (backfill not yet run).

## Files

- `src/algo/live.py` — single file, CLI entry point with all three commands.
- `positions.json` — state file in project root.
- `src/server/models_db.py` — add `created_at` column to `PlayerRecord`.
- Scanner discovery code — store `createdAt` when adding new players.

## Future Automation Path

This design keeps all logic in functions that return data structures (not just print). When automation comes, the `scan` function returns buy/sell signal lists that can feed directly into `TradeAction` queue entries in the DB.
