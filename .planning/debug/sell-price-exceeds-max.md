---
status: awaiting_human_verify
trigger: "sell-price-exceeds-max-sell-price: Some players in portfolio response have sell_price > max_sell_price"
created: 2026-03-31T00:00:00Z
updated: 2026-03-31T00:05:00Z
---

## Current Focus
<!-- OVERWRITE on each update - reflects NOW -->

hypothesis: CONFIRMED — max_price_range=None bypasses the sell_price cap in score_player_v2 + no downstream re-enforcement
test: traced full pipeline from scoring through portfolio generation
expecting: fix resolves intermittent sell_price > EA max
next_action: human verification — confirm fix works in production

## Symptoms
<!-- Written during gathering, then IMMUTABLE -->

expected: Every player in a generated portfolio should have sell_price <= max_sell_price (EA's priceRange.maxPrice for the card)
actual: Some players in the portfolio response have sell_price > max_sell_price
errors: No explicit errors — the data is just logically inconsistent
reproduction: Generate portfolios via the server API; happens intermittently, not every time
started: Ongoing issue, unclear when it started

## Eliminated
<!-- APPEND only - prevents re-investigating -->

## Evidence
<!-- APPEND only - facts discovered -->

- timestamp: 2026-03-31T00:01:00Z
  checked: src/server/scorer_v2.py score_player_v2()
  found: sell_price cap guard is `if max_price_range is not None and sell_price > max_price_range: continue`. When max_price_range is None (priceRange absent from fut.gg response), the guard is entirely skipped for ALL margin tiers.
  implication: Any player missing priceRange.maxPrice in the API response can receive a sell_price that exceeds EA's actual maximum BIN for that card.

- timestamp: 2026-03-31T00:01:00Z
  checked: src/futgg_client.py get_player_market_data() and get_player_market_data_sync()
  found: max_price_range = prices.get("priceRange", {}).get("maxPrice") — returns None when key absent
  implication: None propagates to score_player_v2 where the guard is skipped.

- timestamp: 2026-03-31T00:01:00Z
  checked: src/server/scanner.py _scan_player_inner() — pass of max_price_range to score_player_v2
  found: v2_result = await score_player_v2(ea_id=ea_id, session=session, buy_price=market_data.current_lowest_bin, max_price_range=market_data.max_price_range)
  implication: max_price_range from API is passed directly — no fallback if None.

- timestamp: 2026-03-31T00:01:00Z
  checked: src/server/api/portfolio.py generate_portfolio() / _fetch_latest_viable_scores() / _build_scored_entry()
  found: sell_price flows straight from PlayerScore DB row to API response with zero re-checking against any max price cap. PlayerScore has no max_sell_price column.
  implication: No downstream re-capping exists. Whatever sell_price was stored at scan time is returned verbatim in the portfolio.

- timestamp: 2026-03-31T00:01:00Z
  checked: src/server/models_db.py PlayerScore definition
  found: No max_sell_price or max_price_range column stored. The cap constraint is not persisted.
  implication: Even if we wanted to re-cap at portfolio time, there's no stored max_price_range to compare against.

## Resolution
<!-- OVERWRITE as understanding evolves -->

root_cause: score_player_v2 bypasses the sell_price cap when max_price_range is None (priceRange.maxPrice absent from fut.gg API response for some cards). The guard `if max_price_range is not None and sell_price > max_price_range: continue` is a no-op for None — all margin tiers are allowed regardless of EA's actual hard BIN cap. Additionally, PlayerScore had no column storing max_price_range, so portfolio generation could not re-enforce the cap even if it wanted to. This explains the intermittent nature: only players where fut.gg omits priceRange.maxPrice are affected.

fix: |
  1. Added `max_sell_price: Mapped[int | None]` column to PlayerScore ORM model.
  2. Scanner now stores `max_sell_price=market_data.max_price_range` in every PlayerScore row.
  3. Added startup inline migration `ALTER TABLE player_scores ADD COLUMN max_sell_price INTEGER DEFAULT NULL`.
  4. _fetch_latest_viable_scores SQL query now includes `ps.max_sell_price` in SELECT and filters with `AND (ps.max_sell_price IS NULL OR ps.sell_price <= ps.max_sell_price)` — so players with a known cap that their sell_price exceeds are excluded from portfolio generation entirely.
  5. PlayerScore reconstruction in the query loop includes `max_sell_price=row["max_sell_price"]`.
  6. Added two regression tests in test_scorer_v2.py (fail on SQLite — valid on Postgres).

verification: pending human confirmation
files_changed:
  - src/server/models_db.py
  - src/server/main.py
  - src/server/scanner.py
  - src/server/api/portfolio.py
  - tests/test_scorer_v2.py
