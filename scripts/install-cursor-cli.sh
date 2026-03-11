#!/usr/bin/env bash
#
# Install Cursor agent CLI into /opt/cursor-agent.
# Works on Linux x64 and arm64.
#
# Usage:
#   ./scripts/install-cursor-cli.sh            # auto-detect arch
#   CURSOR_CLI_VERSION=latest ./scripts/install-cursor-cli.sh
#
set -euo pipefail

INSTALL_DIR="/opt/cursor-agent"
CURSOR_CLI_VERSION="${CURSOR_CLI_VERSION:-latest}"

ARCH=$(uname -m)
case "$ARCH" in
  x86_64)  PLATFORM="linux-x64" ;;
  aarch64) PLATFORM="linux-arm64" ;;
  arm64)   PLATFORM="linux-arm64" ;;
  *)
    echo "ERROR: Unsupported architecture: $ARCH" >&2
    exit 1
    ;;
esac

echo "Installing Cursor agent CLI (${PLATFORM})..."

mkdir -p "$INSTALL_DIR"

# Cursor distributes the CLI via their install script.
# We download it and extract to INSTALL_DIR.
DOWNLOAD_URL="https://cli.cursor.com/linux/${PLATFORM}/cursor-agent"

echo "Downloading from ${DOWNLOAD_URL}..."
if curl -fsSL -o "${INSTALL_DIR}/cursor-agent" "$DOWNLOAD_URL" 2>/dev/null; then
  chmod +x "${INSTALL_DIR}/cursor-agent"
  ln -sf "${INSTALL_DIR}/cursor-agent" /usr/local/bin/agent
  echo "Cursor agent CLI installed to ${INSTALL_DIR}/cursor-agent"
  echo "Symlinked to /usr/local/bin/agent"
  agent --version 2>/dev/null && echo "Verified." || echo "Warning: version check failed (may need runtime auth)."
else
  echo "WARNING: Could not download Cursor CLI from ${DOWNLOAD_URL}." >&2
  echo "The CLI must be mounted at runtime via:" >&2
  echo "  docker run -v /path/to/cursor-agent:/usr/local/bin/agent ..." >&2
  exit 0
fi
