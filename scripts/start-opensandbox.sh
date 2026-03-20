#!/usr/bin/env bash
#
# Start the OpenSandbox server inside Docker.
# Installs opensandbox-server, writes a docker-runtime config, then starts the server.
#
set -euo pipefail

echo "==> Installing opensandbox-server..."
pip install opensandbox-server -q

echo "==> Generating config..."
if [ ! -f /root/.sandbox.toml ]; then
  opensandbox-server init-config /root/.sandbox.toml --example docker
else
  echo "Config already exists at /root/.sandbox.toml; reusing existing config."
fi

# Patch localhost → 0.0.0.0 so the server is reachable from other containers.
sed -i 's|host = "127.0.0.1"|host = "0.0.0.0"|g' /root/.sandbox.toml 2>/dev/null || true
sed -i 's|host = "localhost"|host = "0.0.0.0"|g'  /root/.sandbox.toml 2>/dev/null || true

echo "==> Effective config:"
cat /root/.sandbox.toml
echo ""

echo "==> Starting opensandbox-server..."
exec opensandbox-server
