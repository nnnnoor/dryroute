"""POST /trips, GET /trips, GET /dashboard, POST /demo/seed-trips (docs/api-contracts.md)."""
from app.integrations.google_calendar import dashboard_service, request_store
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.routes import check_trip_params

router = APIRouter()
USER = "demo"  # SessionStore scopes this key to the connected browser


class TripIn(BaseModel):
    from_lat: float = Field(ge=-90, le=90)
    from_lon: float = Field(ge=-180, le=180)
    to_lat: float | None = Field(None, ge=-90, le=90)
    to_lon: float | None = Field(None, ge=-180, le=180)
    parking_id: str | None = None
    depart_at: datetime | None = None
    arrive_by: datetime | None = None
    take: Literal["safe", "usual"] | None = None  # default: what the recommendation says
    event_id: str | None = None                   # set when the trip is for a calendar event


@router.post("/trips")
def record_trip(body: TripIn, request: Request):
    """Call when the student starts a trip (not on every /routes lookup)."""
    destination = check_trip_params(request, body.to_lat, body.to_lon, body.parking_id, body.depart_at, body.arrive_by)
    try:
        return dashboard_service(request).record_trip(USER, body.from_lat, body.from_lon, destination, body.take,
                                                       body.depart_at, body.arrive_by, body.event_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/trips")
def trips(request: Request):
    """The student's trips, newest first."""
    return sorted(request_store(request).get_trips(USER), key=lambda t: t["created_at"], reverse=True)


@router.get("/dashboard")
def dashboard(request: Request):
    return dashboard_service(request).summary(USER)


@router.post("/demo/seed-trips")
def seed_trips(request: Request):
    """Fill the dashboard with a realistic past week (replaces earlier demo trips; real trips stay)."""
    return dashboard_service(request).seed_demo_trips(USER)
