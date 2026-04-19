#!/usr/bin/env bash
# Create a backup of the running Autobot install and drop it on the host.
#
# The project root is mounted at /app inside the `web` container, so writing
# to /app/backups/<file> from inside the container produces a file directly
# on the host at ./backups/<file>.
#
# Usage:
#   scripts/backup.sh                       # DB + workspaces, no secrets
#   scripts/backup.sh --include-env         # also bundle .env
#   scripts/backup.sh --include-secrets     # decrypt credentials into the bundle
#   scripts/backup.sh --include-env --include-secrets

set -euo pipefail

# Resolve project root from this script's location so it works no matter where
# the caller runs it from.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
BACKUPS_DIR="${PROJECT_ROOT}/backups"

INCLUDE_ENV=""
INCLUDE_SECRETS=""
for arg in "$@"; do
    case "$arg" in
        --include-env)     INCLUDE_ENV="--include-env" ;;
        --include-secrets) INCLUDE_SECRETS="--include-secrets" ;;
        -h|--help)
            sed -n '2,12p' "$0"
            exit 0
            ;;
        *)
            echo "Unknown option: $arg" >&2
            echo "Use -h for usage." >&2
            exit 2
            ;;
    esac
done

mkdir -p "${BACKUPS_DIR}"

# Check the web container is actually running — export-bundle needs a live
# Flask process with DB access.
if ! docker compose ps --services --filter "status=running" 2>/dev/null | grep -qx web; then
    echo "✗ The 'web' service is not running. Start it with 'docker compose up -d'." >&2
    exit 1
fi

TIMESTAMP="$(date +%Y-%m-%d_%H-%M)"
FILENAME="autobot_${TIMESTAMP}.tar.gz"
CONTAINER_PATH="/app/backups/${FILENAME}"
HOST_PATH="${BACKUPS_DIR}/${FILENAME}"

cd "${PROJECT_ROOT}"

echo "→ Writing bundle to ${HOST_PATH}"
if [[ -n "$INCLUDE_ENV" || -n "$INCLUDE_SECRETS" ]]; then
    echo "  ⚠ Bundle will contain secrets in plaintext. Handle with care."
fi

# -T disables TTY so the script works in CI/cron; --yes skips the CLI prompt.
docker compose exec -T web flask export-bundle \
    -o "${CONTAINER_PATH}" \
    --yes \
    ${INCLUDE_ENV} \
    ${INCLUDE_SECRETS}

if [[ ! -f "${HOST_PATH}" ]]; then
    echo "✗ Expected ${HOST_PATH} but it's not there. Did the volume mount change?" >&2
    exit 1
fi

# The container writes as root; chown back to the host user so the file can
# be deleted/moved without sudo. Done from inside the container since the
# file is currently owned by root on the host too.
HOST_UID="$(id -u)"
HOST_GID="$(id -g)"
docker compose exec -T web chown "${HOST_UID}:${HOST_GID}" "${CONTAINER_PATH}" || true

SIZE="$(du -h "${HOST_PATH}" | cut -f1)"
echo "✓ Backup ready: ${HOST_PATH} (${SIZE})"
