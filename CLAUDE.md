# FC26 OP Sell List Generator

## What this does

Given a coin budget, finds the best players to OP sell (list above market price) on the FC26 Ultimate Team transfer market. Outputs a ranked list of ~100 players optimized for expected profit.

## How to run

```bash
python -m src.main --budget 1000000
```

## Architecture

- `src/main.py` — Entry point. Discovery → fetch market data → score → optimize → display + CSV export
- `src/futgg_client.py` — fut.gg API client (player discovery, prices, completed sales, price history, live listings)
- `src/futbin_client.py` — FUTBIN sitemap cross-reference for sold/expired data (not used in current scoring flow)
- `src/config.py` — Constants (EA 5% tax rate, target 100 players, margin bounds)
- `src/models.py` — Pydantic data models (Player, SaleRecord, PricePoint, PlayerMarketData, etc.)
- `src/optimizer.py` — Portfolio optimizer with swap loop (currently unused — optimizer is inline in main.py)
- `src/scoring/` — Sub-scorers (legacy — current scoring is inline in main.py's `score_player()`)

## Data sources

### fut.gg API (primary — all scoring uses this)
- `/api/fut/players/v2/26/?page=N&price__gte=X&price__lte=Y` — paginated player list with price filtering
- `/api/fut/player-prices/26/{eaId}/` — current price, completedAuctions (100 sales), liveAuctions (all visible), hourly price history, momentum, overview
- `/api/fut/player-item-definitions/26/{eaId}/` — card definition (stats, club, league, nation, rarity)
- `/api/fut/metarank/players/?ids=X,Y,Z` — meta ranking scores

### FUTBIN (optional — for sold/expired verification)
- Sitemap at `/26/player/{0,1,2}/sitemap.xml` — all FUTBIN player IDs + slugs
- fut.gg sitemap at `/sitemap-player-detail-26.xml` — maps EA IDs to name slugs
- Cross-reference sitemaps by slug to map EA resource ID → FUTBIN ID
- Sales page at `/26/sales/{futbinId}/x?platform=ps` — auctions table with sold vs expired
- EA resource ID verified via `p{eaId}.png` in image URLs on sales page

## Current scoring approach

For each player:
1. Get 100 most recent completed sales from fut.gg
2. Build price-at-time lookup from hourly price history
3. For each margin (40% down to 3%): count sales where `sold_price >= price_at_that_hour × (1 + margin)`
4. Pick the highest margin that has 3+ verified OP sales
5. `expected_profit = net_profit × op_ratio` (where op_ratio = op_sales / total_sales)
6. `efficiency = expected_profit / buy_price`
7. Sort by efficiency, fill budget greedily, swap loop replaces expensive cards with cheaper alternatives

## Key design decisions

- **Price-at-time verification**: OP sales are checked against the market price when the sale happened, not the current BIN. Prevents false OP detection from price drops.
- **Minimum 3 OP sales**: Filters out lucky one-off sales that aren't repeatable patterns.
- **Minimum 7 sales/hr**: Only considers liquid cards with enough market activity.
- **Minimum 20 live listings**: Ensures the card has active market presence.
- **Budget-based price range**: min = 0.5% of budget, max = 10% of budget. Prevents one card consuming the whole budget.
- **Efficiency sorting**: `expected_profit / buy_price` naturally favors cheaper cards with decent OP ratios over expensive cards with tiny OP ratios.

## Python setup

Python 3.12 at `C:\Users\maftu\AppData\Local\Programs\Python\Python312`. Dependencies: httpx, pydantic, rich, click.
