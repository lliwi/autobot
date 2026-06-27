#!/usr/bin/env bash
# Export Autobot diagnostics (Incidents, Reviewer, Logs) into a single tarball
# you can hand over for debugging.
#
# It runs scripts/export_diagnostics.py inside a running container (so it reads
# the real DB + Redis log ring), adds the raw container logs (web + worker),
# and bundles everything into ./diagnostics/autobot-diagnostics-<timestamp>.tar.gz.
#
# The exporter is copied into the container at runtime, so no image rebuild is
# needed — it works against whatever is already running.
#
# Usage:
#   scripts/export-diagnostics.sh                 # prod stack, last 7 days
#   scripts/export-diagnostics.sh --dev           # use the dev compose instead
#   scripts/export-diagnostics.sh --days 30       # widen the time window (0 = all)
#   scripts/export-diagnostics.sh --limit 500     # cap rows per table
#   scripts/export-diagnostics.sh --service worker  # run inside worker, not web
#
# Output contains log messages, tracebacks and agent output — treat as sensitive.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}"

COMPOSE=(docker compose -f docker-compose.prod.yml)
DAYS=7
LIMIT=0
LOG_LIMIT=2000
SERVICE=web

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dev)        COMPOSE=(docker compose) ;;
        --days)       DAYS="${2:?--days needs a value}"; shift ;;
        --limit)      LIMIT="${2:?--limit needs a value}"; shift ;;
        --log-limit)  LOG_LIMIT="${2:?--log-limit needs a value}"; shift ;;
        --service)    SERVICE="${2:?--service needs a value}"; shift ;;
        -h|--help)    sed -n '2,20p' "$0"; exit 0 ;;
        *) echo "Unknown option: $1" >&2; echo "Use -h for usage." >&2; exit 2 ;;
    esac
    shift
done

TS="$(date +%Y%m%d-%H%M%S)"
NAME="autobot-diagnostics-${TS}"
OUT_DIR="diagnostics/${NAME}"
CONTAINER_DIR="/tmp/${NAME}"

# Confirm the target service is actually running before we try to exec into it.
if ! "${COMPOSE[@]}" ps --services --filter status=running 2>/dev/null | grep -qx "${SERVICE}"; then
    echo "✗ Service '${SERVICE}' is not running for this compose file." >&2
    echo "  Start the stack first (scripts/start.sh) or pass --dev / --service." >&2
    exit 1
fi

mkdir -p "${OUT_DIR}"
echo "→ Exporting incidents, reviewer and logs from '${SERVICE}' (last ${DAYS}d)…"

# 1. Push the exporter into the container and run it against the live DB + Redis.
"${COMPOSE[@]}" cp scripts/export_diagnostics.py "${SERVICE}:/tmp/export_diagnostics.py"
"${COMPOSE[@]}" exec -T "${SERVICE}" python /tmp/export_diagnostics.py \
    --out "${CONTAINER_DIR}" --days "${DAYS}" --limit "${LIMIT}" --log-limit "${LOG_LIMIT}"

# 2. Copy the JSON exports out of the container.
"${COMPOSE[@]}" cp "${SERVICE}:${CONTAINER_DIR}/." "${OUT_DIR}/"

# 3. Add the raw container stdout logs (complements the Redis ring, which is
#    capped and reset if Redis restarts). docker --since wants a duration; convert
#    days→hours (omit the filter when --days 0 means "everything").
SINCE_ARGS=()
if [[ "${DAYS}" != "0" ]]; then
    SINCE_ARGS=(--since "$((DAYS * 24))h")
fi
echo "→ Capturing raw container logs (web, worker)…"
"${COMPOSE[@]}" logs --no-color "${SINCE_ARGS[@]}" web    > "${OUT_DIR}/container-web.log"    2>&1 || true
"${COMPOSE[@]}" logs --no-color "${SINCE_ARGS[@]}" worker > "${OUT_DIR}/container-worker.log" 2>&1 || true

# 4. Clean up inside the container (best-effort).
"${COMPOSE[@]}" exec -T "${SERVICE}" rm -rf "${CONTAINER_DIR}" /tmp/export_diagnostics.py 2>/dev/null || true

# 5. Bundle and remove the loose directory.
tar -czf "${OUT_DIR}.tar.gz" -C diagnostics "${NAME}"
rm -rf "${OUT_DIR}"

echo ""
echo "✓ Diagnostics bundle written to:"
echo "    ${PROJECT_ROOT}/${OUT_DIR}.tar.gz  ($(du -h "${OUT_DIR}.tar.gz" | cut -f1))"
echo "  Contents: incidents.json, reviewer.json, runs.json, logs_ring.json,"
echo "            manifest.json, container-web.log, container-worker.log"
echo "  ⚠ May contain sensitive data (tracebacks, agent output) — share with care."
