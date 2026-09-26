"""/calendar/events and /calendar/next-event (docs/api-contracts.md)."""
from fastapi import APIRouter, HTTPException, Query, Request, Response

router = APIRouter(prefix="/calendar")


@router.get("/events")
def events(request: Request, hours: int = Query(48, ge=1, le=168)):
    return request.app.state.calendar.events(hours)


@router.get("/next-event")
def next_event(
    request: Request,
    from_lat: float | None = Query(None, ge=-90, le=90),
    from_lon: float | None = Query(None, ge=-180, le=180),
):
    if (from_lat is None) != (from_lon is None):
        raise HTTPException(400, "Give both from_lat and from_lon, or neither (then the saved home is used)")
    result = request.app.state.calendar.next_event(from_lat, from_lon)
    if result is None:
        return Response(status_code=204)
    return result
