# Dashboard Rebuild Design

## Overview

Complete rebuild of the web dashboard (`dashboard.html`) from scratch. Replace the current vanilla JS implementation with Alpine.js for reactivity. Add time filtering, profit ranking views, stale card tracking, and sortable tables.

**Tech:** Single HTML file, Alpine.js (CDN), Chart.js (CDN), served from FastAPI `GET /dashboard`.

**Theme:** Dark theme, purple accent (`#6c5ce7`), responsive (single column on mobile).

## Layout

### Header (sticky)
- Title: "OP Seller Dashboard"
- Scanner status pill (green pulse = running, red = stopped, yellow = unknown)
- API URL input (persisted to localStorage)
- Refresh button + last updated timestamp

### Tab Bar
Four tabs: **Profit** | **Stale Cards** | **Portfolio** | **Scanner**

Each tab renders its own content area below the tab bar.

---

## Tab 1: Profit

### Time Filter
Preset buttons: `1h | 24h | 7d | 30d | All`. Applies to all data in this tab. Default: `All`.

### Stats Row (4 cards)
- Total Spent
- Total Earned
- Realized Profit
- Unrealized P&L

Values colored green (positive) or red (negative).

### Top Profitters Table
Cards ranked by total realized profit across all their sales.

| Column | Description |
|--------|-------------|
| Name | Player name |
| Times Sold | Number of completed sales |
| Total Spent | Sum of buy prices for this card |
| Total Earned | Sum of sell prices |
| Realized Profit | Total earned - total spent (after tax) |

Default sort: Realized Profit desc. All columns sortable (click header toggles asc/desc, arrow indicator on active column).

### Profit Rate Table
Cards ranked by profit per hour.

| Column | Description |
|--------|-------------|
| Name | Player name |
| Profit/hr | Realized profit / total hours active |
| Total Profit | Realized profit |
| Time Active | Duration from first buy to last sell |

Default sort: Profit/hr desc. All columns sortable.

"Time active" = time from first buy to last sell within the filtered time range. Cards with 0 sells excluded from this table.

---

## Tab 2: Stale Cards

### View 1: Longest Unsold

Cards that were bought but haven't sold yet, ranked by how long they've been held.

Has its own time filter: `1h | 24h | 7d | 30d | All`. Filter scopes to buys within the time window.

| Column | Description |
|--------|-------------|
| Name | Player name |
| Buy Price | Price paid |
| Time Since Buy | Duration since buy event |
| Status | Current status (BOUGHT/LISTED) |

Default sort: Time Since Buy desc. All columns sortable.

Only shows cards with no corresponding sell — cards currently held.

### View 2: Avg Sale Time

Per-card average sale frequency. Cards that haven't sold are penalized to the bottom.

Has its own time filter.

| Column | Description |
|--------|-------------|
| Name | Player name |
| Total Sales | Number of completed sales |
| Time Period | Duration of observation window |
| Avg Time Between Sales | Time period / total sales |

Default sort: Avg Time Between Sales desc (slowest sellers first). All columns sortable.

Cards with 0 sales: show "No sales" in the avg column and sort them to the bottom regardless of sort direction.

---

## Tab 3: Portfolio

### Time Filter
Preset buttons: `1h | 24h | 7d | 30d | All`. Filters portfolio entries by trade activity within the window.

### Portfolio Table
Current portfolio state per player.

| Column | Description |
|--------|-------------|
| Name | Player name |
| Status | BOUGHT / LISTED / SOLD / EXPIRED / PENDING |
| Buy Price | Price paid |
| Sell Price | Listed sell price |
| Times Sold | Number of completed sales |
| Realized P&L | Profit from completed sales |
| Unrealized P&L | Estimated P&L from held cards |
| Current BIN | Latest market price |

Default sort: Name asc. All columns sortable.

---

## Tab 4: Scanner

No time filter. Shows current state.

### Stats Grid (6 cards)
- Success Rate (1h) — percentage
- Players in DB — count
- Queue Depth — count
- Circuit Breaker — state string
- Last Scan — relative time ("X minutes ago")
- Daily Cap — count / cap with progress bar

### Top Scored Players Table

| Column | Description |
|--------|-------------|
| Name | Player name |
| Rating | Card rating |
| Position | Player position |
| Buy Price | Recommended buy price |
| Margin | OP margin percentage |
| OP Ratio | Ratio of OP sales |
| Expected Profit/hr | Projected hourly profit |
| Efficiency | Expected profit / buy price |

Default sort: Efficiency desc. All columns sortable.

---

## Backend Changes

### Time Filter Support

Add `?since=` query parameter to these endpoints:
- `GET /api/v1/profit/summary?since=1h`
- `GET /api/v1/portfolio/status?since=24h`

Accepted values: `1h`, `24h`, `7d`, `30d`, or omit for all data.

Implementation: filter `TradeRecord.recorded_at >= now() - interval` in the SQL queries. The FIFO matching logic in profit summary must only consider trades within the window.

### New Endpoint: Stale Cards

`GET /api/v1/portfolio/stale?since=7d`

Response:
```json
{
  "longest_unsold": [
    {
      "ea_id": 12345,
      "name": "Player Name",
      "buy_price": 50000,
      "bought_at": "2026-04-01T10:00:00Z",
      "time_since_buy_hours": 96.5,
      "status": "LISTED"
    }
  ],
  "avg_sale_time": [
    {
      "ea_id": 12345,
      "name": "Player Name",
      "total_sales": 4,
      "first_activity": "2026-03-29T10:00:00Z",
      "last_activity": "2026-04-05T10:00:00Z",
      "time_period_hours": 168.0,
      "avg_hours_between_sales": 42.0
    }
  ]
}
```

For `longest_unsold`: query `TradeRecord` for buys that have no subsequent sell (FIFO-unmatched), optionally filtered by `since`. Join with latest trade status.

For `avg_sale_time`: aggregate sales per ea_id within the time window. Compute `time_period / total_sales`. Cards with 0 sales get `avg_hours_between_sales: null` — frontend sorts them to bottom.

### Profit Rate Data

Extend `GET /api/v1/profit/summary` response `per_player` items to include:

```json
{
  "profit_per_hour": 285.5,
  "first_buy_at": "2026-04-01T10:00:00Z",
  "last_sell_at": "2026-04-05T10:00:00Z",
  "active_hours": 96.0
}
```

Computed as: `realized_profit / hours_between(first_buy, last_sell)` within the filtered window. Only populated for cards with at least 1 sell.

---

## Sorting Implementation

All tables support client-side sorting via Alpine.js:
- Click column header to sort by that column (default desc for numeric, asc for text)
- Click again to reverse direction
- Arrow indicator on active sort column
- Stable sort to preserve order of equal values

---

## Frontend Architecture (Alpine.js)

Single `x-data` store on `<body>` with:
- `activeTab`: current tab name
- `filters`: per-section time filter state
- `data`: response data from each endpoint
- `sort`: per-table sort column and direction
- Helper methods: `fetchProfit()`, `fetchPortfolio()`, `fetchStale()`, `fetchHealth()`, `fetchPlayers()`, `sortBy(table, column)`

Tab switching: `x-show` on tab content divs, `@click` on tab buttons.

Time filter buttons: `@click` updates filter state and re-fetches relevant data.

Tables: `x-for` over sorted/filtered data arrays. Column headers have `@click="sortBy('tableName', 'column')"`.

---

## Migration Notes

- `dashboard.html` is completely rewritten — no code carried over
- All existing API endpoints remain backwards-compatible (new `since` param is optional, defaults to no filter = all data)
- `GET /dashboard` route in `src/server/main.py` unchanged — still serves the HTML file
- New `/api/v1/portfolio/stale` endpoint added to `src/server/api/portfolio_status.py`
