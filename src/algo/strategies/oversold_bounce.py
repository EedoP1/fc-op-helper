"""Oversold bounce strategy — buy after consecutive down days.

When a card drops for N consecutive days, it's often panic selling that
overshoots fair value. Buy on the Nth down day and sell on the first
uptick or at a profit/loss target. Works best on liquid cards where
panic is temporary.
"""
from datetime import datetime
from collections import defaultdict
from src.algo.models import Signal, Portfolio
from src.algo.strategies.base import Strategy


class OversoldBounceStrategy(Strategy):
    """Buy after N consecutive down days, sell on first uptick or target."""

    name = "oversold_bounce"

    def __init__(self, params: dict):
        self.params = params
        self.down_days: int = params.get("down_days", 3)  # consecutive drops to trigger buy
        self.min_total_drop: float = params.get("min_total_drop", 0.08)  # min cumulative drop over the streak
        self.profit_target: float = params.get("profit_target", 0.10)
        self.stop_loss: float = params.get("stop_loss", 0.12)
        self.sell_on_uptick: bool = params.get("sell_on_uptick", True)  # sell on first green day
        self.position_pct: float = params.get("position_pct", 0.02)
        self._history: dict[int, list[int]] = defaultdict(list)
        self._buy_prices: dict[int, int] = {}
        self._prev_price: dict[int, int] = {}

    def on_tick(
        self, ea_id: int, price: int, timestamp: datetime, portfolio: Portfolio,
    ) -> list[Signal]:
        history = self._history[ea_id]
        history.append(price)

        holding = portfolio.holdings(ea_id)
        signals = []

        if holding > 0:
            buy_price = self._buy_prices.get(ea_id, price)
            pct_change = (price - buy_price) / buy_price if buy_price > 0 else 0
            prev = self._prev_price.get(ea_id, price)

            # Sell on uptick (price went up from yesterday), profit target, or stop loss
            if pct_change >= self.profit_target or pct_change <= -self.stop_loss:
                signals.append(Signal(action="SELL", ea_id=ea_id, quantity=holding))
                self._buy_prices.pop(ea_id, None)
            elif self.sell_on_uptick and price > prev and pct_change > 0:
                # Only sell on uptick if we're in profit (above breakeven after tax)
                signals.append(Signal(action="SELL", ea_id=ea_id, quantity=holding))
                self._buy_prices.pop(ea_id, None)
        else:
            n = self.down_days + 1  # need N+1 prices to see N drops
            if len(history) >= n:
                recent = history[-n:]
                # Check all consecutive drops
                all_down = all(recent[i] < recent[i - 1] for i in range(1, len(recent)))
                if all_down:
                    total_drop = (recent[0] - recent[-1]) / recent[0] if recent[0] > 0 else 0
                    if total_drop >= self.min_total_drop:
                        buy_budget = int(portfolio.cash * self.position_pct)
                        quantity = buy_budget // price if price > 0 else 0
                        if quantity > 0:
                            signals.append(Signal(action="BUY", ea_id=ea_id, quantity=quantity))
                            self._buy_prices[ea_id] = price

        self._prev_price[ea_id] = price
        return signals

    def param_grid(self) -> list[dict]:
        combos = []
        for down_days in [3, 4, 5]:
            for min_total_drop in [0.06, 0.10, 0.15]:
                for profit_target in [0.08, 0.12, 0.18]:
                    for sell_on_uptick in [True, False]:
                        combos.append({
                            "down_days": down_days,
                            "min_total_drop": min_total_drop,
                            "profit_target": profit_target,
                            "stop_loss": 0.12,
                            "sell_on_uptick": sell_on_uptick,
                            "position_pct": 0.02,
                        })
        return combos
