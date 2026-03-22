#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# Read the host-side port from .env (falls back to the compose default 18080)
MAESTRO_PORT="$(awk -F= '/^MAESTRO_PORT=/{print $2}' .env 2>/dev/null | tail -n1)"
MAESTRO_PORT="${MAESTRO_PORT:-18080}"
HOST_URL="http://127.0.0.1:${MAESTRO_PORT}"

echo "[workbench] Waiting for Maestro container to start..."
until docker compose ps --services --status running 2>/dev/null | grep -q '^maestro$'; do
  sleep 1
done

echo "[workbench] Waiting for Maestro API health check (${HOST_URL})..."
until curl -fsS "${HOST_URL}/api/health" >/dev/null 2>&1; do
  sleep 1
done

echo "[workbench] Launching TUI inside container..."
exec docker compose exec maestro maestro tui --url http://127.0.0.1:8080
