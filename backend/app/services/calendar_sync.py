"""Classes from the calendar -> where they are -> when to leave (docs/api-contracts.md, /calendar/*).

Leave-by time: arrive at the lot by (class start - arrival buffer - walk from the lot - time to find a
spot), then subtract the drive time of the route the recommendation says to take (safe route when
rerouting, else usual).
"""
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import Settings
from app.db.store import Store
from app.services.parking import ParkingService, distance_m
from app.services.trips import TripPlanner
from app.timeutil import iso_utc

ONLINE = re.compile(r"\b(online|zoom|teams|remote|virtual)\b", re.IGNORECASE)
TOKEN = re.compile(r"[A-Z][A-Z0-9/]*")


class CalendarService:
    def __init__(self, source, store: Store, trips: TripPlanner, parking: ParkingService,
                 settings: Settings, buildings_path: Path):
        self.source = source  # FakeCalendar and GoogleCalendar both provide list_events(time_min, time_max)
        self.store = store
        self.trips = trips
        self.parking = parking
        self.settings = settings
        self.buildings = json.loads(buildings_path.read_text(encoding="utf-8"))

    # ---------------------------------------------------------------- locations
    def resolve(self, location: str | None) -> dict | None:
        """FIU building for a location like "PC 213", "PC213" or "Graham Center (GC) 243"; None if unknown
        or online."""
        if not location or ONLINE.search(location):
            return None
        for token in TOKEN.findall(location.upper()):
            for code in (token, token.rstrip("0123456789")):  # "PC213" -> "PC"
                if code in self.buildings:
                    return {"code": code, **self.buildings[code]}
        return None

    # ---------------------------------------------------------------- events
    def events(self, hours: int, now: datetime | None = None) -> list[dict]:
        now = now or datetime.now(timezone.utc)
        return [self._event(e)[0] for e in self._timed(now, now + timedelta(hours=hours))]

    def next_event(self, from_lat: float | None = None, from_lon: float | None = None,
                   now: datetime | None = None) -> dict | None:
        """The next in-person event and the plan to get there. None when nothing is coming up."""
        return self.plan_next(from_lat, from_lon, now)[0]

    def plan_next(self, from_lat: float | None = None, from_lon: float | None = None,
                  now: datetime | None = None) -> tuple[dict | None, dict | None]:
        """(next_event response, full /routes-style trip). The trip is None when there's no event or
        its location couldn't be resolved. Alerts use the full trip (risky streets, closures)."""
        now = now or datetime.now(timezone.utc)
        upcoming = self._timed(now, now + timedelta(days=self.settings.calendar_lookahead_days))
        in_person = [e for e in upcoming if e.get("location") and not ONLINE.search(e["location"])]
        if not in_person:
            return None, None
        event, building = self._event(in_person[0])
        user = self.store.get_user()
        if building is None or (from_lat is None and not user.get("home")):
            return {**event, "recommended_departure": None, "leave_in_minutes": None,
                    "trip": None, "route_query": None}, None

        if from_lat is None:
            from_lat, from_lon = user["home"]["lat"], user["home"]["lon"]
        start = datetime.fromisoformat(in_person[0]["start"]["dateTime"])

        parking_id, walk_min = self._pick_lot(building, user)
        parked_by = start - timedelta(minutes=user["arrival_buffer_minutes"] + walk_min)
        # Reach the lot early enough to find a spot (more on weekday mornings)
        search_min = self.parking.search_minutes(parking_id, parked_by) if parking_id else 0
        arrive_by = parked_by - timedelta(minutes=search_min)
        if parking_id:
            dest = {"parking_id": parking_id}
        else:
            dest = {"to_lat": building["lat"], "to_lon": building["lon"]}
        trip = self.trips.plan(from_lat, from_lon, **dest, arrive_by=arrive_by)

        take = "safe" if trip["recommendation"]["action"] in ("reroute", "reroute_caution") else "usual"
        route = trip[take]
        # Round down to the minute: "leave at 8:27", a few seconds early rather than late
        depart = datetime.fromisoformat(route["depart_at"]).replace(second=0, microsecond=0)
        parking = trip["parking"]
        return {
            **event,
            "recommended_departure": iso_utc(depart),
            "leave_in_minutes": round((depart - now).total_seconds() / 60),
            "trip": {
                "compromised": trip["compromised"],
                "recommendation": trip["recommendation"],
                "take": take,
                "eta_minutes": route["eta_minutes"],
                "parking_id": parking_id,
                "parking_name": parking["planned"]["name"] if parking else None,
                "parking_hazardous": parking["hazardous"] if parking else False,
                "parking_search_minutes": search_min,
                "walk_minutes": walk_min,
            },
            "route_query": {"from_lat": from_lat, "from_lon": from_lon, **dest, "arrive_by": iso_utc(arrive_by)},
        }, trip

    # ---------------------------------------------------------------- helpers
    def _timed(self, time_min, time_max) -> list[dict]:
        """Events with a start time (all-day events have only a date and are skipped)."""
        return [e for e in self.source.list_events(time_min, time_max) if "dateTime" in e.get("start", {})]

    def _event(self, e: dict) -> tuple[dict, dict | None]:
        building = self.resolve(e.get("location"))
        return {
            "event_id": e["id"],
            "event_name": e.get("summary"),
            "start_time": iso_utc(datetime.fromisoformat(e["start"]["dateTime"])),
            "end_time": iso_utc(datetime.fromisoformat(e["end"]["dateTime"])),
            "location": e.get("location"),
            "location_point": {"lat": building["lat"], "lon": building["lon"]} if building else None,
        }, building

    def _pick_lot(self, building: dict, user: dict) -> tuple[str | None, int]:
        """The user's preferred lot (always honored, however far), else the nearest usable one.
        (None, 0) when no lot is preferred and no usable lot is within walking range."""
        target = (building["lon"], building["lat"])
        parking_id = user.get("preferred_parking_id")
        if parking_id:
            dist = distance_m(self.parking.center(parking_id), target)
        else:
            parking_id, dist = self.parking.nearest_suggestable(building["lat"], building["lon"])
            if dist > self.settings.max_lot_walk_m:
                return None, 0
        return parking_id, round(dist * self.settings.walk_detour / self.settings.walk_m_per_min)
