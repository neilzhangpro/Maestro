"""Watch WORKFLOW.md for changes and trigger hot-reload."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Callable

from maestro.workflow.config import ServiceConfig
from maestro.workflow.loader import WorkflowDefinition, WorkflowLoadError, load_workflow

log = logging.getLogger(__name__)


class WorkflowWatcher:
    """Poll-based file watcher for WORKFLOW.md hot-reload.

    Invalid reloads preserve the last known good config and emit warnings.
    """

    def __init__(
        self,
        path: Path,
        on_reload: Callable[[ServiceConfig], None],
        *,
        poll_interval_s: float = 2.0,
    ) -> None:
        self._path = path.resolve()
        self._on_reload = on_reload
        self._poll_interval = poll_interval_s
        self._last_mtime: float = 0.0
        self._last_good: ServiceConfig | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        self._last_mtime = self._current_mtime()
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        log.info("Workflow watcher started: %s", self._path)

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)

    def check_once(self) -> bool:
        mtime = self._current_mtime()
        if mtime <= self._last_mtime:
            return False
        self._last_mtime = mtime
        return self._try_reload()

    def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            self._stop_event.wait(self._poll_interval)
            if self._stop_event.is_set():
                break
            try:
                self.check_once()
            except Exception:
                log.warning("Watcher poll error", exc_info=True)

    def _try_reload(self) -> bool:
        try:
            wd = load_workflow(self._path)
            config = ServiceConfig.from_workflow(wd)
        except (WorkflowLoadError, Exception) as exc:
            log.error(
                "WORKFLOW.md reload failed — keeping last good config: %s", exc,
            )
            return False

        log.info("WORKFLOW.md reloaded successfully.")
        self._last_good = config
        self._on_reload(config)
        return True

    def _current_mtime(self) -> float:
        try:
            return self._path.stat().st_mtime
        except OSError:
            return 0.0
