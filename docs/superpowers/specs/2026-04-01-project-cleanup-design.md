# Project Cleanup Design

**Date:** 2026-04-01
**Scope:** Full codebase cleanup — tests, dead code, DB optimization, architecture refactor

## Overview

Sequential four-phase cleanup of the op-seller project. Each phase leaves the codebase in a known-good state before the next begins. Tests must pass at the end of every phase.

## Phase 1: Green Tests

**Goal:** All tests pass. No test weakening — fix code or update tests to reflect current reality.

**Known issues:**
- `test_portfolio.py` imports `SnapshotPricePoint` which no longer exists in `models_db.py`
- Full suite needs to be run to discover additional failures

**Process:**
1. Commit uncommitted WIP changes (clean baseline)
2. Fix import errors so all tests collect
3. Run full suite, fix failures one by one
4. Integration tests (testcontainers Postgres) included
5. All tests green before proceeding

## Phase 2: Dead Code Removal

**Goal:** Remove all unused code, generated files, and stale artifacts.

**Targets:**

| Item | Action |
|------|--------|
| `research_*.py` (6 files at root) | Delete |
| `.claude/worktrees/` (6 stale agent worktrees) | Delete |
| `test_*.db`, `test_*.db-shm`, `test_*.db-wal` | Delete, add to `.gitignore` |
| `dashboard.html` | Check if used, likely delete |
| `scanner.log` | Delete, add to `.gitignore` |
| `*.csv` (27 export files at root) | Delete, add to `.gitignore` |
| Unused imports across all `src/` and `tests/` | Remove |
| Unused functions/classes across all modules | Remove |
| `src/scorer.py` if it still exists | Delete (replaced by `scorer_v2.py`) |
| Stale references to v1 scorer | Update or remove |

**Process:**
1. Grep every module for unused imports and dead functions
2. Verify nothing references deleted code before removing
3. Run tests after removal to confirm nothing breaks
4. Update `.gitignore` for generated files

## Phase 3: DB Analysis & Optimization

**Goal:** Understand what's in the 7GB database, find waste, propose and implement changes.

### Step 1 — Analyze (no changes)
- Query each table for row count, approximate size, oldest/newest timestamps
- Identify which tables consume the most space
- Check for orphaned data (scores/snapshots for players no longer in `players` table)
- Check for duplicate or redundant rows
- Analyze `live_auction_prices` JSON column in `market_snapshots` — likely biggest space consumer

### Step 2 — Propose optimizations
- Tighter retention policies where appropriate
- Column-level changes (drop unused columns, change types)
- Index review — needed vs missing
- Normalization opportunities (repeated JSON blobs)
- One-time purge scripts for identified waste

### Step 3 — Implement agreed changes
- Write standalone migration scripts (not inline)
- Apply retention changes to config
- Run cleanup, measure space savings
- User approval required before any destructive DB changes

## Phase 4: Architecture Refactor

**Goal:** Aggressive review and restructure of every module for cleaner boundaries and maintainability. No behavioral changes.

**Targets:**

| Area | Issue | Action |
|------|-------|--------|
| `portfolio.py` (981L) | Too many responsibilities | Split into focused modules (generate, confirm, swap, status) |
| Inline migrations in `server/main.py` | Not scalable | Extract to Alembic versioned migrations |
| `scorer_v2.py` raw SQL | Large SQL string, no IDE support | Evaluate SQLAlchemy expressions or parameterized builder |
| `scanner.py` (732L) | Large file, multiple concerns | Review for extraction opportunities |
| `actions.py` (524L) | Moderate size | Review — may be fine as-is |
| Module boundaries | Potential cross-layer tangles | Map dependency graph, enforce clean layers |
| `src/main.py` CLI | Mixes display + orchestration | Clean separation of concerns |
| `futbin_client.py` | Only used by `health_check.py` | Evaluate if still needed |
| `models.py` vs `models_db.py` | Two model layers | Review if Pydantic models are still needed or dead weight |
| Error handling | Varies across modules | Standardize approach |

**Process:**
1. Review each module file by file
2. Refactor one module at a time, run tests after each
3. Same functionality, cleaner structure
4. Commit after each logical refactor unit

## Constraints

- `.planning/` directory left as-is (kept for history)
- No test weakening — failures mean bugs to fix
- Tests must pass at end of every phase
- DB changes require user approval before execution
- No behavioral changes in Phase 4 — pure structural refactor

## Execution Order

```
Phase 1 (Green Tests) -> Phase 2 (Dead Code) -> Phase 3 (DB) -> Phase 4 (Architecture)
```

Each phase depends on the previous one being complete and green.
