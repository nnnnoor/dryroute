"""A trip = route + (when it ends at an FIU lot) the parking check. Used by /routes and the calendar."""
from datetime import datetime

from app.db.store import Store
from app.services.parking import ParkingService
from app.services.route_planner import RoutePlanner


class TripPlanner:
    def __init__(self, planner: RoutePlanner, parking: ParkingService, store: Store):
        self.planner = planner
        self.parking = parking
        self.store = store

    def plan(self, from_lat: float, from_lon: float, to_lat: float | None = None, to_lon: float | None = None,
             parking_id: str | None = None, depart_at: datetime | None = None,
             arrive_by: datetime | None = None) -> dict:
        """Give either to_lat/to_lon or a known parking_id. Raises ValueError when start and destination
        are the same place."""
        if parking_id is not None:
            to_lon, to_lat = self.parking.center(parking_id)
        result = self.planner.plan(from_lat, from_lon, to_lat, to_lon, depart_at, arrive_by)
        if parking_id is not None:
            result["parking"] = self.parking.assess(parking_id)
            extra = self.parking.message(result["parking"])
            if extra:
                result["recommendation"]["message"] += " " + extra
        return result
