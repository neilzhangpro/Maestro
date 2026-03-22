#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

TUI_CMD="$ROOT_DIR/scripts/run-tui-in-container.sh"
SESSION_NAME="${MAESTRO_TMUX_SESSION:-maestro}"

# ── Ensure Docker daemon is running ─────────────────────────────────────────
ensure_docker_running() {
  # Fast path: daemon already responding
  if docker info > /dev/null 2>&1; then
    return 0
  fi

  echo "[workbench] Docker daemon not responding — attempting to start it..."

  if [[ "$(uname -s)" == "Darwin" ]]; then
    # Try OrbStack first (lighter, no GUI required), then Docker Desktop
    if [[ -d "/Applications/OrbStack.app" ]]; then
      echo "[workbench] Starting OrbStack..."
      open -a OrbStack
    elif [[ -d "/Applications/Docker.app" ]]; then
      echo "[workbench] Starting Docker Desktop..."
      open -a Docker
    else
      echo "[workbench] ERROR: Neither Docker Desktop nor OrbStack found in /Applications." >&2
      echo "[workbench]        Install one and try again." >&2
      exit 1
    fi
  else
    # Linux: try starting the system service
    if command -v systemctl > /dev/null 2>&1; then
      echo "[workbench] Starting Docker via systemctl..."
      sudo systemctl start docker
    else
      echo "[workbench] ERROR: Docker daemon is not running. Start it manually." >&2
      exit 1
    fi
  fi

  # Poll until the daemon is ready (up to 60 s)
  local max_wait=60
  local waited=0
  echo -n "[workbench] Waiting for Docker daemon"
  while ! docker info > /dev/null 2>&1; do
    if (( waited >= max_wait )); then
      echo ""
      echo "[workbench] ERROR: Docker daemon did not become ready after ${max_wait}s." >&2
      exit 1
    fi
    echo -n "."
    sleep 2
    (( waited += 2 ))
  done
  echo " ready (${waited}s)"
}

ensure_docker_running
# ────────────────────────────────────────────────────────────────────────────

if command -v tmux >/dev/null 2>&1; then
  if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
    echo "[workbench] Reusing tmux session: $SESSION_NAME"
    exec tmux attach -t "$SESSION_NAME"
  fi

  echo "[workbench] Starting tmux session: $SESSION_NAME"
  tmux new-session -d -s "$SESSION_NAME" -c "$ROOT_DIR" "docker compose up -d --build; printf '\n[workbench] Services started.\n'; exec \$SHELL -l"
  tmux split-window -h -t "$SESSION_NAME:0" -c "$ROOT_DIR" "docker compose logs -f"
  tmux split-window -v -t "$SESSION_NAME:0.1" -c "$ROOT_DIR" "$TUI_CMD"
  tmux select-layout -t "$SESSION_NAME:0" tiled
  exec tmux attach -t "$SESSION_NAME"
fi

if [[ "$(uname -s)" == "Darwin" ]] && command -v osascript >/dev/null 2>&1; then
  echo "[workbench] tmux not found; opening log window and launching TUI here."
  docker compose up -d --build
  # Open a separate window just for logs (background)
  osascript -e "tell application \"Terminal\" to do script \"cd '$ROOT_DIR' && docker compose logs -f\"" > /dev/null 2>&1 || true
  # Run TUI in the current terminal so the user sees it immediately
  exec "$TUI_CMD"
fi

echo "[workbench] tmux not available; falling back to current terminal."
docker compose up -d --build
echo "[workbench] Services started."
echo "[workbench] Tail logs with: docker compose logs -f"
echo "[workbench] Launching TUI in current terminal..."
exec "$TUI_CMD"
