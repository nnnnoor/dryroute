"""GET /alerts, POST /alerts/{alert_id}/read, POST /alerts/refresh (docs/api-contracts.md)."""
from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/alerts")
USER = "demo"  # one demo user until Google login exists


@router.get("")
def alerts(request: Request, include_resolved: bool = False):
    return request.app.state.alerts.for_user(USER, include_resolved)


@router.post("/refresh")
def refresh(request: Request):
    """Rebuild now instead of waiting for the next due poll (useful in the demo)."""
    service = request.app.state.alerts
    service.refresh(USER)
    return service.for_user(USER)


@router.post("/{alert_id}/read")
def mark_read(alert_id: str, request: Request):
    if not request.app.state.alerts.mark_read(USER, alert_id):
        raise HTTPException(404, f"Unknown alert_id {alert_id!r}")
    return {"alert_id": alert_id, "read": True}
