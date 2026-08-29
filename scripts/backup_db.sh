#!/usr/bin/env bash
# Dump the bot's PostgreSQL database into ./backups/chatgpttg_<timestamp>.sql.gz
#
# Works in both states:
#   - postgres service is running  -> pg_dump inside that container
#   - postgres service is stopped  -> a throwaway postgres container is started on the same `db` volume
#     (never done while the service is up: two postmasters on one data dir would corrupt it)
#
# Restore (into an empty database):  gunzip -c backups/chatgpttg_<ts>.sql.gz | docker compose exec -T postgres psql -U postgres -d chatgpttg
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

PG_USER="${POSTGRES_USER:-postgres}"
PG_PASSWORD="${POSTGRES_PASSWORD:-password}"
PG_DB="${POSTGRES_DB:-chatgpttg}"
PG_IMAGE="postgres:15.3"   # must match docker-compose.yml (pg_dump version >= server version)

BACKUP_DIR="$PROJECT_DIR/backups"
mkdir -p "$BACKUP_DIR"
OUT="$BACKUP_DIR/${PG_DB}_$(date +%Y%m%d_%H%M%S).sql.gz"

if docker compose ps --status running postgres 2>/dev/null | grep -q postgres; then
    echo "postgres service is running — dumping via docker compose exec"
    docker compose exec -T -e PGPASSWORD="$PG_PASSWORD" postgres \
        pg_dump -U "$PG_USER" -d "$PG_DB" --no-owner --no-privileges | gzip > "$OUT"
else
    PROJECT="$(docker compose config --format json 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin)["name"])')"
    VOLUME="${PROJECT}_db"
    if ! docker volume inspect "$VOLUME" >/dev/null 2>&1; then
        echo "Docker volume $VOLUME not found" >&2
        exit 1
    fi
    echo "postgres service is stopped — starting a temporary container on volume $VOLUME"
    TMP="pg_backup_$$"
    docker run -d --rm --name "$TMP" \
        -v "$VOLUME:/var/lib/postgresql/data" \
        -e POSTGRES_PASSWORD="$PG_PASSWORD" "$PG_IMAGE" >/dev/null
    trap 'docker stop "$TMP" >/dev/null 2>&1 || true' EXIT
    until docker exec "$TMP" pg_isready -U "$PG_USER" >/dev/null 2>&1; do sleep 1; done
    docker exec -e PGPASSWORD="$PG_PASSWORD" "$TMP" \
        pg_dump -U "$PG_USER" -d "$PG_DB" --no-owner --no-privileges | gzip > "$OUT"
fi

# a truncated dump is worse than none: make sure the file is a complete SQL dump
if ! gunzip -c "$OUT" | tail -5 | grep -q "PostgreSQL database dump complete"; then
    echo "Dump looks incomplete: $OUT" >&2
    exit 1
fi

echo "Backup written: $OUT ($(du -h "$OUT" | cut -f1))"
