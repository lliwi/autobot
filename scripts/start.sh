#!/usr/bin/env bash
# Start the Autobot stack in PRODUCTION and apply pending DB migrations.
#
# Uses docker-compose.prod.yml: web is served by gunicorn (gthread workers, no
# reloader, no debugger) from the baked Dockerfile.prod image, so it serves
# concurrent long-lived SSE chat streams reliably and a file write by an agent
# never restarts the server mid-response. For local development/testing with
# hot-reload, use scripts/start.dev.sh instead.
#
# Order matters: the database comes up first, migrations run against it, and
# only then do web and worker start serving. `flask db upgrade` is idempotent.
#
# Usage:
#   scripts/start.sh                # full stack (web, worker, kali, postgres, redis)
#   scripts/start.sh --no-kali      # skip the kali pentest sidecar (flaky build)
#   scripts/start.sh --build        # force-rebuild the prod image first
#   scripts/start.sh --logs         # tail web logs after everything is up
#
# Flags can be combined, e.g. `scripts/start.sh --build --logs`.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Every compose command targets the production compose file explicitly.
COMPOSE=(docker compose -f docker-compose.prod.yml)

NO_KALI=""
BUILD=""
FOLLOW_LOGS=""
for arg in "$@"; do
    case "$arg" in
        --no-kali) NO_KALI="1" ;;
        --build)   BUILD="--build" ;;
        --logs)    FOLLOW_LOGS="1" ;;
        -h|--help)
            sed -n '2,21p' "$0"
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

# A .env is required — services load it via env_file and the app reads its
# secret key, DB URL and Codex token from there.
if [[ ! -f .env ]]; then
    echo "✗ No .env file found in ${PROJECT_ROOT}." >&2
    echo "  Run '${COMPOSE[*]} run --rm web flask onboard' for first-time setup." >&2
    exit 1
fi

# 1. Bring up the data stores first (no build needed — official images).
echo "→ Starting postgres and redis…"
"${COMPOSE[@]}" up -d postgres redis

# 2. Wait for postgres to accept connections before migrating.
echo -n "→ Waiting for postgres to be ready"
for _ in $(seq 1 30); do
    if "${COMPOSE[@]}" exec -T postgres pg_isready -U autobot >/dev/null 2>&1; then
        echo " ✓"
        break
    fi
    echo -n "."
    sleep 1
done
if ! "${COMPOSE[@]}" exec -T postgres pg_isready -U autobot >/dev/null 2>&1; then
    echo ""
    echo "✗ postgres did not become ready in time." >&2
    exit 1
fi

# 3. Apply migrations via a one-off container (--no-deps avoids the flaky kali
#    build). This builds the prod image if it doesn't exist yet.
echo "→ Applying database migrations…"
"${COMPOSE[@]}" run --rm --no-deps ${BUILD} web flask db upgrade

# 4. Start the application services.
if [[ -n "$NO_KALI" ]]; then
    echo "→ Starting web + worker (skipping kali sidecar)…"
    "${COMPOSE[@]}" up -d ${BUILD} --no-deps web worker
else
    echo "→ Starting full stack (web, worker, kali)…"
    "${COMPOSE[@]}" up -d ${BUILD}
fi

echo ""
echo "✓ Autobot (production / gunicorn) is up on port 5000."
"${COMPOSE[@]}" ps

if [[ -n "$FOLLOW_LOGS" ]]; then
    echo ""
    echo "→ Tailing web logs (Ctrl-C to stop)…"
    "${COMPOSE[@]}" logs -f web
fi
