"""Demo helpers: a test class starting soon, so trip alerts can be shown on stage (docs/api-contracts.md, /demo/*).

POST /demo/test-event adds one in-person class to this browser's calendar (whatever it's connected to: Google,
iCal import or the demo schedule), starting about 2 hours from now at an FIU building, so it's inside the
alert window. Combined with POST /demo/scenario {"scenario": "storm"}, the dashboard shows a route alert.
The event lives only in this browser's session; DELETE /demo/test-event removes it.
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.integrations.google_calendar import check_origin, invalidate_alerts, session
from app.timeutil import iso_utc

router = APIRouter(prefix="/demo")
TEST_ID = "dryroute_test_event"


class TestEventIn(BaseModel):
    minutes_from_now: int = Field(120, ge=30, le=600)  # the alert window is 12 h
    building: str = Field("PC", max_length=10)         # FIU building code, e.g. "PC", "GL", "CP"


class WithTestEvents:
    """Calendar source that adds the session's test event to the real calendar's events."""

    def __init__(self, source, connection):
        self.source, self.connection = source, connection

    def list_events(self, time_min, time_max):
        extra = [e for e in self.connection.get("test_events", [])
                 if time_min <= datetime.fromisoformat(e["start"]["dateTime"]) < time_max]
        events = self.source.list_events(time_min, time_max) + extra
        return sorted(events, key=lambda e: e["start"].get("dateTime") or e["start"].get("date"))


def _connection(request: Request) -> dict:
    check_origin(request)
    connection = session(request, required=True)
    if not connection:
        raise HTTPException(400, "Connect a calendar (or choose the demo schedule) first.")
    return connection


@router.post("/test-event")
def add_test_event(request: Request, body: TestEventIn | None = None):
    body = body or TestEventIn()
    connection = _connection(request)
    code = body.building.upper()
    building = request.app.state.calendar.buildings.get(code)
    if building is None:
        raise HTTPException(404, f"Unknown FIU building code {body.building!r}")
    now = datetime.now(timezone.utc)
    start = (now + timedelta(minutes=body.minutes_from_now)).replace(second=0, microsecond=0)
    start -= timedelta(minutes=start.minute % 5)  # a class-like time, e.g. 3:35
    connection["test_events"] = [{
        "id": TEST_ID, "summary": "DryRoute test class", "location": f"{code} 100",
        "start": {"dateTime": start.isoformat()}, "end": {"dateTime": (start + timedelta(minutes=75)).isoformat()},
    }]
    invalidate_alerts(connection)
    return {"event_id": TEST_ID, "event_name": "DryRoute test class", "start_time": iso_utc(start),
            "location": f"{code} 100", "building": building["name"]}


@router.delete("/test-event")
def remove_test_event(request: Request):
    connection = _connection(request)
    connection["test_events"] = []
    invalidate_alerts(connection)
    return {"removed": True}
