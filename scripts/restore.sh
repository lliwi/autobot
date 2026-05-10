#!/usr/bin/env bash
# Restore an Autobot backup produced by scripts/backup.sh or flask export-bundle.
#
# Backups live in ./backups/ on the host, accessible inside the container at
# /app/backups/ because the project root is bind-mounted at /app.
#
# Usage:
#   scripts/restore.sh                              # restore most recent backup
#   scripts/restore.sh backups/autobot_2026-04-20_19-13.tar.gz
#   scripts/restore.sh --list                       # list available backups

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
BACKUPS_DIR="${PROJECT_ROOT}/backups"

# ── argument parsing ────────────────────────────────────────────────────────

BUNDLE=""
for arg in "$@"; do
    case "$arg" in
        --list|-l)
            echo "Available backups:"
            ls -lth "${BACKUPS_DIR}"/*.tar.gz 2>/dev/null \
                | awk '{print $NF, $5}' \
                | while read -r path size; do echo "  ${size}  ${path}"; done \
                || echo "  (none found in ${BACKUPS_DIR}/)"
            exit 0
            ;;
        -h|--help)
            sed -n '2,12p' "$0"
            exit 0
            ;;
        -*)
            echo "Unknown option: $arg" >&2
            echo "Use -h for usage." >&2
            exit 2
            ;;
        *)
            BUNDLE="$arg"
            ;;
    esac
done

# ── resolve bundle path ─────────────────────────────────────────────────────

if [[ -z "$BUNDLE" ]]; then
    # Pick the most recent backup automatically
    BUNDLE="$(ls -t "${BACKUPS_DIR}"/*.tar.gz 2>/dev/null | head -1)"
    if [[ -z "$BUNDLE" ]]; then
        echo "✗ No backups found in ${BACKUPS_DIR}/" >&2
        exit 1
    fi
    echo "→ No bundle specified — using most recent: ${BUNDLE}"
fi

# Resolve to absolute path
BUNDLE="$(cd "$(dirname "$BUNDLE")" && pwd)/$(basename "$BUNDLE")"

if [[ ! -f "$BUNDLE" ]]; then
    echo "✗ Bundle not found: ${BUNDLE}" >&2
    exit 1
fi

# Translate host path → container path (/app/... because of bind mount)
if [[ "$BUNDLE" == "${PROJECT_ROOT}"* ]]; then
    CONTAINER_PATH="/app${BUNDLE#"${PROJECT_ROOT}"}"
else
    echo "✗ Bundle must be inside the project directory (${PROJECT_ROOT})." >&2
    echo "  Copy it here first: cp /path/to/bundle.tar.gz ${BACKUPS_DIR}/" >&2
    exit 1
fi

# ── preflight checks ────────────────────────────────────────────────────────

if ! docker compose ps --services --filter "status=running" 2>/dev/null | grep -qx web; then
    echo "✗ The 'web' service is not running. Start it with 'docker compose up -d'." >&2
    exit 1
fi

# ── confirm and restore ─────────────────────────────────────────────────────

SIZE="$(du -h "${BUNDLE}" | cut -f1)"
echo "→ Restoring from: ${BUNDLE} (${SIZE})"
echo "  This will write into the database and workspaces."
read -r -p "  Continue? [y/N] " REPLY
[[ "$REPLY" =~ ^[Yy]$ ]] || { echo "Aborted."; exit 0; }

cd "${PROJECT_ROOT}"

docker compose exec -T web flask import-bundle \
    -i "${CONTAINER_PATH}" \
    --yes \
    --overwrite

echo "✓ Restore complete. Restart services to pick up any .env changes:"
echo "  docker compose restart"
