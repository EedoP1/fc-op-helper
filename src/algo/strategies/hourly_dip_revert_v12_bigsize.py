"""v12 with qty_cap=6 — isolate position-sizing vs partial-sell contribution."""
import logging
from datetime import datetime
from collections import defaultdict, deque

from src.algo.models import Signal, Portfolio
from src.algo.strategies.base import Strategy
from src.algo.strategies.hourly_dip_revert_v12 import HourlyDipRevertV12Strategy

logger = logging.getLogger(__name__)


class HourlyDipRevertV12BigsizeStrategy(HourlyDipRevertV12Strategy):
    name = "hourly_dip_revert_v12_bigsize"

    def param_grid_hourly(self) -> list[dict]:
        base = {
            "median_window_h": 24,
            "smooth_window_h": 3,
            "outlier_tol": 0.05,
            "dip_pct": 0.05,
            "confirm_hours": 2,
            "profit_target": 0.25,
            "stop_loss": 0.15,
            "max_hold_h": 48,
            "min_price": 10000,
            "max_price": 80000,
            "max_positions": 8,
            "min_age_days": 7,
            "burn_in_h": 72,
        }
        return [{**base, "qty_cap": c} for c in (10,)]
