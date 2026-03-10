"""POST /api/v1/refresh — trigger immediate poll cycle (SPEC §13.7.2)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING

from fastapi import APIRouter, HTTPException

if TYPE_CHECKING:
    from maestro.orchestrator.scheduler import Scheduler

router = APIRouter(prefix="/api/v1", tags=["refresh"])

_scheduler: Scheduler | None = None


def init(scheduler: "Scheduler") -> None:
    global _scheduler
    _scheduler = scheduler


@router.post("/refresh", status_code=202)
def refresh() -> dict[str, Any]:
    if _scheduler is None:
        raise HTTPException(500, "Scheduler not initialized")
    _scheduler.request_immediate_poll()
    return {
        "queued": True,
        "requested_at": datetime.now(timezone.utc).isoformat(),
        "operations": ["poll", "reconcile"],
    }
