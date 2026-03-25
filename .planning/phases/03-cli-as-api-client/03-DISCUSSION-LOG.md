# Phase 3: CLI as API Client - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-03-25
**Phase:** 03-cli-as-api-client
**Areas discussed:** Server connection, Output changes, CSV export, Offline behavior

---

## Server Connection

| Option | Description | Selected |
|--------|-------------|----------|
| Default localhost + --url flag | Default to http://localhost:8000, override with --url flag. Simple, works for local dev and remote later. | ✓ |
| Environment variable | OP_SELLER_URL env var. Good for CI/scripts, but requires setup. | |
| Config file | Read from a .opseller.toml or similar. More setup, but persistent per-machine. | |

**User's choice:** Default localhost + --url flag
**Notes:** None

| Option | Description | Selected |
|--------|-------------|----------|
| Flag: --player {ea_id} | Same entry point, add --player flag. Mutually exclusive with --budget. Keeps it simple. | ✓ |
| Subcommands: portfolio / player | python -m src.main portfolio --budget X / python -m src.main player {id}. Cleaner separation but more CLI restructuring. | |

**User's choice:** Flag: --player {ea_id}
**Notes:** None

---

## Output Changes

| Option | Description | Selected |
|--------|-------------|----------|
| Same columns | Keep the existing Rich table identical. Staleness/trends are available via --player detail. Keeps the list clean. | ✓ |
| Add staleness indicator | Add an is_stale column or marker for players with old data. | |
| Add trend + staleness | Add trend direction arrow and staleness marker. More info but busier table. | |

**User's choice:** Same columns
**Notes:** None

| Option | Description | Selected |
|--------|-------------|----------|
| Rich panel + score table | Player info panel at top, score breakdown table below, trend summary line at bottom. | ✓ |
| Compact single table | Everything in one flat table. Minimal but dense. | |
| You decide | Claude picks the best layout based on the API response structure. | |

**User's choice:** Rich panel + score table
**Notes:** None

---

## CSV Export

| Option | Description | Selected |
|--------|-------------|----------|
| Keep auto-export | Same behavior as today — always write CSV after display. Same columns, mapped from API response. | ✓ |
| Opt-in with --csv flag | Only export when user passes --csv. Avoids file clutter. | |
| Drop CSV export | Remove it entirely. Users can get data from the API directly. | |

**User's choice:** Keep auto-export
**Notes:** None

---

## Offline Behavior

| Option | Description | Selected |
|--------|-------------|----------|
| Clean error + exit | Print a clear message and exit with code 1. No fallback. | ✓ |
| Fallback to direct scoring | If server is down, fall back to the old direct fut.gg scoring pipeline. | |
| Retry with timeout | Retry connection 3 times with 2s intervals, then error. | |

**User's choice:** Clean error + exit
**Notes:** None

| Option | Description | Selected |
|--------|-------------|----------|
| Remove it | Delete the old pipeline from main.py. It lives in git history if ever needed. | ✓ |
| Keep behind a flag | Add a --direct flag to bypass the API and score locally. | |

**User's choice:** Remove old direct-scoring code
**Notes:** None

---

## Claude's Discretion

- HTTP client choice for API calls
- Error handling for non-connection API errors
- API response field mapping to display/CSV columns
- Whether --verbose flag is still relevant

## Deferred Ideas

None — discussion stayed within phase scope.
