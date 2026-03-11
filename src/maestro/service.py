"""Maestro service — the main orchestration lifecycle."""

from __future__ import annotations

import logging
import os
import signal
import threading
from pathlib import Path

from maestro.orchestrator.scheduler import Scheduler
from maestro.workflow.config import ConfigError, ServiceConfig, validate_dispatch_config
from maestro.workflow.loader import WorkflowLoadError, load_workflow
from maestro.workflow.watcher import WorkflowWatcher

log = logging.getLogger(__name__)


class MaestroService:
    """Starts the orchestrator, workflow watcher, and optional HTTP server."""

    def __init__(
        self,
        workflow_path: Path | None = None,
        *,
        port: int | None = None,
    ) -> None:
        self._workflow_path = workflow_path
        self._port_override = port
        self._scheduler: Scheduler | None = None
        self._watcher: WorkflowWatcher | None = None
        self._http_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._run_manager = None

    def start(self) -> None:
        self._setup_logging()

        wd = load_workflow(self._workflow_path)
        config = ServiceConfig.from_workflow(wd)
        validate_dispatch_config(config)

        port = self._port_override
        if port is None and config.server.port is not None:
            port = config.server.port

        self._scheduler = Scheduler(config, on_state_change=self._push_state)

        self._watcher = WorkflowWatcher(
            config.workflow_path,
            on_reload=self._on_config_reload,
        )
        self._watcher.start()

        if port is not None:
            self._start_http(config, port)

        self._register_signals()
        self._scheduler.start()

        log.info("Maestro service running. Press Ctrl+C to stop.")
        self._stop_event.wait()

    def stop(self) -> None:
        log.info("Stopping Maestro service.")
        if self._scheduler:
            self._scheduler.stop()
        if self._watcher:
            self._watcher.stop()
        self._stop_event.set()

    def _on_config_reload(self, config: ServiceConfig) -> None:
        if self._scheduler:
            self._scheduler.reload_config(config)

    def _push_state(self) -> None:
        """Push orchestrator state change to WebSocket subscribers via RunManager."""
        if self._run_manager and self._scheduler:
            snapshot = self._scheduler.state.snapshot()
            self._run_manager._broadcast({
                "type": "state_update",
                "state": snapshot,
            })

    def _start_http(self, config: ServiceConfig, port: int) -> None:
        def _run_server():
            import uvicorn
            from maestro.api.main import create_app

            app, run_manager = create_app(config, self._scheduler)
            self._run_manager = run_manager
            host = os.environ.get("MAESTRO_HTTP_HOST", "0.0.0.0")
            uvicorn.run(app, host=host, port=port, log_level="warning")

        self._http_thread = threading.Thread(target=_run_server, daemon=True)
        self._http_thread.start()
        host = os.environ.get("MAESTRO_HTTP_HOST", "0.0.0.0")
        log.info("HTTP API listening on http://%s:%d", host, port)

    def _register_signals(self) -> None:
        def handler(signum, frame):
            self.stop()

        try:
            signal.signal(signal.SIGINT, handler)
            signal.signal(signal.SIGTERM, handler)
        except (OSError, ValueError):
            pass

    @staticmethod
    def _setup_logging() -> None:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
