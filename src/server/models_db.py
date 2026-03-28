"""SQLAlchemy ORM table definitions for the persistent scanner."""
from datetime import datetime
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, Integer, Float, DateTime, Boolean, Index, Text, ForeignKey, UniqueConstraint
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
    futgg_url: Mapped[str | None] = mapped_column(String(200), nullable=True)


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
    expected_profit_per_hour: Mapped[float | None] = mapped_column(Float, nullable=True)
    scorer_version: Mapped[str | None] = mapped_column(String(5), nullable=True)  # "v1"|"v2"

    __table_args__ = (
        Index("ix_player_scores_ea_id_scored_at", "ea_id", "scored_at"),
        Index("ix_player_scores_viable_ea_scored", "is_viable", "ea_id", "scored_at"),
        Index("ix_player_scores_epph_null", "expected_profit_per_hour"),  # speeds up v1-purge DELETE on startup
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

    __table_args__ = (
        UniqueConstraint("snapshot_id", "sold_at", "sold_price", name="uq_snapshot_sale"),
    )


class SnapshotPricePoint(Base):
    """Price history observation attached to a market snapshot."""

    __tablename__ = "snapshot_price_points"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("market_snapshots.id", ondelete="CASCADE"), index=True
    )
    recorded_at: Mapped[datetime] = mapped_column(DateTime)
    lowest_bin: Mapped[int] = mapped_column(Integer)

    __table_args__ = (
        Index("ix_spp_snapshot_recorded_bin", "snapshot_id", "recorded_at", "lowest_bin"),
    )


class ListingObservation(Base):
    """Individual listing tracked across scan snapshots (per D-01)."""

    __tablename__ = "listing_observations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fingerprint: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    ea_id: Mapped[int] = mapped_column(Integer, index=True)
    buy_now_price: Mapped[int] = mapped_column(Integer)
    market_price_at_obs: Mapped[int] = mapped_column(Integer)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime)
    expected_expiry_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    scan_count: Mapped[int] = mapped_column(Integer, default=1)
    outcome: Mapped[str | None] = mapped_column(String(10), nullable=True)  # "sold"|"expired"|None
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class DailyListingSummary(Base):
    """Aggregated daily stats per player per margin tier (per D-13, D-14)."""

    __tablename__ = "daily_listing_summaries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ea_id: Mapped[int] = mapped_column(Integer, index=True)
    date: Mapped[str] = mapped_column(String(10), index=True)  # YYYY-MM-DD
    margin_pct: Mapped[int] = mapped_column(Integer)
    op_listed_count: Mapped[int] = mapped_column(Integer, default=0)
    op_sold_count: Mapped[int] = mapped_column(Integer, default=0)
    op_expired_count: Mapped[int] = mapped_column(Integer, default=0)
    total_listed_count: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (
        Index("ix_daily_summary_ea_id_date_margin", "ea_id", "date", "margin_pct"),
        UniqueConstraint("ea_id", "date", "margin_pct", name="uq_daily_summary_ea_id_date_margin"),
    )


class PortfolioSlot(Base):
    """A confirmed player slot in the active portfolio."""

    __tablename__ = "portfolio_slots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ea_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    buy_price: Mapped[int] = mapped_column(Integer)
    sell_price: Mapped[int] = mapped_column(Integer)
    added_at: Mapped[datetime] = mapped_column(DateTime)


class TradeAction(Base):
    """Pending/active action queue entry. One row per queued work item."""

    __tablename__ = "trade_actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ea_id: Mapped[int] = mapped_column(Integer, index=True)
    action_type: Mapped[str] = mapped_column(String(10))   # "BUY" | "LIST" | "RELIST"
    status: Mapped[str] = mapped_column(String(20), default="PENDING")  # "PENDING" | "IN_PROGRESS" | "DONE" | "CANCELLED"
    target_price: Mapped[int] = mapped_column(Integer)
    player_name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_trade_actions_status_created_at", "status", "created_at"),
    )


class TradeRecord(Base):
    """One row per lifecycle event (bought, listed, sold, expired)."""

    __tablename__ = "trade_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ea_id: Mapped[int] = mapped_column(Integer, index=True)
    action_type: Mapped[str] = mapped_column(String(10))   # "buy" | "list" | "relist"
    price: Mapped[int] = mapped_column(Integer)
    outcome: Mapped[str] = mapped_column(String(20))        # "bought" | "listed" | "sold" | "expired"
    recorded_at: Mapped[datetime] = mapped_column(DateTime)

    __table_args__ = (
        Index("ix_trade_records_ea_id_outcome", "ea_id", "outcome"),
        Index("ix_trade_records_recorded_at", "recorded_at"),
    )
