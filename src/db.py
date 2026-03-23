"""SQLite database layer for persisting market data and scores."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src.models import Player, PricePoint, SaleRecord

DB_PATH = Path(__file__).parent.parent / "op_history.db"


def get_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Get a database connection with row factory."""
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Create all tables if they don't exist."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS players (
            resource_id     INTEGER PRIMARY KEY,
            name            TEXT NOT NULL,
            rating          INTEGER NOT NULL,
            position        TEXT NOT NULL,
            nation          TEXT NOT NULL,
            league          TEXT NOT NULL,
            club            TEXT NOT NULL,
            card_type       TEXT NOT NULL,
            pace            INTEGER DEFAULT 0,
            shooting        INTEGER DEFAULT 0,
            passing         INTEGER DEFAULT 0,
            dribbling       INTEGER DEFAULT 0,
            defending       INTEGER DEFAULT 0,
            physical        INTEGER DEFAULT 0,
            current_lowest_bin INTEGER,
            listing_count   INTEGER,
            price_tier      TEXT,
            last_updated    TEXT
        );

        CREATE TABLE IF NOT EXISTS player_sales (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            resource_id     INTEGER NOT NULL,
            sold_at         TEXT NOT NULL,
            sold_price      INTEGER NOT NULL,
            lowest_bin_at_time INTEGER NOT NULL,
            FOREIGN KEY (resource_id) REFERENCES players(resource_id)
        );
        CREATE INDEX IF NOT EXISTS idx_sales_rid_time
            ON player_sales(resource_id, sold_at);

        CREATE TABLE IF NOT EXISTS price_history (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            resource_id     INTEGER NOT NULL,
            recorded_at     TEXT NOT NULL,
            lowest_bin      INTEGER NOT NULL,
            median_bin      INTEGER,
            listing_count   INTEGER,
            FOREIGN KEY (resource_id) REFERENCES players(resource_id)
        );
        CREATE INDEX IF NOT EXISTS idx_price_rid_time
            ON price_history(resource_id, recorded_at);

        CREATE TABLE IF NOT EXISTS player_scores (
            resource_id     INTEGER PRIMARY KEY,
            hoss            REAL DEFAULT 0,
            profit_margin   REAL DEFAULT 0,
            price_stability REAL DEFAULT 0,
            supply          REAL DEFAULT 0,
            tier_peer       REAL DEFAULT 0,
            buyer_psychology REAL DEFAULT 0,
            market_timing   REAL DEFAULT 0,
            composite       REAL DEFAULT 0,
            best_op_margin  REAL DEFAULT 0,
            computed_at     TEXT,
            FOREIGN KEY (resource_id) REFERENCES players(resource_id)
        );
    """)
    conn.commit()


def upsert_player(conn: sqlite3.Connection, player: Player, lowest_bin: int,
                   listing_count: int, price_tier: str) -> None:
    """Insert or update a player record."""
    conn.execute("""
        INSERT INTO players (resource_id, name, rating, position, nation, league,
                             club, card_type, pace, shooting, passing, dribbling,
                             defending, physical, current_lowest_bin, listing_count,
                             price_tier, last_updated)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(resource_id) DO UPDATE SET
            current_lowest_bin=excluded.current_lowest_bin,
            listing_count=excluded.listing_count,
            price_tier=excluded.price_tier,
            last_updated=excluded.last_updated
    """, (
        player.resource_id, player.name, player.rating, player.position,
        player.nation, player.league, player.club, player.card_type,
        player.pace, player.shooting, player.passing, player.dribbling,
        player.defending, player.physical, lowest_bin, listing_count,
        price_tier, datetime.now(timezone.utc).isoformat(),
    ))
    conn.commit()


def insert_sales(conn: sqlite3.Connection, sales: list[SaleRecord]) -> None:
    """Bulk insert sale records (skips duplicates by checking existing timestamps)."""
    if not sales:
        return
    conn.executemany("""
        INSERT OR IGNORE INTO player_sales (resource_id, sold_at, sold_price, lowest_bin_at_time)
        VALUES (?, ?, ?, ?)
    """, [(s.resource_id, s.sold_at.isoformat(), s.sold_price, s.lowest_bin_at_time)
          for s in sales])
    conn.commit()


def insert_price_points(conn: sqlite3.Connection, points: list[PricePoint]) -> None:
    """Bulk insert price history points."""
    if not points:
        return
    conn.executemany("""
        INSERT INTO price_history (resource_id, recorded_at, lowest_bin, median_bin, listing_count)
        VALUES (?, ?, ?, ?, ?)
    """, [(p.resource_id, p.recorded_at.isoformat(), p.lowest_bin,
           p.median_bin, p.listing_count) for p in points])
    conn.commit()


def get_player_sales(conn: sqlite3.Connection, resource_id: int,
                     days: int = 30) -> list[SaleRecord]:
    """Get sale records for a player within the lookback window."""
    rows = conn.execute("""
        SELECT resource_id, sold_at, sold_price, lowest_bin_at_time
        FROM player_sales
        WHERE resource_id = ?
          AND sold_at >= datetime('now', ?)
        ORDER BY sold_at DESC
    """, (resource_id, f"-{days} days")).fetchall()

    return [SaleRecord(
        resource_id=r["resource_id"],
        sold_at=datetime.fromisoformat(r["sold_at"]),
        sold_price=r["sold_price"],
        lowest_bin_at_time=r["lowest_bin_at_time"],
    ) for r in rows]


def get_price_history(conn: sqlite3.Connection, resource_id: int,
                      days: int = 30) -> list[PricePoint]:
    """Get price history for a player within the lookback window."""
    rows = conn.execute("""
        SELECT resource_id, recorded_at, lowest_bin, median_bin, listing_count
        FROM price_history
        WHERE resource_id = ?
          AND recorded_at >= datetime('now', ?)
        ORDER BY recorded_at ASC
    """, (resource_id, f"-{days} days")).fetchall()

    return [PricePoint(
        resource_id=r["resource_id"],
        recorded_at=datetime.fromisoformat(r["recorded_at"]),
        lowest_bin=r["lowest_bin"],
        median_bin=r["median_bin"],
        listing_count=r["listing_count"],
    ) for r in rows]


def get_all_players_in_tier(conn: sqlite3.Connection, tier: str) -> list[dict]:
    """Get all players in a specific price tier with their HOSS scores."""
    rows = conn.execute("""
        SELECT p.resource_id, ps.hoss
        FROM players p
        LEFT JOIN player_scores ps ON p.resource_id = ps.resource_id
        WHERE p.price_tier = ?
    """, (tier,)).fetchall()
    return [dict(r) for r in rows]


def upsert_player_scores(conn: sqlite3.Connection, resource_id: int,
                          scores: dict) -> None:
    """Insert or update computed scores for a player."""
    conn.execute("""
        INSERT INTO player_scores (resource_id, hoss, profit_margin, price_stability,
                                    supply, tier_peer, buyer_psychology, market_timing,
                                    composite, best_op_margin, computed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(resource_id) DO UPDATE SET
            hoss=excluded.hoss, profit_margin=excluded.profit_margin,
            price_stability=excluded.price_stability, supply=excluded.supply,
            tier_peer=excluded.tier_peer, buyer_psychology=excluded.buyer_psychology,
            market_timing=excluded.market_timing, composite=excluded.composite,
            best_op_margin=excluded.best_op_margin, computed_at=excluded.computed_at
    """, (
        resource_id, scores.get("hoss", 0), scores.get("profit_margin", 0),
        scores.get("price_stability", 0), scores.get("supply", 0),
        scores.get("tier_peer", 0), scores.get("buyer_psychology", 0),
        scores.get("market_timing", 0), scores.get("composite", 0),
        scores.get("best_op_margin", 0),
        datetime.now(timezone.utc).isoformat(),
    ))
    conn.commit()
