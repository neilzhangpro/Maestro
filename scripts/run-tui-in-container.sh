#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "[workbench] Waiting for Maestro container to start..."
until docker compose ps --services --status running 2>/dev/null | grep -q '^maestro$'; do
  sleep 1
done

echo "[workbench] Waiting for Maestro API health check..."
until curl -fsS http://127.0.0.1:8080/api/health >/dev/null 2>&1; do
  sleep 1
done

echo "[workbench] Launching TUI inside container..."
exec docker compose exec maestro maestro tui --url http://127.0.0.1:8080
