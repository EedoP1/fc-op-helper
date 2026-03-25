---
phase: quick
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - src/server/scanner.py
  - src/server/main.py
  - src/config.py
autonomous: true
must_haves:
  truths:
    - "Server completes bootstrap discovery + initial scoring of all players in under 5 minutes"
    - "Normal dispatch loop is unaffected after initial heating completes"
    - "Circuit breaker still protects against API failures during initial heating"
  artifacts:
    - path: "src/server/scanner.py"
      provides: "run_initial_scoring method and batched bootstrap writes"
    - path: "src/server/main.py"
      provides: "Chained bootstrap -> initial scoring startup sequence"
    - path: "src/config.py"
      provides: "INITIAL_SCORING_CONCURRENCY constant"
  key_links:
    - from: "src/server/main.py"
      to: "src/server/scanner.py"
      via: "bootstrap one-shot job chains into run_initial_scoring"
      pattern: "run_initial_scoring"
---

<objective>
Speed up initial server heating (bootstrap discovery + first scoring pass) to complete in under 5 minutes.

Purpose: Currently, after bootstrap seeds ~1000 players into the DB with next_scan_at=now, the regular dispatch loop picks up only 10 players every 30 seconds (SCAN_CONCURRENCY * 2 per SCAN_DISPATCH_INTERVAL). This means initial scoring takes ~50 minutes. The fix is a dedicated initial scoring pass with higher concurrency and batched DB operations.

Output: Modified scanner with fast initial scoring, batched bootstrap DB writes, and chained startup sequence.
</objective>

<execution_context>
@.planning/STATE.md
</execution_context>

<context>
@src/server/scanner.py
@src/server/main.py
@src/config.py
@src/futgg_client.py
@src/server/db.py
@src/server/models_db.py
@src/server/scheduler.py

<interfaces>
From src/server/scanner.py:
```python
class ScannerService:
    def __init__(self, session_factory: async_sessionmaker, circuit_breaker: CircuitBreaker): ...
    async def start(self) -> None: ...
    async def run_bootstrap(self) -> None: ...  # Seeds DB with discovered players
    async def scan_player(self, ea_id: int) -> None: ...  # Scores one player (2 API calls + DB write)
    async def dispatch_scans(self) -> None: ...  # Picks up 10 due players per cycle
```

From src/config.py:
```python
SCAN_CONCURRENCY = 5              # concurrent scans in normal dispatch
SCAN_DISPATCH_INTERVAL = 30       # seconds between dispatch checks
SCANNER_MIN_PRICE = 11_000
SCANNER_MAX_PRICE = 200_000
```

From src/server/main.py:
```python
# Bootstrap is queued as a one-shot APScheduler job (non-blocking)
scheduler.add_job(scanner.run_bootstrap, id="bootstrap", replace_existing=True)
```
</interfaces>
</context>

<tasks>

<task type="auto">
  <name>Task 1: Add batched bootstrap writes and initial scoring method to ScannerService</name>
  <files>src/server/scanner.py, src/config.py</files>
  <action>
**In src/config.py:**
- Add `INITIAL_SCORING_CONCURRENCY = 10` constant (double the normal 5, aggressive but respectful for one-time use)
- Add `INITIAL_SCORING_BATCH_SIZE = 50` constant (how many players to process before yielding)

**In src/server/scanner.py:**
- Import `INITIAL_SCORING_CONCURRENCY` and `INITIAL_SCORING_BATCH_SIZE` from config

**Optimize `run_bootstrap()`:**
- Replace the individual `session.execute(stmt)` loop with batch inserts: collect all values dicts in a list, then use `session.execute(sqlite_insert(PlayerRecord), values_list)` with a single `on_conflict_do_update`. If SQLAlchemy's bulk upsert doesn't support on_conflict per-row, use `session.execute()` in chunks of 200 within a single transaction (still far fewer round-trips than one-per-player).
- Log timing: capture `time.monotonic()` before/after discovery and DB write phases.

**Add `run_initial_scoring()` method:**
```python
async def run_initial_scoring(self) -> None:
    """Score all unscored active players with elevated concurrency.

    Called once after bootstrap. Uses INITIAL_SCORING_CONCURRENCY (10)
    instead of normal SCAN_CONCURRENCY (5) to complete faster.
    Processes players in batches of INITIAL_SCORING_BATCH_SIZE to avoid
    overwhelming the event loop.
    """
    import time
    start = time.monotonic()

    async with self._session_factory() as session:
        stmt = (
            select(PlayerRecord.ea_id)
            .where(
                PlayerRecord.is_active == True,
                PlayerRecord.last_scanned_at == None,
            )
            .order_by(PlayerRecord.ea_id)
        )
        result = await session.execute(stmt)
        unscored_ids = [row[0] for row in result.all()]

    total = len(unscored_ids)
    logger.info(f"Initial scoring: {total} unscored players")

    semaphore = asyncio.Semaphore(INITIAL_SCORING_CONCURRENCY)
    scored = 0

    async def _scan_with_sem(ea_id: int) -> None:
        nonlocal scored
        async with semaphore:
            await self.scan_player(ea_id)
            scored += 1
            if scored % 100 == 0:
                logger.info(f"Initial scoring progress: {scored}/{total}")

    # Process in batches to avoid creating thousands of tasks at once
    for i in range(0, total, INITIAL_SCORING_BATCH_SIZE):
        batch = unscored_ids[i:i + INITIAL_SCORING_BATCH_SIZE]
        tasks = [asyncio.create_task(_scan_with_sem(eid)) for eid in batch]
        await asyncio.gather(*tasks, return_exceptions=True)

    elapsed = time.monotonic() - start
    logger.info(f"Initial scoring complete: {scored}/{total} players in {elapsed:.1f}s")
```

This method reuses `scan_player()` which already handles circuit breaker, retries, DB writes, and tier classification. The key speedup comes from:
1. Processing ALL players at once (not waiting for dispatch cycles)
2. Higher concurrency (10 vs 5)
3. No 30-second waits between batches

**Add `run_bootstrap_and_score()` convenience method:**
```python
async def run_bootstrap_and_score(self) -> None:
    """Run bootstrap discovery then immediately score all discovered players.

    Single method for startup chaining — called as one-shot job.
    """
    await self.run_bootstrap()
    await self.run_initial_scoring()
```
  </action>
  <verify>
    <automated>cd C:/Users/maftu/Projects/op-seller && python -c "from src.server.scanner import ScannerService; from src.config import INITIAL_SCORING_CONCURRENCY, INITIAL_SCORING_BATCH_SIZE; print('imports OK'); assert INITIAL_SCORING_CONCURRENCY == 10; assert INITIAL_SCORING_BATCH_SIZE == 50; assert hasattr(ScannerService, 'run_initial_scoring'); assert hasattr(ScannerService, 'run_bootstrap_and_score'); print('all checks passed')"</automated>
  </verify>
  <done>ScannerService has run_initial_scoring() and run_bootstrap_and_score() methods. Config has INITIAL_SCORING_CONCURRENCY=10 and INITIAL_SCORING_BATCH_SIZE=50. Bootstrap DB writes are batched.</done>
</task>

<task type="auto">
  <name>Task 2: Chain bootstrap into initial scoring in server startup</name>
  <files>src/server/main.py</files>
  <action>
**In src/server/main.py lifespan():**
- Change the bootstrap one-shot job from `scanner.run_bootstrap` to `scanner.run_bootstrap_and_score`:
  ```python
  scheduler.add_job(scanner.run_bootstrap_and_score, id="bootstrap", replace_existing=True)
  ```
- Update the log message: `"Server started. Bootstrap + initial scoring queued."`

This is the only change needed — the one-shot job will now run discovery, seed the DB, then immediately score all players with elevated concurrency. The regular dispatch loop runs in parallel and handles any players that get their next_scan_at scheduled during initial scoring.
  </action>
  <verify>
    <automated>cd C:/Users/maftu/Projects/op-seller && python -c "import ast; tree = ast.parse(open('src/server/main.py').read()); source = open('src/server/main.py').read(); assert 'run_bootstrap_and_score' in source, 'missing run_bootstrap_and_score'; assert 'run_bootstrap,' not in source.replace('run_bootstrap_and_score', ''), 'still using old run_bootstrap'; print('startup chain verified')"</automated>
  </verify>
  <done>Server startup chains bootstrap discovery directly into initial scoring via a single one-shot job. No manual intervention needed — server starts, discovers players, and scores them all within ~5 minutes.</done>
</task>

</tasks>

<verification>
Time budget analysis (for ~1000 players, 11k-200k range):
- Discovery: ~50-100 pages at ~0.15s each = ~15s
- Bootstrap DB writes (batched): < 1s
- Initial scoring: ~1000 players, each needs 2 API calls (definition + prices fetched in parallel via gather), concurrency 10, 0.15s delay per request = ~1000 * 0.15s / 10 = ~15s for the API calls alone. With network latency and DB writes, estimate ~2-3 minutes total.
- **Total estimated: ~3 minutes** (well under the 5-minute target)

Verify the existing test suite still passes:
```bash
cd C:/Users/maftu/Projects/op-seller && python -m pytest tests/ -x -q
```
</verification>

<success_criteria>
- Server bootstrap + initial scoring completes in under 5 minutes for the full 11k-200k player range
- All existing tests pass without modification
- Regular dispatch loop behavior is unchanged after initial heating
- Circuit breaker is respected during initial scoring (scan_player already checks it)
- Progress logging shows scoring advancement every 100 players
</success_criteria>

<output>
After completion, verify by starting the server and monitoring logs for timing:
```bash
cd C:/Users/maftu/Projects/op-seller && python -m uvicorn src.server.main:app --host 0.0.0.0 --port 8000
```
Watch for: "Initial scoring complete: X/Y players in Z.Zs" log line confirming < 300s.
</output>
