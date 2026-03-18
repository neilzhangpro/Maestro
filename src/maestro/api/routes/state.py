"""GET /api/v1/state — runtime state snapshot (SPEC §13.7.2)."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from fastapi import APIRouter, HTTPException

if TYPE_CHECKING:
    from maestro.orchestrator.scheduler import Scheduler

router = APIRouter(prefix="/api/v1", tags=["state"])

_scheduler: Scheduler | None = None


def init(scheduler: "Scheduler") -> None:
    global _scheduler
    _scheduler = scheduler


@router.get("/state")
def get_state() -> dict[str, Any]:
    if _scheduler is None:
        raise HTTPException(500, "Scheduler not initialized")
    return _scheduler.snapshot()
