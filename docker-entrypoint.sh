#!/usr/bin/env bash
set -euo pipefail

# -----------------------------------------------------------------
# Cursor agent CLI discovery
# -----------------------------------------------------------------
# Priority: 1) already on PATH  2) /opt/cursor-agent  3) mounted volume
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
  echo "Install it at build time (INSTALL_CURSOR_CLI=1) or mount it:" >&2
  echo "  docker run -v /path/to/agent:/usr/local/bin/agent ..." >&2
  exit 1
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
# -----------------------------------------------------------------
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

echo "Starting Maestro..."
exec "$@"
