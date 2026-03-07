#!/usr/bin/env bash
# Run tests fully in Docker: builds app image, starts postgres, runs pytest.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
COMPOSE_FILE="$PROJECT_DIR/docker-compose.test.yml"

cleanup() {
    docker compose -f "$COMPOSE_FILE" down
}
trap cleanup EXIT

docker compose -f "$COMPOSE_FILE" up --build --abort-on-container-exit --exit-code-from test_runner "$@"
