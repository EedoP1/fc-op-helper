# Quant trading methodology — and what it means for FC26

A research synthesis for a beginner who has spent ~80 iterations hand-crafting strategies on 27 days of FC26 Ultimate Team data, captured 0.7% of the theoretical profit ceiling, and is wondering whether the loop itself is the wrong tool.

Goal of this doc: explain how professional quants actually structure this kind of work, name the techniques you should learn, map your specific pitfalls to textbook ones, and give a concrete recommended pivot.

---

## 1. The standard quant research pipeline

Professional quant research is **not** "think of a rule, backtest it, ship if it works." That loop is what you have been doing, and it is exactly the loop Lopez de Prado calls "drink-driving research" — every backtest you look at biases the next strategy you write, until the only thing you've optimized for is the noise in your training window. ([Reasonable Deviations summary of AFML](https://reasonabledeviations.com/notes/adv_fin_ml/))

Real quant teams run an assembly line, with explicit handoffs:

```
                 +----------------------+
                 |  1. Data curation    |   clean ticks, sales, microstructure
                 +----------+-----------+
                            v
                 +----------------------+
                 |  2. Feature/signal   |   build a LIBRARY of features
                 |     research         |   (not strategies). Score each
                 +----------+-----------+   for predictive power in isolation.
                            v
                 +----------------------+
                 |  3. Labeling +       |   define "what would a profitable
                 |     supervised model |   trade have looked like" precisely,
                 +----------+-----------+   then fit a model to predict it.
                            v
                 +----------------------+
                 |  4. Validation       |   purged/embargoed CV, walk-forward,
                 |     (anti-overfit)   |   Deflated Sharpe, multi-trial penalty
                 +----------+-----------+
                            v
                 +----------------------+
                 |  5. Portfolio        |   bet sizing, capacity, correlation
                 |     construction     |   with existing strategies
                 +----------+-----------+
                            v
                 +----------------------+
                 |  6. Execution model  |   slippage, fill probability,
                 |     (TCA)            |   transaction cost analysis
                 +----------+-----------+
                            v
                 +----------------------+
                 |  7. Live + monitor   |   alpha decay tracking, regime
                 |                      |   detection, kill criteria
                 +----------------------+
```

A few things to internalise:

- **Most strategies die in stage 4 (validation) or stage 6 (execution)**, not in research. Backtests are easy to make pretty. Surviving purged cross-validation, the deflated Sharpe penalty, *and* a realistic execution cost model is hard. ([QuantStart on backtesting pitfalls](https://www.quantstart.com/articles/Successful-Backtesting-of-Algorithmic-Trading-Strategies-Part-II/), [BSIC backtest series](https://bsic.it/backtesting-series-episode-2-cross-validation-techniques/))
- **Stages 2 and 3 are decoupled on purpose.** Feature researchers don't know which strategy their feature will end up in; strategy/model builders compose features they didn't author. This kills the "I tweaked the gate until the PnL went up" feedback loop you've been stuck in.
- **A backtest is a verification step, not a research step.** Lopez de Prado's "Marcos' Second Law": *backtesting while researching is like drink driving*. Use feature importance, not PnL, to decide whether a feature is real. ([AFML notes](https://reasonabledeviations.com/notes/adv_fin_ml/))

You have been collapsing stages 2-7 into a single human-in-the-loop, and using backtest PnL as both the design signal *and* the verification signal. That's the structural reason the loop has stalled at 0.7% capture.

## 2. Which subfield of quant finance applies to FC26

Your problem is closest to **cross-sectional supervised return prediction in a low-frequency, high-friction, illiquid market**. Concretely:

- "Cross-sectional" because at every hour you're choosing *which cards* to buy, not whether to be long the whole market. The natural model output is a **rank**: of the ~10k cards in the 11k-200k range, which 100 have the highest expected forward return? This is the same setup as long/short equity factor investing. ([Building Cross-Sectional Strategies by Learning to Rank, Poh et al.](https://arxiv.org/pdf/2012.07149))
- "Low frequency" because you act hourly at most — closer to Fama-French daily factor models than to HFT. This is good news: you don't need to model order book microstructure.
- "High friction / illiquid" because of your ~9.6% loader drag. This is the same regime that statistical-arbitrage practitioners face in small-cap or off-the-run instruments, where transaction-cost analysis (TCA) often *is* the alpha. ([Trading Costs, Frazzini-Israel-Moskowitz](https://spinup-000d1a-wp-offload-media.s3.amazonaws.com/faculty/wp-content/uploads/sites/3/2021/08/Trading-Cost.pdf), [Slippage non-linear modeling](https://quantjourney.substack.com/p/slippage-a-comprehensive-analysis))

Canonical references that map directly onto your situation:

1. **Marcos Lopez de Prado, *Advances in Financial Machine Learning* (2018)** — the bible for "I have labeled financial data and want to fit an ML model without fooling myself." Chapters 3 (triple-barrier labels), 4 (sample weighting), 7 (purged CV), 11-14 (backtest statistics) are directly relevant.
2. **Stefan Jansen, *Machine Learning for Algorithmic Trading* (2nd ed.)** — much more practical / code-first. The gradient boosting and alpha factor library chapters are a good template. ([repo](https://github.com/stefan-jansen/machine-learning-for-trading))
3. **Poh, Lim, Zohren, Roberts, "Building Cross-Sectional Systematic Strategies By Learning to Rank" (2020)** — the exact framing for "given N assets, predict which ones will outperform." Reports ~3x Sharpe improvement vs. traditional ranking. ([arXiv](https://arxiv.org/pdf/2012.07149))

## 3. The "I have labels but can't predict forward" problem

You have **1,850 ground-truth profitable (card, buy_hour, sell_hour) triples** in a 27-day window. You can't predict them forward. This is the central tension in financial ML.

### Why this is hard

- **Concept drift / regime change.** Markets are non-stationary. Patterns that produced labels in week 1 may simply not exist in week 4 because of a promo release, a meta shift, or a price-anchor change. Alpha decays continuously; in equities even seconds of latency can cost ~6-10% of edge. ([Maven Securities on alpha decay](https://www.mavensecurities.com/alpha-decay-what-does-it-look-like-and-what-does-it-mean-for-systematic-traders/))
- **Selection bias.** With 80+ strategies tested, the chance that *one* of them beat the rest by luck alone is overwhelming. The Deflated Sharpe Ratio quantifies exactly this: even if every strategy has true Sharpe = 0, the best of N trials will typically post a positive Sharpe in-sample. ([Bailey & Lopez de Prado, Deflated Sharpe Ratio](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf))
- **Look-ahead via overlapping labels.** Your trades have hours-long windows, which means a label at time t shares information with labels at t±H. Standard k-fold CV leaks. ([Purged cross-validation, Wikipedia](https://en.wikipedia.org/wiki/Purged_cross-validation))
- **The bid-ask spread eats 90%+ of small signals.** This is the loader drag. It's not a quirk; it's the dominant fact about your market and is the dominant fact about *any* illiquid market. Quants routinely find that strategies with 15% gross annual return collapse to ~0% net after realistic costs. ([Backtesting traps](https://www.luxalgo.com/blog/backtesting-traps-common-errors-to-avoid/))

### Named techniques you should learn (in order of expected value for FC26)

1. **Triple-Barrier labeling.** For each (card, hour) candidate, define three barriers: profit-take (e.g., +20%), stop-loss (e.g., -8%), and time-out (e.g., 72h). The label is which barrier was hit first. This is much truer to your actual buy-and-hold-then-list mechanic than "did the price go up?" ([Hudson & Thames on triple barrier](https://hudsonthames.org/does-meta-labeling-add-to-signal-efficacy-triple-barrier-method/))
2. **Meta-labeling.** Build a primary model that says "this card looks like it might pop" with high *recall* (catches most real opps but lots of false positives). Then build a secondary classifier whose only job is "given the primary fired, is this one of the real ones?" Lopez de Prado's data shows this dramatically improves precision and Sharpe even when raw return drops slightly. **This is exactly the right shape for your problem** — your hand-crafted strategies are weak primary models with no meta-classifier. ([Meta-Labeling, Wikipedia](https://en.wikipedia.org/wiki/Meta-Labeling), [Hudson & Thames meta-labeling](https://hudsonthames.org/does-meta-labeling-add-to-signal-efficacy-triple-barrier-method/))
3. **Purged + embargoed k-fold CV (or Combinatorial Purged CV).** Required because your labels overlap in time. Without it, every cross-validated number you produce is contaminated. ([CPCV paper summary](https://towardsai.net/p/l/the-combinatorial-purged-cross-validation-method))
4. **Deflated Sharpe Ratio.** When you've tried 80 strategies, the apparent Sharpe of your best one needs to be deflated for selection bias. You can apply this *retroactively* to your iter1-iter80 history and it will probably tell you several "winners" are noise. ([DSR paper](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf))
5. **Walk-forward analysis.** Train on weeks 1-2, test on week 3. Slide the window. Strategies must hold up across folds, not just on the full sample. ([WFV practical guide](https://medium.com/@ahmedfahad04/understanding-walk-forward-validation-in-time-series-analysis-a-practical-guide-ea3814015abf))
6. **Regime detection (HMM or k-means on volatility/volume features).** Different market regimes (post-promo dump, mid-week lull, weekend pump) almost certainly have different generating processes. A single rule fitted across all regimes is fitting an average that exists nowhere. ([Market Regime Detection with HMM, QuantStart](https://www.quantstart.com/articles/market-regime-detection-using-hidden-markov-models-in-qstrader/))

## 4. The pitfalls you have already hit (mapped to the textbook names)

| Your symptom | Textbook name | What practitioners do |
|---|---|---|
| 80+ iterations, "almost all fail" | **Multiple-testing / selection bias** | Apply Deflated Sharpe; report results with N-trials penalty; pre-register hypotheses ([DSR](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf)) |
| Loader drag eats most signals | **Transaction cost dominates alpha** | Build a TCA model first; design strategies whose *gross* edge clears TCA by 2-3x; non-linear slippage models ([Trading Costs](https://spinup-000d1a-wp-offload-media.s3.amazonaws.com/faculty/wp-content/uploads/sites/3/2021/08/Trading-Cost.pdf)) |
| One rule applied to all 1,850 opps | **Aggregation across regimes** | Cluster opps by causal subtype; build per-cluster models; or feed regime label as a model feature ([HMM regime detection](https://www.quantstart.com/articles/market-regime-detection-using-hidden-markov-models-in-qstrader/)) |
| Captured strategies aren't even in the 1,850 catalog | **Wrong target distribution / objective mismatch** | Define the label *first*, then build features to predict *that* label. Don't let the strategy define what it predicts. ([Triple barrier](https://hudsonthames.org/does-meta-labeling-add-to-signal-efficacy-triple-barrier-method/)) |
| 27 days of data, 80 strategies | **Overfitting on small sample** | Lasso/elastic net to shrink features; bagging with `max_samples` set by label uniqueness; cross-validation is non-negotiable ([AFML notes](https://reasonabledeviations.com/notes/adv_fin_ml/), [supervised learning small sample](https://link.springer.com/article/10.1007/s42521-021-00046-2)) |
| Optimizing single PnL number | **Single-metric tunnel vision** | Industry standard: report Sharpe + Calmar + max drawdown + Profit Factor + capacity simultaneously; reject strategies with Sharpe < 1 net of cost; quant funds want > 2 ([QuantStart on Sharpe](https://www.quantstart.com/articles/Sharpe-Ratio-for-Algorithmic-Trading-Performance-Measurement/), [Quantra metrics overview](https://blog.quantinsti.com/performance-metrics-risk-metrics-optimization/)) |
| Researcher (you) writes strategy AND evaluates it | **Backtest-as-research** (Marcos' 2nd Law) | Separate roles: feature library, model fitting, evaluation. You can simulate this with your own discipline by building features in one repo/notebook and only seeing PnL in another. |

## 5. Recommended pivot — ranked by expected value

**Headline:** Stop hand-crafting strategies. Your 80-iter loop is a slow Bayesian optimizer where you, the human, are the noisy gradient. A laptop running gradient-boosted trees over a feature library will explore that space in an afternoon and make fewer of the bias mistakes you've been making. The catalog of 1,850 opps is your training label set — that is *enormously* valuable and you've been ignoring it.

Here is the EV-ranked path forward.

### EV-rank 1: Build a labeled supervised pipeline from scratch (~1-2 weeks)

Concrete steps:

1. **Define labels with the triple-barrier method.** For every (card, hour t) tuple in your dataset, label it by what would have happened in the next 72h: profit-take +20% net of EA tax (label=1), stop-loss -8% (label=-1), or time-out (label=0). You will get ~10x more labels than your hand-curated 1,850 — and the labels are well-defined statistical objects, not heuristics.
2. **Build a feature library** of 50-200 features per (card, hour). Examples:
   - Price-relative-to-X: distance to 24h/72h/7d min, max, median.
   - Liquidity: listings count, sales/hr, listings-to-sales ratio.
   - Momentum: 1h/6h/24h returns; rolling mean reversion z-score.
   - Cross-sectional rank: this card's price percentile within its rating bucket / position / promo.
   - Calendar: hour of day, day of week, hours-since-promo-release.
   - Regime: HMM state from market-wide volume + volatility.
   - Card metadata: rating, league, position, card_type one-hots.
3. **Fit a LightGBM ranker or binary classifier.** LightGBM/XGBoost dominate tabular financial data; deep learning rarely beats them at this scale. ([Stefan Jansen on boosting for trading](https://stefan-jansen.github.io/machine-learning-for-trading/12_gradient_boosting_machines/), [Regime-aware LightGBM walk-forward](https://www.mdpi.com/2079-9292/15/6/1334))
4. **Validate with purged-embargoed walk-forward.** Train on days 1-14, embargo day 15, test on days 16-21, etc. Report mean OOS Sharpe across folds, not the best fold.
5. **Add a meta-labeling layer.** Take the model's "buy" predictions, then train a second classifier on the harder question "given the model said buy, will it actually clear 20% net?" Use the meta-classifier's probability as the bet size.
6. **Model execution costs explicitly.** Encode the loader drag as a per-trade cost in the backtest. Strategies that don't clear 2x the drag get auto-rejected.

This is a real project — it's not a one-shot prompt — but it replaces your hand-crafted loop with a process that respects 60 years of accumulated quant lessons.

### EV-rank 2: Invest in execution-model improvements first (~3-5 days)

The 9.6% loader drag is the dominant economic fact of your problem. Even a perfect predictor of the 1,850 opps would be neutered by it. Specifically:

- Build a TCA model: for each card and hour, what is the *realistic* fillable buy price (something like 25th percentile of recent BIN listings) and the realistic sellable price (something like 75th percentile of recent sales, minus 5% tax)?
- Re-run your existing best strategy with that realistic model. If it still works, the strategy is real and your old backtest was just optimistic. If it dies, the loader drag was the killer and no amount of strategy tuning will fix it without execution-side improvements (better timing of buys, smarter listing-price laddering, etc.).
- Consider whether the right product is not "predict the trade" but "execute better" — i.e. shave the loader drag from 9.6% to 6% via better order placement, which is worth more than any new strategy you'll discover.

### EV-rank 3: Cluster the 1,850 opps into causal subtypes (~2-3 days)

Run k-means or a Gaussian mixture on features-of-the-opps (price tier, hours-since-promo, momentum at buy time, sales velocity, card type). You will likely find 3-7 clear clusters. Each cluster is a different *causal mechanism* (promo dump bounce, weekend pump, content-induced demand spike, etc.). Build per-cluster models. This alone will outperform any single-rule strategy because you are no longer averaging over incompatible processes.

### EV-rank 4 (lowest): Continue hand-crafting strategies

Don't. The 80-iter null streak is exactly what theory predicts: human-in-the-loop search over a noisy backtest landscape, with no penalty for multiple testing, on a 27-day window, under high transaction costs, will fail almost certainly. Each new iter is also burning precious "out-of-sample purity" because you are subtly using prior iter results to inform the next one.

## 6. Resources worth following up

Books / canonical references:

- **Lopez de Prado, *Advances in Financial Machine Learning*** — required reading. Chapters 3, 4, 7, 11-14 most relevant. ([summary notes](https://reasonabledeviations.com/notes/adv_fin_ml/))
- **Lopez de Prado, *Machine Learning for Asset Managers*** — shorter, more recent, focused on the validation problem.
- **Stefan Jansen, *Machine Learning for Algorithmic Trading*** — practical, code-first, good for implementation. ([repo](https://github.com/stefan-jansen/machine-learning-for-trading))
- **Ernie Chan, *Algorithmic Trading: Winning Strategies and Their Rationale*** — beginner-friendly, gives a lot of intuition for why simple strategies fail.

Papers:

- **Bailey & Lopez de Prado, "The Deflated Sharpe Ratio"** ([SSRN PDF](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf)) — apply this to your iter1-iter80 history.
- **Poh, Lim, Zohren, Roberts, "Building Cross-Sectional Systematic Strategies By Learning to Rank"** ([arXiv](https://arxiv.org/pdf/2012.07149)) — direct template for ranking N cards.
- **Frazzini, Israel, Moskowitz, "Trading Costs"** ([PDF](https://spinup-000d1a-wp-offload-media.s3.amazonaws.com/faculty/wp-content/uploads/sites/3/2021/08/Trading-Cost.pdf)) — empirical work on how transaction costs eat alpha.
- **WorldQuant's "101 Formulaic Alphas"** ([overview](https://stefan-jansen.github.io/machine-learning-for-trading/24_alpha_factor_library/)) — concrete examples of features.

Blogs / communities:

- **Hudson & Thames** ([hudsonthames.org](https://hudsonthames.org/)) — practitioner site, lots of meta-labeling and triple-barrier worked examples.
- **QuantStart** ([quantstart.com](https://www.quantstart.com/)) — beginner-to-intermediate explainers, especially backtesting and HMM.
- **Microsoft Qlib** ([github.com/microsoft/qlib](https://github.com/microsoft/qlib)) — open-source quant platform. Contains many alpha factor implementations and a walk-forward backtester you could adapt.

Courses:

- **Coursera: "Machine Learning and Reinforcement Learning in Finance"** (NYU/Halperin).
- **WorldQuant University** — free MSc-level material on factor research.

---

*Bottom line: you're at iteration 80 of a process that, at industry standards, should have produced 0 deployed strategies and instead produced 1 (floor_buy_v19_ext). That's actually the right outcome for this loop. The next 80 iterations of the same loop will produce roughly 1 more. The next 80 hours invested in a proper supervised pipeline could produce 10x that — because you're finally using the 1,850 labels you've been ignoring.*
