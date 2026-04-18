---
phase: quick-260418-ddp
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - src/config.py
  - src/futgg_client.py
  - src/server/scanner_discovery.py
  - tests/test_futgg_client.py
  - tests/mock_client.py
autonomous: true
requirements:
  - QUICK-260418-DDP-01  # Remove SCANNER_MAX_PRICE upper-bound cap from discovery so high-priced release-day promos enter DB immediately

must_haves:
  truths:
    - "Discovery no longer caps player search at 500,000 coins — TOTS/TOTY/Icon cards priced 700k-2M+ enter PlayerRecord on release day, not 24h-8d later."
    - "FutGGClient.discover_players, when called with max_price=0, builds URLs WITHOUT the price__lte query parameter (and without silently auto-filling max_price from budget)."
    - "FutGGClient.discover_players with an explicit max_price > 0 still sends price__lte=<max_price> (pre-existing behaviour preserved)."
    - "scanner_discovery.run_bootstrap and scanner_discovery.run_discovery both call discover_players WITHOUT enforcing an upper-bound price cap."
    - "The MIN bound (SCANNER_MIN_PRICE = 11,000) continues to be enforced unchanged on both call sites."
    - "The 'mark cold' branch in run_discovery (scanner_discovery.py:244-254) continues to function — larger discovery result sets legitimately shrink the mark-cold set; semantics unchanged."
  artifacts:
    - path: "src/config.py"
      provides: "SCANNER_MAX_PRICE = 0 sentinel; SCANNER_MIN_PRICE = 11_000 unchanged"
      contains: "SCANNER_MIN_PRICE = 11_000"
    - path: "src/futgg_client.py"
      provides: "discover_players with no auto-fill of max_price from budget; max_price <= 0 means 'no upper bound'"
      exports: ["FutGGClient"]
    - path: "src/server/scanner_discovery.py"
      provides: "run_bootstrap and run_discovery passing max_price=0 (no cap)"
      exports: ["run_bootstrap", "run_discovery"]
    - path: "tests/test_futgg_client.py"
      provides: "New tests proving no price__lte param is emitted when max_price=0; explicit max_price>0 case still works; budget no longer auto-fills"
    - path: "tests/mock_client.py"
      provides: "MockClient.discover_players honors max_price=0 as 'no upper bound' to mirror real-client semantics"
  key_links:
    - from: "src/server/scanner_discovery.py (run_bootstrap, run_discovery)"
      to: "src/futgg_client.py:discover_players"
      via: "keyword args min_price=SCANNER_MIN_PRICE, max_price=SCANNER_MAX_PRICE"
      pattern: "discover_players\\([^)]*max_price="
    - from: "src/futgg_client.py:discover_players"
      to: "fut.gg /api/fut/players/v2/26/ URL construction"
      via: "conditional append of &price__lte query param"
      pattern: "price__lte"
---

<objective>
Remove the `SCANNER_MAX_PRICE=500_000` upper-bound cap from scanner discovery so
high-priced new-release promo cards (700k–2M+ TOTS/TOTY/Icons) enter the DB
on release day instead of lagging 24h–8 days behind.

Purpose: Current `price__lte=500000` on the fut.gg player list endpoint silently
hides release-day promos from bootstrap/periodic discovery. A card only enters
`PlayerRecord` once its price naturally drifts below 500k — by which time the
early-cycle OP sell window is closed. Removing the cap fixes a real data-loss
bug on the most profitable card subset.

Output:
- `SCANNER_MAX_PRICE` becomes a `0` sentinel (kept in config for semantic clarity).
- `FutGGClient.discover_players` respects `max_price=0` as "no upper bound" and
  drops the accidental `budget * 0.10` auto-fill that would otherwise override
  the sentinel.
- Both `run_bootstrap` and `run_discovery` call `discover_players` without an
  effective upper-bound cap.
- New unit tests prove the URL construction: `max_price=0` → no `price__lte`.
- `MockClient.discover_players` mirrors real-client semantics for test parity.

Scope is explicit: do NOT tune `SCAN_INTERVAL_SECONDS`, `SCAN_CONCURRENCY`, or
thread-pool sizing. Do NOT touch `src/algo/**` or `tests/algo/**`. Do NOT touch
the "mark cold" branch semantics.
</objective>

<execution_context>
@$HOME/.claude/get-shit-done/workflows/execute-plan.md
@$HOME/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@CLAUDE.md
@.planning/STATE.md

@src/config.py
@src/futgg_client.py
@src/server/scanner_discovery.py
@src/protocols.py
@tests/test_futgg_client.py
@tests/mock_client.py
@tests/test_scanner_discovery.py
@.planning/quick/260418-c65-fix-scanner-data-loss-bugs-playwright-ch/deferred-items.md

<interfaces>
<!-- Extracted from current codebase. Executor uses these directly, no exploration needed. -->

From src/futgg_client.py (current signature — DO NOT change the signature, only the body):
```python
async def discover_players(
    self, budget: int, max_pages: int = 999,
    min_price: int = 0, max_price: int = 0,
) -> list[dict]:
    """Discover all tradeable player cards within a price range."""
    if max_price <= 0:
        max_price = int(budget * 0.10)   # ← THIS IS THE BUG: auto-fill overrides the "no cap" sentinel
    if min_price <= 0:
        min_price = 1000

    all_candidates = []
    seen_ids: set[int] = set()

    for page_num in range(1, max_pages + 1):
        logger.info(f"Fetching player list page {page_num}...")
        url = f"/api/fut/players/v2/26/?page={page_num}"
        if min_price > 0:
            url += f"&price__gte={min_price}"
        if max_price > 0:
            url += f"&price__lte={max_price}"
        ...
```

From src/protocols.py (Protocol signature — leave shape unchanged; semantics of
max_price=0 must mean "no cap" for any implementation):
```python
async def discover_players(
    self, budget: int, min_price: int = 0, max_price: int = 0,
) -> list[dict]: ...
```

From tests/mock_client.py (MockClient implementation of the Protocol — must be
updated so max_price=0 means "no upper bound" there too, otherwise unit tests
using MockClient will see zero results when new callers pass max_price=0):
```python
async def discover_players(
    self, budget: int, min_price: int = 0, max_price: int = 0,
) -> list[dict]:
    return [
        {"ea_id": p.player.resource_id, "price": p.current_lowest_bin}
        for p in self._players.values()
        if min_price <= p.current_lowest_bin <= max_price
    ]
```

From src/server/scanner_discovery.py (both call sites — same pattern):
```python
players = await client.discover_players(
    budget=SCANNER_MAX_PRICE,
    min_price=SCANNER_MIN_PRICE,
    max_price=SCANNER_MAX_PRICE,
)
```
The `budget` arg is ONLY used inside discover_players as the auto-fill source
for `max_price` when `max_price <= 0`. Once we remove that auto-fill,
`budget` becomes effectively unused for filtering. The arg is kept (signature
compat), but pass `budget=0` at the call sites (any value works — just not
SCANNER_MAX_PRICE, which is being semantically repurposed to "0 = no cap").
</interfaces>

<decisions>
D-01: Keep SCANNER_MAX_PRICE in config as an explicit `0` sentinel rather than
      deleting it, per user note "If keeping as sentinel (0), semantically
      clearer than deleting." Minimizes churn — import in
      scanner_discovery.py does not need to be ripped out.
D-02: Remove the `if max_price <= 0: max_price = int(budget * 0.10)` auto-fill
      inside discover_players. It is the exact mechanism that would cause the
      sentinel to silently re-introduce a cap (500000 → 50000 cap! worse). The
      `if min_price <= 0: min_price = 1000` fallback stays unchanged — we
      explicitly pass SCANNER_MIN_PRICE=11000 so the branch never fires, but
      CLI callers that still pass raw budgets continue to work.
D-03: Update MockClient.discover_players so max_price=0 means "no upper
      bound" — mirrors the real client. Otherwise any future mock-based test
      that passes max_price=0 would silently return [].
D-04: Pre-existing baseline failures (14 listed in the deferred-items.md
      referenced in context) are out of scope. The success criteria uses a
      targeted pytest selector, not `pytest -x`.
D-05: No live-discovery smoke checkpoint — user explicitly accepted the
      throughput impact and said tuning is separate scope. Unit tests prove
      URL construction; live validation is deferred to the user's own scanner
      run post-merge.
</decisions>

</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: Remove max_price auto-fill in discover_players and add URL-building tests</name>
  <files>src/futgg_client.py, tests/test_futgg_client.py</files>
  <behavior>
    RED phase — extend tests/test_futgg_client.py with three new tests that
    exercise discover_players URL construction. All tests mock FutGGClient._get
    and assert on the `path` argument it receives.

    Test 1 — `test_discover_players_no_max_price_omits_price_lte`:
      - Create a FutGGClient, patch `client._get` as an AsyncMock that returns
        `{"data": [], "next": None}` on the first call.
      - Call `await client.discover_players(budget=0, min_price=11_000, max_price=0)`.
      - Assert `client._get` was called with a path that:
        - contains `price__gte=11000`
        - does NOT contain `price__lte` (i.e. `"price__lte" not in path`)
        - contains `page=1`

    Test 2 — `test_discover_players_with_max_price_includes_price_lte`:
      - Patch `client._get` similarly.
      - Call `await client.discover_players(budget=0, min_price=11_000, max_price=200_000)`.
      - Assert the path contains both `price__gte=11000` AND `price__lte=200000`.

    Test 3 — `test_discover_players_no_budget_autofill_when_max_price_zero`:
      - Same pattern; call with `budget=500_000, min_price=11_000, max_price=0`.
      - Assert the path STILL does not contain `price__lte` — proves the old
        `budget * 0.10` auto-fill is gone. (If it were present, path would
        contain `price__lte=50000`.)

    GREEN phase — edit src/futgg_client.py discover_players:
      - DELETE the 2 lines: `if max_price <= 0: max_price = int(budget * 0.10)`.
      - LEAVE the `if min_price <= 0: min_price = 1000` fallback untouched
        (CLI callers with raw budgets still need a sensible floor; scanner
        callers override with SCANNER_MIN_PRICE=11000 so this branch is
        harmless for them).
      - LEAVE the rest of the URL-building loop unchanged. The existing
        guards `if min_price > 0` and `if max_price > 0` already handle the
        sentinel correctly once the auto-fill is removed.
      - Optionally update the docstring to note that `max_price=0` means
        "no upper bound."

    Verify tests go RED on baseline (without src change) to prove they exercise
    the right behaviour, then GREEN after the src change.
  </behavior>
  <action>
    1. Open tests/test_futgg_client.py and APPEND the three new async tests
       listed above. Use `AsyncMock` from `unittest.mock` (already imported).
       Follow the existing style in that file — top-level `async def` test
       functions (pytest-asyncio is configured; note the file has no
       `@pytest.mark.asyncio` on existing tests, so don't add one).

       Test skeleton:
       ```python
       async def test_discover_players_no_max_price_omits_price_lte(client):
           client._get = AsyncMock(return_value={"data": [], "next": None})
           await client.discover_players(budget=0, min_price=11_000, max_price=0)
           path = client._get.call_args.args[0]
           assert "price__gte=11000" in path
           assert "price__lte" not in path
           assert "page=1" in path
       ```

    2. Run ONLY the new tests — they must FAIL on the unchanged source:
       ```
       python -m pytest tests/test_futgg_client.py::test_discover_players_no_max_price_omits_price_lte -x
       python -m pytest tests/test_futgg_client.py::test_discover_players_no_budget_autofill_when_max_price_zero -x
       ```
       Expect: Test 1 fails because the auto-fill turns `budget=0, max_price=0`
       into `max_price = 0` (accidentally right in this case — if it fails on
       a different assertion, investigate before moving on). Test 3 MUST fail
       because `budget=500_000` auto-fills `max_price=50_000` → URL includes
       `price__lte=50000`. If Test 3 passes on baseline, the assertion is
       wrong — fix it.

    3. Edit src/futgg_client.py discover_players:
       ```python
       # BEFORE:
       if max_price <= 0:
           max_price = int(budget * 0.10)
       if min_price <= 0:
           min_price = 1000
       # AFTER:
       if min_price <= 0:
           min_price = 1000
       # max_price <= 0 is a valid sentinel meaning "no upper bound" —
       # do NOT auto-fill from budget (would silently re-introduce a cap).
       ```

    4. Re-run the new tests — all three must pass:
       ```
       python -m pytest tests/test_futgg_client.py -x
       ```
       All pre-existing tests in this file (the 8 PricesFetchError / shell
       tests) must also still pass.

    5. Commit:
       ```
       git add src/futgg_client.py tests/test_futgg_client.py
       git commit -m "fix(scanner): drop budget*0.10 auto-fill in discover_players

       The max_price<=0 sentinel is meant to mean 'no upper bound', but the
       auto-fill silently replaces it with budget * 0.10, which for the
       scanner path resulted in a hidden price__lte=500000 cap — hiding
       release-day TOTS/TOTY/Icon promo cards from discovery.

       Add URL-construction tests proving:
       - max_price=0 emits URL without price__lte
       - max_price>0 still emits price__lte
       - budget no longer auto-fills max_price when max_price=0"
       ```
  </action>
  <verify>
    <automated>python -m pytest tests/test_futgg_client.py -x -q</automated>
  </verify>
  <done>
    - src/futgg_client.py no longer contains `max_price = int(budget * 0.10)`.
    - tests/test_futgg_client.py has three new tests covering URL construction.
    - All 11 tests in tests/test_futgg_client.py pass.
    - Commit is on HEAD.
  </done>
</task>

<task type="auto">
  <name>Task 2: Repurpose SCANNER_MAX_PRICE to 0 sentinel and update scanner_discovery + MockClient</name>
  <files>src/config.py, src/server/scanner_discovery.py, tests/mock_client.py</files>
  <action>
    1. Edit src/config.py line 32:
       ```python
       # BEFORE:
       SCANNER_MAX_PRICE = 500_000
       # AFTER:
       # Sentinel: 0 = no upper-bound price cap. Keeps high-priced release-day
       # TOTS/TOTY/Icon promos in the scanner's discovery set.
       # (Interpreted by FutGGClient.discover_players and MockClient.)
       SCANNER_MAX_PRICE = 0
       ```
       LEAVE SCANNER_MIN_PRICE = 11_000 unchanged on line 31.

    2. Edit src/server/scanner_discovery.py. Both call sites (lines ~47-51 for
       run_bootstrap and ~178-182 for run_discovery) currently pass:
       ```python
       budget=SCANNER_MAX_PRICE,
       min_price=SCANNER_MIN_PRICE,
       max_price=SCANNER_MAX_PRICE,
       ```
       With SCANNER_MAX_PRICE now == 0, this is already semantically correct
       AND the budget auto-fill is gone, so NO code change is strictly required
       at the call sites. HOWEVER: update both to be explicit and readable:
       ```python
       # run_bootstrap — line ~47
       players = await client.discover_players(
           budget=0,                          # not used for filtering (auto-fill removed)
           min_price=SCANNER_MIN_PRICE,
           max_price=SCANNER_MAX_PRICE,       # 0 = no upper bound
       )
       # run_discovery — line ~178
       players = await client.discover_players(
           budget=0,
           min_price=SCANNER_MIN_PRICE,
           max_price=SCANNER_MAX_PRICE,
       )
       ```
       Keep the SCANNER_MAX_PRICE import — it documents the "no cap" intent
       at the call site.

    3. Edit tests/mock_client.py lines 104-111. The current filter
       `if min_price <= p.current_lowest_bin <= max_price` would return [] when
       max_price=0. Update to mirror real client semantics:
       ```python
       async def discover_players(
           self, budget: int, min_price: int = 0, max_price: int = 0,
       ) -> list[dict]:
           return [
               {"ea_id": p.player.resource_id, "price": p.current_lowest_bin}
               for p in self._players.values()
               if p.current_lowest_bin >= min_price
               and (max_price <= 0 or p.current_lowest_bin <= max_price)
           ]
       ```

    4. Audit: grep the whole src/ and tests/ tree for any other hardcoded
       500000 / 500_000 / SCANNER_MAX_PRICE references — there should be none
       remaining that semantically mean "scanner price cap." (Verified during
       planning: only the 5 references in src/config.py and
       src/server/scanner_discovery.py exist; no test references the constant
       directly.) If the audit surfaces anything new (e.g. leftover
       comparisons in health_check or portfolio paths), leave them
       un-touched only if they are NOT an upper-bound price filter on
       discovery — otherwise update consistently. Command:
       ```
       grep -rn "SCANNER_MAX_PRICE\|\\b500_000\\b\\|\\b500000\\b" src/ tests/
       ```

    5. Run the full non-algo, non-integration test suite. Baseline already has
       14 pre-existing failures (see deferred-items.md referenced in context).
       After this task, the same 14 must be the ONLY failures (no new
       regressions):
       ```
       python -m pytest --ignore=tests/algo --ignore=tests/integration -q
       ```
       Pay particular attention to:
       - tests/test_futgg_client.py — all 11 pass (from Task 1).
       - tests/test_scanner_discovery.py — all 3 pass (they mock
         discover_players with AsyncMock and don't assert args, so unaffected).
       - tests/test_scanner.py::test_run_bootstrap_inserts_player_records —
         must still pass (the mock returns preset players; call-arg
         assertions are not made).
       - Any test using MockClient with max_price=0 or max_price > 0 —
         both must work (the new guard `max_price <= 0 or ...` handles both).

    6. Commit:
       ```
       git add src/config.py src/server/scanner_discovery.py tests/mock_client.py
       git commit -m "fix(scanner): remove 500k price cap from discovery (SCANNER_MAX_PRICE=0)

       High-priced release-day promo cards (700k-2M+ TOTS/TOTY/Icons) were
       invisible to bootstrap and periodic discovery until their price
       drifted below 500k — a 24h-8d lag that closed the early OP sell
       window on the most profitable subset.

       SCANNER_MAX_PRICE is now the 0 sentinel for 'no upper bound'.
       The budget*0.10 auto-fill in discover_players was dropped in the
       previous commit, so this change is now safe.

       MockClient.discover_players updated to mirror the real-client
       semantics (max_price<=0 means no upper bound).

       Call sites made more explicit (budget=0) since budget is no longer
       used as a filter input — only the kwarg min_price/max_price matter.

       Out of scope (explicit, per user): no scan interval or concurrency
       tuning. Higher throughput load will be tuned separately if needed."
       ```
  </action>
  <verify>
    <automated>python -m pytest tests/test_futgg_client.py tests/test_scanner_discovery.py tests/test_scanner.py::test_run_bootstrap_inserts_player_records -x -q</automated>
  </verify>
  <done>
    - src/config.py: SCANNER_MAX_PRICE = 0 with comment explaining sentinel.
    - src/server/scanner_discovery.py: both call sites pass budget=0,
      max_price=SCANNER_MAX_PRICE, and still reference SCANNER_MIN_PRICE.
    - tests/mock_client.py: MockClient.discover_players honors max_price=0
      as "no upper bound."
    - Targeted pytest selector passes: test_futgg_client.py (all 11),
      test_scanner_discovery.py (all 3), and
      test_scanner.py::test_run_bootstrap_inserts_player_records.
    - Full suite (excl. tests/algo, tests/integration) shows no NEW
      failures vs the 14 baseline failures listed in deferred-items.md.
    - Commit is on HEAD.
  </done>
</task>

</tasks>

<verification>
Phase-level checks (run after all auto tasks land):

1. Full non-algo, non-integration suite:
   ```
   python -m pytest --ignore=tests/algo --ignore=tests/integration -q
   ```
   Expected: NEW failures = 0. Baseline failures (14, documented in
   .planning/quick/260418-c65-fix-scanner-data-loss-bugs-playwright-ch/deferred-items.md)
   may still fail — they are out of scope per user constraint.

2. Targeted change-surface tests:
   ```
   python -m pytest tests/test_futgg_client.py tests/test_scanner_discovery.py -v
   ```
   Expected: 14 passed (11 in test_futgg_client + 3 in test_scanner_discovery),
   0 failed.

3. Static audit:
   ```
   grep -rn "SCANNER_MAX_PRICE" src/ tests/
   ```
   Expected output (after changes):
     src/config.py: SCANNER_MAX_PRICE = 0
     src/server/scanner_discovery.py (import + 2 call sites)
   No other references.

4. Grep for lingering 500_000/500000 "price cap" constants outside scope:
   ```
   grep -rn "500_000\|500000" src/
   ```
   Expected: no results that semantically mean "scanner discovery upper bound."
   (If unrelated 500_000 values appear — e.g. budget defaults in CLI — leave
   them untouched.)

5. Git log check — two new commits authored in this plan:
   ```
   git log --oneline -3
   ```
   Expected head: Task 2 commit on top, Task 1 commit under it.
</verification>

<success_criteria>
- `SCANNER_MAX_PRICE == 0` in src/config.py with a sentinel comment.
- `FutGGClient.discover_players` no longer auto-fills max_price from budget.
- New unit tests in tests/test_futgg_client.py prove:
  - `max_price=0` → URL lacks `price__lte`.
  - `max_price>0` → URL contains `price__lte=<value>`.
  - `budget=500_000, max_price=0` → URL STILL lacks `price__lte`.
- `src/server/scanner_discovery.py` passes `max_price=SCANNER_MAX_PRICE` (= 0)
  at both run_bootstrap and run_discovery call sites.
- `MockClient.discover_players` honors `max_price=0` as "no upper bound."
- No new test failures vs baseline-documented 14 pre-existing failures.
- Two atomic commits committed to the branch.
- Out-of-scope items explicitly untouched: SCAN_INTERVAL_SECONDS,
  SCAN_CONCURRENCY, pool sizing, src/algo/**, tests/algo/**, run_discovery
  "mark cold" branch logic.
</success_criteria>

<output>
After completion, create
`.planning/quick/260418-ddp-remove-scanner-max-price-cap-discover-al/260418-ddp-SUMMARY.md`
with:
- Truths achieved (copy from must_haves.truths, mark each as verified).
- Artifacts modified (files + commit SHAs).
- Test delta (new tests passing; baseline failures unchanged).
- Out-of-scope items explicitly confirmed untouched.
- Recommended follow-up: none specific — user will tune throughput
  separately if needed, and validate live-discovery surfacing high-priced
  promos on their next scanner run.
</output>
