#!/usr/bin/env bash
set -euo pipefail

# -----------------------------------------------------------------
# Detect backend from WORKFLOW.md front matter
# -----------------------------------------------------------------
BACKEND="cursor"
if [ -f WORKFLOW.md ]; then
  DETECTED=$(python3 -c "
import re, sys
with open('WORKFLOW.md') as f:
    text = f.read()
m = re.search(r'^backend:\s*(\S+)', text, re.MULTILINE)
print(m.group(1) if m else 'cursor')
" 2>/dev/null) || true
  if [ -n "$DETECTED" ]; then
    BACKEND="$DETECTED"
  fi
fi
echo "Detected backend: ${BACKEND}"

# -----------------------------------------------------------------
# Cursor agent CLI verification (when using cursor backend)
# -----------------------------------------------------------------
if [ "$BACKEND" = "cursor" ]; then
  if ! command -v agent &>/dev/null; then
    for candidate in \
      /opt/cursor-agent/cursor-agent \
      /opt/cursor-agent/current/cursor-agent \
      /opt/cursor-agent/current/agent; do
      if [ -x "$candidate" ]; then
        ln -sf "$candidate" /usr/local/bin/agent
        break
      fi
    done
  fi

  if ! command -v agent &>/dev/null; then
    echo "ERROR: Cursor agent CLI not found." >&2
    echo "The Docker image may need to be rebuilt:" >&2
    echo "  docker compose build --no-cache maestro" >&2
    exit 1
  fi
  echo "Cursor agent CLI: $(command -v agent)"
fi

# -----------------------------------------------------------------
# Claude Code CLI verification (when using claude_code backend)
# -----------------------------------------------------------------
if [ "$BACKEND" = "claude_code" ]; then
  if ! command -v claude &>/dev/null; then
    echo "ERROR: Claude Code CLI not found." >&2
    echo "The Docker image may need to be rebuilt:" >&2
    echo "  docker compose build --no-cache maestro" >&2
    exit 1
  fi
  echo "Claude Code CLI: $(command -v claude)"

  if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
    echo "WARNING: ANTHROPIC_API_KEY is not set. Claude Code will fail at runtime." >&2
  fi
fi

# -----------------------------------------------------------------
# Workspace root override
# -----------------------------------------------------------------
if [ -n "${MAESTRO_WORKSPACE_ROOT:-}" ]; then
  export WORKSPACE_ROOT="$MAESTRO_WORKSPACE_ROOT"
fi

# -----------------------------------------------------------------
# Token exchange: convert CURSOR_API_KEY → CURSOR_AUTH_TOKEN once
# so the agent subprocess does not hit macOS Keychain issues.
# (only relevant for cursor backend)
# -----------------------------------------------------------------
if [ "$BACKEND" = "cursor" ]; then
  if [ -z "${CURSOR_AUTH_TOKEN:-}" ] && [ -n "${CURSOR_API_KEY:-}" ]; then
    echo "Exchanging CURSOR_API_KEY for auth token..."
    TOKEN=$(python3 -c "
import httpx, json, sys
try:
    r = httpx.post(
        'https://api2.cursor.sh/auth/exchange_user_api_key',
        headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ${CURSOR_API_KEY}'},
        json={}, timeout=15,
    )
    r.raise_for_status()
    t = r.json().get('accessToken', '')
    if t:
        print(t)
    else:
        print('', file=sys.stderr)
        sys.exit(1)
except Exception as e:
    print(str(e), file=sys.stderr)
    sys.exit(1)
" 2>/dev/null) || true
    if [ -n "$TOKEN" ]; then
      export CURSOR_AUTH_TOKEN="$TOKEN"
      unset CURSOR_API_KEY
      echo "Auth token obtained."
    else
      echo "WARNING: Token exchange failed; agent may fall back to CURSOR_API_KEY."
    fi
  fi
fi

echo "Starting Maestro..."
exec "$@"
