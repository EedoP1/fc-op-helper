# Iter 94 — TRUE Stack PnL Audit

## 1. Engine semantics

`python -m src.algo run --strategy NAME` runs ONE strategy in a sandbox with its OWN
$1M budget. The `--all` flag iterates strategies and runs each as a SEPARATE,
independent backtest — there is NO built-in cross-strategy portfolio mode in the
engine. To measure a real shared-portfolio stack, you must write a "combo"
strategy (e.g. `stack_audit.py`) that internally allocates a single $1M across
all sub-strategies, owns the sub-strategy instances, and routes their signals
through one `Portfolio`. Same-tick BUY conflicts on the same `ea_id` are
de-duplicated by priority; per-arm cash counters enforce capital constraints.

## 2. Per-strategy single-run filtered PnL (--min-sph 2, $1M each)

| Strategy             |     PnL | Trades | Win%  |
|----------------------|--------:|-------:|------:|
| floor_buy_v19        | 480,350 |     19 | 78.9% |
| floor_buy_v19_ext    | 214,912 |     22 | 27.3% |
| floor_buy_v24        | 257,375 |     16 | 81.2% |
| post_dump_v15        | -50,100 |     34 | 44.1% |
| daily_trend_dip_v5   | 142,899 |     31 | 67.7% |
| monday_rebound_v1    |  90,700 |      7 | 71.4% |
| mid_dip_v2           | 143,597 |     35 | 74.3% |
| low_dip_v3           | 198,159 |     44 | 63.6% |
| **(stack subtotal)** |**1,477,892** | **208** | — |
| floor_buy_v27 (new)  |  94,674 |     18 | 33.3% |
| floor_buy_v31 (top)  | 511,975 |     20 | 85.0% |

## 3. Sum-of-singles (paper ceiling, 8 stack members)

**$1,477,892** — this is what the "$1.49M paper stack" claim reduces to once
you stop double-counting Δstack figures across iterations.

## 4. TRUE combined PnL — equal_split mode ($125k × 8 = $1M shared)

**$70,022 / 43 trades / 60.5% win / 99.1% max DD** — REALISTIC single-account number.

## 5. TRUE combined PnL — no_contention mode ($1M × 8, no capital constraint)

**-$118,227 / 46 trades / 39.1% win / 100% max DD** — isolates pure trade-overlap
penalty. Worse than equal_split because per-arm $1M lets weak arms (post_dump_v15,
floor_buy_v19_ext) over-position into losers without their normal cash starvation.

## 6. Stacking penalty (sum − equal_split)

**$1,477,892 − $70,022 = $1,407,870 destroyed** by capital sharing + trade-level
priority conflicts. Equal_split keeps only **4.7%** of the paper sum.

## 7. Trade-overlap penalty (sum − no_contention)

**$1,477,892 − (−$118,227) = $1,596,119 destroyed** purely by shared trade ledger
(no cash constraint). Trade-overlap alone wipes out >100% of the paper claim,
because dedup hands trades to the *priority* arm — which is sometimes a worse
fit for that specific entry than the arm whose single-run optimization picked
it. Per-arm $1M also enables losing-arm over-allocation.

## 8. Verdict on the "$1.49M paper stack" claim

**The $1.49M figure was ~95% artifact, ~5% real.**

- Real, measurable when running together: **$70,022** (equal_split, realistic
  single-account).
- Artifact from naive single-run summing: **$1,419,978** (95.3% of claim).

The paper number was constructed by adding Δstack/single-run PnLs across
iterations as if each strategy ran in its own universe — which is exactly what
`--strategy NAME` measures and exactly what real deployment is NOT. Once
forced through a single $1M with shared trade ledger + priority dedup +
per-arm cash:
- ~30% of the trades vanish (43 vs 208 sum).
- The cash constraint pins each arm to $125k, which kills the harvest-mode
  big-qty entries that drove `floor_buy_v19`'s $480k single-run.
- Trade conflicts hand winners to higher-priority arms and force lower-priority
  arms into worse alternatives or no entry.

The Δstack accounting from prior iters is not a portfolio measurement — it's
a marginal-contribution-to-an-imaginary-pool measurement.

## 9. Recommendation

- **Best single strategy under filter:** `floor_buy_v31` at **$511,975** (20 trades, 85% win).
- **TRUE combined stack (equal_split):** **$70,022** — 7.3× WORSE than running
  `floor_buy_v31` alone with the full $1M.
- **To exceed $1.49M:** equal_split would need to actually hit $1.49M. It hits
  $70k. **Refuted decisively.** Stop chasing wider stacks; chase deeper single
  strategies. `floor_buy_v31` alone outperforms the entire 8-arm stack by
  **+$441,953**.

**Action:** Deploy `floor_buy_v31` solo with full $1M. The stack thesis is dead
in its current composition. Future stacking experiments must start from the
combo measurement, not the singles sum.
