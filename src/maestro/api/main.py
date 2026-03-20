"""FastAPI application for Maestro — REST API and WebSocket events."""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from maestro.api.routes import issues as issues_routes
from maestro.api.routes import runs as runs_routes
from maestro.api.routes import state as state_routes
from maestro.api.routes import refresh as refresh_routes
from maestro.api.run_manager import RunManager

if TYPE_CHECKING:
    from maestro.orchestrator.scheduler import Scheduler
    from maestro.workflow.config import ServiceConfig

log = logging.getLogger(__name__)

def create_app(
    config: "ServiceConfig | None" = None,
    scheduler: "Scheduler | None" = None,
) -> FastAPI:
    """Build the FastAPI application with optional scheduler integration."""

    run_manager = RunManager()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if config is not None:
            issues_routes.init(config)

        if scheduler is not None:
            state_routes.init(scheduler)
            refresh_routes.init(scheduler)

        if config is not None and scheduler is not None:
            runs_routes.init(config, scheduler, run_manager)

        log.info("Maestro API started.")
        yield

    app = FastAPI(title="Maestro API", version="0.2.0", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(issues_routes.router)
    app.include_router(runs_routes.router)
    app.include_router(state_routes.router)
    app.include_router(refresh_routes.router)

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/v1/orchestrator")
    def orchestrator_snapshot():
        """Combined state endpoint for the dashboard."""
        result: dict = {"status": "no_scheduler"}
        if scheduler:
            snapshot = scheduler.snapshot()
            snapshot["runs"] = run_manager.list_runs()
            result = snapshot
        return result

    @app.websocket("/api/ws")
    async def websocket_endpoint(ws: WebSocket):
        await ws.accept()
        queue = run_manager.subscribe()
        try:
            while True:
                event = await queue.get()
                await ws.send_text(json.dumps(event, default=str))
        except (WebSocketDisconnect, Exception):
            pass
        finally:
            run_manager.unsubscribe(queue)

    @app.get("/")
    def root():
        return JSONResponse({"name": "Maestro", "version": "0.2.0", "api": "/api"})

    return app, run_manager
