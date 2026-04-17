"""Analyze a backtest_results.json for per-week PnL, weekday distribution, and
correlation with promo_dip_buy.

Usage:
    python scripts/analyze_backtest.py [strategy_name]

If strategy_name is given, pick that entry from backtest_results.json (best combo
if multiple). Otherwise analyze the first entry (which is sorted by sharpe).

Compares against promo_dip_buy_trades.json if it exists (a saved snapshot of
the winning promo_dip_buy trades). That file is produced by running:

    python -m src.algo run --strategy promo_dip_buy --budget 1000000 --days 0
    cp backtest_results.json promo_dip_buy_results.json
"""
import json
import sys
import math
from collections import defaultdict
from datetime import datetime

def load_trades(path, strategy_name=None):
    with open(path) as f:
        results = json.load(f)
    if strategy_name:
        matching = [r for r in results if r["strategy_name"] == strategy_name]
        if not matching:
            raise SystemExit(f"No results for strategy '{strategy_name}'")
        # pick best pnl combo
        best = max(matching, key=lambda r: r["total_pnl"])
    else:
        best = results[0]
    return best

def iso_week_key(dt_str):
    dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
    y, w, _ = dt.isocalendar()
    return (y, w)

def weekday(dt_str):
    dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
    return dt.weekday()

def analyze(result, budget=1_000_000):
    trades = result.get("trades", [])
    name = result["strategy_name"]
    params = result["params"]

    print(f"\n{'='*72}")
    print(f"Strategy: {name}")
    print(f"Params:   {params}")
    print(f"Budget:   {budget:,}  Final: {result['final_budget']:,}  "
          f"PnL: {result['total_pnl']:+,}  Trades: {len(trades)}  "
          f"Win%: {result['win_rate']:.1%}  "
          f"MaxDD: {result['max_drawdown']:.1%}")
    print(f"{'='*72}")

    # per-ISO-week PnL (by sell_time)
    week_pnl = defaultdict(int)
    week_trades = defaultdict(int)
    week_weekdays = defaultdict(set)
    for t in trades:
        w = iso_week_key(t["sell_time"])
        week_pnl[w] += t["net_profit"]
        week_trades[w] += 1
        week_weekdays[w].add(weekday(t["buy_time"]))
        week_weekdays[w].add(weekday(t["sell_time"]))

    print(f"\nPer-ISO-week PnL (by sell_time):")
    print(f"  {'Year-Wk':<12} {'PnL':>14} {'% of budget':>12} {'Trades':>8} {'Distinct WDs':>14}")
    target = int(budget * 0.25)
    hitting = 0
    for w in sorted(week_pnl.keys()):
        pnl = week_pnl[w]
        pct = pnl / budget
        wd_names = sorted(week_weekdays[w])
        hit_mark = " OK" if pnl >= target else ""
        print(f"  {w[0]}-W{w[1]:02d}    {pnl:>+14,} {pct:>11.1%}  "
              f"{week_trades[w]:>7}   {len(wd_names)} weekdays{hit_mark}")
        if pnl >= target:
            hitting += 1

    # weekday distribution
    wd_counts = defaultdict(int)
    for t in trades:
        wd_counts[weekday(t["buy_time"])] += 1
    print(f"\nBuy weekday distribution (0=Mon..6=Sun):")
    for d in range(7):
        print(f"  {['Mon','Tue','Wed','Thu','Fri','Sat','Sun'][d]}: {wd_counts[d]}")

    distinct_wd_per_week = [len(week_weekdays[w]) for w in week_weekdays]
    if distinct_wd_per_week:
        avg_wd = sum(distinct_wd_per_week) / len(distinct_wd_per_week)
        print(f"\nAvg distinct weekdays per week (buy+sell): {avg_wd:.1f}")

    return week_pnl, week_trades, trades

def correlation_with_promo(my_trades, promo_file="promo_dip_buy_results.json"):
    """Pearson correlation between daily trade counts of this strategy vs promo_dip_buy."""
    try:
        with open(promo_file) as f:
            promo_result = json.load(f)[0]
        promo_trades = promo_result.get("trades", [])
    except (FileNotFoundError, IndexError, KeyError):
        print(f"\n(No {promo_file} reference — run promo_dip_buy and copy the results to compute correlation.)")
        return None

    # daily buy count per day
    def daily_counts(trades):
        c = defaultdict(int)
        for t in trades:
            day = t["buy_time"][:10]
            c[day] += 1
        return c

    me = daily_counts(my_trades)
    them = daily_counts(promo_trades)
    days = sorted(set(me.keys()) | set(them.keys()))
    if len(days) < 3:
        print(f"\n(Not enough days to compute correlation; need ≥3.)")
        return None

    xs = [me[d] for d in days]
    ys = [them[d] for d in days]
    mx = sum(xs) / len(xs)
    my_ = sum(ys) / len(ys)
    num = sum((x - mx) * (y - my_) for x, y in zip(xs, ys))
    dx2 = sum((x - mx) ** 2 for x in xs)
    dy2 = sum((y - my_) ** 2 for y in ys)
    if dx2 == 0 or dy2 == 0:
        print(f"\nCorrelation with promo_dip_buy: N/A (zero variance)")
        return 0.0
    r = num / math.sqrt(dx2 * dy2)
    print(f"\nCorrelation with promo_dip_buy (daily buy counts): {r:+.3f}")
    return r


def main():
    results_path = "backtest_results.json"
    strategy_name = sys.argv[1] if len(sys.argv) > 1 else None
    result = load_trades(results_path, strategy_name)
    _, _, trades = analyze(result)
    correlation_with_promo(trades)


if __name__ == "__main__":
    main()
