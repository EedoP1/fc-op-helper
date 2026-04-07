"""SQLAlchemy ORM tables for the algo trading backtester."""
from datetime import datetime
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import Integer, BigInteger, Float, DateTime, String, Text, Index
from src.server.db import Base


class PriceHistory(Base):
    """Daily price data per player from FUTBIN historical scrape."""

    __tablename__ = "price_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ea_id: Mapped[int] = mapped_column(Integer, index=True)
    futbin_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime)
    price: Mapped[int] = mapped_column(Integer)

    __table_args__ = (
        Index("ix_price_history_ea_id_timestamp", "ea_id", "timestamp"),
    )


class BacktestResult(Base):
    """Output of a single backtest run (one strategy + one param combo)."""

    __tablename__ = "backtest_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy_name: Mapped[str] = mapped_column(String(100))
    params: Mapped[str] = mapped_column(Text)  # JSON-encoded param dict
    started_budget: Mapped[int] = mapped_column(Integer)
    final_budget: Mapped[int] = mapped_column(BigInteger)
    total_pnl: Mapped[int] = mapped_column(BigInteger)
    total_trades: Mapped[int] = mapped_column(Integer)
    win_rate: Mapped[float] = mapped_column(Float)
    max_drawdown: Mapped[float] = mapped_column(Float)
    sharpe_ratio: Mapped[float] = mapped_column(Float)
    run_at: Mapped[datetime] = mapped_column(DateTime)

    __table_args__ = (
        Index("ix_backtest_results_strategy", "strategy_name"),
    )
