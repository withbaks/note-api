#!/usr/bin/env bash
set -euo pipefail

PG_HOST="${NOTE_PG_HOST:-localhost}"
PG_PORT="${NOTE_PG_PORT:-5433}"
REDIS_HOST="${NOTE_REDIS_HOST:-localhost}"
REDIS_PORT="${NOTE_REDIS_PORT:-6380}"
DOCKER_TIMEOUT="${NOTE_DOCKER_TIMEOUT:-15}"

port_open() {
  nc -z -w 2 "$1" "$2" >/dev/null 2>&1
}

if port_open "$PG_HOST" "$PG_PORT" && port_open "$REDIS_HOST" "$REDIS_PORT"; then
  echo "Postgres ($PG_HOST:$PG_PORT) and Redis ($REDIS_HOST:$REDIS_PORT) already running — skipping docker compose."
  exit 0
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is not installed. Start Postgres on $PG_HOST:$PG_PORT and Redis on $REDIS_HOST:$REDIS_PORT, then rerun make dev." >&2
  exit 1
fi

echo "Starting postgres and redis via docker compose (timeout ${DOCKER_TIMEOUT}s)..."

docker compose up -d postgres redis &
compose_pid=$!

(
  sleep "$DOCKER_TIMEOUT"
  if kill -0 "$compose_pid" 2>/dev/null; then
    echo "" >&2
    echo "docker compose is taking too long — Docker Desktop may be stuck." >&2
    echo "Try: quit and reopen Docker Desktop, or run only the API if services are already up:" >&2
    echo "  uv run uvicorn note_api.main:app --app-dir apps/api/src --reload --host 0.0.0.0 --port 8000" >&2
    kill "$compose_pid" 2>/dev/null || true
    exit 124
  fi
) &
watchdog_pid=$!

if wait "$compose_pid"; then
  kill "$watchdog_pid" 2>/dev/null || true
  wait "$watchdog_pid" 2>/dev/null || true
  echo "Docker services started."
  exit 0
fi

status=$?
kill "$watchdog_pid" 2>/dev/null || true
wait "$watchdog_pid" 2>/dev/null || true
exit "$status"
