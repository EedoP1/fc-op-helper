"""SQLAlchemy ORM table definitions for the persistent scanner."""
from datetime import datetime
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, Integer, Float, DateTime, Boolean, Index, Text, ForeignKey
from src.server.db import Base


class PlayerRecord(Base):
    """ORM model for player metadata and scan scheduling."""

    __tablename__ = "players"

    ea_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    rating: Mapped[int] = mapped_column(Integer)
    position: Mapped[str] = mapped_column(String(10))
    nation: Mapped[str] = mapped_column(String(100))
    league: Mapped[str] = mapped_column(String(100))
    club: Mapped[str] = mapped_column(String(100))
    card_type: Mapped[str] = mapped_column(String(50))
    scan_tier: Mapped[str] = mapped_column(String(10), default="normal")
    last_scanned_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    next_scan_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    listing_count: Mapped[int] = mapped_column(Integer, default=0)
    sales_per_hour: Mapped[float] = mapped_column(Float, default=0.0)


class PlayerScore(Base):
    """ORM model for scored player data (one row per scan result)."""

    __tablename__ = "player_scores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ea_id: Mapped[int] = mapped_column(Integer, index=True)
    scored_at: Mapped[datetime] = mapped_column(DateTime)
    buy_price: Mapped[int] = mapped_column(Integer)
    sell_price: Mapped[int] = mapped_column(Integer)
    net_profit: Mapped[int] = mapped_column(Integer)
    margin_pct: Mapped[int] = mapped_column(Integer)
    op_sales: Mapped[int] = mapped_column(Integer)
    total_sales: Mapped[int] = mapped_column(Integer)
    op_ratio: Mapped[float] = mapped_column(Float)
    expected_profit: Mapped[float] = mapped_column(Float)
    efficiency: Mapped[float] = mapped_column(Float)
    sales_per_hour: Mapped[float] = mapped_column(Float)
    is_viable: Mapped[bool] = mapped_column(Boolean, default=True)

    __table_args__ = (
        Index("ix_player_scores_ea_id_scored_at", "ea_id", "scored_at"),
    )


class MarketSnapshot(Base):
    """Raw market data snapshot captured during a player scan."""

    __tablename__ = "market_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ea_id: Mapped[int] = mapped_column(Integer, index=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime)
    current_lowest_bin: Mapped[int] = mapped_column(Integer)
    listing_count: Mapped[int] = mapped_column(Integer)
    live_auction_prices: Mapped[str] = mapped_column(Text)

    __table_args__ = (
        Index("ix_market_snapshots_ea_id_captured_at", "ea_id", "captured_at"),
        Index("ix_market_snapshots_captured_at", "captured_at"),
    )


class SnapshotSale(Base):
    """Individual sale record attached to a market snapshot."""

    __tablename__ = "snapshot_sales"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("market_snapshots.id", ondelete="CASCADE"), index=True
    )
    sold_at: Mapped[datetime] = mapped_column(DateTime)
    sold_price: Mapped[int] = mapped_column(Integer)


class SnapshotPricePoint(Base):
    """Price history observation attached to a market snapshot."""

    __tablename__ = "snapshot_price_points"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("market_snapshots.id", ondelete="CASCADE"), index=True
    )
    recorded_at: Mapped[datetime] = mapped_column(DateTime)
    lowest_bin: Mapped[int] = mapped_column(Integer)
