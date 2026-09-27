"""GET /alerts, POST /alerts/{alert_id}/read, POST /alerts/refresh (docs/api-contracts.md)."""
from fastapi import APIRouter, HTTPException, Request
from app.integrations.google_calendar import alerts_service

router = APIRouter(prefix="/alerts")
USER = "demo"  # SessionStore scopes this key to the connected browser


@router.get("")
def alerts(request: Request, include_resolved: bool = False):
    return alerts_service(request).for_user(USER, include_resolved)


@router.post("/refresh")
def refresh(request: Request):
    """Rebuild now instead of waiting for the next due poll (useful in the demo)."""
    service = alerts_service(request)
    service.refresh(USER)
    return service.for_user(USER)


@router.post("/{alert_id}/read")
def mark_read(alert_id: str, request: Request):
    if not alerts_service(request).mark_read(USER, alert_id):
        raise HTTPException(404, f"Unknown alert_id {alert_id!r}")
    return {"alert_id": alert_id, "read": True}
