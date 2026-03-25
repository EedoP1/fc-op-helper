# Phase 4: Refactor Scoring + DB - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-03-25
**Phase:** 04-refactor-scoring-db
**Areas discussed:** Listing tracking, Scoring formula, Scan frequency, Data retention, Integration strategy

---

## Initial Scope (User Override)

The user was presented with 4 gray areas (Scorer output type, Model unification, Config consolidation, DB schema cleanup) but overrode with a completely different vision:

**User's vision:** Track every single listing of every card over time — know which were listed, sold, and expired. Use this data to create a better expected profit per card by knowing exactly how many cards were OP listed and how many of them sold.

---

## Listing Tracking Mechanism

| Option | Description | Selected |
|--------|-------------|----------|
| Fingerprint matching | Match listings by (ea_id + buyNowPrice + timing). Cross-reference completedAuctions for sold/expired determination. | ✓ |
| Price bucket tracking | Track counts at each price level per scan, not individual listings. | |
| I know the API better | Let user explain available data. | |

**User's choice:** Fingerprint matching
**Notes:** None

---

## Scoring Formula

| Option | Description | Selected |
|--------|-------------|----------|
| OP sell rate | expected_profit = net_profit × (OP_sold / OP_listed). True conversion rate. | ✓ |
| Weighted by margin | Group by margin tier, track sell rate per tier. | |
| Different idea | User-defined formula. | |

**User's choice:** OP sell rate, adjusted to per hour
**Notes:** "We must adjust to per hour, we need to see how many sales we are expecting to get per hour, which is achievable by tracking every listing of the card."

---

## Integration Strategy

| Option | Description | Selected |
|--------|-------------|----------|
| Replace scorer entirely | New scorer reads from listing tracking DB. Old score_player() retired. | ✓ |
| Parallel scoring | Keep current scorer + add listing-based as secondary. | |
| Phased migration | Collect data alongside current scoring, switch when enough data accumulates. | |

**User's choice:** Replace scorer entirely
**Notes:** None

---

## Data Retention

| Option | Description | Selected |
|--------|-------------|----------|
| Keep everything | Store all listing observations indefinitely. | |
| Rolling window | Keep 7 days of individual data, aggregate older into daily summaries. | ✓ |
| Match market data retention | Use existing 30-day retention. | |

**User's choice:** Rolling window (7 days individual, daily summaries after)
**Notes:** None

---

## Sold vs Expired Determination

| Option | Description | Selected |
|--------|-------------|----------|
| Cross-reference completedAuctions | Match disappeared listings against completedAuctions by price + timeframe. | ✓ |
| Assume sold if price matched | Simpler but less precise matching. | |
| Track disappearance only | Don't distinguish sold vs expired. | |

**User's choice:** Cross-reference completedAuctions
**Notes:** None

---

## OP Listing Classification

| Option | Description | Selected |
|--------|-------------|----------|
| Same margin logic | Use same margin tiers (3%-40%) as current scorer. | ✓ |
| Single threshold | Fixed % above market. | |
| Dynamic per player | Historical margin distribution. | |

**User's choice:** Same margin logic
**Notes:** None

---

## Scan Frequency

| Option | Description | Selected |
|--------|-------------|----------|
| Keep current intervals | Fixed 30min/1hr/2.5hr tiers. | |
| Increase hot frequency | 10-15min hot tier. | |
| Separate tracking job | Lightweight listing-only snapshots. | |

**User's choice:** Adaptive scan timing based on listing expiry (user-proposed approach)
**Notes:** "In FC26, when you list a card it has to be listed for at least 1 hour, so when we get the current listings, we can see which card is under 1 hour and has the most minutes, so from that we know that after that many minutes we must get the new data. We should give us some minutes before to be safe and not miss cards." Example: 16 listings, youngest has 26 minutes left → scan in ~23 minutes.

---

## API Field Discovery

| Option | Description | Selected |
|--------|-------------|----------|
| User checks | User looks at API response. | |
| User knows fields | User describes available fields. | |
| Figure it out | Make test API call to discover fields. | ✓ |

**User's choice:** Figure it out (research task)
**Notes:** Currently only extracting buyNowPrice from liveAuctions. Need to discover auction IDs, expiry timestamps, etc.

---

## Claude's Discretion

- DB schema design for listing tracking tables
- Fingerprint matching algorithm details
- Bootstrapping period handling
- Migration strategy for existing data
- Purge job for rolling window
- Scanner restructuring for adaptive timing

## Deferred Ideas

None — discussion stayed within phase scope.
