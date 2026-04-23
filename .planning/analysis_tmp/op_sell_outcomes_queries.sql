-- OP Sell Outcomes Analysis — SQL used by op_sell_outcomes_analysis.py
-- All queries operate on the LIVE PostgreSQL DB (op_seller@localhost:5432).
-- The SQLite file op_seller.db is 0 bytes / abandoned.
--
-- Core "slot instance" definition: one per `bought` trade_record event.
-- Between that bought and the next 'sold' event (or the next 'bought', whichever comes first),
-- any 'listed' / 'expired' / 'sold' events belong to that instance.
--
-- Note on event semantics (verified empirically on the live DB):
--   - Every bought is followed within ~1s by a 'listed' event (333/333 cases in a 7d sample).
--   - Most relists produce a new 'listed' event, but some do NOT (transfer-list-cycle.ts
--     calls relistAll() inline without reporting; automation-loop RELIST actions do report).
--   - `expired` is reported reliably on every TL sweep that sees an expired item.
--   - Therefore, use expired count (not listed count) as the reliable proxy for relist cycles.

-- =============================================================================
-- Data map / schema probe (Step 0)
-- =============================================================================
SELECT outcome, action_type, COUNT(*) AS n,
       MIN(recorded_at) AS first_seen, MAX(recorded_at) AS last_seen
  FROM trade_records
 GROUP BY outcome, action_type
 ORDER BY outcome, action_type;

-- =============================================================================
-- 1) Build slot instances: one row per bought event.
-- =============================================================================
-- Uses window function LEAD to find the NEXT bought for the same ea_id; the close
-- of this instance is the first 'sold' before that next bought.
WITH bought AS (
  SELECT ea_id, recorded_at AS bought_at, price AS buy_price, id AS bought_id,
         LEAD(recorded_at) OVER (PARTITION BY ea_id ORDER BY recorded_at, id) AS next_bought_at
    FROM trade_records
   WHERE outcome = 'bought'
     AND recorded_at >= NOW() - INTERVAL '7 days'
),
first_listed AS (
  SELECT b.ea_id, b.bought_at, MIN(t.recorded_at) AS listed_at, MIN(t.price) AS listed_price
    FROM bought b
    JOIN trade_records t
      ON t.ea_id = b.ea_id
     AND t.outcome = 'listed'
     AND t.recorded_at >= b.bought_at
     AND t.recorded_at < COALESCE(b.next_bought_at, NOW() + INTERVAL '1 year')
     AND t.recorded_at < b.bought_at + INTERVAL '5 minutes'  -- initial list is within seconds
   GROUP BY b.ea_id, b.bought_at
),
first_sold AS (
  SELECT b.ea_id, b.bought_at, MIN(t.recorded_at) AS sold_at, MIN(t.price) AS sold_price
    FROM bought b
    JOIN trade_records t
      ON t.ea_id = b.ea_id
     AND t.outcome = 'sold'
     AND t.recorded_at >= b.bought_at
     AND t.recorded_at < COALESCE(b.next_bought_at, NOW() + INTERVAL '1 year')
   GROUP BY b.ea_id, b.bought_at
),
num_expires AS (
  SELECT b.ea_id, b.bought_at, COUNT(*) AS expires_count
    FROM bought b
    JOIN trade_records t
      ON t.ea_id = b.ea_id
     AND t.outcome = 'expired'
     AND t.recorded_at >= b.bought_at
     AND t.recorded_at < COALESCE(b.next_bought_at, NOW() + INTERVAL '1 year')
   GROUP BY b.ea_id, b.bought_at
)
SELECT b.ea_id, b.bought_at, b.buy_price,
       fl.listed_at, fl.listed_price,
       fs.sold_at, fs.sold_price,
       COALESCE(ne.expires_count, 0) AS expires_count,
       CASE
         WHEN fs.sold_at IS NOT NULL AND COALESCE(ne.expires_count,0) = 0 THEN 'fast_sell'
         WHEN fs.sold_at IS NOT NULL                                   THEN 'eventual_sell'
         ELSE 'never_sold'
       END AS bucket,
       EXTRACT(EPOCH FROM (COALESCE(fs.sold_at, NOW()) - b.bought_at)) / 3600 AS hours_tied_up
  FROM bought b
  LEFT JOIN first_listed fl ON fl.ea_id = b.ea_id AND fl.bought_at = b.bought_at
  LEFT JOIN first_sold   fs ON fs.ea_id = b.ea_id AND fs.bought_at = b.bought_at
  LEFT JOIN num_expires  ne ON ne.ea_id = b.ea_id AND ne.bought_at = b.bought_at
 ORDER BY b.bought_at DESC;

-- =============================================================================
-- 2) Join slot instances to pre-scan scorer rationale and player metadata.
-- =============================================================================
-- For each slot instance, pick the most recent player_scores row with
-- scored_at <= bought_at (the recommendation that caused the buy).
WITH slot_instances AS (
  -- ... same CTE as query 1 ...
  SELECT 1 AS placeholder_use_query_1
)
SELECT si.*, p.name, p.rating, p.position, p.league, p.nation, p.card_type,
       p.sales_per_hour AS p_sales_per_hour,
       p.listings_per_hour AS p_listings_per_hour,
       p.listing_count AS p_listing_count,
       ps.margin_pct, ps.op_sales, ps.total_sales, ps.op_ratio,
       ps.expected_profit, ps.efficiency, ps.sales_per_hour AS s_sales_per_hour,
       ps.scorer_version, ps.expected_profit_per_hour
  FROM slot_instances si
  LEFT JOIN players p ON p.ea_id = si.ea_id
  LEFT JOIN LATERAL (
      SELECT * FROM player_scores
       WHERE ea_id = si.ea_id AND scored_at <= si.bought_at
       ORDER BY scored_at DESC LIMIT 1
  ) ps ON true;

-- =============================================================================
-- 3) Baseline counts per bucket + sell-through (Q1)
-- =============================================================================
SELECT bucket,
       COUNT(*) AS n,
       AVG(EXTRACT(EPOCH FROM (sold_at - bought_at))/3600) AS mean_hours_to_sell,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (sold_at - bought_at))/3600) AS median_hours_to_sell,
       SUM(buy_price) AS gross_cost,
       SUM(sold_price) AS gross_revenue,
       SUM(sold_price * 0.95 - buy_price) AS net_profit
  FROM slot_instances
 GROUP BY bucket;

-- =============================================================================
-- 4) PPH distribution across SOLD slots (Q2)
-- =============================================================================
SELECT percentile_cont(0.25) WITHIN GROUP (ORDER BY pph) AS p25,
       percentile_cont(0.50) WITHIN GROUP (ORDER BY pph) AS p50,
       percentile_cont(0.75) WITHIN GROUP (ORDER BY pph) AS p75,
       percentile_cont(0.90) WITHIN GROUP (ORDER BY pph) AS p90,
       MIN(pph) AS min_pph, MAX(pph) AS max_pph,
       AVG(pph) AS mean_pph
  FROM (
    SELECT (sold_price * 0.95 - buy_price) /
           GREATEST(EXTRACT(EPOCH FROM (sold_at - bought_at))/3600, 0.01) AS pph
      FROM slot_instances WHERE bucket IN ('fast_sell','eventual_sell')
  ) x;

-- =============================================================================
-- 5) Attribute profile: sell-through lift vs PPH lift (Q3/Q4)
-- =============================================================================
-- Generalized pattern — repeat for each attribute (rating bucket, position, card_type, ...):
SELECT attr_value,
       COUNT(*) AS n,
       SUM(CASE WHEN bucket IN ('fast_sell','eventual_sell') THEN 1 ELSE 0 END)::float / COUNT(*) AS sell_through,
       AVG(CASE WHEN bucket IN ('fast_sell','eventual_sell')
                 THEN (sold_price*0.95 - buy_price) /
                      GREATEST(EXTRACT(EPOCH FROM (sold_at - bought_at))/3600, 0.01)
           END) AS mean_pph_sold,
       AVG((COALESCE(sold_price,0)*0.95 - buy_price) / GREATEST(hours_tied_up, 0.01)) AS capital_weighted_pph
  FROM (
    SELECT si.*, /* expression yielding attr_value */ p.card_type AS attr_value
      FROM slot_instances si JOIN players p USING (ea_id)
  ) x
 GROUP BY attr_value HAVING COUNT(*) >= 5
 ORDER BY mean_pph_sold DESC NULLS LAST;

-- =============================================================================
-- 6) Repeat offenders (Q7)
-- =============================================================================
SELECT ea_id,
       COUNT(*) AS slot_instances,
       SUM(CASE WHEN bucket = 'never_sold' THEN 1 ELSE 0 END) AS never_sold_count,
       SUM(expires_count) AS total_expires,
       SUM(CASE WHEN bucket = 'never_sold' THEN buy_price ELSE 0 END) AS capital_stuck_now,
       SUM(buy_price * hours_tied_up) AS capital_hours_wasted,
       SUM(CASE WHEN bucket IN ('fast_sell','eventual_sell') THEN sold_price*0.95 - buy_price ELSE 0 END) AS net_profit
  FROM slot_instances
 GROUP BY ea_id
 ORDER BY capital_hours_wasted DESC LIMIT 20;

-- =============================================================================
-- 7) Dead-on-arrival signals for never-sold slots (Q6)
-- =============================================================================
-- For each never-sold slot, check for warning signs from the pre-scan context.
SELECT si.ea_id, si.buy_price, si.bought_at, ps.total_sales, ps.op_sales,
       ps.sales_per_hour AS s_sales_per_hour, p.listing_count AS listings_at_scan,
       (ps.total_sales < 10) AS low_total_sales_flag,
       (p.listing_count > COALESCE(ps.sales_per_hour,0) * 24) AS high_listings_vs_sph_flag,
       CASE WHEN max_snap_price > 0 AND si.buy_price > max_snap_price * 1.1 THEN TRUE ELSE FALSE END AS recent_price_spike_flag
  FROM slot_instances si
  LEFT JOIN players p ON p.ea_id = si.ea_id
  LEFT JOIN LATERAL (
      SELECT * FROM player_scores WHERE ea_id = si.ea_id AND scored_at <= si.bought_at
       ORDER BY scored_at DESC LIMIT 1
  ) ps ON true
  LEFT JOIN LATERAL (
      SELECT MAX(current_lowest_bin) AS max_snap_price FROM market_snapshots
       WHERE ea_id = si.ea_id AND captured_at BETWEEN si.bought_at - INTERVAL '48 hours' AND si.bought_at
  ) ms ON true
 WHERE si.bucket = 'never_sold';
