"""Refresh the integration test database by copying production Postgres data.

Usage:
    1. Stop containers: docker compose down
    2. Run: python scripts/setup_test_db.py
    3. Start containers: docker compose up -d

Copies D:/op-seller/postgres_data -> D:/op-seller/postgres_data_test.
The test container (postgres-test) runs on port 5433 with this copy.
Production data on port 5432 is never touched by tests.
"""
import shutil
import sys
from pathlib import Path

SOURCE = Path("D:/op-seller/postgres_data")
TARGET = Path("D:/op-seller/postgres_data_test")


def main():
    if not SOURCE.exists():
        print(f"Source not found: {SOURCE}", file=sys.stderr)
        sys.exit(1)

    print(f"Copying {SOURCE} -> {TARGET}")
    if TARGET.exists():
        print("Removing old test data...")
        shutil.rmtree(TARGET)

    print("Copying (this may take a few minutes for ~11GB)...")
    shutil.copytree(SOURCE, TARGET)
    print("Done. Run 'docker compose up -d' to start both containers.")


if __name__ == "__main__":
    main()
