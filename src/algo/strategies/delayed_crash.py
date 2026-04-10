"""Delayed crash entry — wait for the cascade to end before buying.

Data shows lag-1 autocorrelation of +0.157: down days predict more down days.
After a >5% market crash, the next day is still negative 10/14 times.
But cards that drop >=15% in a week recover 8%+ within 21 days 62% of the time.

The edge: don't buy the first dip. Track consecutive down days per card.
Only buy AFTER seeing the first up day following a multi-day crash.
This filters out cards still in freefall and catches the reversal.

Tax survival: targeting 12-18% recoveries from crash bottom, well above 5.26%.
"""
from datetime import datetime
from collections import defaultdict
from src.algo.models import Signal, Portfolio
from src.algo.strategies.base import Strategy


class DelayedCrashStrategy(Strategy):
    """Buy after crash stops cascading (first up day after N down days)."""

    name = "delayed_crash"

    def __init__(self, params: dict):
        self.params = params
        self.lookback: int = params.get("lookback", 14)
        self.crash_pct: float = params.get("crash_pct", 0.15)
        self.min_down_days: int = params.get("min_down_days", 2)
        self.profit_target: float = params.get("profit_target", 0.12)
        self.stop_loss: float = params.get("stop_loss", 0.15)
        self.max_hold_days: int = params.get("max_hold_days", 21)
        self.position_pct: float = params.get("position_pct", 0.02)
        self._history: dict[int, list[int]] = defaultdict(list)
        self._buy_prices: dict[int, int] = {}
        self._buy_days: dict[int, int] = defaultdict(int)

    def on_tick(
        self, ea_id: int, price: int, timestamp: datetime, portfolio: Portfolio,
    ) -> list[Signal]:
        history = self._history[ea_id]
        history.append(price)

        holding = portfolio.holdings(ea_id)
        signals = []

        if holding > 0:
            self._buy_days[ea_id] += 1
            buy_price = self._buy_prices.get(ea_id, price)
            pct_change = (price - buy_price) / buy_price if buy_price > 0 else 0

            if (pct_change >= self.profit_target
                    or pct_change <= -self.stop_loss
                    or self._buy_days[ea_id] >= self.max_hold_days):
                signals.append(Signal(action="SELL", ea_id=ea_id, quantity=holding))
                self._buy_prices.pop(ea_id, None)
                self._buy_days.pop(ea_id, None)
        else:
            if len(history) >= self.lookback:
                recent_high = max(history[-self.lookback:])
                if recent_high > 0 and price > 0:
                    drop_pct = (recent_high - price) / recent_high

                    # Must have crashed enough from recent high
                    if drop_pct >= self.crash_pct:
                        # Count consecutive down days ending at previous tick
                        # Current price is UP from yesterday = reversal signal
                        if len(history) >= self.min_down_days + 2:
                            consecutive_down = 0
                            for i in range(len(history) - 2, 0, -1):
                                if history[i] < history[i - 1]:
                                    consecutive_down += 1
                                else:
                                    break

                            # Reversal: had N+ down days, today is UP
                            today_up = price > history[-2]
                            if consecutive_down >= self.min_down_days and today_up:
                                buy_budget = int(portfolio.cash * self.position_pct)
                                quantity = buy_budget // price
                                if quantity > 0:
                                    signals.append(Signal(
                                        action="BUY", ea_id=ea_id, quantity=quantity,
                                    ))
                                    self._buy_prices[ea_id] = price
                                    self._buy_days[ea_id] = 0

        return signals

    def param_grid(self) -> list[dict]:
        combos = []
        for lookback in [7, 14, 21]:
            for crash_pct in [0.10, 0.15, 0.20]:
                for min_down_days in [2, 3, 4]:
                    for profit_target in [0.08, 0.12, 0.18]:
                        combos.append({
                            "lookback": lookback,
                            "crash_pct": crash_pct,
                            "min_down_days": min_down_days,
                            "profit_target": profit_target,
                            "stop_loss": 0.15,
                            "max_hold_days": 21,
                            "position_pct": 0.02,
                        })
        return combos
