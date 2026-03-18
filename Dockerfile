FROM python:3.11-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates git nodejs npm \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src/ src/

RUN pip install --no-cache-dir -e .

COPY WORKFLOW.md ./
COPY config/ config/

# ---------------------------------------------------------------
# Cursor agent CLI — downloaded from official Cursor distribution
#
# Override version at build time:
#   docker build --build-arg CURSOR_AGENT_VERSION=2026.02.27-e7d2ef6 .
# ---------------------------------------------------------------
ARG CURSOR_AGENT_VERSION=2026.02.27-e7d2ef6
COPY scripts/install-cursor-cli.sh /tmp/install-cursor-cli.sh
RUN chmod +x /tmp/install-cursor-cli.sh && \
    CURSOR_AGENT_VERSION=${CURSOR_AGENT_VERSION} /tmp/install-cursor-cli.sh && \
    rm -f /tmp/install-cursor-cli.sh

# ---------------------------------------------------------------
# RTK CLI — token-optimized proxy for Bash-heavy Claude Code flows
# ---------------------------------------------------------------
ARG RTK_VERSION=0.28.2
RUN set -eux; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
      amd64) rtk_arch="x86_64-unknown-linux-musl" ;; \
      arm64) rtk_arch="aarch64-unknown-linux-gnu" ;; \
      *) echo "Unsupported architecture: $arch" >&2; exit 1 ;; \
    esac; \
    curl -fsSL "https://github.com/rtk-ai/rtk/releases/download/v${RTK_VERSION}/rtk-${rtk_arch}.tar.gz" -o /tmp/rtk.tar.gz; \
    tar -xzf /tmp/rtk.tar.gz -C /tmp; \
    install -m 0755 /tmp/rtk /usr/local/bin/rtk; \
    rm -f /tmp/rtk.tar.gz /tmp/rtk; \
    rtk --version

# ---------------------------------------------------------------
# Claude Code CLI — installed via npm from @anthropic-ai/claude-code
# ---------------------------------------------------------------
RUN npm install -g @anthropic-ai/claude-code && \
    claude --version 2>/dev/null && echo "Claude Code CLI installed." || \
    echo "Warning: claude --version check failed (may need runtime API key)."

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

RUN mkdir -p /data/workspaces

ENV MAESTRO_WORKSPACE_ROOT=/data/workspaces
ENV PYTHONUNBUFFERED=1

EXPOSE 8080

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["maestro", "start", "--port", "8080"]
