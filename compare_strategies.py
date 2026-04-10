"""Compare best combos across time windows."""
import asyncio
import json
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from src.algo.engine import load_price_data, run_sweep_parallel
from src.algo.strategies import discover_strategies
from src.server.db import Base

import os
DB_URL = os.environ.get("DATABASE_URL", "postgresql+asyncpg://op_seller:op_seller@localhost:5432/op_seller")
BUDGET = 5_000_000
TARGET = ["crash_recovery", "delayed_crash", "saturday_massacre", "bracket_vol", "regime_crash"]


async def main():
    engine = create_async_engine(DB_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    strats = discover_strategies()
    classes = [strats[n] for n in TARGET if n in strats]

    all_results = {}

    for days in [30, 60, 90, 120, 180]:
        price_data, _ = await load_price_data(session_factory, min_price=10000, days=days)
        print(f"\n--- {days} DAYS ({len(price_data)} players) ---")
        results = run_sweep_parallel(classes, price_data, budget=BUDGET, max_workers=8)

        # Best per strategy by sharpe
        best = {}
        for r in results:
            name = r["strategy_name"]
            if name not in best or r["sharpe_ratio"] > best[name]["sharpe_ratio"]:
                best[name] = r

        for name in TARGET:
            if name in best:
                r = best[name]
                pnl = r["total_pnl"]
                ret = pnl / BUDGET * 100
                print(
                    f"  {name:<22} PnL: {pnl:>12,} ({ret:>6.1f}%)  "
                    f"Win: {r['win_rate']:.1%}  Trades: {r['total_trades']:>5}  "
                    f"Sharpe: {r['sharpe_ratio']:.3f}  MaxDD: {r['max_drawdown']:.1%}"
                )
                all_results.setdefault(name, {})[days] = {
                    "pnl": pnl, "return_pct": ret,
                    "win_rate": r["win_rate"], "trades": r["total_trades"],
                    "sharpe": r["sharpe_ratio"], "max_dd": r["max_drawdown"],
                    "params": r["params"],
                }

    # Summary table
    print("\n\n" + "=" * 120)
    print("ROBUSTNESS SUMMARY — Best Sharpe combo per strategy across all windows")
    print("=" * 120)
    print(f"{'Strategy':<22} {'30d PnL':>12} {'60d PnL':>12} {'90d PnL':>12} {'120d PnL':>12} {'180d PnL':>12} {'Consistent?':>12}")
    print("-" * 120)
    for name in TARGET:
        if name in all_results:
            vals = all_results[name]
            cols = []
            positive_count = 0
            for d in [30, 60, 90, 120, 180]:
                if d in vals:
                    pnl = vals[d]["pnl"]
                    cols.append(f"{pnl:>12,}")
                    if pnl > 0:
                        positive_count += 1
                else:
                    cols.append(f"{'N/A':>12}")
            consistent = "YES" if positive_count >= 4 else "NO"
            print(f"{name:<22} {'  '.join(cols)}  {consistent:>10}")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
