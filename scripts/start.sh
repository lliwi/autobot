#!/usr/bin/env bash
# Start the whole Autobot stack and apply pending DB migrations.
#
# Order matters: the database comes up first, migrations run against it, and
# only then do the app (web) and worker start serving. Migrations are
# idempotent — `flask db upgrade` is a no-op when the schema is already at head.
#
# Usage:
#   scripts/start.sh                # full stack (web, worker, kali, postgres, redis)
#   scripts/start.sh --no-kali      # skip the kali pentest sidecar (avoids its
#                                   #   flaky apt build); web+worker still start
#   scripts/start.sh --build        # force-rebuild the web/worker image first
#   scripts/start.sh --logs         # tail web logs after everything is up
#
# Flags can be combined, e.g. `scripts/start.sh --no-kali --logs`.

set -euo pipefail

# Resolve project root from this script's location so it works from anywhere.
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
            sed -n '2,16p' "$0"
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

# A .env is required — the services load it via env_file and the app reads its
# secret key, DB URL and Codex token from there.
if [[ ! -f .env ]]; then
    echo "✗ No .env file found in ${PROJECT_ROOT}." >&2
    echo "  Run 'docker compose run --rm web flask onboard' for first-time setup." >&2
    exit 1
fi

# 1. Bring up the data stores first (no build needed — official images).
echo "→ Starting postgres and redis…"
docker compose up -d postgres redis

# 2. Wait for postgres to accept connections before migrating.
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

# 3. Apply migrations via a one-off container (--no-deps avoids touching the
#    kali sidecar, whose image build is flaky). This builds the web image if
#    it doesn't exist yet.
echo "→ Applying database migrations…"
docker compose run --rm --no-deps ${BUILD} web flask db upgrade

# 4. Start the application services.
if [[ -n "$NO_KALI" ]]; then
    echo "→ Starting web + worker (skipping kali sidecar)…"
    docker compose up -d ${BUILD} --no-deps web worker
else
    echo "→ Starting full stack (web, worker, kali)…"
    docker compose up -d ${BUILD}
fi

echo ""
echo "✓ Autobot is up at http://localhost:5000"
docker compose ps

if [[ -n "$FOLLOW_LOGS" ]]; then
    echo ""
    echo "→ Tailing web logs (Ctrl-C to stop)…"
    docker compose logs -f web
fi
