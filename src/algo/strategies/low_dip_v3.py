"""low_dip_v3 — widen dd gate (iter 88).

Iter 87 confirmed burn-in fix is no-op. v1 fired only 41 trades and captured 8
of 81 Tue/Wed/Thu band catalog opps.

Pre-analysis (.planning/analysis_tmp/iter88_preanalysis.py):
  - Tue/Wed/Thu band opps (whitelist+sph>=2): 81
  - v1 captured: 8 (10% of catalog opps; 19.5% of v1 trades hit the catalog)
  - NOT-captured 73: of those, lc<15 only adds 6 opps (10..15: 1 only)
  - NOT-captured 73: of those, dd<0.20 has 21 opps (0.15..0.20: 13)

  Variant baseline-eligible counts (catalog opps that pass gate at buy_hour):
    v1 baseline (lc>=15, dd>=0.20):     55
    A: lc>=10, dd>=0.20:                 55  (lc loosening = NO-OP)
    B: lc>=15, dd>=0.15:                 67  (+22% catalog)
    C: lc>=10, dd>=0.15:                 67  (lc still no-op)
    D: lc>=12, dd>=0.18:                 60

  Variant A is identical to baseline -> the lc gate is not the binder. The
  binder is dd_72h. Variant B pulls in 12 extra catalog opps; variant D adds
  only 5. Variant B wins on capture and median ROI is actually higher
  (28.9% vs 27.8%) because the dd 0.15-0.20 zone is dominated by mid-trend
  pullbacks that bounce harder than deep capitulations.

  v1 fires-per-catalog-catch ratio = 5.12 (portfolio cap + once-per-day fire
  saturate well before gate width). Predicted +12 catches -> ~+2-3 actual
  catches -> ~+$25-50k organic. v1 PnL/catch was $21.4k.

Hypothesis: v3 = v1 + dd_min 0.20->0.15. Should add 2-3 trades, raise win
rate slightly (median ROI better in 0.15-0.20 zone), preserve overlap (still
band-virgin).

Honesty:
  Predicted +$25-50k vs threshold +$20k. PASS but tight. The lc loosening
  variants A and C add nothing (catalog has only 1 opp in lc 10-15 zone), so
  no need to widen lc. Keep lc>=15 for noise hygiene.

Anti-overfit:
  dd 0.15-0.20 is shallower drawdown — risk is catching mid-bounce that
  reverts. Mitigation: keep all other v1 gates intact (lc thickness, weekday
  filter, smoothed exit, profit_target). If win rate drops below 55%, the
  shallow-dd zone is catching knives -> revert.

All else identical to v1.
"""
from __future__ import annotations

from src.algo.strategies.low_dip_v1 import LowDipV1Strategy


class LowDipV3Strategy(LowDipV1Strategy):
    name = "low_dip_v3"

    def param_grid(self) -> list[dict]:
        return self.param_grid_hourly()

    def param_grid_hourly(self) -> list[dict]:
        return [{
            "fire_hour_utc": 0,
            "min_price": 13000,
            "max_price": 20000,
            "dd_min": 0.15,        # widened from 0.20
            "dd_window_h": 72,
            "lc_min_avg_24h": 15.0,  # unchanged - lc loosening is a no-op
            "skip_friday": True,
            "skip_weekend": True,
            "skip_monday": True,
            "smooth_window_h": 3,
            "outlier_tol": 0.08,
            "profit_target": 0.20,
            "max_hold_h": 144,
            "smoothed_stop": 0.25,
            "stop_consec_hours": 14,
            "basket_size": 8,
            "qty_cap": 8,
            "notional_per_trade": 125_000,
            "max_positions": 8,
            "min_age_days": 7,
            "burn_in_h": 96,
        }]
