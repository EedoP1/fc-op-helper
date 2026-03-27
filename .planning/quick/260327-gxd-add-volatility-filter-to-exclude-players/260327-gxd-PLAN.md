---
phase: quick
plan: 260327-gxd
type: execute
wave: 1
depends_on: []
files_modified:
  - src/config.py
  - src/server/api/portfolio.py
  - tests/test_portfolio.py
autonomous: true
requirements: []
must_haves:
  truths:
    - "Players whose price increased more than the threshold over 3 days are excluded from portfolio results"
    - "Players with insufficient price history (fewer than 3 days of snapshots) are NOT excluded"
    - "The volatility filter applies to GET /portfolio, POST /portfolio/generate, POST /portfolio/swap-preview, and DELETE /portfolio/{ea_id} replacement candidates"
  artifacts:
    - path: "src/config.py"
      provides: "VOLATILITY_MAX_PRICE_INCREASE_PCT and VOLATILITY_LOOKBACK_DAYS constants"
    - path: "src/server/api/portfolio.py"
      provides: "Volatility filter function and its application in all portfolio query paths"
    - path: "tests/test_portfolio.py"
      provides: "Tests verifying volatile players are excluded and stable players are kept"
  key_links:
    - from: "src/server/api/portfolio.py"
      to: "src/server/models_db.MarketSnapshot"
      via: "SQL query comparing oldest vs newest current_lowest_bin within lookback window"
      pattern: "MarketSnapshot.*current_lowest_bin"
---

<objective>
Add a volatility filter to portfolio selection that excludes players whose price has increased
significantly (e.g., >30%) over the last 3 days. Players whose price spiked recently are risky
OP sell targets because the OP sell scoring was likely computed against lower historical prices,
and the current elevated price makes the margin unreliable.

Purpose: Prevent buying into players at inflated prices where OP sell margins are based on stale
lower-price data.

Output: Modified portfolio endpoints that filter out volatile players before running the optimizer.
</objective>

<execution_context>
@$HOME/.claude/get-shit-done/workflows/execute-plan.md
@$HOME/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@src/config.py
@src/server/api/portfolio.py
@src/server/models_db.py
@tests/test_portfolio.py
</context>

<interfaces>
<!-- Key types and contracts the executor needs. -->

From src/server/models_db.py:
```python
class MarketSnapshot(Base):
    __tablename__ = "market_snapshots"
    id: Mapped[int]
    ea_id: Mapped[int]          # indexed
    captured_at: Mapped[datetime]  # indexed (ea_id, captured_at)
    current_lowest_bin: Mapped[int]
    listing_count: Mapped[int]
    live_auction_prices: Mapped[str]  # JSON list

class PlayerScore(Base):
    __tablename__ = "player_scores"
    ea_id: Mapped[int]          # indexed
    scored_at: Mapped[datetime]
    buy_price: Mapped[int]
    is_viable: Mapped[bool]
    # ... other score fields

class PlayerRecord(Base):
    __tablename__ = "players"
    ea_id: Mapped[int]          # primary key
    is_active: Mapped[bool]
    last_scanned_at: Mapped[datetime | None]
    # ... other metadata fields
```

From src/server/api/portfolio.py:
```python
def _build_scored_entry(score: PlayerScore, record: PlayerRecord) -> dict
# Used by: get_portfolio, generate_portfolio, swap_preview, delete_portfolio_player
# All follow the same pattern: query viable scores + records, build scored_list, run optimize_portfolio()
```

From src/config.py:
```python
STALE_THRESHOLD_HOURS = 4
MARKET_DATA_RETENTION_DAYS = 30
```
</interfaces>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: Add volatility filter config and helper function</name>
  <files>src/config.py, src/server/api/portfolio.py</files>
  <behavior>
    - Test 1: Player with 50% price increase over 3 days is flagged as volatile (ea_id returned in volatile set)
    - Test 2: Player with 10% price increase over 3 days is NOT flagged as volatile
    - Test 3: Player with fewer than 2 snapshot timestamps in the lookback window is NOT flagged (insufficient data)
    - Test 4: Player with price DECREASE is NOT flagged
    - Test 5: Multiple players — only the volatile ones appear in the volatile set
  </behavior>
  <action>
    1. Add two constants to src/config.py:
       - VOLATILITY_MAX_PRICE_INCREASE_PCT = 30 (percent — players with >30% price increase are excluded)
       - VOLATILITY_LOOKBACK_DAYS = 3 (how far back to check)

    2. In src/server/api/portfolio.py, add an async helper function:

       async def _get_volatile_ea_ids(session: AsyncSession, ea_ids: list[int]) -> set[int]:
           """Return ea_ids whose price increased more than VOLATILITY_MAX_PRICE_INCREASE_PCT
           over the last VOLATILITY_LOOKBACK_DAYS days.

           For each player, compares the earliest MarketSnapshot.current_lowest_bin in the
           lookback window against the latest. If (latest - earliest) / earliest > threshold,
           the player is volatile.

           Players with fewer than 2 distinct snapshot timestamps in the window are skipped
           (not enough data to determine trend).
           """

       Implementation approach:
       - Query MarketSnapshot rows where ea_id IN (ea_ids) AND captured_at >= (now - lookback_days)
       - Group by ea_id. For each group, find the row with min(captured_at) and max(captured_at)
       - Use two subqueries or a single query with window functions. Simplest: use a raw SQL approach
         with func.min/func.max on captured_at, then fetch the prices at those timestamps.
       - Actually simplest: do TWO aggregation queries per ea_id batch:
         (a) earliest price per ea_id in window (subquery on min captured_at)
         (b) latest price per ea_id in window (subquery on max captured_at)
         Then compare in Python.
       - Return set of ea_ids where increase_pct > VOLATILITY_MAX_PRICE_INCREASE_PCT / 100.

    3. Import the new config constants and AsyncSession in portfolio.py. Add MarketSnapshot import.
  </action>
  <verify>
    <automated>python -m pytest tests/test_portfolio.py -x -v -k "volatil"</automated>
  </verify>
  <done>_get_volatile_ea_ids function exists, config constants defined, unit tests pass</done>
</task>

<task type="auto">
  <name>Task 2: Apply volatility filter to all portfolio endpoints</name>
  <files>src/server/api/portfolio.py, tests/test_portfolio.py</files>
  <action>
    Apply the volatility filter in all four portfolio query paths. The pattern is the same in each:
    after querying rows (PlayerScore + PlayerRecord), before building scored_list, call
    _get_volatile_ea_ids and exclude those ea_ids.

    1. In get_portfolio() (GET /portfolio):
       - After `rows = result.all()` (line ~139), while still inside the `async with sf() as session:` block,
         collect ea_ids from rows and call `volatile = await _get_volatile_ea_ids(session, [score.ea_id for score, record in rows])`
       - Filter: `rows = [(s, r) for s, r in rows if s.ea_id not in volatile]`
       - Log at INFO level how many players were filtered: "Volatility filter removed {N} of {total} candidates"

    2. In generate_portfolio() (POST /portfolio/generate):
       - Same pattern, same location (after rows query, inside session block)

    3. In swap_preview() (POST /portfolio/swap-preview):
       - Same pattern, applied before the existing excluded_ea_ids filter

    4. In delete_portfolio_player() (DELETE /portfolio/{ea_id}):
       - Same pattern, after the viable candidates query, inside the session block

    IMPORTANT: The session must still be open when _get_volatile_ea_ids is called (it needs DB access).
    Move the volatile filter call INSIDE the `async with sf() as session:` block, before the block exits.

    5. Add an integration test in tests/test_portfolio.py:
       - Create a seeded app with MarketSnapshot data showing one player with a 50% price spike
         over 3 days and another player that is stable
       - Call GET /portfolio and verify the volatile player is excluded from results
       - Call POST /portfolio/generate and verify same exclusion
  </action>
  <verify>
    <automated>python -m pytest tests/test_portfolio.py -x -v</automated>
  </verify>
  <done>All four portfolio endpoints filter out volatile players. Integration test confirms volatile player excluded, stable player included.</done>
</task>

</tasks>

<verification>
- python -m pytest tests/test_portfolio.py -x -v — all portfolio tests pass including new volatility tests
- python -m pytest tests/ -x — no regressions in other test files
- Verify config constants exist: grep "VOLATILITY" src/config.py
</verification>

<success_criteria>
- GET /portfolio excludes players with >30% price increase over 3 days
- POST /portfolio/generate excludes same
- POST /portfolio/swap-preview excludes same
- DELETE /portfolio/{ea_id} replacement candidates exclude same
- Players with insufficient snapshot history are not penalized
- All existing portfolio tests still pass
- New tests cover the volatility filter logic
</success_criteria>

<output>
After completion, create `.planning/quick/260327-gxd-add-volatility-filter-to-exclude-players/260327-gxd-SUMMARY.md`
</output>
