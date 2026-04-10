# Phase 2: Dead Code Removal — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove all dead code, unused files, and generated artifacts. Update `.gitignore` to prevent future accumulation.

**Architecture:** Two entire modules (`src/health_check.py` and `src/futbin_client.py`) are dead — they form a circular dependency on each other and nothing else imports them. Six research scripts at root are one-off validation tools. Generated files (test DBs, logs, CSVs) need gitignore rules. Stale worktrees from Claude Code agents need cleanup.

**Tech Stack:** Python 3.12, git

---

### Task 1: Delete dead Python modules

**Files:**
- Delete: `src/health_check.py` (401 lines)
- Delete: `src/futbin_client.py` (367 lines)
- Delete: `tests/test_health_check.py` (7 lines, placeholder only)

These modules are completely dead:
- `futbin_client.py` — FUTBIN HTTP client, only imported by `health_check.py`
- `health_check.py` — FUTBIN health monitor, imports `futbin_client.py`, nothing imports it
- `test_health_check.py` — placeholder file with only a docstring explaining removal

- [ ] **Step 1: Verify nothing imports these modules**

```bash
grep -r "from src.health_check\|from src.futbin_client\|import health_check\|import futbin_client" src/ tests/ --include="*.py" | grep -v ".claude/worktrees"
```

Expected: Only `src/health_check.py:21` importing futbin_client. No external imports.

- [ ] **Step 2: Delete the files**

```bash
rm src/health_check.py src/futbin_client.py tests/test_health_check.py
```

- [ ] **Step 3: Run tests to confirm nothing breaks**

```bash
python -m pytest tests/ --ignore=tests/integration -v
```

Expected: All tests pass (same count as after Phase 1).

- [ ] **Step 4: Commit**

```bash
git add -u
git commit -m "chore: remove dead FUTBIN modules (health_check.py, futbin_client.py)

Both modules were part of the FUTBIN health monitor which was deprecated
when the project moved to fut.gg API only. health_check.py was the sole
consumer of futbin_client.py, and nothing imported health_check.py."
```

---

### Task 2: Delete research scripts

**Files:**
- Delete: `research_python_profile.py` (180 lines)
- Delete: `research_sql_agg.py` (75 lines)
- Delete: `research_summary_vs_raw.py` (154 lines)
- Delete: `research_timing.py` (104 lines)
- Delete: `research_timing_fair.py` (122 lines)
- Delete: `research_verify_match.py` (158 lines)

These are one-off validation/profiling scripts that served their purpose during scorer_v2 development.

- [ ] **Step 1: Delete all research files**

```bash
rm research_python_profile.py research_sql_agg.py research_summary_vs_raw.py research_timing.py research_timing_fair.py research_verify_match.py
```

- [ ] **Step 2: Commit**

```bash
git add -u
git commit -m "chore: remove 6 research scripts (793 lines)

One-off validation and profiling scripts from scorer_v2 development.
No longer needed — the SQL scorer is verified and in production."
```

---

### Task 3: Clean up generated files and update .gitignore

**Files:**
- Delete: `op_seller.db` (empty, 0 bytes)
- Delete: `test_debug.db`, `test_debug.db-shm`, `test_debug.db-wal`
- Delete: `test_fix.db`, `test_fix.db-shm`, `test_fix.db-wal`
- Delete: `scanner.log`
- Modify: `.gitignore`

- [ ] **Step 1: Delete generated files**

```bash
rm -f op_seller.db test_debug.db test_debug.db-shm test_debug.db-wal test_fix.db test_fix.db-shm test_fix.db-wal scanner.log
```

- [ ] **Step 2: Update .gitignore**

Add rules for test databases, logs, IDE config, and build artifacts. The updated `.gitignore` should be:

```
__pycache__/
*.pyc
*.csv
*.db
*.db-shm
*.db-wal
*.log
.env
.idea/
.wxt/
.output/
# Ignore root-level test scripts (legacy), but not tests/ directory
/test_*.py
```

Changes from current:
- `op_history.db` replaced with `*.db` (covers all SQLite databases)
- Added `*.db-shm` and `*.db-wal` (SQLite WAL mode files)
- Added `*.log` (scanner.log and any future logs)
- Added `.idea/` (JetBrains IDE)
- Added `.wxt/` and `.output/` (extension build artifacts)

- [ ] **Step 3: Commit**

```bash
git add .gitignore
git rm --cached op_seller.db test_debug.db test_debug.db-shm test_debug.db-wal test_fix.db test_fix.db-shm test_fix.db-wal scanner.log 2>/dev/null || true
git commit -m "chore: clean generated files and expand .gitignore

Remove test databases, empty DB, and log file. Expand .gitignore to
cover all SQLite files, logs, IDE config, and build artifacts."
```

---

### Task 4: Clean up stale Claude Code worktrees

**Files:**
- Delete: `.claude/worktrees/` (6 stale agent worktrees with full src/ copies)

- [ ] **Step 1: Check if any worktrees are active**

```bash
git worktree list
```

Expected: Only the main worktree. If others are listed, they need `git worktree remove` first.

- [ ] **Step 2: Remove worktree directory**

```bash
rm -rf .claude/worktrees
```

- [ ] **Step 3: Prune stale worktree references**

```bash
git worktree prune
```

- [ ] **Step 4: Add .claude/worktrees/ to .gitignore if not already excluded**

Check if `.claude/` is already gitignored. If not, add it:

```
.claude/worktrees/
```

- [ ] **Step 5: Commit if .gitignore changed**

```bash
git add .gitignore
git commit -m "chore: remove stale Claude Code worktrees and gitignore them"
```

---

### Task 5: Final verification

- [ ] **Step 1: Run full test suite**

```bash
python -m pytest tests/ --ignore=tests/integration -v
```

Expected: All tests pass. Same count as Phase 1.

- [ ] **Step 2: Verify no dangling imports**

```bash
python -c "import src.main; import src.server.main; import src.optimizer; import src.futgg_client; import src.models; import src.protocols; import src.server.scanner; import src.server.scorer_v2; import src.server.listing_tracker; print('All imports OK')"
```

Expected: "All imports OK"

- [ ] **Step 3: Check git status is clean**

```bash
git status
```

Expected: No untracked Python files at root (research_*.py gone). Only `.planning/`, `brand/`, `dashboard.html`, `docs/`, and extension build dirs remain as untracked.
