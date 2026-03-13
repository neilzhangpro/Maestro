.PHONY: up down restart logs maestro-logs sandbox-logs \
        build dev tui install test clean help

# ── Docker Compose ──────────────────────────────────────────────────────────

## Start all services (opensandbox + maestro) in the background
up:
	docker compose up -d --build
	@echo ""
	@echo "  Maestro   → http://localhost:8080"
	@echo "  OpenSandbox → http://localhost:8899"
	@echo ""
	@echo "  Run 'make tui' to launch terminal workbench."
	@echo "  Run 'make logs' to tail all logs."

## Stop all services
down:
	docker compose down

## Rebuild and restart all services
restart:
	docker compose down
	docker compose up -d --build

## Tail logs from all services
logs:
	docker compose logs -f

## Tail Maestro logs only
maestro-logs:
	docker compose logs -f maestro

## Tail OpenSandbox logs only
sandbox-logs:
	docker compose logs -f opensandbox

## Rebuild images without starting
build:
	docker compose build

# ── Local Development ────────────────────────────────────────────────────────

## Create .venv and install all dependencies (including sandbox + dev extras)
install:
	python3 -m venv .venv
	.venv/bin/pip install -e ".[dev,sandbox]"
	@echo ""
	@echo "  Activate with: source .venv/bin/activate"

## Start Maestro locally (requires .venv and a running opensandbox-server)
dev:
	@if [ ! -f .env ]; then cp .env.example .env; echo "Created .env from .env.example — fill in your keys."; fi
	set -a && . ./.env && set +a && .venv/bin/maestro start

## Launch the terminal workbench (connect to running Maestro)
tui:
	.venv/bin/maestro tui

## Start opensandbox-server locally (separate terminal)
sandbox-dev:
	@echo "Starting local opensandbox-server on :8899 ..."
	.venv/bin/pip install opensandbox-server -q
	SANDBOX_PORT=8899 .venv/bin/opensandbox-server

# ── Testing ──────────────────────────────────────────────────────────────────

## Run the Maestro unit test suite
test:
	.venv/bin/pytest tests/ -v --tb=short

# ── Cleanup ──────────────────────────────────────────────────────────────────

## Remove build artefacts, caches, and Docker volumes
clean:
	docker compose down -v --remove-orphans
	rm -rf .venv __pycache__ src/maestro/__pycache__ .pytest_cache .ruff_cache

# ── Help ─────────────────────────────────────────────────────────────────────

## Print this help message
help:
	@echo ""
	@echo "Maestro — available targets:"
	@echo ""
	@grep -E '^##' Makefile | sed 's/## /  /' | paste - <(grep -E '^[a-z].*:' Makefile | sed 's/:.*//')  || \
	 grep -E '^(## |[a-z][a-z-]*:)' Makefile | awk '/^## /{desc=$$0; next} {printf "  %-18s %s\n", $$1, desc}' | sed 's/## //'
	@echo ""

.DEFAULT_GOAL := help
