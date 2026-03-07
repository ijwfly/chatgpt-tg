#!/usr/bin/env bash
# Run tests locally: starts test postgres if needed, runs pytest on host.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
COMPOSE_FILE="$PROJECT_DIR/docker-compose.test.yml"

export POSTGRES_HOST="${POSTGRES_HOST:-localhost}"
export POSTGRES_PORT="${POSTGRES_PORT:-15432}"

# Start test postgres if not running
if ! docker compose -f "$COMPOSE_FILE" ps --status running postgres_test 2>/dev/null | grep -q postgres_test; then
    echo "Starting test postgres..."
    docker compose -f "$COMPOSE_FILE" up -d postgres_test
    echo "Waiting for postgres to be healthy..."
    until docker compose -f "$COMPOSE_FILE" ps --status running postgres_test 2>/dev/null | grep -q healthy; do
        sleep 1
    done
    echo "Postgres ready."
fi

cleanup() {
    echo "Stopping test containers..."
    docker compose -f "$COMPOSE_FILE" down
}
trap cleanup EXIT

# Activate venv if present and not already active
if [ -z "${VIRTUAL_ENV:-}" ] && [ -d "$PROJECT_DIR/venv" ]; then
    source "$PROJECT_DIR/venv/bin/activate"
fi

# Run tests, forwarding all arguments
python -m pytest tests/ "$@"
