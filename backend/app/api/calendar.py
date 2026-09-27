"""/calendar/events and /calendar/next-event (docs/api-contracts.md)."""
from fastapi import APIRouter, HTTPException, Query, Request, Response
from app.integrations.google_calendar import COOKIE, calendar_service, check_origin, configured, session, set_cookie

router = APIRouter(prefix="/calendar")


@router.get("/events")
def events(request: Request, hours: int = Query(48, ge=1, le=168)):
    return calendar_service(request).events(hours)


@router.get("/next-event")
def next_event(
    request: Request,
    from_lat: float | None = Query(None, ge=-90, le=90),
    from_lon: float | None = Query(None, ge=-180, le=180),
):
    if (from_lat is None) != (from_lon is None):
        raise HTTPException(400, "Give both from_lat and from_lon, or neither (then the saved home is used)")
    result = calendar_service(request).next_event(from_lat, from_lon)
    if result is None:
        return Response(status_code=204)
    return result


@router.get("/connection")
def connection(request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    current = session(request)
    source = current["source"] if current else "disconnected"
    connected = bool(current and (source == "demo" or (source == "google" and current["tokens"])))
    return {"connected": connected, "source": source, "google_configured": configured(request.app.state.settings)}


@router.post("/demo")
def demo(request: Request, response: Response):
    check_origin(request)
    state = request.app.state
    state.calendar_sessions.sessions.pop(request.cookies.get(COOKIE), None)
    key = state.calendar_sessions.create("demo", profile=state.store.get_user())
    set_cookie(response, state.settings, COOKIE, key)
    return {"connected": True, "source": "demo", "google_configured": configured(state.settings)}


@router.delete("/connection")
def disconnect(request: Request, response: Response):
    check_origin(request)
    state = request.app.state
    state.calendar_sessions.sessions.pop(request.cookies.get(COOKIE), None)
    # Tombstone prevents silently substituting the demo after disconnect.
    set_cookie(response, state.settings, COOKIE, state.calendar_sessions.create("disconnected"))
    return {"connected": False, "source": "disconnected", "google_configured": configured(state.settings)}
