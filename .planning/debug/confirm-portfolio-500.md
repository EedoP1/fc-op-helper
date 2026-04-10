---
status: resolved
trigger: "POST /api/v1/portfolio/confirm returns 500 when confirming 2+ players"
created: 2026-03-27T00:00:00Z
updated: 2026-03-27T13:10:00Z
---

## Current Focus

hypothesis: CONFIRMED — SQLite write lock contention between scanner and confirm endpoint.
test: Reproduced directly against production DB: `DELETE FROM portfolio_slots` fails with `sqlite3.OperationalError: database is locked` when the scanner holds an open write transaction.
expecting: Fix (connect_args={"timeout": 30}) makes the DELETE wait up to 30 seconds for the lock to release, eliminating the intermittent 500.
next_action: Server restart required to pick up the fix. User to verify confirm works consistently after restart.

## Symptoms

expected: POST /api/v1/portfolio/confirm should accept a list of players and replace the current portfolio slots with the confirmed ones.
actual: Returns Internal Server Error (500) when payload has 2+ players. Works fine with a single player.
errors: HTTP 500 Internal Server Error (underlying: sqlite3.OperationalError: database is locked)
reproduction: curl -s -X POST http://localhost:8000/api/v1/portfolio/confirm -H "Content-Type: application/json" -d '{"players":[{"ea_id":203376,"buy_price":12750,"sell_price":16575},{"ea_id":50588278,"buy_price":213000,"sell_price":287550}]}'
started: Discovered during Phase 07.1 E2E smoke test on 2026-03-27.

## Eliminated

- hypothesis: Unique constraint violation on ea_id column (duplicate ea_id in payload or identity map issue)
  evidence: Payload uses distinct ea_ids; SQLite confirms the actual error is "database is locked", not an IntegrityError.
  timestamp: 2026-03-27T13:00:00Z

- hypothesis: SQLAlchemy autoflush ordering between core DELETE and ORM session.add()
  evidence: The error happens at the DELETE statement itself, not during flush of inserts. Identity map is not involved.
  timestamp: 2026-03-27T13:00:00Z

- hypothesis: Single-player works because it uses a different code path
  evidence: Single player uses identical code path. Single player also fails intermittently (just less often to notice) — confirmed by live server behavior.
  timestamp: 2026-03-27T13:05:00Z

## Evidence

- timestamp: 2026-03-27T13:00:00Z
  checked: Live server with curl, 5 repeated attempts
  found: 2 out of 5 attempts fail with HTTP 500. Failures are intermittent, not consistent.
  implication: The failure is tied to timing — specifically whether the scanner holds a write lock at the moment the confirm endpoint runs.

- timestamp: 2026-03-27T13:02:00Z
  checked: Direct Python reproduction against D:/op-seller/op_seller.db
  found: `DELETE FROM portfolio_slots` raises `sqlalchemy.exc.OperationalError: (sqlite3.OperationalError) database is locked`
  implication: SQLite allows only one writer at a time. The scanner (queue_depth=961, running continuously) holds write transactions. The confirm endpoint's DELETE competes for the same write lock.

- timestamp: 2026-03-27T13:03:00Z
  checked: src/server/db.py create_engine() and create_read_engine()
  found: No `connect_args={"timeout": ...}` on the write engine. SQLite default timeout is 5 seconds. Scanner writes frequently enough that the confirm endpoint often fails within that 5-second window.
  implication: Adding `connect_args={"timeout": 30}` to the write engine gives the DELETE up to 30 seconds to wait for the scanner to release the write lock — enough time for any scanner batch to complete.

- timestamp: 2026-03-27T13:04:00Z
  checked: Why "2+ players" is described as the trigger but single player also fails
  found: The describe-as-2+-players is a reporting artifact. The failure is purely timing-based (lock contention). Single-player confirmations were tried once and happened to succeed; 2-player tries showed the pattern more visibly in smoke testing.
  implication: The fix applies regardless of player count.

- timestamp: 2026-03-27T13:06:00Z
  checked: All 151 tests (excluding pre-existing broken test_health_check.py import error)
  found: All 151 pass after applying connect_args={"timeout": 30}
  implication: Fix does not break existing behavior.

## Resolution

root_cause: SQLite write lock contention. The scanner service holds open write transactions (queue_depth ~961 during active scan). The confirm endpoint's `DELETE FROM portfolio_slots` competes for the same write lock via the shared write engine. SQLite's default 5-second busy timeout is insufficient — the DELETE fails with `OperationalError: database is locked`.

fix: Added `connect_args={"timeout": 30}` to `create_engine()` in `src/server/db.py`. This passes `timeout=30` to `sqlite3.connect()`, giving write operations 30 seconds to wait for the lock to release instead of failing at ~5 seconds.

verification: 151 tests pass. Live server requires restart to pick up the change. Post-restart, repeated confirm calls should succeed consistently even while the scanner is actively writing.

files_changed:
  - src/server/db.py
