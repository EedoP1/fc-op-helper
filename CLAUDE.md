# FC26 OP Sell List Generator

## What this does

Given a coin budget, finds the best players to OP sell (list above market price) on the FC26 Ultimate Team transfer market. Outputs a ranked list of ~100 players optimized for expected profit.

## How to run

```bash
python -m src.main --budget 1000000
```

## Project structure

```
src/
├── __init__.py
├── config.py        — Constants (EA 5% tax, target 100 players)
├── futgg_client.py  — fut.gg API client (discovery, prices, sales, history)
├── scorer.py        — OP sell scoring (price-at-time verified OP detection)
├── optimizer.py     — Portfolio optimizer (efficiency sorting, swap loop, backfill)
├── main.py          — CLI entry point, display, CSV export
└── models.py        — Pydantic data models (Player, SaleRecord, PricePoint, etc.)
```

## Data source: fut.gg API

- `/api/fut/players/v2/26/?page=N&price__gte=X&price__lte=Y` — paginated player list with price filtering
- `/api/fut/player-prices/26/{eaId}/` — current price, completedAuctions (100 sales), liveAuctions (all visible), hourly price history, momentum, overview
- `/api/fut/player-item-definitions/26/{eaId}/` — card definition (stats, club, league, nation, rarity)

## Scoring approach

For each player:
1. Get 100 most recent completed sales from fut.gg
2. Build price-at-time lookup from hourly price history
3. For each margin (40% down to 3%): count sales where `sold_price >= price_at_that_hour × (1 + margin)`
4. Pick the highest margin that has 3+ verified OP sales
5. `expected_profit = net_profit × op_ratio` (where op_ratio = op_sales / total_sales)
6. `efficiency = expected_profit / buy_price`
7. Sort by efficiency, fill budget greedily, swap loop replaces expensive cards with cheaper alternatives

## Filters

- Minimum 3 OP sales at the chosen margin (filters out lucky one-offs)
- Minimum 7 sales/hr (only liquid cards)
- Minimum 20 live listings (active market presence)
- Price range: 0.5%–10% of budget (prevents one card consuming the whole budget)

## Key design decisions

- **Price-at-time verification**: OP sales checked against market price when the sale happened, not current BIN. Prevents false OP detection from price drops.
- **Efficiency sorting**: `expected_profit / buy_price` naturally favors cheaper cards with decent OP ratios over expensive cards with tiny OP ratios. This fills ~60-70 slots instead of 14.
- **No FUTBIN dependency**: All scoring uses fut.gg API only. FUTBIN integration exists in git history but was removed — the sold/expired data was useful but added complexity and rate limiting issues.

## Python setup

Python 3.12. Dependencies: httpx, pydantic, rich, click (`pip install -r requirements.txt`).
