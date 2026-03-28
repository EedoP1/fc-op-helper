# Phase 9: Real-World Server Integration Tests - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-03-28
**Phase:** 09-comprehensive-api-integration-performance-test-suite
**Areas discussed:** Server Under Test, Test Scope, Cleanup Strategy, Performance Thresholds

---

## Previous Attempt Learnings

The first attempt at Phase 9 produced 74 tests that all passed against an empty DB with mocked scanner. When run against a copy of the real DB:
- 65/74 tests failed due to ReadTimeout (10s client timeout too short for real data)
- After increasing timeout to 30s: 72/74 passed
- The 2 remaining failures were tests assuming empty DB (count == 0)

**User's reaction:** Tests were too polite. They tested happy paths with fake ea_ids (100, 200, 300). Real bugs happen with real data under concurrent access.

## Server Under Test

| Option | Description | Selected |
|--------|-------------|----------|
| Real main.py | Start the actual server the user runs — no mocks at all | auto |
| Test harness with real scanner | Keep harness structure but swap mocks for real components | |
| Parameterized (both) | Run same tests against both harness and real server | |

**User's choice:** Real main.py — "NOTHING on the server should be mocked"
**Notes:** User was explicit that the whole point is testing the real server, not a simplified version

## Test Scope

| Option | Description | Selected |
|--------|-------------|----------|
| All real-world workflows | Portfolio gen, concurrent removes, remove/replace, lifecycle races, rapid API, scanner states | auto |
| Critical paths only | Just portfolio gen and action lifecycle | |
| Incremental | Start with basic, add more later | |

**User's choice:** All real-world workflows
**Notes:** User listed specific bugs: fragile insertion, slow generation, slow removal, duplicate players on concurrent remove. "I am sure there are many many more problems"

## Cleanup Strategy

| Option | Description | Selected |
|--------|-------------|----------|
| Selective cleanup | Reset mutable tables, preserve scored data | auto |
| Full reset per test | Copy fresh DB for every test | |
| No cleanup | Let data accumulate across tests | |

**User's choice:** Selective cleanup
**Notes:** Preserve the real scored player data that makes tests meaningful

## Performance Thresholds

| Option | Description | Selected |
|--------|-------------|----------|
| Strict real-world SLAs | health<100ms, pending<200ms, status<300ms, profit<200ms | auto |
| Relaxed for real DB | 2-5x higher thresholds for real data volume | |
| Measure only, no threshold | Log p95 but don't fail on it | |

**User's choice:** Strict thresholds
**Notes:** User explicitly rejected relaxing thresholds: "waiting more than 300ms for an action makes sense?" — performance issues are bugs to fix

## Test Philosophy

**User's explicit instruction:** "we expect to find bugs, don't do anything to get green tests, the tests that fail will become an issue that we later solve in the server itself, thats the whole point"

## Claude's Discretion

None — user had strong opinions on all areas

## Deferred Ideas

None — discussion stayed within phase scope
