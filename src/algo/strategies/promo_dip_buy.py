"""Promo dip buy — hybrid strategy for Friday promo cards.

Every Friday promo release crashes 50-75% over hours/days as supply floods in.
Then they rally as supply dries up and demand returns.

Two buy layers:
  1. Strong signal: buy anytime a promo card's 12h median trend >= 21%
  2. Snapshot: at 176h after release (Saturday morning after cards leave packs),
     rank all remaining promo cards by trend, buy top N

Sell: after 48h hold delay, sell when 24h median trend drops below 5%
for 3 consecutive hours.

Works on hourly market_snapshots data.
"""
from datetime import datetime
from collections import defaultdict
from src.algo.models import Signal, Portfolio
from src.algo.strategies.base import Strategy


class PromoDipBuyStrategy(Strategy):
    """Buy promo cards via strong signal + snapshot ranking, sell on trend stall."""

    name = "promo_dip_buy"

    def __init__(self, params: dict):
        self.params = params
        # Entry: buy when 12h median trend >= this (strong signal layer)
        self.trend_pct: float = params.get("trend_pct", 0.21)
        # How many hours back for buy trend comparison
        self.trend_lookback: int = params.get("trend_lookback", 12)
        # Snapshot layer: hours after release to take snapshot
        self.snapshot_hour: int = params.get("snapshot_hour", 176)
        # Snapshot layer: buy top N cards by trend
        self.snapshot_top_n: int = params.get("snapshot_top_n", 3)
        # Snapshot layer: minimum trend floor for snapshot candidates
        self.snapshot_floor: float = params.get("snapshot_floor", 0.0)
        # Sell: lookback window for sell trend comparison
        self.sell_lookback: int = params.get("sell_lookback", 24)
        # Sell: trend threshold — sell when median trend drops to this
        self.sell_trend_pct: float = params.get("sell_trend_pct", 0.05)
        # Sell: consecutive hours trend must be below threshold
        self.sell_confirm_hours: int = params.get("sell_confirm_hours", 3)
        # Sell: minimum hold time before sell check starts
        self.stop_delay_hours: int = params.get("stop_delay_hours", 48)
        # Max hold time in hours before force-sell
        self.max_hold_hours: int = params.get("max_hold_hours", 336)
        # Price filters
        self.min_price: int = params.get("min_price", 12000)
        self.max_price: int = params.get("max_price", 61000)
        # Position sizing: max % of portfolio per card
        self.max_position_pct: float = params.get("max_position_pct", 0.10)

        # Internal state
        self._old_cards: set[int] = set()
        self._created_at_map: dict[int, datetime] = {}
        self._promo_ids: set[int] = set()
        self._first_seen_ts: dict[int, datetime] = {}
        self._first_seen_price: dict[int, int] = {}
        self._tracked_low: dict[int, int] = {}
        self._history: dict[int, list[tuple[datetime, int]]] = defaultdict(list)
        self._bought: set[int] = set()
        self._peak_prices: dict[int, int] = {}
        self._buy_ts: dict[int, datetime] = {}
        # Batch tracking for snapshot layer
        self._batch_map: dict[int, str] = {}  # ea_id -> batch_key
        self._batch_snapshot_done: dict[str, bool] = {}
        self._batch_created: dict[str, datetime] = {}
        # Sell confirmation counter
        self._sell_stall_count: dict[int, int] = defaultdict(int)

    def set_existing_ids(self, existing_ids: set[int]):
        self._old_cards = existing_ids

    def set_created_at_map(self, created_at_map: dict):
        self._created_at_map = created_at_map
        # Identify promo batches: 10+ cards created in the same hour on a Friday
        hour_buckets: dict[tuple, list[int]] = defaultdict(list)
        for ea_id, created in created_at_map.items():
            cr = created.replace(tzinfo=None) if created.tzinfo else created
            if cr.weekday() == 4:  # Friday
                bucket = (cr.year, cr.month, cr.day, cr.hour)
                hour_buckets[bucket].append(ea_id)
        self._promo_ids = set()
        for bucket, ids in hour_buckets.items():
            if len(ids) >= 10:
                self._promo_ids.update(ids)
                key = f"{bucket[0]}-{bucket[1]:02d}-{bucket[2]:02d}"
                for ea_id in ids:
                    self._batch_map[ea_id] = key
                self._batch_created[key] = datetime(*bucket[:3], bucket[3])

    def on_tick_batch(
        self, ticks: list[tuple[int, int]], timestamp: datetime, portfolio: Portfolio,
    ) -> list[Signal]:
        signals = []
        ts_clean = timestamp.replace(tzinfo=None) if timestamp.tzinfo else timestamp

        # ── Track state + SELL logic ──

        for ea_id, price in ticks:
            if ea_id not in self._created_at_map:
                continue

            # Track first seen and history
            if ea_id not in self._first_seen_ts:
                self._first_seen_ts[ea_id] = timestamp
                self._first_seen_price[ea_id] = price
                self._tracked_low[ea_id] = price

            self._history[ea_id].append((timestamp, price))

            if price < self._tracked_low[ea_id]:
                self._tracked_low[ea_id] = price

            holding = portfolio.holdings(ea_id)

            if holding > 0:
                # Update peak
                if price > self._peak_prices.get(ea_id, 0):
                    self._peak_prices[ea_id] = price

                buy_ts = self._buy_ts.get(ea_id)
                hold_hours = 0
                if buy_ts:
                    bt_clean = buy_ts.replace(tzinfo=None) if buy_ts.tzinfo else buy_ts
                    hold_hours = (ts_clean - bt_clean).total_seconds() / 3600

                sell = False

                # Sell when 24h median trend drops below 5% for 3 consecutive hours
                sell_history = self._history[ea_id]
                sell_lb = self.sell_lookback
                if hold_hours >= self.stop_delay_hours and len(sell_history) >= sell_lb * 2:
                    sell_recent = sorted([p for _, p in sell_history[-sell_lb:]])
                    sell_older = sorted([p for _, p in sell_history[-sell_lb*2:-sell_lb]])
                    sell_recent_med = sell_recent[len(sell_recent) // 2]
                    sell_older_med = sell_older[len(sell_older) // 2]
                    if sell_older_med > 0:
                        sell_trend = (sell_recent_med - sell_older_med) / sell_older_med
                        if sell_trend <= self.sell_trend_pct:
                            self._sell_stall_count[ea_id] += 1
                        else:
                            self._sell_stall_count[ea_id] = 0

                        if self._sell_stall_count[ea_id] >= self.sell_confirm_hours:
                            sell = True

                # Force sell after max hold
                if hold_hours >= self.max_hold_hours:
                    sell = True

                if sell:
                    signals.append(Signal(action="SELL", ea_id=ea_id, quantity=holding))
                    self._peak_prices.pop(ea_id, None)
                    self._buy_ts.pop(ea_id, None)
                    self._sell_stall_count.pop(ea_id, None)

        # ── BUY LAYER 1: Strong signal (21%+ trend anytime) ──

        strong_buys = []
        for ea_id, price in ticks:
            if ea_id not in self._created_at_map:
                continue
            if ea_id in self._bought or portfolio.holdings(ea_id) > 0:
                continue
            if ea_id not in self._promo_ids:
                continue

            created = self._created_at_map[ea_id]
            cr_clean = created.replace(tzinfo=None) if created.tzinfo else created
            days_since = (ts_clean - cr_clean).days
            if days_since < 0 or days_since > 13:
                continue

            if not (self.min_price <= price <= self.max_price):
                continue

            history = self._history[ea_id]
            lb = self.trend_lookback
            if len(history) < lb * 2:
                continue

            recent_prices = sorted([p for _, p in history[-lb:]])
            older_prices = sorted([p for _, p in history[-lb*2:-lb]])
            recent_med = recent_prices[len(recent_prices) // 2]
            older_med = older_prices[len(older_prices) // 2]
            if older_med <= 0:
                continue
            trend = (recent_med - older_med) / older_med

            if trend >= self.trend_pct:
                strong_buys.append((ea_id, price))

        # ── BUY LAYER 2: Snapshot (top N at 176h after release) ──

        snapshot_buys = []
        for batch_key, batch_created in self._batch_created.items():
            if self._batch_snapshot_done.get(batch_key):
                continue

            hours_since = (ts_clean - batch_created).total_seconds() / 3600
            if hours_since < self.snapshot_hour:
                continue

            # This is the snapshot tick — rank all batch cards now
            self._batch_snapshot_done[batch_key] = True

            already_bought_ids = self._bought | {eid for eid, _ in strong_buys}

            candidates = []
            for ea_id, price in ticks:
                if self._batch_map.get(ea_id) != batch_key:
                    continue
                if ea_id in already_bought_ids or portfolio.holdings(ea_id) > 0:
                    continue
                if ea_id not in self._promo_ids:
                    continue
                if not (self.min_price <= price <= self.max_price):
                    continue

                history = self._history.get(ea_id, [])
                lb = self.trend_lookback
                if len(history) < lb * 2:
                    continue

                recent_prices = sorted([p for _, p in history[-lb:]])
                older_prices = sorted([p for _, p in history[-lb*2:-lb]])
                recent_med = recent_prices[len(recent_prices) // 2]
                older_med = older_prices[len(older_prices) // 2]
                if older_med <= 0:
                    continue
                trend = (recent_med - older_med) / older_med

                if trend < self.snapshot_floor:
                    continue

                candidates.append((ea_id, price, trend))

            # Rank by trend, buy top N
            candidates.sort(key=lambda x: x[2], reverse=True)
            for ea_id, price, trend in candidates[:self.snapshot_top_n]:
                snapshot_buys.append((ea_id, price))

        # ── Size and execute all buys ──

        potential_buys = strong_buys + snapshot_buys
        if potential_buys:
            sell_revenue = sum(
                next((p * sig.quantity * 95 // 100 for eid, p in ticks if eid == sig.ea_id), 0)
                for sig in signals if sig.action == "SELL"
            )
            available_cash = portfolio.cash + sell_revenue
            per_card = available_cash // len(potential_buys)

            if self.max_position_pct > 0:
                price_map = {eid: p for eid, p in ticks}
                portfolio_value = portfolio.cash + sell_revenue + sum(
                    price_map.get(pos.ea_id, pos.buy_price) * pos.quantity
                    for pos in portfolio.positions
                )
                max_spend = int(portfolio_value * self.max_position_pct)
                per_card = min(per_card, max_spend)

            for ea_id, price in potential_buys:
                quantity = per_card // price if price > 0 else 0
                if quantity > 0:
                    signals.append(Signal(action="BUY", ea_id=ea_id, quantity=quantity))
                    self._peak_prices[ea_id] = price
                    self._buy_ts[ea_id] = timestamp
                    self._bought.add(ea_id)

        return signals

    def param_grid(self) -> list[dict]:
        """Daily data grid — delegates to hourly grid."""
        return self.param_grid_hourly()

    def param_grid_hourly(self) -> list[dict]:
        """Hourly market_snapshots grid — winning params from backtesting."""
        return [{
            "trend_pct": 0.21,
            "trend_lookback": 12,
            "snapshot_hour": 176,
            "snapshot_top_n": 3,
            "snapshot_floor": 0.0,
            "sell_lookback": 24,
            "sell_trend_pct": 0.05,
            "sell_confirm_hours": 3,
            "stop_delay_hours": 48,
            "max_hold_hours": 336,
            "min_price": 12000,
            "max_price": 61000,
            "max_position_pct": 0.10,
        }]
