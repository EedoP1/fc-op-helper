# Quick Task 260326-wac: FUTBIN Health Monitor - Context

**Gathered:** 2026-03-26
**Status:** Ready for planning

<domain>
## Task Boundary

Build a FUTBIN health monitor that runs hourly, picks 10 random players from the DB, fetches their FUTBIN sales/listing data, and compares against our DB to track accuracy. Full audit: sold/expired rates, prices, listing counts, time coverage, margin distribution.

</domain>

<decisions>
## Implementation Decisions

### FUTBIN Data Access
- **Simple CLI script** — no browser automation, no scheduling
- Use httpx to fetch FUTBIN pages and parse HTML tables
- FUTBIN sales page table has: Date, Listed for, Sold for, EA Tax, Net Price, Type
- Sold listings have non-zero "Sold for" column; expired have 0
- Run manually: `python -m src.health_check`

### Health Metrics Scope
- **Full audit** comparing:
  - Sold/expired sell-through rate (flag if delta > 10%)
  - Sale price accuracy (do our snapshot_sales prices match FUTBIN's sold prices?)
  - Listing price ranges and averages
  - Listing counts (how many observations vs FUTBIN listings)
  - Time coverage overlap
  - Margin distribution (what % of listings are at each OP margin tier)

### FUTBIN ID Mapping
- Use **FUTBIN search** to resolve ea_id → futbin_id at runtime (only for the 10 selected players per run)
- Do NOT pre-scrape all 1,783 players — FUTBIN will rate-limit/block
- Cache resolved futbin_ids in the players table (add `futbin_id` column) so repeat checks don't need re-search
- Gradually builds up the mapping over time (10 new per hour)

### Claude's Discretion
- Output: Store results in a DB table (`health_checks`) + log summary
- Scheduling: Use APScheduler (already in the project) to run hourly
- Player selection: Random 10 from active players with sufficient observations

</decisions>

<specifics>
## Specific Ideas

- FUTBIN URL requires futbin_id (different from ea_id) — need a mapping approach
- We proved the JS extraction works via `document.querySelectorAll('table')[0]` in Chrome
- The table has 500 rows per page
- Results should include per-player scores and an overall health score

</specifics>

<canonical_refs>
## Canonical References

- FUTBIN sales page: `https://www.futbin.com/26/sales/{futbin_id}/{name}?platform=ps`
- Earlier browser extraction confirmed table structure: Date, Listed for, Sold for, EA Tax, Net Price, Type

</canonical_refs>
