# Phase 3: CLI as API Client - Context

**Gathered:** 2026-03-25
**Status:** Ready for planning

<domain>
## Phase Boundary

The CLI is refactored from a direct scorer into a thin display layer that queries the running backend API. All scoring and portfolio logic executes on the server; the terminal just presents results. No new API endpoints (Phase 2 complete), no Chrome extension (v2), no new scoring logic.

</domain>

<decisions>
## Implementation Decisions

### Server Connection
- **D-01:** Default server URL is `http://localhost:8000`, overridable with `--url` flag on the CLI
- **D-02:** Player detail accessed via `--player {ea_id}` flag, mutually exclusive with `--budget`
- **D-03:** Old direct-scoring code (FutGGClient calls, local scoring pipeline) is removed from main.py entirely — lives in git history only

### Output Format
- **D-04:** Portfolio table keeps the same columns as the current Rich table output — no new staleness/trend columns
- **D-05:** Player detail view uses Rich panel at top (name, rating, position, club) + score breakdown table below + trend summary line at bottom

### CSV Export
- **D-06:** CSV auto-export after portfolio display is kept — same behavior as today, same columns, mapped from API response

### Offline Behavior
- **D-07:** When server is unreachable, print a clear error message with the server URL and startup instructions, then exit with code 1. No fallback to direct scoring, no retries.

### Claude's Discretion
- HTTP client choice for API calls (httpx, requests, or urllib)
- Error handling for non-connection API errors (400, 500, etc.)
- How to map API response fields to the existing display/CSV column format
- Whether to keep or remove the `--verbose` flag (may not be relevant without direct scoring)

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Requirements
- `.planning/REQUIREMENTS.md` — CLI-01, CLI-02, CLI-03 are the requirements for this phase

### Prior Phase Context
- `.planning/phases/01-persistent-scanner/01-CONTEXT.md` — Endpoint design decisions, staleness handling
- `.planning/phases/02-full-api-surface/02-CONTEXT.md` — Portfolio endpoint response format, player detail response format, trend indicators

### Existing Code
- `src/main.py` — Current CLI entry point to refactor (remove direct scoring, add API calls)
- `src/server/api/portfolio.py` — Portfolio endpoint the CLI will call (response format reference)
- `src/server/api/players.py` — Player detail endpoint the CLI will call (response format reference)
- `src/server/main.py` — Server app (for startup instructions in error message)

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `display_results()` in `src/main.py`: Rich table rendering — reuse with minor input mapping changes
- `export_csv()` in `src/main.py`: CSV export — reuse with API response field mapping
- `Console(force_terminal=True)` and Rich imports: Keep existing terminal formatting

### Established Patterns
- Click decorators for CLI flags (`@click.command()`, `@click.option()`)
- `asyncio.run()` for async entry point
- Rich Panel + Table for structured output
- Logger per module pattern

### Integration Points
- CLI calls `GET /api/v1/portfolio?budget=X` for portfolio mode
- CLI calls `GET /api/v1/players/{ea_id}` for player detail mode
- API responses already contain all fields needed for display and CSV export
- `src/main.py` imports of `FutGGClient`, `score_player`, `optimize_portfolio` will be removed

</code_context>

<specifics>
## Specific Ideas

No specific requirements — open to standard approaches for all implementation details.

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 03-cli-as-api-client*
*Context gathered: 2026-03-25*
