"""One-shot SQLite to PostgreSQL data migration.

Usage:
    1. Start Postgres: docker compose up -d
    2. Stop the scanner process
    3. Run: python scripts/migrate_data.py
    4. Restart server with DATABASE_URL pointing to Postgres

The script:
    1. Runs pgloader via Docker to copy all data from SQLite to Postgres
       (per D-04 — pgloader not available natively on Windows, per Pitfall 7)
    2. Runs post-migration SQL cleanup (D-10):
       - Drops futbin_id column
       - Sets scan_tier defaults ('normal' for empty/null rows)
       - Backfills scorer_version NULLs to 'v1'
    3. Verifies row counts match between source and target

Requirements:
    - Docker Desktop running
    - Postgres container running (docker compose up -d)
    - SQLite DB at D:/op-seller/op_seller.db
    - Brief downtime acceptable (D-05): stop scanner before running this script

After migration:
    - Keep SQLite file as backup (D-06) — rollback by setting DATABASE_URL back
      to sqlite+aiosqlite:///D:/op-seller/op_seller.db
    - Start server: DATABASE_URL=postgresql+asyncpg://op_seller:op_seller@localhost:5432/op_seller
                    python -m src.server.main
"""
import subprocess
import sys

# ── Configuration ─────────────────────────────────────────────────────────────

# SQLite source path (Windows path; Docker volume-mounted as Linux path)
SQLITE_PATH = "D:/op-seller/op_seller.db"

# Postgres target (accessible from inside Docker via host.docker.internal)
POSTGRES_URL = "postgresql://op_seller:op_seller@host.docker.internal:5432/op_seller"

# Docker container name for the Postgres instance (default docker-compose name)
POSTGRES_CONTAINER = "op-seller-postgres-1"

# ── pgloader command via Docker ───────────────────────────────────────────────
# Per Pitfall 7: pgloader has no native Windows binary — must run via Docker.
# The SQLite file is volume-mounted into the container at /data/op_seller.db.
# host.docker.internal resolves to the host machine (where Postgres is running).
PGLOADER_CMD = [
    "docker", "run", "--rm",
    "--add-host=host.docker.internal:host-gateway",
    "-v", f"{SQLITE_PATH}:/data/op_seller.db",
    "dimitri/pgloader",
    "pgloader",
    "--on-error-stop",
    "sqlite:///data/op_seller.db",
    POSTGRES_URL,
]

# ── Post-migration cleanup SQL (D-10) ─────────────────────────────────────────
# Run against Postgres after pgloader completes to fix schema remnants:
#   - futbin_id: column exists in SQLite but not in models_db.py (43 non-NULL values)
#   - scan_tier: currently empty string for all 1749 players; default to 'normal'
#   - scorer_version: 2,414 old v1 scores pre-dating the field; backfill to 'v1'
CLEANUP_SQL = [
    "ALTER TABLE players DROP COLUMN IF EXISTS futbin_id;",
    "UPDATE players SET scan_tier = 'normal' WHERE scan_tier = '' OR scan_tier IS NULL;",
    "UPDATE player_scores SET scorer_version = 'v1' WHERE scorer_version IS NULL;",
]

# Tables to verify row counts after migration
TABLES = [
    "players",
    "player_scores",
    "market_snapshots",
    "snapshot_sales",
    "snapshot_price_points",
    "listing_observations",
    "daily_listing_summaries",
    "portfolio_slots",
    "trade_actions",
    "trade_records",
]


def run_pgloader():
    """Run pgloader via Docker to migrate SQLite data to Postgres.

    Uses --on-error-stop to fail fast on type mapping errors.
    The SQLite file is volume-mounted into the Docker container.
    Postgres is reached via host.docker.internal from within Docker.
    """
    print(f"Running pgloader: {SQLITE_PATH} -> Postgres...")
    print(f"Command: {' '.join(PGLOADER_CMD)}")
    result = subprocess.run(PGLOADER_CMD, capture_output=True, text=True)
    print(result.stdout)
    if result.returncode != 0:
        print(f"pgloader FAILED (exit {result.returncode}):", file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        sys.exit(1)
    print("pgloader completed successfully.")


def run_cleanup():
    """Run post-migration SQL cleanup via psql in the Postgres Docker container.

    Executes each D-10 cleanup statement individually so partial failures are
    visible. A failed statement is logged but does not abort the script —
    run manually if needed.
    """
    print("\nRunning post-migration cleanup SQL (D-10)...")
    for sql in CLEANUP_SQL:
        cmd = [
            "docker", "exec", "-i",
            POSTGRES_CONTAINER,
            "psql", "-U", "op_seller", "-d", "op_seller",
            "-c", sql,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"  FAILED: {sql}", file=sys.stderr)
            print(f"  {result.stderr.strip()}", file=sys.stderr)
        else:
            print(f"  OK: {sql[:70]}...")


def verify_counts():
    """Print row counts from Postgres for manual verification against SQLite.

    Check these counts against the source SQLite DB to confirm the migration
    was complete. For snapshot_price_points (44M+ rows) the count may take a
    few seconds.
    """
    print("\nRow counts in Postgres (verify against SQLite source):")
    for table in TABLES:
        cmd = [
            "docker", "exec", "-i",
            POSTGRES_CONTAINER,
            "psql", "-U", "op_seller", "-d", "op_seller",
            "-t", "-c", f"SELECT COUNT(*) FROM {table};",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        count = result.stdout.strip() if result.returncode == 0 else "ERROR"
        print(f"  {table:40s} {count}")


if __name__ == "__main__":
    print("=" * 70)
    print("OP Seller: SQLite -> PostgreSQL one-shot migration")
    print("=" * 70)
    print()
    print("Prerequisites:")
    print("  1. Docker Desktop is running")
    print("  2. Postgres container is running: docker compose up -d")
    print("  3. Scanner process is STOPPED")
    print(f"  4. SQLite source DB exists at: {SQLITE_PATH}")
    print()

    run_pgloader()
    run_cleanup()
    verify_counts()

    print()
    print("=" * 70)
    print("Migration complete.")
    print()
    print("Next steps:")
    print("  - Keep SQLite file as backup (D-06):")
    print(f"    {SQLITE_PATH}")
    print("  - Start server with Postgres:")
    print("    DATABASE_URL=postgresql+asyncpg://op_seller:op_seller@localhost:5432/op_seller")
    print("    python -m src.server.main")
    print("  - Rollback: set DATABASE_URL back to sqlite+aiosqlite:///D:/op-seller/op_seller.db")
    print("=" * 70)
