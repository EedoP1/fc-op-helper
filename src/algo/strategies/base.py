"""Abstract base class for trading strategies."""
from abc import ABC, abstractmethod
from datetime import datetime
from src.algo.models import Signal, Portfolio


class Strategy(ABC):
    """All strategies must implement this interface."""

    name: str

    @abstractmethod
    def __init__(self, params: dict):
        ...

    @abstractmethod
    def on_tick(
        self, ea_id: int, price: int, timestamp: datetime, portfolio: Portfolio,
    ) -> list[Signal]:
        """Process a single price tick. Return BUY/SELL signals or empty list.

        Called once per player per hour. The strategy only sees current and
        past data — never future prices.
        """
        ...

    @abstractmethod
    def param_grid(self) -> list[dict]:
        """Return all parameter combinations to sweep."""
        ...
