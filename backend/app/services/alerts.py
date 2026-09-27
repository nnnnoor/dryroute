"""In-app alerts (docs/api-contracts.md, /alerts). The app polls GET /alerts; this rebuilds them when due.

Sources:
- "trip": the next class's trip (calendar + routing + parking): flooded route, flood-prone lot,
  heavy traffic, closures on the way.
- "nws":  National Weather Service flood alerts in effect at departure.
- "tide": Biscayne Bay (Virginia Key) water level at or above the NWS flood thresholds.

Each alert has a stable id per situation, so a rebuild updates it instead of adding a duplicate, keeps
"read" (unless it got more severe), and marks alerts whose situation is over as resolved. A source that
failed this time resolves nothing: "couldn't check" is not "all clear".
"""
import hashlib
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.config import Settings
from app.db.store import Store
from app.integrations.live_conditions import LiveConditions
from app.services.calendar_sync import CalendarService
from app.services.parking import ParkingService
from app.timeutil import iso_utc

MIAMI = ZoneInfo("America/New_York")
SEVERITY_RANK = {"info": 0, "warning": 1, "critical": 2}
NWS_SEVERITY = {"Extreme": "critical", "Severe": "critical", "Moderate": "warning"}
FIU_CENTER = (25.7563, -80.3736)
LIVE_HORIZON = timedelta(hours=3)  # live conditions (NWS validity, tide predictions) are short-range


def _clock(iso: str) -> str:
    """ "2026-09-28T12:25:00Z" -> "8:25 AM" (Miami time), for alert text."""
    return f"{datetime.fromisoformat(iso).astimezone(MIAMI):%I:%M %p}".lstrip("0")


class AlertService:
    def __init__(self, store: Store, calendar: CalendarService, parking: ParkingService,
                 live: LiveConditions | None, settings: Settings):
        self.store = store
        self.calendar = calendar
        self.parking = parking
        self.live = live
        self.settings = settings
        self._last_refresh: dict[str, float] = {}

    # ---------------------------------------------------------------- API
    def for_user(self, user_id: str = "demo", include_resolved: bool = False,
                 now: datetime | None = None) -> list[dict]:
        if time.monotonic() - self._last_refresh.get(user_id, -1e9) >= self.settings.alerts_refresh_seconds:
            self.refresh(user_id, now)
        alerts = [a for a in self.store.get_alerts(user_id) if include_resolved or a["active"]]
        alerts.sort(key=lambda a: (SEVERITY_RANK[a["severity"]], a["updated_at"]), reverse=True)
        return alerts

    def mark_read(self, user_id: str, alert_id: str) -> bool:
        for a in self.store.get_alerts(user_id):
            if a["alert_id"] == alert_id:
                self.store.save_alert(user_id, {**a, "read": True})
                return True
        return False

    def invalidate(self) -> None:
        """Rebuild on the next poll (e.g. after the demo scenario changes)."""
        self._last_refresh.clear()

    # ---------------------------------------------------------------- building
    def refresh(self, user_id: str = "demo", now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        candidates, checked = [], set()

        event, trip = self.calendar.plan_next(now=now)
        checked.add("trip")
        depart = None
        if event and trip:
            start = datetime.fromisoformat(event["start_time"])
            if start - now <= timedelta(hours=self.settings.alert_lookahead_hours):
                candidates += self._trip_alerts(event, trip)
                depart = datetime.fromisoformat(event["recommended_departure"])

        if self.live:
            at = depart if depart and now <= depart <= now + LIVE_HORIZON else now
            destination = (event["location_point"]["lat"], event["location_point"]["lon"]) \
                if event and event.get("location_point") else FIU_CENTER
            home = self.store.get_user(user_id)["home"]
            points = [(home["lat"], home["lon"]), destination] if home else [destination]
            conditions = self.live.check(points, at, now)
            if conditions["nws_alerts"]["status"] == "ok":
                checked.add("nws")
                candidates += [self._nws_alert(a) for a in conditions["nws_alerts"]["data"]]
            if conditions["tide"]["status"] == "ok":
                checked.add("tide")
                tide = self._tide_alert(conditions["tide"]["data"], at)
                candidates += [tide] if tide else []

        self._save(user_id, candidates, checked, now)
        self._last_refresh[user_id] = time.monotonic()

    def _trip_alerts(self, event: dict, trip: dict) -> list[dict]:
        name, event_id = event["event_name"], event["event_id"]
        summary = event["trip"]
        route = trip[summary["take"]]
        leave = _clock(event["recommended_departure"])
        base = {"source": "trip", "event_id": event_id, "segment_id": None, "parking_id": None,
                "expires_at": event["start_time"]}
        alerts = []

        if trip["compromised"]:
            parking_text = self.parking.message(trip["parking"]) if trip["parking"] else None
            route_text = trip["recommendation"]["message"]
            if parking_text:  # the parking sentence gets its own alert
                route_text = route_text.replace(" " + parking_text, "")
            worst = max((s for s in trip["usual"]["risk_segments"] if s["risk_label"] == "high"),
                        key=lambda s: s["risk_score"], default=None)
            alerts.append({**base, "key": f"route_flood|{event_id}", "type": "route_flood",
                           "severity": "critical" if trip["recommendation"]["action"] == "no_alternative" else "warning",
                           "title": f"High flood risk on your route to {name}",
                           "message": f"{route_text} Leave by {leave}.",
                           "segment_id": worst["segment_id"] if worst else None})

        if trip["parking"] and trip["parking"]["hazardous"]:
            alerts.append({**base, "key": f"parking_flood|{event_id}", "type": "parking_flood", "severity": "warning",
                           "title": f"{trip['parking']['planned']['name']} may flood",
                           "message": self.parking.message(trip["parking"]),
                           "parking_id": trip["parking"]["planned"]["parking_id"]})

        delay = route.get("traffic_delay_minutes")
        if delay and delay >= self.settings.alert_traffic_delay_minutes:
            alerts.append({**base, "key": f"leave_earlier|{event_id}", "type": "leave_earlier", "severity": "info",
                           "title": f"Heavy traffic on the way to {name}",
                           "message": f"Traffic adds about {round(delay)} min. Leave by {leave}."})

        for c in route["closures"]:
            what = "closed" if c["full_closure"] else "under construction"
            alerts.append({**base, "key": f"closure|{event_id}|{c['closure_id']}", "type": "closure",
                           "severity": "info", "title": f"Road work on your route to {name}",
                           "message": f"{c['name']} is {what}. Your leave-by time already allows for it."})
        return alerts

    def _nws_alert(self, a: dict) -> dict:
        return {"key": f"nws|{a['id']}", "source": "nws", "type": "weather_warning",
                "severity": NWS_SEVERITY.get(a["severity"], "info"),
                "title": a["event"], "message": a.get("headline") or f"{a['event']} for {a.get('area')}.",
                "event_id": None, "segment_id": None, "parking_id": None, "expires_at": a.get("ends")}

    def _tide_alert(self, t: dict, at: datetime) -> dict | None:
        if not t["above_minor"]:
            return None
        return {"key": f"tide|{at.astimezone(MIAMI):%Y-%m-%d}", "source": "tide", "type": "tide",
                "severity": "critical" if t["above_moderate"] else "warning",
                "title": "High tide in Biscayne Bay",
                "message": (f"Water at Virginia Key is expected to reach {t['expected_peak_ft']:.1f} ft, above the "
                            f"{'moderate' if t['above_moderate'] else 'minor'} flood level. Low bayfront streets "
                            f"(Brickell, near the Miami River) may flood even without rain."),
                "event_id": None, "segment_id": None, "parking_id": None, "expires_at": None}

    def _save(self, user_id: str, candidates: list[dict], checked: set[str], now: datetime) -> None:
        stamp = iso_utc(now)
        existing = {a["alert_id"]: a for a in self.store.get_alerts(user_id)}
        fresh = set()
        for c in candidates:
            alert_id = "al_" + hashlib.sha1(c.pop("key").encode()).hexdigest()[:10]
            fresh.add(alert_id)
            old = existing.get(alert_id)
            still_read = bool(old and old["active"] and old["read"]
                              and SEVERITY_RANK[c["severity"]] <= SEVERITY_RANK[old["severity"]])
            self.store.save_alert(user_id, {
                **c, "alert_id": alert_id, "active": True, "read": still_read, "resolved_at": None,
                "created_at": old["created_at"] if old and old["active"] else stamp, "updated_at": stamp})
        for alert_id, old in existing.items():
            if old["active"] and alert_id not in fresh and old["source"] in checked:
                self.store.save_alert(user_id, {**old, "active": False, "resolved_at": stamp, "updated_at": stamp})
