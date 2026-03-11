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
COPY dashboard/ dashboard/

# ---------------------------------------------------------------
# Cursor agent CLI
#
# Option A (recommended): install at build time
#   docker build --build-arg INSTALL_CURSOR_CLI=1 -t maestro .
#
# Option B: mount host CLI into the container at runtime
#   docker run -v $HOME/.local/share/cursor-agent:/opt/cursor-agent ...
#
# The entrypoint adds /opt/cursor-agent/current to PATH automatically.
# ---------------------------------------------------------------
ARG INSTALL_CURSOR_CLI=0
COPY scripts/install-cursor-cli.sh /tmp/install-cursor-cli.sh
RUN chmod +x /tmp/install-cursor-cli.sh && \
    if [ "$INSTALL_CURSOR_CLI" = "1" ]; then \
        /tmp/install-cursor-cli.sh; \
    fi && \
    rm -f /tmp/install-cursor-cli.sh

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

RUN mkdir -p /data/workspaces

ENV MAESTRO_WORKSPACE_ROOT=/data/workspaces
ENV PYTHONUNBUFFERED=1

EXPOSE 8080

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["maestro", "start", "--port", "8080"]
