from __future__ import annotations

from fastapi import APIRouter, Request, Response

from ai_usage_monitor.models.schemas import HealthResponse

router = APIRouter(prefix="/api/v1")


@router.get("/health", response_model=HealthResponse)
def health(request: Request, response: Response) -> HealthResponse:
    database_ok = request.app.state.repository.ping()
    scheduler = request.app.state.scheduler
    scheduler_ok = scheduler is None or (scheduler.running and scheduler.last_cycle_ok)
    if not database_ok or not scheduler_ok:
        response.status_code = 503
    return HealthResponse(
        status="ok" if database_ok and scheduler_ok else "degraded",
        database="ok" if database_ok else "error",
        scheduler="ok"
        if scheduler_ok
        else ("error" if scheduler and scheduler.running else "stopped"),
    )
