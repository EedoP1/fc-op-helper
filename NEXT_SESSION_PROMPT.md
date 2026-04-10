# Algo Trading Strategy Session — Build Profitable FC26 Strategies

## The Task

Build trading strategies for FC26 Ultimate Team cards that are profitable on 10K+ cards with a 5M budget over 90+ days. We have a backtesting engine and 200 days of daily price data. Current best strategy (crash_recovery) makes money but has a razor-thin 1.29x gain/loss ratio and 89% max drawdown. We need smarter strategies grounded in the data patterns below.

## The Engine

Strategy base class: `src/algo/strategies/base.py` — implement `on_tick(ea_id, price, timestamp, portfolio) -> list[Signal]` and `param_grid() -> list[dict]`. Signal is BUY or SELL with ea_id and quantity. Portfolio has `.cash`, `.holdings(ea_id)`, `.positions`. Engine calls on_tick once per card per day (data is daily). 5% EA tax is auto-applied on sells. 500 sells/day cap. Auto-discovery: just drop a .py in `src/algo/strategies/`. Run with `python -m src.algo run --strategy NAME --days 90 --budget 5000000 --min-price 10000`.

See existing strategies for patterns: `src/algo/strategies/crash_recovery.py`, `oversold_bounce.py`, `vol_mean_reversion.py`, `weekday_swing.py`, `mean_reversion.py`, `momentum.py`, `bollinger.py`, `weekly_cycle.py`.

## Data We Have

- **727K daily price points** from FUTBIN, Sep 2025 → Apr 2026 (200 days)
- **4,739 unique cards** by futbin_id, **2,245 cards** with prices ≥10K
- **1 data point per card per day** at midnight UTC — no hourly data

## What The Data Actually Shows

### 1. The market trends DOWN on average
- **Mean daily return: -0.15%** — most cards lose value over time (power creep)
- **33% of cards lost >50% of value** over their lifetime, only 31% gained >50%
- **More expensive = faster decline**: 500K+ cards lose -1.39%/day avg, 10-20K cards gain +0.86%/day
- **Implication**: Any strategy that just holds cards is swimming upstream. Need fast entries and exits.

### 2. Saturday is a massacre, Wednesday is recovery day
Day-of-week average returns across all 210K observations:
- **Saturday: -3.14%** (by far worst — promos drop, liquidation)
- **Friday: -0.19%** (pre-weekend selloff)
- **Sunday: -0.44%** (still negative, hangover)
- **Monday: +0.43%** (recovery starts)
- **Tuesday: +0.23%** (continued recovery)
- **Wednesday: +1.83%** (strongest day — WL prep? content hype?)
- **Thursday: +0.38%** (slight positive)

The Fri→Sat drop averages **-3.14%** (median -1.80%, only 37% of cards go up). The Sat→Wed recovery averages **+2.24%** (median +1.88%, 58.7% positive). But Fri→Wed net is **-0.67%** — the recovery doesn't fully offset the crash, and tax eats the rest.

### 3. Crashes cascade — don't buy the first dip
- After a >5% market crash day, **the next day is negative 10 out of 14 times**
- Fri -7% → Sat -7% → Sun +2.3% (double crash then bounce)
- Consecutive down days do NOT predict bounces: after 3+ down days, the next day is still negative on average
- **Lag-1 autocorrelation: +0.157** — down days predict more down days, up days predict more up days (momentum, not mean reversion at 1-day scale)
- After >10% up day: next day avg +1.56% (momentum continues)
- After >10% down day: next day avg **-3.40%** (crash continues!)

### 4. Crash-and-recovery IS real, but needs patience
When a card drops ≥15% in a week:
- **62.4%** recover 8%+ within 21 days
- **54.9%** recover 12%+ within 21 days
- **45.5%** recover 18%+ within 21 days
- **31.1%** never recover (just keep falling)
- **Average days to best recovery: 11.4**

The 1-in-3 no-recovery rate is what kills naive crash-buying. Need filtering.

### 5. The worst market days (biggest crashes)
Every major crash is a **Saturday**: Mar 7 (-13.6%), Dec 20 (-12.5%), Oct 4 (-8.2%), Nov 29 (-8.0%), Feb 14 (-6.4%), Apr 4 (-5.6%). All Saturdays except Oct 1 (Wed, -8.1%) and Nov 28 (Fri, -6.3%).

Best days cluster on **Wednesday**: Mar 18 (+5.8%), Feb 4 (+4.7%), Dec 3 (+4.3%), Feb 11 (+3.9%).

### 6. Volatility varies wildly by price bracket
| Bracket | Daily Vol | >10% Moves | Avg Return |
|---------|-----------|------------|------------|
| 10-20K | 7.4% | 9.7% | +0.86% |
| 20-50K | 10.2% | 20.1% | -0.39% |
| 50-100K | 9.2% | 13.3% | -0.52% |
| 100-200K | 9.3% | 15.3% | -0.90% |
| 200-500K | 7.9% | 11.3% | -1.15% |
| 500K+ | 7.1% | 8.9% | -1.39% |

20-50K is the sweet spot: highest volatility (most tradeable swings), moderate negative drift. 500K+ has the worst risk/reward.

### 7. Current best strategy performance (crash_recovery)
Best combo across time windows (lb=7, crash=0.15, tp=0.18, sl=0.2):

| Window | PnL | Return | Win% | Trades |
|--------|-----|--------|------|--------|
| 30 days | +1.76M | 35.3% | 68.3% | 1,048 |
| 60 days | +1.06M | 21.1% | 65.5% | 995 |
| 90 days | +2.79M | 55.8% | 66.2% | 2,620 |
| 120 days | +2.66M | 53.3% | 66.7% | 2,988 |
| 180 days | +1.43M | 28.6% | 64.4% | 3,097 |

Problems: 89% max drawdown, 1.29x gain/loss ratio (barely positive edge), heavily concentrated in recent volatile period.

## What Strategies Should Exploit

Based on the data, these are the real edges:

1. **Sat crash → Wed recovery cycle** — -3.14% Sat, +1.83% Wed. But can't just buy Sat sell Wed (net -0.67% + 5% tax = loss). Need to filter for cards with STRONGER-than-average weekly patterns, or combine with crash magnitude filtering.

2. **Multi-day crash recovery with delayed entry** — Don't buy the first dip (autocorrelation says it keeps going). Wait 2-3 days AFTER a big drop, then buy. The 62% recovery rate at 8%+ is real, but the 31% total-loss rate needs filtering out.

3. **Price bracket optimization** — 20-50K cards have the best risk/reward. Strategies should weight positions toward this range. Avoid 500K+ cards entirely.

4. **Market regime awareness** — When the broad market is crashing (many cards down simultaneously), DON'T buy. Wait for the cascade to end. 10/14 post-crash days are still negative.

5. **Momentum continuation** — Short-term momentum is real (autocorrelation +0.157). After a >10% up day, the next day averages +1.56%. This could work as an entry signal if combined with proper exit discipline.

## Constraints & Anti-Patterns

- **5% EA tax** — need >5.26% gross gain just to break even
- **Daily data only** — can't trade intraday, one decision per card per day
- **Power creep** — most cards trend down, holding = losing
- **No short selling** — can only profit from price increases
- **Oversold bounce is a myth at 1-day scale** — consecutive down days predict MORE down days, not bounces
- **Don't look at 30-day results in isolation** — last session's "winners" were flukes. Validate across 30/60/90/120/180 day windows.

## What To Build

Design 3-5 new strategies based on the patterns above. For each strategy:
1. Explain the edge in terms of the data (cite specific numbers)
2. Explain why the 5% tax doesn't kill it
3. Implement it
4. Run across multiple time windows
5. Compare to crash_recovery baseline

Focus on strategies that have ROBUST edge (work across multiple windows), not the highest single-window PnL. A strategy making +500K consistently across all windows is better than one making +2M on 30 days and -500K on 180 days.
