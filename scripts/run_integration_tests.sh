#!/usr/bin/env bash
# run_integration_tests.sh — One-command integration test runner.
#
# Usage:
#   bash scripts/run_integration_tests.sh
#
# Prerequisites:
#   - Docker and Docker Compose v2 installed
#   - Local Python venv active with requirements.txt installed (pip install -r requirements.txt)
#   - postgres-test service will be started if not already running
#
# What it does:
#   1. Ensures postgres-test container is running and healthy
#   2. Builds and starts api + scanner containers in test configuration
#   3. Waits for the API health endpoint to respond
#   4. Runs pytest tests/integration/ with -x (fail-fast) and verbose output
#   5. Tears down the api + scanner containers on exit (postgres-test persists)

set -euo pipefail

COMPOSE_TEST_FLAGS="-f docker-compose.yml -f docker-compose.test.yml -p op_seller_test"
API_HEALTH_URL="http://localhost:8001/api/v1/health"
HEALTH_MAX_WAIT=30

# Tear down api + scanner on exit (postgres-test persists across runs)
cleanup() {
    echo ""
    echo "--- Tearing down test containers ---"
    docker compose ${COMPOSE_TEST_FLAGS} down --remove-orphans || true
}
trap cleanup EXIT

echo "=== Step 1: Ensure postgres-test is running ==="
docker compose up -d postgres-test

echo "--- Waiting for postgres-test to be healthy ---"
WAIT=0
until docker compose ps postgres-test | grep -q "healthy"; do
    if [ "$WAIT" -ge 60 ]; then
        echo "ERROR: postgres-test did not become healthy within 60s"
        exit 1
    fi
    sleep 2
    WAIT=$((WAIT + 2))
done
echo "postgres-test is healthy."

echo ""
echo "=== Step 2: Build and start api + scanner in test configuration ==="
docker compose ${COMPOSE_TEST_FLAGS} up -d --build api scanner

echo ""
echo "=== Step 3: Wait for API health endpoint ==="
echo "Polling ${API_HEALTH_URL} for up to ${HEALTH_MAX_WAIT}s..."
curl --silent --retry $((HEALTH_MAX_WAIT / 2)) \
    --retry-delay 2 \
    --retry-connrefused \
    --fail \
    --max-time 5 \
    "${API_HEALTH_URL}" > /dev/null
echo "API is healthy."

echo ""
echo "=== Step 4: Running integration tests ==="
python -m pytest tests/integration/ -x -v --timeout=120

echo ""
echo "=== All integration tests passed ==="
