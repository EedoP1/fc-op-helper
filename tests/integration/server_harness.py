"""Test server harness for integration tests.

This module runs inside a FRESH subprocess spawned by conftest.py via:

    Popen(["uvicorn", "tests.integration.server_harness:app", ...], env=env)

where `env` contains TEST_DB_PATH set by conftest before Popen is called.

Isolation model:
    Setting os.environ["DATABASE_URL"] at module load time here affects ONLY
    this server subprocess — the test process (conftest, test files) is a
    separate process and is not affected. This is why it is safe to mutate
    os.environ at the top level: there is no shared state between the two
    processes.

The harness sets DATABASE_URL BEFORE importing anything from src.server,
because src.config reads the env var at import time. Once DATABASE_URL is
set, the REAL server app is imported and re-exported as `app`. This means:

    - Real ScannerService starts and connects to the test DB
    - Real CircuitBreaker is used
    - Real APScheduler runs
    - Real lifespan executes (stale-score purge, scanner.start(), scheduler)

Per D-01 and D-03: No mocks. If the scanner needs fut.gg API and it is
unavailable, the test fails — that is a design issue to solve, not to mock.
"""
import os

# Must be set before ANY import from src.server (src.config reads env at import time).
_db_path = os.environ["TEST_DB_PATH"]
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_db_path}"

from src.server.main import app  # noqa: E402 — intentional late import after env is set

__all__ = ["app"]
