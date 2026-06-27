#!/usr/bin/env bash
# Start the Autobot stack for DEVELOPMENT/testing and apply DB migrations.
#
# Uses docker-compose.yml: the Flask dev server (`flask run --debug`) with the
# source bind-mounted at /app, so code changes hot-reload. Do NOT use this on a
# production host — the dev server can't serve concurrent SSE chat streams
# reliably and the reloader cuts in-flight streams. Use scripts/start.sh for
# production (gunicorn).
#
# Usage:
#   scripts/start.dev.sh            # full stack (web, worker, kali, postgres, redis)
#   scripts/start.dev.sh --no-kali  # skip the kali sidecar (flaky apt build)
#   scripts/start.dev.sh --build    # force-rebuild the web/worker image first
#   scripts/start.dev.sh --logs     # tail web logs after everything is up

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

NO_KALI=""
BUILD=""
FOLLOW_LOGS=""
for arg in "$@"; do
    case "$arg" in
        --no-kali) NO_KALI="1" ;;
        --build)   BUILD="--build" ;;
        --logs)    FOLLOW_LOGS="1" ;;
        -h|--help)
            sed -n '2,20p' "$0"
            exit 0
            ;;
        *)
            echo "Unknown option: $arg" >&2
            echo "Use -h for usage." >&2
            exit 2
            ;;
    esac
done

cd "${PROJECT_ROOT}"

if [[ ! -f .env ]]; then
    echo "✗ No .env file found in ${PROJECT_ROOT}." >&2
    echo "  Run 'docker compose run --rm web flask onboard' for first-time setup." >&2
    exit 1
fi

echo "→ Starting postgres and redis…"
docker compose up -d postgres redis

echo -n "→ Waiting for postgres to be ready"
for _ in $(seq 1 30); do
    if docker compose exec -T postgres pg_isready -U autobot >/dev/null 2>&1; then
        echo " ✓"
        break
    fi
    echo -n "."
    sleep 1
done
if ! docker compose exec -T postgres pg_isready -U autobot >/dev/null 2>&1; then
    echo ""
    echo "✗ postgres did not become ready in time." >&2
    exit 1
fi

echo "→ Applying database migrations…"
docker compose run --rm --no-deps ${BUILD} web flask db upgrade

if [[ -n "$NO_KALI" ]]; then
    echo "→ Starting web + worker (skipping kali sidecar)…"
    docker compose up -d ${BUILD} --no-deps web worker
else
    echo "→ Starting full stack (web, worker, kali)…"
    docker compose up -d ${BUILD}
fi

echo ""
echo "✓ Autobot (dev) is up at http://localhost:5000"
docker compose ps

if [[ -n "$FOLLOW_LOGS" ]]; then
    echo ""
    echo "→ Tailing web logs (Ctrl-C to stop)…"
    docker compose logs -f web
fi
