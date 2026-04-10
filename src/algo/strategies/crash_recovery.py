"""Crash recovery strategy — buy cards that dropped hard, sell on recovery.

FC cards crash during promos and market panics but often partially recover.
With daily data, we look for cards that have dropped >X% from their N-day
rolling high, then sell at a profit target or cut losses at a time stop.
The 5% EA tax means we need >5.26% recovery just to break even, so we
target larger recoveries (8-20%).
"""
from datetime import datetime
from collections import defaultdict
from src.algo.models import Signal, Portfolio
from src.algo.strategies.base import Strategy


class CrashRecoveryStrategy(Strategy):
    """Buy after crash from recent high, sell at profit target or time stop."""

    name = "crash_recovery"

    def __init__(self, params: dict):
        self.params = params
        self.lookback: int = params.get("lookback", 14)  # days to find peak
        self.crash_pct: float = params.get("crash_pct", 0.15)  # min drop from peak to buy
        self.profit_target: float = params.get("profit_target", 0.12)  # sell when up X% from buy
        self.stop_loss: float = params.get("stop_loss", 0.15)  # cut loss if drops X% more from buy
        self.max_hold_days: int = params.get("max_hold_days", 21)  # force sell after N days
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

            # Sell: profit target hit, stop loss hit, or held too long
            if (pct_change >= self.profit_target
                    or pct_change <= -self.stop_loss
                    or self._buy_days[ea_id] >= self.max_hold_days):
                signals.append(Signal(action="SELL", ea_id=ea_id, quantity=holding))
                self._buy_prices.pop(ea_id, None)
                self._buy_days.pop(ea_id, None)
        else:
            if len(history) >= self.lookback:
                recent_high = max(history[-self.lookback:])
                if recent_high > 0:
                    drop_pct = (recent_high - price) / recent_high
                    if drop_pct >= self.crash_pct:
                        buy_budget = int(portfolio.cash * self.position_pct)
                        quantity = buy_budget // price if price > 0 else 0
                        if quantity > 0:
                            signals.append(Signal(action="BUY", ea_id=ea_id, quantity=quantity))
                            self._buy_prices[ea_id] = price
                            self._buy_days[ea_id] = 0

        return signals

    def param_grid(self) -> list[dict]:
        combos = []
        for lookback in [7, 14, 21, 30]:
            for crash_pct in [0.10, 0.15, 0.20, 0.25]:
                for profit_target in [0.08, 0.12, 0.18]:
                    for stop_loss in [0.10, 0.15, 0.20]:
                        combos.append({
                            "lookback": lookback,
                            "crash_pct": crash_pct,
                            "profit_target": profit_target,
                            "stop_loss": stop_loss,
                            "max_hold_days": 21,
                            "position_pct": 0.02,
                        })
        return combos
