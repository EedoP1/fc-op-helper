---
phase: quick-260418-czo
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - src/futgg_client.py
  - src/server/scanner.py
  - tools/rescan_stuck_players.py
  - tests/test_futgg_client.py
  - tests/test_scanner.py
autonomous: true
requirements:
  - CZO-01-preserve-market-data-when-current-bin-missing
  - CZO-02-skip-snapshot-when-current-bin-zero
  - CZO-03-rescan-stuck-players-one-off

must_haves:
  truths:
    - "When defn is valid AND prices is valid BUT _extract_current_bin(prices) returns None/0, get_player_market_data returns a PlayerMarketData shell (current_lowest_bin=0, listing_count=0, price_history=[], sales=[], live_auction_prices=[], live_auctions_raw=[], futgg_url from defn, max_price_range from prices, created_at parsed from defn.createdAt) — NOT None."
    - "The sync variant get_player_market_data_sync has identical behaviour: returns a PlayerMarketData shell with the same fields when current_bin is falsy."
    - "The PricesFetchError path (defn OK, prices None) is unchanged — still raises, still feeds tenacity retry."
    - "The both-None full-outage path is unchanged — still returns None."
    - "The defn-None path is unchanged — still returns None."
    - "When market_data.current_lowest_bin <= 0, scanner.scan_player does NOT insert a MarketSnapshot row."
    - "When market_data.current_lowest_bin <= 0, scanner.scan_player DOES still update PlayerRecord.last_scanned_at AND record.created_at (when market_data.created_at is present and record.created_at is None)."
    - "tools/rescan_stuck_players.py exists, uses src.config.DATABASE_URL, finds active PlayerRecords with created_at IS NULL AND last_scanned_at IS NOT NULL, and sets their next_scan_at to utcnow()."
    - "tools/rescan_stuck_players.py prompts before committing unless --yes is passed, and prints the affected row count."
    - "The remediation script is NOT executed by the agent (user will run it manually against the live DB after merge)."
    - "pytest passes for the new/modified tests (pre-existing baseline failures from quick-260418-c65/deferred-items.md remain out of scope)."
  artifacts:
    - path: "src/futgg_client.py"
      provides: "get_player_market_data{,_sync} now return a PlayerMarketData shell when current_bin is falsy instead of returning None"
    - path: "src/server/scanner.py"
      provides: "MarketSnapshot insert guarded by market_data.current_lowest_bin > 0; PlayerRecord updates still run"
    - path: "tools/rescan_stuck_players.py"
      provides: "One-off remediation script that queues the ~118 stuck pre-fix cards for immediate rescan"
    - path: "tests/test_futgg_client.py"
      provides: "2 new tests for the shell-return-when-no-current-bin behaviour (async + sync); existing tests updated since the previous 'returns None' expectation is now 'returns shell'"
    - path: "tests/test_scanner.py"
      provides: "1 new test: test_no_snapshot_when_current_lowest_bin_zero — PlayerRecord.last_scanned_at + created_at updated, but no MarketSnapshot row"
  key_links:
    - from: "src/futgg_client.py::get_player_market_data"
      to: "PlayerMarketData shell (current_lowest_bin=0)"
      via: "replacement of 'if not current_bin: return None' with shell-construction branch"
      pattern: "current_lowest_bin=0"
    - from: "src/futgg_client.py::get_player_market_data_sync"
      to: "PlayerMarketData shell (current_lowest_bin=0)"
      via: "replacement of 'if not current_bin: return None' with shell-construction branch"
      pattern: "current_lowest_bin=0"
    - from: "src/server/scanner.py::_scan_player_inner"
      to: "snapshot write gated by current_lowest_bin > 0"
      via: "if market_data is not None and market_data.current_lowest_bin > 0"
      pattern: "current_lowest_bin > 0"
    - from: "tools/rescan_stuck_players.py"
      to: "PlayerRecord.next_scan_at = utcnow()"
      via: "sqlalchemy update() on filtered rows"
      pattern: "update\\(PlayerRecord\\)[\\s\\S]*next_scan_at"
---

<objective>
Close the last scanner silent-data-loss hole and unstick the 118 cards that it
has already stranded.

Previous plan quick-260418-c65 fixed three silent-data-loss bugs, but one hole
remains: when `defn` is valid AND `prices` is valid BUT `_extract_current_bin(prices)`
returns None/0 (card momentarily untradeable — no liveAuctions AND no
currentPrice.price), both `get_player_market_data` and `get_player_market_data_sync`
return None silently. Scanner's success path on None sets `last_scanned_at` but
does NOT populate `created_at`, leaving the card invisible to `promo_dip_buy`'s
Friday-batch detection (which keys on `created_at`). 118 cards in the live DB
are currently stuck in this state.

Fix: both futgg_client paths return a `PlayerMarketData` shell (with
`current_lowest_bin=0` and empty market arrays) instead of None. Scanner then
guards the `MarketSnapshot` write behind `current_lowest_bin > 0` (no polluting
zero-BIN rows in `market_snapshots`) while still updating PlayerRecord's
`last_scanned_at` and `created_at` fields from the shell. Ship a one-off
remediation script that marks the ~118 stuck active rows for immediate rescan.

Purpose: every active card in the 11k-200k range must have `created_at`
populated after its first scan, regardless of whether it was instantaneously
tradeable at that moment. That's the contract `promo_dip_buy` depends on.

Output: 2 edited source files + 1 new script + 2 edited test files;
`pytest tests/test_futgg_client.py tests/test_scanner.py` green. Script
committed as code but NOT executed — user runs it manually against the live
DB after merge.
</objective>

<execution_context>
@$HOME/.claude/get-shit-done/workflows/execute-plan.md
@$HOME/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@CLAUDE.md
@.planning/STATE.md

<!-- Files being edited — executor should read each before modifying -->
@src/futgg_client.py
@src/server/scanner.py
@src/models.py
@tests/test_futgg_client.py
@tests/test_scanner.py

<!-- Prior related plan + summary — understand what already changed and why -->
@.planning/quick/260418-c65-fix-scanner-data-loss-bugs-playwright-ch/260418-c65-PLAN.md
@.planning/quick/260418-c65-fix-scanner-data-loss-bugs-playwright-ch/260418-c65-SUMMARY.md
@.planning/quick/260418-c65-fix-scanner-data-loss-bugs-playwright-ch/deferred-items.md

<interfaces>
<!-- Key contracts the executor needs. Extracted from codebase. -->

PlayerMarketData (src/models.py:43-54):
```python
class PlayerMarketData(BaseModel):
    player: Player
    current_lowest_bin: int
    listing_count: int
    price_history: list[PricePoint]
    sales: list[SaleRecord]
    live_auction_prices: list[int] = []
    live_auctions_raw: list[dict] = []
    futgg_url: Optional[str] = None
    max_price_range: Optional[int] = None
    created_at: Optional[datetime] = None
```
The shell we construct when current_bin is falsy uses current_lowest_bin=0,
listing_count=0, empty lists for price_history/sales/live_auction_prices/live_auctions_raw,
and preserves player, futgg_url, max_price_range, created_at from the real defn/prices.

PlayerRecord (src/server/models_db.py:8-30) — relevant fields:
```python
ea_id: Mapped[int] = mapped_column(Integer, primary_key=True)
last_scanned_at: Mapped[datetime | None]
next_scan_at: Mapped[datetime | None]
is_active: Mapped[bool] = mapped_column(Boolean, default=True)
listing_count: Mapped[int] = mapped_column(Integer, default=0)
created_at: Mapped[datetime | None]
```

futgg_client.py current behaviour (after quick-260418-c65) — both async and sync:
- line 156 (async) / 253 (sync): `current_bin = self._extract_current_bin(prices)`
- line 157 / 254: `if not current_bin: return None`
- lines 160-180 / 257-277: build the full PlayerMarketData

The fix replaces `if not current_bin: return None` with a branch that builds a
shell PlayerMarketData with current_lowest_bin=0 and empty arrays, preserving
player (from defn), futgg_url, max_price_range, created_at. The non-shell path
(current_bin truthy) is unchanged.

Scanner current behaviour (src/server/scanner.py:378-386):
```python
# Persist raw market snapshot
if market_data is not None:
    snapshot = MarketSnapshot(
        ea_id=ea_id,
        captured_at=now,
        current_lowest_bin=market_data.current_lowest_bin,
        listing_count=market_data.listing_count,
    )
    session.add(snapshot)
```
Fix: add `and market_data.current_lowest_bin > 0` to the guard. The PlayerRecord
update block immediately below (lines 388-400) already checks `if market_data is not None`
and is compatible with our shell — `listing_count` will be 0, `listings_per_hour`
stays 0.0, `name` / `futgg_url` / `created_at` will be populated from the shell.

The v3 scoring block (lines 320-329) has its own guard:
`if market_data is not None and market_data.current_lowest_bin > 0:` — this
already means a zero-BIN shell correctly skips scoring, writing the
"not viable" PlayerScore at lines 360-375 with buy_price=0. That's correct
behaviour for a shell; no change needed there.

Existing tests in tests/test_futgg_client.py that need updating:
- test_defn_ok_prices_ok_no_bin_returns_none (line 31) — NOW asserts shell
- test_sync_defn_ok_prices_ok_no_bin_returns_none (line 87) — NOW asserts shell
These two tests encoded the buggy silent-None behaviour. Rename to …_returns_shell
and update assertions to check the shell shape.

Existing test_no_snapshot_on_none_market_data (tests/test_scanner.py:248) —
still valid; its scenario is `market_data=None` (full outage / defn fail), which
still returns None from futgg_client, so the test is unchanged.

New scanner test goes next to it: market_data is a shell (NOT None,
current_lowest_bin=0), asserting PlayerRecord.last_scanned_at + created_at are
set but MarketSnapshot count is 0.

tools/ directory does NOT currently exist. Create it. `tools/__init__.py` should
exist so `python -m tools.rescan_stuck_players` works (the plan-description
example uses this invocation).

src/config.py DATABASE_URL (line 54):
```python
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql+asyncpg://op_seller:op_seller@localhost:5432/op_seller")
```
</interfaces>

<out_of_scope>
- src/algo/** and tests/algo/** — uncommitted user work; do not touch.
- The 14 pre-existing baseline failures documented in quick-260418-c65/deferred-items.md.
- Do NOT execute tools/rescan_stuck_players.py; the user will run it manually.
- Do NOT change _extract_current_bin or the overview.averageBin fallback — that's the upstream detection; we're fixing the downstream handling.
- Do NOT change the PricesFetchError semantics or the full-outage / defn-only-failure paths.
</out_of_scope>
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: futgg_client — preserve market_data shell when current_bin is falsy</name>
  <files>src/futgg_client.py, tests/test_futgg_client.py</files>
  <behavior>
    - Test 1 (async): `test_get_player_market_data_returns_shell_when_no_current_bin`
      - Mock `get_player_definition` to return a valid defn dict with `createdAt` = a real ISO timestamp (e.g. `"2026-04-10T12:00:00Z"`).
      - Mock `get_player_prices` to return a valid prices dict with `liveAuctions=[]`, `currentPrice={"price": 0}`, no `overview` (or `overview.averageBin=0`). `_extract_current_bin` returns None for this input.
      - Assert: result is NOT None. `result.current_lowest_bin == 0`. `result.listing_count == 0`. `result.price_history == []`. `result.sales == []`. `result.live_auction_prices == []`. `result.live_auctions_raw == []`. `result.created_at` is a datetime matching the ISO input.
      - Assert: `result.player` is populated (Player with name/rating derived from defn).

    - Test 2 (sync): `test_get_player_market_data_sync_returns_shell_when_no_current_bin`
      - Same setup but for the sync variant, using a MagicMock sync_client for the definitions HTTP call (matches the pattern in existing sync tests) and a `prices_fetcher` callable returning the zero-bin prices dict.
      - Same assertions as Test 1.

    - Updated Test 3 (async): `test_defn_ok_prices_ok_no_bin_returns_shell` (was `..._returns_none`)
      - Rename. Mock defn with NO `createdAt` field (unchanged from current test) and prices with empty arrays (unchanged).
      - Now asserts the result is NOT None, `current_lowest_bin == 0`, `created_at is None` (defn has no createdAt).

    - Updated Test 4 (sync): `test_sync_defn_ok_prices_ok_no_bin_returns_shell` (was `..._returns_none`)
      - Same rename + shell-shape assertions.

    - Unchanged tests (must still pass):
      - `test_defn_ok_prices_none_raises` — still raises PricesFetchError.
      - `test_both_none_returns_none` — still returns None (full outage).
      - `test_sync_defn_ok_prices_none_raises` — still raises.
      - `test_sync_both_none_returns_none` — still returns None.
  </behavior>
  <action>
Edit `src/futgg_client.py`:

1. In `get_player_market_data` (async, lines 135-180), replace:

```python
player = self._build_player(defn)
current_bin = self._extract_current_bin(prices)
if not current_bin:
    return None

raw_auctions = prices.get("liveAuctions", [])
max_price_range = prices.get("priceRange", {}).get("maxPrice")
created_at_raw = defn.get("createdAt")
created_at = None
if created_at_raw:
    try:
        created_at = datetime.fromisoformat(created_at_raw.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        pass
return PlayerMarketData(
    player=player,
    current_lowest_bin=current_bin,
    listing_count=len(raw_auctions),
    price_history=self._parse_price_history(ea_id, prices),
    sales=self._parse_sales(ea_id, prices),
    live_auction_prices=[a["buyNowPrice"] for a in raw_auctions],
    live_auctions_raw=raw_auctions,
    futgg_url=defn.get("url"),
    max_price_range=max_price_range,
    created_at=created_at,
)
```

with:

```python
player = self._build_player(defn)
current_bin = self._extract_current_bin(prices)
max_price_range = prices.get("priceRange", {}).get("maxPrice")
created_at_raw = defn.get("createdAt")
created_at = None
if created_at_raw:
    try:
        created_at = datetime.fromisoformat(created_at_raw.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        pass

if not current_bin:
    # Card momentarily untradeable (no liveAuctions AND no currentPrice.price).
    # Return a shell so downstream can still populate PlayerRecord.created_at /
    # last_scanned_at — critical for promo_dip_buy's Friday-batch detection.
    # Scanner guards the MarketSnapshot write on current_lowest_bin > 0.
    return PlayerMarketData(
        player=player,
        current_lowest_bin=0,
        listing_count=0,
        price_history=[],
        sales=[],
        live_auction_prices=[],
        live_auctions_raw=[],
        futgg_url=defn.get("url"),
        max_price_range=max_price_range,
        created_at=created_at,
    )

raw_auctions = prices.get("liveAuctions", [])
return PlayerMarketData(
    player=player,
    current_lowest_bin=current_bin,
    listing_count=len(raw_auctions),
    price_history=self._parse_price_history(ea_id, prices),
    sales=self._parse_sales(ea_id, prices),
    live_auction_prices=[a["buyNowPrice"] for a in raw_auctions],
    live_auctions_raw=raw_auctions,
    futgg_url=defn.get("url"),
    max_price_range=max_price_range,
    created_at=created_at,
)
```

Note the key refactor: move the `max_price_range` / `created_at` parsing ABOVE
the `if not current_bin:` branch, because both the shell path and the full path
need those values. The full path's `raw_auctions` initialisation moves to just
above its `PlayerMarketData(...)` call — that's fine; the shell doesn't need
`raw_auctions`.

2. In `get_player_market_data_sync` (sync, lines 182-277), apply the exact same
   refactor: hoist `max_price_range` + `created_at` parsing above the
   `if not current_bin:` branch, replace the branch body with an early-return
   shell constructor, and keep the happy path unchanged.

3. Update the three-way docstring in `get_player_market_data` (lines 137-143):

```python
"""Fetch and assemble full market data for a single player card.

Four result paths:
  - both endpoints failed → return None (full outage; silent skip)
  - defn failed, prices OK → return None (rare defn-only failure)
  - defn OK, prices None → raise PricesFetchError (recoverable; scanner retries)
  - defn OK, prices OK → return PlayerMarketData. current_lowest_bin == 0
    when the card is momentarily untradeable (no liveAuctions AND no currentPrice
    AND no overview.averageBin); caller must guard MarketSnapshot writes on
    current_lowest_bin > 0, but PlayerRecord.created_at / last_scanned_at should
    still be updated from the shell.
"""
```

(Keep the equivalent behaviour — but no separate docstring — on the sync
variant; sync has a different docstring style already, adjust in kind.)

Edit `tests/test_futgg_client.py`:

1. Rename and rewrite `test_defn_ok_prices_ok_no_bin_returns_none` (line 31) →
   `test_defn_ok_prices_ok_no_bin_returns_shell`:

```python
async def test_defn_ok_prices_ok_no_bin_returns_shell(client):
    """Card momentarily untradeable: both endpoints succeed, no current_bin
    — returns a shell PlayerMarketData (current_lowest_bin=0), NOT None."""
    client.get_player_definition = AsyncMock(return_value={
        "eaId": 1,
        "overall": 85,
        "position": 19,
        "commonName": "Test",
        "rarity": {"slug": "gold"},
        "club": {},
        "league": {},
        "nation": {},
    })
    client.get_player_prices = AsyncMock(return_value={
        "liveAuctions": [],
        "completedAuctions": [],
        "history": [],
    })
    result = await client.get_player_market_data(ea_id=1)
    assert result is not None
    assert result.current_lowest_bin == 0
    assert result.listing_count == 0
    assert result.price_history == []
    assert result.sales == []
    assert result.live_auction_prices == []
    assert result.live_auctions_raw == []
    assert result.created_at is None  # defn has no createdAt field
    assert result.player.name == "Test"
    assert result.player.rating == 85
```

2. Add a NEW test below it:

```python
async def test_get_player_market_data_returns_shell_when_no_current_bin(client):
    """Shell path also preserves createdAt from defn — critical for promo_dip_buy."""
    from datetime import datetime, timezone
    client.get_player_definition = AsyncMock(return_value={
        "eaId": 42,
        "overall": 84,
        "position": 19,
        "commonName": "Shell",
        "rarity": {"slug": "gold"},
        "club": {},
        "league": {},
        "nation": {},
        "url": "https://www.fut.gg/players/foo-42/",
        "createdAt": "2026-04-10T12:00:00Z",
    })
    client.get_player_prices = AsyncMock(return_value={
        "liveAuctions": [],
        "completedAuctions": [],
        "history": [],
        "currentPrice": {"price": 0},
        "priceRange": {"maxPrice": 15000000},
    })
    result = await client.get_player_market_data(ea_id=42)
    assert result is not None
    assert result.current_lowest_bin == 0
    assert result.listing_count == 0
    assert result.futgg_url == "https://www.fut.gg/players/foo-42/"
    assert result.max_price_range == 15000000
    assert result.created_at == datetime(2026, 4, 10, 12, 0, 0, tzinfo=timezone.utc)
    assert result.player.resource_id == 42
```

3. Rename and rewrite `test_sync_defn_ok_prices_ok_no_bin_returns_none`
   (line 87) → `test_sync_defn_ok_prices_ok_no_bin_returns_shell`:

```python
def test_sync_defn_ok_prices_ok_no_bin_returns_shell(client):
    """Sync path: both endpoints succeed, no current_bin → shell PlayerMarketData."""
    sync_client = MagicMock()
    defn_resp = MagicMock()
    defn_resp.status_code = 200
    defn_resp.json.return_value = {"data": {
        "eaId": 1,
        "overall": 85,
        "position": 19,
        "commonName": "Test",
        "rarity": {"slug": "gold"},
        "club": {},
        "league": {},
        "nation": {},
    }}
    defn_resp.raise_for_status = MagicMock()
    sync_client.get.return_value = defn_resp

    prices_fetcher = MagicMock(return_value={
        "liveAuctions": [],
        "completedAuctions": [],
        "history": [],
    })

    result = client.get_player_market_data_sync(
        ea_id=1, sync_client=sync_client, prices_fetcher=prices_fetcher,
    )
    assert result is not None
    assert result.current_lowest_bin == 0
    assert result.listing_count == 0
    assert result.player.name == "Test"
```

4. Add a NEW sync test below it:

```python
def test_get_player_market_data_sync_returns_shell_when_no_current_bin(client):
    """Sync shell path: preserves createdAt from defn."""
    from datetime import datetime, timezone
    sync_client = MagicMock()
    defn_resp = MagicMock()
    defn_resp.status_code = 200
    defn_resp.json.return_value = {"data": {
        "eaId": 42,
        "overall": 84,
        "position": 19,
        "commonName": "Shell",
        "rarity": {"slug": "gold"},
        "club": {},
        "league": {},
        "nation": {},
        "url": "https://www.fut.gg/players/foo-42/",
        "createdAt": "2026-04-10T12:00:00Z",
    }}
    defn_resp.raise_for_status = MagicMock()
    sync_client.get.return_value = defn_resp

    prices_fetcher = MagicMock(return_value={
        "liveAuctions": [],
        "completedAuctions": [],
        "history": [],
        "currentPrice": {"price": 0},
        "priceRange": {"maxPrice": 15000000},
    })

    result = client.get_player_market_data_sync(
        ea_id=42, sync_client=sync_client, prices_fetcher=prices_fetcher,
    )
    assert result is not None
    assert result.current_lowest_bin == 0
    assert result.futgg_url == "https://www.fut.gg/players/foo-42/"
    assert result.max_price_range == 15000000
    assert result.created_at == datetime(2026, 4, 10, 12, 0, 0, tzinfo=timezone.utc)
```

Keep all four existing tests (`test_defn_ok_prices_none_raises`,
`test_both_none_returns_none`, `test_sync_defn_ok_prices_none_raises`,
`test_sync_both_none_returns_none`) unchanged. After this task, the file should
have 8 tests total (was 6).

Commit pattern (follow quick-260418-c65's RED/GREEN split):
- RED commit: `test(czo): add failing tests for market_data shell on no current_bin`
  — write the 4 new/renamed tests first; confirm they fail against current code.
- GREEN commit: `fix(czo): preserve market_data shell when current_bin is None`
  — implement the futgg_client.py changes; tests now pass.
  </action>
  <verify>
    <automated>python -m pytest tests/test_futgg_client.py -x -v</automated>
  </verify>
  <done>
    - `src/futgg_client.py` — both async and sync paths return a PlayerMarketData
      shell (current_lowest_bin=0, empty arrays, preserved player/futgg_url/max_price_range/created_at)
      when `_extract_current_bin` returns None/0.
    - PricesFetchError, both-None, defn-only-None paths unchanged.
    - `tests/test_futgg_client.py` — 8 tests total, all passing.
    - Two RED/GREEN commits in history.
  </done>
</task>

<task type="auto" tdd="true">
  <name>Task 2: Scanner — guard MarketSnapshot write on current_lowest_bin > 0</name>
  <files>src/server/scanner.py, tests/test_scanner.py</files>
  <behavior>
    - Test 1 (new): `test_no_snapshot_when_current_lowest_bin_zero`
      - Seed a PlayerRecord (ea_id=200) with `created_at=None`, `last_scanned_at=None`.
      - Stub `mock_client.get_player_market_data_sync` to return a shell
        PlayerMarketData — real pydantic instance with `current_lowest_bin=0`,
        `listing_count=0`, empty arrays, `created_at=datetime(2026, 4, 10, ...)`.
      - Run `svc.scan_player(200)`.
      - Assert: zero MarketSnapshot rows for ea_id=200.
      - Assert: PlayerRecord.last_scanned_at is NOT None.
      - Assert: PlayerRecord.created_at is NOT None (the shell's created_at was
        copied into the record).

    - Existing `test_snapshot_created_on_scan` (line 223) must still pass —
      current_lowest_bin > 0 path unchanged.

    - Existing `test_no_snapshot_on_none_market_data` (line 248) must still pass
      — market_data=None path (full outage) unchanged.

    - Test 4: `pytest tests/test_futgg_client.py tests/test_scanner.py` passes.
      Pre-existing baseline failures (deferred-items.md) are untouched — this
      task must not introduce any NEW failures in test_scanner.py or other
      files beyond the one new test added.
  </behavior>
  <action>
Edit `src/server/scanner.py`:

1. At the MarketSnapshot write block (current lines 378-386):

```python
# Persist raw market snapshot
if market_data is not None:
    snapshot = MarketSnapshot(
        ea_id=ea_id,
        captured_at=now,
        current_lowest_bin=market_data.current_lowest_bin,
        listing_count=market_data.listing_count,
        )
    session.add(snapshot)
```

change the guard to:

```python
# Persist raw market snapshot. Skip when current_lowest_bin == 0 (card
# momentarily untradeable) — we get a shell PlayerMarketData for those,
# and inserting a zero-BIN row would mislead any downstream analysis that
# treats a MarketSnapshot as evidence of an observed live BIN. The
# PlayerRecord update below still runs so created_at / last_scanned_at get
# populated — that's the whole point of the shell.
if market_data is not None and market_data.current_lowest_bin > 0:
    snapshot = MarketSnapshot(
        ea_id=ea_id,
        captured_at=now,
        current_lowest_bin=market_data.current_lowest_bin,
        listing_count=market_data.listing_count,
    )
    session.add(snapshot)
```

No other changes to scanner.py — the PlayerRecord update block immediately
below (lines 388-400) already reads `market_data.created_at` / `listing_count`
/ `futgg_url` correctly; those fields are all present on the shell (listing_count=0,
which is fine — PlayerRecord.listing_count is just a cached int).

The v3 scoring block (lines 320-329) already guards on
`market_data.current_lowest_bin > 0`, so the shell correctly falls through to
the "not viable" PlayerScore path. No change needed there.

Edit `tests/test_scanner.py`:

1. Add a new test right after `test_no_snapshot_on_none_market_data` (currently
   ending at ~line 265). The test fixture infrastructure (db, circuit_breaker,
   scanner, _seed_player_record) is already in place — match the existing
   style:

```python
async def test_no_snapshot_when_current_lowest_bin_zero(scanner):
    """Shell PlayerMarketData (current_lowest_bin=0) → no MarketSnapshot row
    BUT PlayerRecord.last_scanned_at and created_at are still populated.

    This is the fix for the last silent-data-loss hole: cards that are
    momentarily untradeable must still have created_at set so promo_dip_buy
    can find them by Friday-batch detection.
    """
    from datetime import datetime, timezone
    from src.models import PlayerMarketData, Player

    svc, session_factory, mock_client = scanner

    # Build a shell PlayerMarketData — real pydantic instance, NOT a mock,
    # to match what futgg_client now returns for the no-current-bin case.
    shell = PlayerMarketData(
        player=Player(
            resource_id=200, name="Shell Player", rating=85, position="ST",
            nation="Brazil", league="LaLiga", club="Real Madrid", card_type="gold",
        ),
        current_lowest_bin=0,
        listing_count=0,
        price_history=[],
        sales=[],
        live_auction_prices=[],
        live_auctions_raw=[],
        futgg_url="https://www.fut.gg/players/shell-200/",
        max_price_range=15_000_000,
        created_at=datetime(2026, 4, 10, 12, 0, 0, tzinfo=timezone.utc),
    )
    mock_client.get_player_market_data_sync = MagicMock(return_value=shell)

    async with session_factory() as session:
        session.add(PlayerRecord(
            ea_id=200, name="Shell Player", rating=85, position="ST",
            nation="Brazil", league="LaLiga", club="Real Madrid", card_type="gold",
        ))
        await session.commit()

    await svc.scan_player(200)

    async with session_factory() as session:
        # No MarketSnapshot row — we skip the write for zero-BIN shells.
        snaps = (await session.execute(
            select(MarketSnapshot).where(MarketSnapshot.ea_id == 200)
        )).scalars().all()
        assert len(snaps) == 0, \
            f"Expected no MarketSnapshot rows for zero-BIN shell, got {len(snaps)}"

        # PlayerRecord MUST still get last_scanned_at and created_at set —
        # that's the whole point of the shell over a silent None.
        rec = await session.get(PlayerRecord, 200)
        assert rec is not None
        assert rec.last_scanned_at is not None, \
            "Expected last_scanned_at populated even for shell market_data"
        assert rec.created_at is not None, \
            "Expected created_at populated from shell.created_at"
        assert rec.created_at == datetime(2026, 4, 10, 12, 0, 0)
```

Note the final assertion: PlayerRecord.created_at is stored as a naive
datetime (SQLite DateTime) whereas the shell's datetime is tz-aware. If the
equality fails, loosen to `rec.created_at.replace(tzinfo=timezone.utc) == shell.created_at`
— whichever matches the existing PlayerRecord.created_at persistence pattern in
the codebase. Check `src/server/models_db.py` PlayerRecord.created_at column
type (DateTime without timezone=True per the models file, so stored as naive).
The test assertion should therefore use a naive datetime comparison:
`assert rec.created_at == datetime(2026, 4, 10, 12, 0, 0)` (no tzinfo on the RHS).

Commit pattern:
- RED commit: `test(czo): add failing scanner test for zero-BIN shell snapshot skip`
- GREEN commit: `fix(czo): skip MarketSnapshot write when current_lowest_bin is 0`
  </action>
  <verify>
    <automated>python -m pytest tests/test_scanner.py::test_no_snapshot_when_current_lowest_bin_zero tests/test_scanner.py::test_snapshot_created_on_scan tests/test_scanner.py::test_no_snapshot_on_none_market_data -x -v</automated>
  </verify>
  <done>
    - `src/server/scanner.py` — MarketSnapshot write guarded by
      `market_data.current_lowest_bin > 0`.
    - PlayerRecord update block untouched (still updates last_scanned_at,
      created_at, listing_count, etc. from the shell).
    - New test `test_no_snapshot_when_current_lowest_bin_zero` passes.
    - Existing `test_snapshot_created_on_scan` and `test_no_snapshot_on_none_market_data` still pass.
    - Two RED/GREEN commits in history.
  </done>
</task>

<task type="auto" tdd="false">
  <name>Task 3: Remediation script — tools/rescan_stuck_players.py</name>
  <files>tools/__init__.py, tools/rescan_stuck_players.py</files>
  <action>
Create the tools package and the one-off remediation script. No tests — it's
an ad-hoc script that hits the live DB and is NOT exercised by CI. The user
runs it manually after merge.

1. Create `tools/__init__.py` as an empty file (so `python -m tools.rescan_stuck_players`
   works as a module invocation). Use the Write tool.

2. Create `tools/rescan_stuck_players.py`:

```python
"""One-off remediation: unstick the ~118 pre-czo cards with created_at=NULL.

These active PlayerRecords were scanned before quick-260418-czo fixed the
"current_bin is None → return None → created_at never set" silent-data-loss
hole. They have last_scanned_at set but created_at=NULL, which makes them
invisible to promo_dip_buy's Friday-batch detection.

The fix: set their next_scan_at = utcnow() so the next dispatch cycle picks
them up. With the czo fix in place, the rescan will either populate a real
current_bin (and a real MarketSnapshot) or a shell (and still populate
created_at). Either way they're unstuck.

NOT scheduled; NOT a migration. Run once against the live DB after the czo
fix is deployed.

Usage:
    python -m tools.rescan_stuck_players         # prompts for confirmation
    python -m tools.rescan_stuck_players --yes   # skip prompt
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import datetime

from sqlalchemy import select, update, func
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from src.config import DATABASE_URL
from src.server.models_db import PlayerRecord

logger = logging.getLogger(__name__)


async def count_stuck(session_factory: async_sessionmaker[AsyncSession]) -> int:
    """Count active players with created_at IS NULL AND last_scanned_at IS NOT NULL."""
    async with session_factory() as session:
        result = await session.execute(
            select(func.count()).select_from(PlayerRecord).where(
                PlayerRecord.is_active == True,  # noqa: E712
                PlayerRecord.created_at.is_(None),
                PlayerRecord.last_scanned_at.is_not(None),
            )
        )
        return result.scalar() or 0


async def requeue_stuck(session_factory: async_sessionmaker[AsyncSession]) -> int:
    """Set next_scan_at = utcnow() for all stuck active players. Returns row count."""
    now = datetime.utcnow()
    async with session_factory() as session:
        result = await session.execute(
            update(PlayerRecord)
            .where(
                PlayerRecord.is_active == True,  # noqa: E712
                PlayerRecord.created_at.is_(None),
                PlayerRecord.last_scanned_at.is_not(None),
            )
            .values(next_scan_at=now)
        )
        await session.commit()
        return result.rowcount or 0


async def main(yes: bool) -> None:
    engine = create_async_engine(DATABASE_URL)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        count = await count_stuck(session_factory)
        print(f"Found {count} stuck active players (created_at=NULL, last_scanned_at set).")

        if count == 0:
            print("Nothing to do.")
            return

        if not yes:
            reply = input(f"Requeue all {count} for immediate rescan? [y/N]: ").strip().lower()
            if reply not in ("y", "yes"):
                print("Aborted.")
                return

        updated = await requeue_stuck(session_factory)
        print(f"Set next_scan_at=utcnow() for {updated} players. They'll be picked up by the next dispatch cycle.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Requeue stuck pre-czo PlayerRecords for immediate rescan.")
    parser.add_argument("--yes", action="store_true", help="Skip the confirmation prompt.")
    args = parser.parse_args()
    asyncio.run(main(args.yes))
```

DO NOT run the script. It hits the live DB — the user runs it manually after
merge.

Sanity checks the executor should run (these are cheap and don't touch the DB):
- `python -c "import ast; ast.parse(open('tools/rescan_stuck_players.py').read())"` — syntax check.
- `python -c "from tools.rescan_stuck_players import main, count_stuck, requeue_stuck"` — import check (verifies `src.config.DATABASE_URL` and `src.server.models_db.PlayerRecord` imports resolve without hitting the DB, because SQLAlchemy engine creation is deferred until `main()` is called).

Commit pattern:
- Single commit: `feat(czo): add tools/rescan_stuck_players.py remediation script`
- (No RED/GREEN — this is a one-off script with no tests.)
  </action>
  <verify>
    <automated>python -c "from tools.rescan_stuck_players import main, count_stuck, requeue_stuck; print('imports ok')"</automated>
  </verify>
  <done>
    - `tools/__init__.py` exists (empty).
    - `tools/rescan_stuck_players.py` exists with `count_stuck` + `requeue_stuck` +
      `main(yes)` + `if __name__ == "__main__"` CLI using argparse + `--yes` flag.
    - Script reads DATABASE_URL from `src.config`, uses SQLAlchemy async engine,
      filters `is_active AND created_at IS NULL AND last_scanned_at IS NOT NULL`,
      sets `next_scan_at=utcnow()`, prompts before committing unless `--yes`.
    - Import check passes.
    - Script is NOT executed.
    - One commit in history.
  </done>
</task>

</tasks>

<verification>
Post-task smoke:

```bash
# All targeted tests pass
python -m pytest tests/test_futgg_client.py tests/test_scanner.py -x -v

# Import check on the remediation script (does not hit DB)
python -c "from tools.rescan_stuck_players import main, count_stuck, requeue_stuck; print('imports ok')"
```

Manual sanity greps (optional but recommended):

```bash
# futgg_client must have NO early `return None` between `if not current_bin`
# and the full PlayerMarketData path.
grep -n "return None" src/futgg_client.py
# Expected: HTTP error handlers (2), full-outage paths (2), defn-only-failure
# paths (2). NO returns immediately under `if not current_bin:` — those are
# now shell constructors.

# Scanner guard present
grep -n "current_lowest_bin > 0" src/server/scanner.py
# Expected: line ~321 (v3 scoring) AND the new MarketSnapshot guard.

# Script exists
ls -la tools/rescan_stuck_players.py tools/__init__.py

# Pre-existing baseline failures unchanged (do NOT try to fix them)
python -m pytest tests/test_futgg_client.py tests/test_scanner.py tests/test_playwright_client.py tests/test_scanner_discovery.py -v
# Expected: all czo + c65 tests green; the 14 pre-existing failures documented
# in quick-260418-c65/deferred-items.md are NOT in this list (different files).
```
</verification>

<success_criteria>
- Both futgg_client paths return a shell PlayerMarketData (not None) when
  _extract_current_bin returns None/0.
- Scanner still updates PlayerRecord.last_scanned_at AND created_at for the
  shell case, but does NOT write a MarketSnapshot row.
- PricesFetchError / both-None / defn-None paths all unchanged.
- `tools/rescan_stuck_players.py` exists, imports cleanly, and is NOT executed
  by the agent.
- `tests/test_futgg_client.py` has 8 tests, all passing.
- `tests/test_scanner.py` has +1 test, all existing tests still passing.
- No changes to `src/algo/**` or `tests/algo/**`.
- No `_extract_current_bin` or `overview.averageBin` changes.
- Pre-existing baseline failures from quick-260418-c65/deferred-items.md
  remain out of scope and unchanged.
- 5 commits total: 2 RED/GREEN pairs (Tasks 1 + 2) + 1 feat commit (Task 3).
</success_criteria>

<output>
After completion, create `.planning/quick/260418-czo-close-current-bin-silent-none-hole-remed/260418-czo-SUMMARY.md` documenting:
- Files modified (2 src + 2 test) + files created (1 script + 1 __init__.py)
- Key decisions:
  - Shell over None: cards with no current_bin still yield a PlayerMarketData
    so downstream can populate PlayerRecord.created_at / last_scanned_at —
    the previous silent None stranded 118 cards.
  - Snapshot gate at current_lowest_bin > 0: shell rows don't pollute
    market_snapshots with zero-BIN rows that would mislead downstream analysis.
  - Remediation script is NOT scheduled, NOT a migration — one-off `python -m
    tools.rescan_stuck_players --yes` to unstick the pre-czo cards.
- Test count before/after (6 → 8 in test_futgg_client, +1 in test_scanner)
- Any existing tests that had to be updated (expected: two renames in
  test_futgg_client — `..._returns_none` → `..._returns_shell` — because they
  encoded the buggy silent-None behaviour)
- Commit hashes for the 5 commits
- Confirmation the remediation script was NOT executed by the agent; note the
  stuck-player count is expected to be ~118 at time of user-run.
</output>
