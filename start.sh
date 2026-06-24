#!/usr/bin/env bash
# Start the Autobot stack: build images (cached, so only what changed), bring up
# the services and apply database migrations.
#
# The kali pentest sidecar uses a rolling base image whose repo occasionally
# 404s; it is optional, so we build/start it best-effort and the core stack
# (web, worker, postgres, redis) comes up regardless.
#
# Usage:
#   ./start.sh            # build if needed, start everything, migrate
#   ./start.sh --no-kali  # skip the kali sidecar entirely
#   ./start.sh --rebuild  # force a clean rebuild of web/worker (no cache)

set -euo pipefail
cd "$(dirname "$0")"

WANT_KALI=1
BUILD_ARGS=()
for arg in "$@"; do
  case "$arg" in
    --no-kali) WANT_KALI=0 ;;
    --rebuild) BUILD_ARGS+=(--no-cache) ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

if [[ ! -f .env ]]; then
  echo "✗ No .env file found. Create one (see .env.example) before starting." >&2
  exit 1
fi

echo "→ Building core images (web, worker)…"
docker compose build "${BUILD_ARGS[@]}" web worker

KALI_OK=0
if [[ "$WANT_KALI" == "1" ]]; then
  echo "→ Building kali sidecar (optional)…"
  if docker compose build "${BUILD_ARGS[@]}" kali; then
    KALI_OK=1
  else
    echo "  ⚠ kali build failed (rolling repo 404?) — continuing without the pentest sidecar."
  fi
fi

echo "→ Starting datastores (postgres, redis)…"
docker compose up -d postgres redis

echo "→ Waiting for postgres to accept connections…"
for i in $(seq 1 30); do
  if docker compose exec -T postgres pg_isready -q 2>/dev/null; then break; fi
  sleep 1
done

echo "→ Applying database migrations (flask db upgrade)…"
docker compose run --rm --no-deps web flask db upgrade

echo "→ Starting web + worker…"
# --force-recreate so web/worker always restart on the new code: handlers and
# scheduler jobs (e.g. the incident-autopilot log handler and drain loop) are
# wired at process start, so a stale container would silently skip them even
# when the mounted volume already has the new code.
if [[ "$KALI_OK" == "1" ]]; then
  docker compose up -d --force-recreate web worker kali
else
  # --no-deps skips the (unbuilt) kali dependency; postgres/redis are already up.
  docker compose up -d --no-deps --force-recreate web worker
fi

echo
echo "✓ Autobot is up — http://localhost:5000"
docker compose ps
