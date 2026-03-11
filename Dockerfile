FROM python:3.11-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates git \
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

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

RUN mkdir -p /data/workspaces

ENV MAESTRO_WORKSPACE_ROOT=/data/workspaces
ENV PYTHONUNBUFFERED=1

EXPOSE 8080

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["maestro", "start", "--port", "8080"]
