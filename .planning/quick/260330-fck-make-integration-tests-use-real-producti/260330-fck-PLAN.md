---
phase: quick
plan: 260330-fck
type: execute
wave: 1
depends_on: []
files_modified:
  - tests/integration/conftest.py
  - tests/integration/server_harness.py
autonomous: true
requirements: []
---

<objective>
Switch integration tests to launch the real production server (`src.server.main:app`) instead of the custom test harness. The only test-vs-production difference should be `DATABASE_URL` pointing to the test DB.

Purpose: Eliminate drift between what tests exercise and what production runs. The harness was a copy of main.py that diverged over time (skipped bootstrap, skipped scheduler jobs, throttled scanner, added warmup queries). Tests should validate the real server.

Output: conftest.py launches `src.server.main:app`, server_harness.py deleted.
</objective>

<execution_context>
@C:/Users/maftu/.claude/get-shit-done/workflows/execute-plan.md
@C:/Users/maftu/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@tests/integration/conftest.py
@src/server/main.py
@src/server/scheduler.py
@tests/integration/server_harness.py
</context>

<tasks>

<task type="auto">
  <name>Task 1: Switch conftest to use production server app and delete harness</name>
  <files>tests/integration/conftest.py, tests/integration/server_harness.py</files>
  <action>
In `tests/integration/conftest.py`, change the uvicorn target from `tests.integration.server_harness:app` to `src.server.main:app` (line 100).

That is the ONLY change to conftest.py. The env dict already passes `DATABASE_URL` to the subprocess, and `src.server.main` reads `DATABASE_URL` via `src.config` at import time — so the production server will connect to the test DB automatically.

Then delete the file `tests/integration/server_harness.py` entirely.

What this means for test behavior (expected, not bugs to fix):
- Bootstrap one-shot job WILL run (downloads players from fut.gg, holds write pool). The 600-iteration readiness poll (60s total) in conftest should handle the startup delay.
- All scheduler jobs WILL run (scan_dispatch, discovery, cleanup, aggregation) via `create_scheduler()`.
- Full production scanner concurrency (SCAN_CONCURRENCY=40, SCAN_DISPATCH_BATCH_SIZE=200) WILL be used.
- No cache warmup queries — first API call may be slower.
- The `is_leftover` migration uses `inspect` + `ALTER TABLE` (production style) instead of `ADD COLUMN IF NOT EXISTS`.

Do NOT add any workarounds, patches, or throttling. The user explicitly wants exact production parity with only DATABASE_URL differing.
  </action>
  <verify>
    <automated>cd C:/Users/maftu/Projects/op-seller && python -c "import ast; ast.parse(open('tests/integration/conftest.py').read()); print('conftest syntax OK')" && python -c "import os; assert not os.path.exists('tests/integration/server_harness.py'), 'harness still exists'; print('harness deleted OK')"</automated>
  </verify>
  <done>
- conftest.py launches `src.server.main:app` (not `tests.integration.server_harness:app`)
- server_harness.py is deleted
- No other changes to conftest.py besides the uvicorn target string
  </done>
</task>

<task type="auto">
  <name>Task 2: Run integration tests to validate production server works</name>
  <files></files>
  <action>
Run the integration test suite to confirm the real production server starts and tests pass:

    cd C:/Users/maftu/Projects/op-seller
    python -m pytest tests/integration/ -x -v --timeout=300 2>&1 | head -100

If the test DB is not available (skip message), that is acceptable — the code change is still correct. The conftest `_check_test_db()` guard will skip gracefully.

If tests fail due to bootstrap timeout (server not ready within 60s), increase the readiness poll iterations from 600 to 1200 (doubles to 120s) to accommodate bootstrap startup. This is the ONE acceptable adjustment — it accounts for production bootstrap needing more startup time than the harness did.

If tests fail for other reasons, diagnose and fix. Do NOT reintroduce harness patterns (throttling, warmup queries, job skipping).
  </action>
  <verify>
    <automated>cd C:/Users/maftu/Projects/op-seller && python -m pytest tests/integration/ -x -v --timeout=300 2>&1 | tail -20</automated>
  </verify>
  <done>
- Integration tests either pass or skip cleanly (test DB not available)
- No test failures caused by the server swap
- server_harness.py does not exist
  </done>
</task>

</tasks>

<verification>
- `grep -n "server_harness" tests/integration/conftest.py` returns nothing
- `grep -n "src.server.main:app" tests/integration/conftest.py` returns the uvicorn line
- `tests/integration/server_harness.py` does not exist
- `python -m pytest tests/integration/ -x` passes or skips (no failures)
</verification>

<success_criteria>
Integration tests launch the exact same FastAPI app as production (`src.server.main:app`), with DATABASE_URL as the sole differentiator. The server_harness.py file is deleted. Tests pass against the real server.
</success_criteria>

<output>
After completion, create `.planning/quick/260330-fck-make-integration-tests-use-real-producti/260330-fck-SUMMARY.md`
</output>
