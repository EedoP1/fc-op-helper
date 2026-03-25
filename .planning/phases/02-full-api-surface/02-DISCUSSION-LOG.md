# Phase 2: Full API Surface - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-03-25
**Phase:** 02-full-api-surface
**Areas discussed:** Portfolio endpoint design, Player detail response, Adaptive scan scheduling, Score history retention
**Mode:** --auto (all decisions auto-selected)

---

## Portfolio Endpoint Design

| Option | Description | Selected |
|--------|-------------|----------|
| On-demand computation | Query DB scores, run optimizer per request | ✓ |
| Cached/precomputed | Precompute portfolio at scan time for common budgets | |
| Hybrid | Cache recent results, invalidate on new scan cycle | |

**User's choice:** [auto] On-demand computation (recommended default)
**Notes:** Budget varies per request and this is a single-user tool — caching adds complexity without clear benefit. Reuses `optimize_portfolio()` unchanged.

---

## Player Detail Response

| Option | Description | Selected |
|--------|-------------|----------|
| Full breakdown + history | All score fields + last 24 score entries + trend indicators | ✓ |
| Minimal current score | Current score only, no history | |
| Full + raw sales | Score + history + raw fut.gg sales data | |

**User's choice:** [auto] Full breakdown + history (recommended default)
**Notes:** Last 24 entries covers ~24h for hot players. Raw sales not stored in DB (live fut.gg data), so excluded.

---

## Adaptive Scan Scheduling

| Option | Description | Selected |
|--------|-------------|----------|
| Enhance existing tiers | Keep hot/normal/cold, adjust intervals within tier based on activity delta | ✓ |
| Replace with continuous | Remove tiers, compute exact interval per player | |
| Activity-based only | Schedule based solely on listing age pattern | |

**User's choice:** [auto] Enhance existing tiers (recommended default)
**Notes:** Builds on Phase 1 tier system rather than replacing it. Adaptive adjustment stays within tier bounds.

---

## Score History Retention

| Option | Description | Selected |
|--------|-------------|----------|
| Keep all indefinitely | No pruning, add index for efficient queries | ✓ |
| Rolling window (7 days) | Prune scores older than 7 days | |
| Tiered retention | Keep hourly for 7d, daily summaries beyond | |

**User's choice:** [auto] Keep all indefinitely (recommended default)
**Notes:** SQLite handles the volume for single-user. Trends need history. Add (ea_id, scored_at DESC) index.

---

## Claude's Discretion

- Portfolio endpoint query optimization
- Player detail response serialization
- Trend calculation algorithm
- Adaptive scheduling formula
- API error response format

## Deferred Ideas

None — discussion stayed within phase scope.
