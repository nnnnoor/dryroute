"""Trips the student committed to (POST /trips) and the personal stats built from them (GET /dashboard).

A trip is recorded when the student starts it, not on every /routes lookup (the app calls /routes all the
time while browsing). Each record is a small summary of the planned trip and which route was taken.

"Time saved" is an estimate: driving into a flooded road costs about settings.flood_detour_minutes
(crawling through water, turning back, detouring). A compromised trip on the safe route saves that,
times the share of flood-prone road it avoided, minus the safe route's extra minutes (never below 0).
"""
import uuid
from datetime import datetime, timedelta, timezone

from app.config import Settings
from app.db.store import Store
from app.services.flood_risk import RiskService
from app.services.trips import TripPlanner
from app.timeutil import iso_utc

# Realistic past trips for the demo (POST /demo/seed-trips): (days ago, from, destination, scenario)
BRICKELL, LITTLE_HAVANA, CORAL_GABLES, DOWNTOWN = (25.7617, -80.1918), (25.7650, -80.2200), (25.7500, -80.2600), (25.7743, -80.1937)
DEMO_TRIPS = [
    (6, BRICKELL, {"parking_id": "way/112781054"}, "storm"),       # -> Parking Garage 6
    (5, LITTLE_HAVANA, {"parking_id": "way/112762942"}, "storm"),  # -> Gold Parking Garage
    (5, BRICKELL, {"parking_id": "way/112762942"}, "live"),
    (3, CORAL_GABLES, {"parking_id": "way/112762946"}, "live"),    # -> Blue Parking Garage
    (2, DOWNTOWN, {"parking_id": "way/112762951"}, "storm"),       # -> Red Parking Garage
    (1, BRICKELL, {"parking_id": "way/112781054"}, "live"),
]


class DashboardService:
    def __init__(self, store: Store, trips: TripPlanner, risk: RiskService, settings: Settings):
        self.store = store
        self.trips = trips
        self.risk = risk
        self.settings = settings

    def record_trip(self, user_id: str, from_lat: float, from_lon: float, destination: dict,
                    take: str | None = None, depart_at: datetime | None = None,
                    arrive_by: datetime | None = None, event_id: str | None = None,
                    now: datetime | None = None, demo: bool = False) -> dict:
        """Plan the trip (same as /routes), then save which route the student took.
        take: "safe" | "usual"; default: what the recommendation says."""
        plan = self.trips.plan(from_lat, from_lon, destination.get("to_lat"), destination.get("to_lon"),
                               destination.get("parking_id"), depart_at, arrive_by)
        action = plan["recommendation"]["action"]
        take = take or ("safe" if action in ("reroute", "reroute_caution") else "usual")
        route, usual, safe = plan[take], plan["usual"], plan["safe"]
        on_safe = take == "safe" and not plan["same_route"]
        avoided_m = plan["comparison"]["high_risk_m_avoided"] if on_safe else 0
        extra = plan["comparison"]["extra_minutes"] if on_safe else 0.0
        share = avoided_m / usual["high_risk_m"] if usual["high_risk_m"] else 0.0
        saved = max(0.0, self.settings.flood_detour_minutes * share - extra) if plan["compromised"] else 0.0
        parking = plan["parking"]
        trip = {
            "trip_id": "tr_" + uuid.uuid4().hex[:10],
            "created_at": iso_utc(now or datetime.now(timezone.utc)),
            "depart_at": route["depart_at"],
            "arrive_at": route["arrive_at"],
            "from": {"lat": from_lat, "lon": from_lon},
            "destination": {**destination, "name": parking["planned"]["name"] if parking else None},
            "event_id": event_id,
            "scenario": self.risk.scenario,
            "take": take,
            "recommendation": action,
            "compromised": plan["compromised"],
            "eta_minutes": route["eta_minutes"],
            "extra_minutes": round(extra, 1),
            "high_risk_m_avoided": avoided_m,
            "high_risk_segments_avoided": plan["comparison"]["high_risk_segments_avoided"] if on_safe else 0,
            "time_saved_minutes": round(saved, 1),
            "parking_id": parking["planned"]["parking_id"] if parking else None,
            "parking_hazardous": parking["hazardous"] if parking else False,
            "demo": demo,
        }
        self.store.save_trip(user_id, trip)
        return trip

    def summary(self, user_id: str) -> dict:
        trips = sorted(self.store.get_trips(user_id), key=lambda t: t["created_at"], reverse=True)
        return {
            "trips_planned": len(trips),
            "time_saved_minutes": round(sum(t["time_saved_minutes"] for t in trips)),
            "risky_segments_avoided": sum(t["high_risk_segments_avoided"] for t in trips),
            "alerts_received": len(self.store.get_alerts(user_id)),
            "high_risk_km_avoided": round(sum(t["high_risk_m_avoided"] for t in trips) / 1000, 1),
            "compromised_trips": sum(t["compromised"] for t in trips),
            "safer_routes_taken": sum(t["take"] == "safe" and t["high_risk_m_avoided"] > 0 for t in trips),
            "recent_trips": trips[:5],
        }

    def seed_demo_trips(self, user_id: str, now: datetime | None = None) -> dict:
        """Replace the demo trips with a realistic week of them (idempotent). Uses the real planner, with
        each trip's scenario for this request only (the shared demo switch doesn't move)."""
        now = now or datetime.now(timezone.utc)
        self.store.delete_demo_trips(user_id)
        for days_ago, (lat, lon), destination, scenario in DEMO_TRIPS:
            with self.risk.using(scenario):
                when = (now - timedelta(days=days_ago)).replace(hour=12, minute=15, second=0, microsecond=0)
                self.record_trip(user_id, lat, lon, destination, depart_at=when, now=when, demo=True)
        return self.summary(user_id)
