"""Refresh the integration test database by dumping production Postgres data.

Usage:
    1. Ensure both postgres (prod, port 5432) and postgres-test (port 5433)
       are running:  docker compose up -d postgres postgres-test
    2. Run: python scripts/setup_test_db.py

Uses pg_dump + psql via docker exec so it's safe to run while production
is live — pg_dump takes a consistent MVCC snapshot without stopping the
server. No local Postgres CLI tools required (everything runs inside the
Docker containers).
"""
import subprocess
import sys

PROD_CONTAINER = "op-seller-postgres-1"
TEST_CONTAINER = "op-seller-postgres-test-1"
DB_USER = "op_seller"
DB_NAME = "op_seller"


def _docker_exec(container: str, cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Run a command inside a Docker container."""
    return subprocess.run(
        ["docker", "exec", container, *cmd],
        check=True,
        **kwargs,
    )


def _docker_exec_interactive(container: str, cmd: list[str], **kwargs):
    """Run a command inside a Docker container with stdin piped."""
    return subprocess.Popen(
        ["docker", "exec", "-i", container, *cmd],
        **kwargs,
    )


def main():
    # 1. Verify both containers are running
    print("Checking prod postgres container...")
    try:
        _docker_exec(PROD_CONTAINER, ["pg_isready", "-U", DB_USER],
                      capture_output=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"Prod postgres not reachable: {e}", file=sys.stderr)
        print(f"Ensure '{PROD_CONTAINER}' is running: docker compose up -d postgres",
              file=sys.stderr)
        sys.exit(1)

    print("Checking test postgres container...")
    try:
        _docker_exec(TEST_CONTAINER, ["pg_isready", "-U", DB_USER],
                      capture_output=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"Test postgres not reachable: {e}", file=sys.stderr)
        print(f"Ensure '{TEST_CONTAINER}' is running: docker compose up -d postgres-test",
              file=sys.stderr)
        sys.exit(1)

    # 2. Drop and recreate the test database
    print("Dropping and recreating test database...")
    _docker_exec(TEST_CONTAINER, [
        "psql", "-U", DB_USER, "-d", "postgres",
        "-c", f"DROP DATABASE IF EXISTS {DB_NAME};",
    ])
    _docker_exec(TEST_CONTAINER, [
        "psql", "-U", DB_USER, "-d", "postgres",
        "-c", f"CREATE DATABASE {DB_NAME} OWNER {DB_USER};",
    ])

    # 3. pg_dump from prod | psql into test (piped between containers)
    print("Dumping prod and restoring into test (this may take a few minutes)...")
    dump = subprocess.Popen(
        ["docker", "exec", PROD_CONTAINER,
         "pg_dump", "-U", DB_USER, "-d", DB_NAME,
         "--no-owner", "--no-acl"],
        stdout=subprocess.PIPE,
    )
    restore = _docker_exec_interactive(
        TEST_CONTAINER,
        ["psql", "-U", DB_USER, "-d", DB_NAME, "-q"],
        stdin=dump.stdout,
    )
    dump.stdout.close()  # allow dump to receive SIGPIPE if restore exits
    restore.wait()
    dump.wait()

    if dump.returncode != 0:
        print(f"pg_dump failed (exit {dump.returncode})", file=sys.stderr)
        sys.exit(1)
    if restore.returncode != 0:
        print(f"psql restore failed (exit {restore.returncode})", file=sys.stderr)
        sys.exit(1)

    # 4. Quick sanity check
    result = subprocess.run(
        ["docker", "exec", TEST_CONTAINER,
         "psql", "-U", DB_USER, "-d", DB_NAME, "-t", "-A",
         "-c", "SELECT count(*) FROM player_scores WHERE is_viable = true;"],
        capture_output=True, text=True,
    )
    count = result.stdout.strip()
    print(f"Done. Test database refreshed — {count} viable player scores.")


if __name__ == "__main__":
    main()
