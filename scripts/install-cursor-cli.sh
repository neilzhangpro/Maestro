#!/usr/bin/env bash
#
# Install Cursor agent CLI into /opt/cursor-agent.
# Downloads the official agent-cli-package tarball from Cursor.
#
# Works on Linux x64 and arm64 (designed for Docker builds).
#
# Usage:
#   ./scripts/install-cursor-cli.sh
#   CURSOR_AGENT_VERSION=2026.02.27-e7d2ef6 ./scripts/install-cursor-cli.sh
#
set -euo pipefail

INSTALL_DIR="/opt/cursor-agent"
CURSOR_AGENT_VERSION="${CURSOR_AGENT_VERSION:-2026.02.27-e7d2ef6}"

OS="$(uname -s)"
case "${OS}" in
  Linux*)   OS="linux" ;;
  Darwin*)  OS="darwin" ;;
  *)
    echo "ERROR: Unsupported OS: ${OS}" >&2
    exit 1
    ;;
esac

ARCH="$(uname -m)"
case "${ARCH}" in
  x86_64|amd64)   ARCH="x64" ;;
  arm64|aarch64)   ARCH="arm64" ;;
  *)
    echo "ERROR: Unsupported architecture: ${ARCH}" >&2
    exit 1
    ;;
esac

DOWNLOAD_URL="https://downloads.cursor.com/lab/${CURSOR_AGENT_VERSION}/${OS}/${ARCH}/agent-cli-package.tar.gz"

echo "Installing Cursor agent CLI (${OS}/${ARCH}, version ${CURSOR_AGENT_VERSION})..."
echo "Download URL: ${DOWNLOAD_URL}"

mkdir -p "${INSTALL_DIR}"

if curl -fSL --progress-bar "${DOWNLOAD_URL}" \
   | tar --strip-components=1 -xzf - -C "${INSTALL_DIR}"; then
  chmod +x "${INSTALL_DIR}/cursor-agent"
  ln -sf "${INSTALL_DIR}/cursor-agent" /usr/local/bin/agent
  ln -sf "${INSTALL_DIR}/cursor-agent" /usr/local/bin/cursor-agent
  echo ""
  echo "Cursor agent CLI installed to ${INSTALL_DIR}"
  echo "Symlinked to /usr/local/bin/agent"
  agent --version 2>/dev/null && echo "Verified." || echo "Warning: version check failed (may need runtime auth)."
else
  echo "ERROR: Failed to download Cursor agent CLI from ${DOWNLOAD_URL}" >&2
  echo "Check your network or try a different CURSOR_AGENT_VERSION." >&2
  exit 1
fi
