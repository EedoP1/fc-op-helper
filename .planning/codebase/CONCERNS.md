# Codebase Concerns

**Analysis Date:** 2026-03-25

## Rate Limiting & API Throttling

**Issue: No exponential backoff or retry logic for rate-limited responses**
- Files: `src/futgg_client.py`
- Impact: If fut.gg API returns 429 (Too Many Requests), the tool fails silently and logs an error but doesn't retry. This can abandon an entire discovery run or fail mid-execution.
- Current mitigation: Fixed 0.15s delay between requests (line 73) is not rate-limit aware
- Recommendations:
  - Implement exponential backoff on 429 responses
  - Add circuit breaker pattern to pause discovery if rate limits detected
  - Consider implementing jitter in the 0.15s delay to reduce thundering herd problems
  - Add configurable concurrency limit (currently hardcoded to 10 in `main.py` line 58)

## Data Quality: Silent Failures in Parsing

**Issue: Bare `except Exception` blocks silently skip malformed API responses**
- Files: `src/futgg_client.py` lines 242-243, 259-260
- Impact: If fut.gg API response structure changes or returns unexpected data, price history or sales records silently fail to parse. The tool continues with incomplete data, producing incorrect OP detection scores.
- Current mitigation: Logging at error level, but execution continues with partial data
- Recommendations:
  - Log specific exception types and response structures that failed to parse
  - Track count of failed parses and warn user if >10% of records fail
  - Consider failing hard on first parse error during discovery phase, soft-failing during individual player scoring

## Data Quality: Incomplete Price History Edge Cases

**Issue: Price-at-time lookup can fail with incomplete historical data**
- Files: `src/scorer.py` lines 96-105
- Impact: `_get_price_at_time()` searches ±2 hours for price data. If no price point exists within that range, it falls back to current BIN, which defeats the purpose of "price-at-time verification". This causes false OP detection for volatile cards where price changed significantly.
- Example: Sale happened 3 hours ago, nearest price data is 4+ hours back, falls back to current (lower) price
- Recommendations:
  - Warn if fallback is used (log with player name and how far from sale time)
  - Consider requiring minimum historical coverage (e.g., price point every 2 hours max)
  - Make fallback behavior configurable (strict vs. lenient)

## Performance: N+1 API Calls During Discovery

**Issue: `discover_players()` makes unnecessary batch calls**
- Files: `src/futgg_client.py` lines 141-200, specifically 179
- Impact: For each page of results, makes a `get_batch_prices()` call. If discovery returns 50 pages with 20 players each = 1000 players = 50 API calls just to get prices. Then makes another 1000 calls for full market data.
- Current design: `discover_players()` filters by price twice (lines 186-189)
- Recommendations:
  - Use fut.gg's built-in price filtering on discovery endpoint (already done with `price__gte` and `price__lte` params)
  - Skip `get_batch_prices()` in discovery loop since prices are already in the response
  - Verify `result["data"][0]` includes "price" field before assuming separate batch call needed

## Performance: Unbounded Discovery Loop

**Issue: `discover_players()` can run indefinitely with default parameters**
- Files: `src/futgg_client.py` lines 141-200
- Impact: Default `max_pages=999` means if fut.gg returns paginated results indefinitely, this keeps fetching. Could consume all API quota, time out, or run for hours.
- Current mitigation: Checks `result.get("next")` to break loop (line 196)
- Recommendations:
  - Add timeout parameter (max seconds to spend in discovery)
  - Log page count and warn if >10 pages fetched
  - Default max_pages to something like 20 (covers ~1000 players) instead of 999

## Missing Error Context in Main Pipeline

**Issue: Failures in pipeline stages don't provide actionable user feedback**
- Files: `src/main.py` lines 47-62
- Impact: If `discover_players()` returns 0 candidates, user sees "No candidates found" but doesn't know if it was:
  - No players in price range (user error)
  - API failure (rate limit, network error)
  - Price filtering too aggressive
- Recommendations:
  - Log candidate count by price bucket before final filter
  - Log how many failed during batch data fetch (line 58-61: `valid_md` filtering)
  - Add summary: "Got X candidates, Y had valid prices, Z scored viable" not just final counts

## Resource Management: CSV Files Accumulate Indefinitely

**Issue: Each run creates a new CSV file, nothing cleans old ones**
- Files: `src/main.py` lines 133-156
- Impact: Running tool 100 times = 100 CSV files in project root. No rotation, cleanup, or archive logic.
- Recommendations:
  - Move CSV to `results/` subdirectory
  - Implement optional cleanup flag (delete CSVs older than N days)
  - Or use single output file mode with append/overwrite option

## Missing Input Validation

**Issue: No validation of budget parameter**
- Files: `src/main.py` line 160, `main.py` line 41
- Impact:
  - Negative budget allowed (would result in empty portfolio)
  - Budget 0 or negative doesn't fail, just returns empty list
  - Very large budget (>1B coins) could cause integer overflow in price calculations
- Recommendations:
  - Validate budget >= 100 (minimum sensible value)
  - Validate budget <= 10B (reasonable max)
  - Return human-readable error for invalid values

## Fragile Area: Optimizer Swap Loop

**Issue: Complex swap algorithm has subtle edge cases**
- Files: `src/optimizer.py` lines 46-82
- Impact:
  - Swap loop condition (line 72) requires `len(replacements) >= 2` — what if 1 better replacement fits? It won't be used.
  - `swaps < 100` loop limit is arbitrary (line 49) — could stop prematurely or run too long
  - No logging of what/why swaps happen — hard to debug if results unexpected
- Test coverage: Only `test_swap_replaces_expensive_with_cheaper()` tests happy path
- Recommendations:
  - Allow single replacement if `repl_ep > expensive["expected_profit"]`
  - Log each swap: "Replaced player X (500 ep) with Y players (550 ep)"
  - Make swap loop limit configurable (currently hardcoded 100)
  - Add test for edge case: what if no replacements fit even partially?

## Incomplete Error Handling in Async Gather

**Issue: `asyncio.gather()` silently discards failed requests**
- Files: `src/futgg_client.py` line 137
- Impact: If 10 of 100 players fail to fetch, they return None in list, get filtered (line 60 in main.py). User never sees which players failed or why.
- Recommendations:
  - Log player EA IDs that failed to fetch with reason
  - Consider `return_exceptions=True` and handle per-result in client

## Test Coverage Gap: API Error Scenarios

**Issue: No tests for API failures, timeouts, or malformed responses**
- Files: `tests/` (all test files use mock client only)
- Impact:
  - Real API error handling untested: HTTP 500, 503, network timeout (30s limit line 55)
  - Malformed JSON responses not tested
  - Empty responses handling not tested
- Recommendations:
  - Add integration tests against real API or API mock server (e.g., responses library)
  - Test timeout scenarios (network delay > 30s)
  - Test malformed JSON in price history and sales parsing

## Test Coverage Gap: Portfolio Edge Cases

**Issue: Limited testing of optimizer boundary conditions**
- Files: `tests/test_optimizer.py`
- Missing scenarios:
  - What if all players have same efficiency? (implementation detail: will depend on list order)
  - What if budget exactly fits N players? (should work but untested)
  - What if single player costs more than budget? (tested: `test_budget_too_small_for_any_player()` does cover this)
  - Swap loop behavior when multiple swaps possible simultaneously
- Recommendations:
  - Add deterministic sorting test for tie-breaking
  - Add test for exact budget match (no backfill needed)
  - Add test for cascading swaps (swap A with BC, then BC with DEFG)

## Architectural Concern: Tight Coupling to Console Output

**Issue: Display logic mixed with business logic in `main.py`**
- Files: `src/main.py` lines 83-156
- Impact: Hard to use this tool programmatically (library mode). Display functions use console.print() directly, can't suppress or redirect output.
- Recommendations:
  - Move display logic to separate module
  - Have `run()` return results dict, let caller decide display
  - Keep `main()` as CLI entry point that calls display functions

## API Contract Assumptions

**Issue: Code assumes fut.gg API contract but doesn't document required fields**
- Files: `src/futgg_client.py`
- Impact:
  - Line 181: assumes `prices[p["eaId"]: p["price"]` structure
  - Line 219: assumes liveAuctions has "buyNowPrice" field
  - Line 226: assumes overview has "averageBin"
  - If fut.gg changes API structure, failures are silent or cryptic
- Recommendations:
  - Add schema validation using pydantic for API responses
  - Create response model for each endpoint (PlayerPriceResponse, PlayerDefinitionResponse, etc.)
  - Document required vs. optional fields in code comments

## Scoring Sensitivity: Hard-Coded Magic Numbers

**Issue: Multiple tuning parameters with no documentation of impact**
- Files: `src/scorer.py`
- Parameters (lines 17-21):
  - `MARGINS = [40, 35, 30, ...]` — what's the game design justification?
  - `MIN_OP_SALES = 3` — why 3 not 2 or 5?
  - `MIN_SALES_PER_HOUR = 7` — why 7?
  - `MIN_LIVE_LISTINGS = 20` — why 20?
- Impact: Changing any of these significantly affects portfolio composition, but no sensitivity analysis or justification provided
- Recommendations:
  - Move to `config.py` with explanatory comments
  - Add CLI flags to override (e.g., `--min-op-sales 5`)
  - Document in CLAUDE.md why each value was chosen

## Potential Data Consistency Issue

**Issue: Sales data must be sorted by time but no explicit guarantee**
- Files: `src/scorer.py` line 40-41
- Impact: Code assumes sales can be unsorted then calculates time_span from first to last. If API returns sales in arbitrary order, time_span could be 0 or very small, failing scoring.
- Current code: sorts sales once (line 40), uses indices correctly
- Recommendations:
  - Add assertion or validation that sales are >= 2 unique timestamps
  - Log time_span for debugging (currently only appears in output dict)

## Dependency Version Risks

**Issue: Loose version pinning in requirements.txt**
- Files: `requirements.txt`
- Versions: `httpx>=0.27`, `pydantic>=2.0`, etc. (no upper bounds)
- Impact: Future major versions could introduce breaking changes. Reproducibility across environments uncertain.
- Recommendations:
  - Pin exact versions: `httpx==0.27.x` with minor upper bound
  - Use `pip freeze > requirements.lock` for CI/deployment
  - Test against latest versions periodically

## Concurrency Semaphore Not Configurable

**Issue: Hard-coded concurrency limits**
- Files: `src/futgg_client.py` line 131 (default 5), `src/main.py` line 58 (hardcoded 10)
- Impact: Different machine specs might benefit from different concurrency. No way to tune without code change.
- Recommendations:
  - Add CLI flag `--concurrency N` (default 10)
  - Pass through to client
  - Document system resource recommendations (e.g., "use 20 for 16+ cores")

## Integer Overflow Risk in Large Budgets

**Issue: Price calculations use Python int (unlimited) but API might return uint32 equivalents**
- Files: Multiple (e.g., `src/scorer.py` line 71: `sell_price = int(buy_price * (1 + margin))`)
- Impact: Low risk in Python (arbitrary precision) but code assumes multiplication won't overflow
- Recommendations:
  - Validate buy_price <= 50M (reasonable max for FC26 market)
  - Add assertion in score_player() that sell_price is reasonable

---

*Concerns audit: 2026-03-25*
