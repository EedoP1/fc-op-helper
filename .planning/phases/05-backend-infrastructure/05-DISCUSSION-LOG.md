# Phase 5: Backend Infrastructure - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-03-26
**Phase:** 05-backend-infrastructure
**Areas discussed:** Action queue design, Trade lifecycle tracking, Player swap mechanics, Profit summary aggregation
**Mode:** Auto (all decisions selected by Claude)

---

## Action Queue Design

| Option | Description | Selected |
|--------|-------------|----------|
| On-demand generation | Backend computes next action when polled — no pre-built queue | ✓ |
| Background job queue | Background task populates queue periodically | |
| Extension-driven | Extension decides what to do, backend just validates | |

**User's choice:** On-demand generation (auto-selected)
**Notes:** Stateless approach — backend inspects portfolio + trade state on each poll to determine next action. Simpler, no queue management overhead.

| Option | Description | Selected |
|--------|-------------|----------|
| Single global queue | One action at a time, sequential processing | ✓ |
| Per-player queues | Parallel queues per player | |

**User's choice:** Single global queue (auto-selected)
**Notes:** Matches BACK-01 requirement. Extension processes one action at a time anyway — no benefit to parallel queues.

| Option | Description | Selected |
|--------|-------------|----------|
| BUY, LIST, RELIST | Three action types matching automation cycle | ✓ |
| BUY, LIST, RELIST, CANCEL | Four types including cancellation | |

**User's choice:** BUY, LIST, RELIST (auto-selected)
**Notes:** Cancellation handled by swap endpoint, not as an action type.

---

## Trade Lifecycle Tracking

| Option | Description | Selected |
|--------|-------------|----------|
| Outcomes only | Record completed events (bought, listed, sold, expired) | ✓ |
| Outcomes + attempts | Also record failed attempts (outbid, price guard skip) | |

**User's choice:** Outcomes only (auto-selected)
**Notes:** Failed attempts are noise for profit tracking. Extension handles retries internally.

| Option | Description | Selected |
|--------|-------------|----------|
| Event-per-row | One row per lifecycle event (buy, list, sell, expire) | ✓ |
| Full-cycle row | One row spanning entire buy→sell cycle | |

**User's choice:** Event-per-row (auto-selected)
**Notes:** More flexible for aggregation. Profit computed by joining buy + sell events.

---

## Player Swap Mechanics

| Option | Description | Selected |
|--------|-------------|----------|
| Re-run optimizer | Use optimize_portfolio() with freed budget + locked remaining | ✓ |
| Next-best player | Return single next-best player by efficiency | |
| Manual selection | Return candidates, user picks | |

**User's choice:** Re-run optimizer (auto-selected)
**Notes:** Reuses existing logic. May return multiple replacements if freed budget allows.

| Option | Description | Selected |
|--------|-------------|----------|
| Cancel pending actions | Remove pending/in-progress actions for swapped player | ✓ |
| Let actions complete | Allow in-flight actions to finish | |

**User's choice:** Cancel pending actions (auto-selected)
**Notes:** Prevents extension from acting on stale portfolio data.

---

## Profit Summary Aggregation

| Option | Description | Selected |
|--------|-------------|----------|
| Totals + per-player | Both aggregate totals and per-player breakdown | ✓ |
| Totals only | Just aggregate numbers | |
| Per-player only | Just individual player stats | |

**User's choice:** Totals + per-player (auto-selected)
**Notes:** Totals for quick view, per-player for identifying which picks performed well.

| Option | Description | Selected |
|--------|-------------|----------|
| All-time only | Single all-time aggregation | ✓ |
| Time windows | Daily, weekly, monthly breakdowns | |

**User's choice:** All-time only (auto-selected)
**Notes:** Simplicity for v1.1. Time windows can be added later.

| Option | Description | Selected |
|--------|-------------|----------|
| Realized only | Profit from completed sell cycles | ✓ |
| Realized + unrealized | Include estimated profit on unsold inventory | |

**User's choice:** Realized only (auto-selected)
**Notes:** Unrealized requires live price lookups — unnecessary complexity for v1.1.

---

## Claude's Discretion

- DB table schema details (column types, indexes)
- API response format details
- Error response structure and HTTP status codes
- Router file organization

## Deferred Ideas

None — discussion stayed within phase scope.
