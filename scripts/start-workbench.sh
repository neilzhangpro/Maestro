#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

TUI_CMD="$ROOT_DIR/scripts/run-tui-in-container.sh"
SESSION_NAME="${MAESTRO_TMUX_SESSION:-maestro}"

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
  echo "[workbench] tmux not found; using Terminal.app tabs on macOS."
  docker compose up -d --build
  osascript <<EOF
tell application "Terminal"
  activate
  do script "cd \"$ROOT_DIR\" && docker compose logs -f"
  do script "cd \"$ROOT_DIR\" && \"$TUI_CMD\""
end tell
EOF
  exit 0
fi

echo "[workbench] tmux not available; falling back to current terminal."
docker compose up -d --build
echo "[workbench] Services started."
echo "[workbench] Tail logs with: docker compose logs -f"
echo "[workbench] Launching TUI in current terminal..."
exec "$TUI_CMD"
