"""Print bar-scorecard for a strategy from backtest_results.json.

Bars (per the autonomous-loop brief):
  1. Total organic PnL >= +$100k (organic = exclude sell_time on data boundary)
  2. BOTH W14 and W15 organic PnL >= +$20k each
  3. Win rate (organic) >= 55%
  4. |corr| with promo_dip_buy_filtered_results.json <= 0.30
  AND: force-sell-at-boundary share < 30% of total PnL
  AND: unfiltered PnL < 50% of filtered PnL  (need both runs)

Usage:
  python scripts/verdict.py <strategy_name>
    Reads <name>_filtered_results.json and (optional) backtest_results.json
    if no filtered file exists.
"""
import json
import math
import os
import sys
from collections import defaultdict
from datetime import datetime


BUDGET = 1_000_000
PROMO_REF = "promo_dip_buy_filtered_results.json"


def load(path: str, strategy_name: str | None = None) -> dict:
    with open(path) as f:
        results = json.load(f)
    if strategy_name:
        match = [r for r in results if r["strategy_name"] == strategy_name]
        if not match:
            raise SystemExit(f"No {strategy_name} in {path}")
        return max(match, key=lambda r: r["total_pnl"])
    return results[0]


def iso_week(dt_str: str) -> tuple[int, int]:
    dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
    y, w, _ = dt.isocalendar()
    return (y, w)


def _detect_boundary(trades: list[dict]) -> str:
    """Find the latest sell_time date in trades (YYYY-MM-DD). Boundary = trades
    sold on that date (force-sell at data end)."""
    if not trades:
        return ""
    latest = max(t["sell_time"][:10] for t in trades)
    return latest


_BOUNDARY_DATE: str = ""


def is_boundary(sell_time: str) -> bool:
    """Trade is force-sell at end if sell_time is on the latest data day.
    `_BOUNDARY_DATE` is set dynamically by `report()` from each run's trades."""
    return bool(_BOUNDARY_DATE) and sell_time.startswith(_BOUNDARY_DATE)


def correlation(my_trades, ref_path=PROMO_REF) -> float | None:
    if not os.path.exists(ref_path):
        return None
    try:
        with open(ref_path) as f:
            ref = json.load(f)[0]
        ref_trades = ref.get("trades", [])
    except Exception:
        return None

    def daily(trades):
        c = defaultdict(int)
        for t in trades:
            c[t["buy_time"][:10]] += 1
        return c

    me = daily(my_trades)
    them = daily(ref_trades)
    days = sorted(set(me.keys()) | set(them.keys()))
    if len(days) < 3:
        return None
    xs = [me[d] for d in days]
    ys = [them[d] for d in days]
    mx = sum(xs) / len(xs)
    my_ = sum(ys) / len(ys)
    num = sum((x - mx) * (y - my_) for x, y in zip(xs, ys))
    dx2 = sum((x - mx) ** 2 for x in xs)
    dy2 = sum((y - my_) ** 2 for y in ys)
    if dx2 == 0 or dy2 == 0:
        return 0.0
    return num / math.sqrt(dx2 * dy2)


def report(strategy_name: str, filtered_path: str, unfiltered_path: str | None = None):
    fr = load(filtered_path, strategy_name)
    trades = fr.get("trades", [])

    global _BOUNDARY_DATE
    _BOUNDARY_DATE = _detect_boundary(trades)
    if _BOUNDARY_DATE:
        print(f"  (detected boundary: {_BOUNDARY_DATE})")

    organic = [t for t in trades if not is_boundary(t["sell_time"])]
    boundary = [t for t in trades if is_boundary(t["sell_time"])]
    organic_pnl = sum(t["net_profit"] for t in organic)
    boundary_pnl = sum(t["net_profit"] for t in boundary)
    total_pnl = organic_pnl + boundary_pnl

    organic_wins = sum(1 for t in organic if t["net_profit"] > 0)
    win_rate_organic = organic_wins / len(organic) if organic else 0.0

    week_pnl_org = defaultdict(int)
    for t in organic:
        week_pnl_org[iso_week(t["sell_time"])] += t["net_profit"]

    corr = correlation(organic + boundary)
    outlier_pnl = sum(t["net_profit"] for t in trades
                      if t["buy_price"] > 0 and (t["sell_price"] / t["buy_price"] - 1) > 0.30)
    outlier_share = (outlier_pnl / total_pnl) if total_pnl else 0.0
    boundary_share = (boundary_pnl / total_pnl) if total_pnl else 0.0

    print(f"\n=== VERDICT: {strategy_name} (filtered: {filtered_path}) ===")
    print(f"  total trades: {len(trades)} (organic {len(organic)}, boundary {len(boundary)})")
    print(f"  total PnL: {total_pnl:+,}    organic: {organic_pnl:+,}    boundary: {boundary_pnl:+,}")
    print(f"  organic win rate: {win_rate_organic:.1%}")

    print("  Per-week ORGANIC PnL:")
    for (y, w) in sorted(week_pnl_org.keys()):
        v = week_pnl_org[(y, w)]
        print(f"    {y}-W{w:02d}: {v:+,} ({v/BUDGET:.1%})")
    print(f"  outlier share (gross >30%): {outlier_share:.1%}")
    print(f"  force-sell boundary share: {boundary_share:.1%}")
    if corr is not None:
        print(f"  |corr| vs promo_dip_buy_filtered: {corr:+.3f}")

    print("\n  --- BARS ---")
    bar1 = organic_pnl >= 100_000
    bar2_w14 = week_pnl_org.get((2026, 14), 0) >= 20_000
    bar2_w15 = week_pnl_org.get((2026, 15), 0) >= 20_000
    bar3 = win_rate_organic >= 0.55
    bar4 = corr is None or abs(corr) <= 0.30
    bar_force = boundary_share < 0.30

    print(f"  [{'PASS' if bar1 else 'FAIL'}] 1. Organic PnL >= +$100k: {organic_pnl:+,}")
    print(f"  [{'PASS' if bar2_w14 else 'FAIL'}] 2a. W14 organic >= +$20k: {week_pnl_org.get((2026,14),0):+,}")
    print(f"  [{'PASS' if bar2_w15 else 'FAIL'}] 2b. W15 organic >= +$20k: {week_pnl_org.get((2026,15),0):+,}")
    print(f"  [{'PASS' if bar3 else 'FAIL'}] 3. Win rate organic >= 55%: {win_rate_organic:.1%}")
    if corr is not None:
        print(f"  [{'PASS' if bar4 else 'FAIL'}] 4. |corr| <= 0.30: {corr:+.3f}")
    print(f"  [{'PASS' if bar_force else 'FAIL'}] X. Force-sell share < 30%: {boundary_share:.1%}")

    if unfiltered_path and os.path.exists(unfiltered_path):
        try:
            uf = load(unfiltered_path, strategy_name)
            ratio = uf["total_pnl"] / fr["total_pnl"] if fr["total_pnl"] else float('inf')
            print(f"\n  Unfiltered PnL: {uf['total_pnl']:+,}    ratio uf/f: {ratio:+.2f}")
            print(f"  [{'PASS' if uf['total_pnl'] < fr['total_pnl'] * 0.5 else 'FAIL'}] X. unfiltered < 50% of filtered (edge is liquidity-driven)")
        except Exception as e:
            print(f"\n  unfiltered load failed: {e}")

    all_pass = bar1 and bar2_w14 and bar2_w15 and bar3 and bar4 and bar_force
    print(f"\n  ALL BARS: {'PASS' if all_pass else 'FAIL'}")
    return all_pass


if __name__ == "__main__":
    name = sys.argv[1]
    fpath = f"{name}_filtered_results.json"
    if not os.path.exists(fpath):
        fpath = "backtest_results.json"
    upath = f"{name}_unfiltered_results.json"
    report(name, fpath, upath)
