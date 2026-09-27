"""In-app alerts (docs/api-contracts.md, /alerts). The app polls GET /alerts; this rebuilds them when due.

Sources:
- "trip": the next class's trip (calendar + routing + parking): flooded route, flood-prone lot,
  heavy traffic, closures on the way.
- "nws":  National Weather Service flood alerts in effect at departure.
- "tide": Biscayne Bay (Virginia Key) water level at or above the NWS flood thresholds.

Each alert has a stable id per situation, so a rebuild updates it instead of adding a duplicate, keeps
"read" (unless it got more severe), and marks alerts whose situation is over as resolved. A source that
failed this time resolves nothing: "couldn't check" is not "all clear".

Alert text names the level (the title starts with "Critical:", "Warning:" or "Info:"; the message says e.g.
"moderate flood level" or "high") but never a measurement (km, ft, minutes): what to do and when, not how
much. The details stay on the /routes screen.
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
from app.services.route_planner import worst_street
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
        self._last_refresh[user_id] = time.monotonic()
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
            conditions = self.live.check([(home["lat"], home["lon"]), destination], at, now)
            if conditions["nws_alerts"]["status"] == "ok":
                checked.add("nws")
                candidates += [self._nws_alert(a) for a in conditions["nws_alerts"]["data"]]
            if conditions["tide"]["status"] == "ok":
                checked.add("tide")
                tide = self._tide_alert(conditions["tide"]["data"], at)
                candidates += [tide] if tide else []

        for c in candidates:
            c["title"] = f"{c['severity'].capitalize()}: {c['title']}"

        self._save(user_id, candidates, checked, now)

    def _trip_alerts(self, event: dict, trip: dict) -> list[dict]:
        name, event_id = event["event_name"], event["event_id"]
        summary = event["trip"]
        route = trip[summary["take"]]
        leave = _clock(event["recommended_departure"])
        base = {"source": "trip", "event_id": event_id, "segment_id": None, "parking_id": None,
                "expires_at": event["start_time"]}
        alerts = []

        if trip["compromised"]:
            action = trip["recommendation"]["action"]
            worst = max((s for s in trip["usual"]["risk_segments"] if s["risk_label"] == "high"),
                        key=lambda s: s["risk_score"], default=None)
            street = worst_street(trip["usual"])
            where = f" around {street}" if street else ""
            advice = {"reroute": "Take the safer route.",
                      "reroute_caution": "Take the safer route and drive carefully: it avoids most of the flooding, "
                                         "not all of it.",
                      "no_alternative": "There is no safer way around. Drive carefully, or leave later if you can."}
            alerts.append({**base, "key": f"route_flood|{event_id}", "type": "route_flood",
                           "severity": "critical" if action == "no_alternative" else "warning",
                           "title": f"Flooding expected on your route to {name}",
                           "message": f"Estimated flood levels are high on your usual route{where}. "
                                      f"{advice[action]} Leave by {leave}.",
                           "segment_id": worst["segment_id"] if worst else None})

        if trip["parking"] and trip["parking"]["hazardous"]:
            lot, alternatives = trip["parking"]["planned"]["name"], trip["parking"]["alternatives"]
            instead = (f"Park at {alternatives[0]['name']} instead." if alternatives
                       else "No nearby lot is clearly safer, so allow extra time.")
            alerts.append({**base, "key": f"parking_flood|{event_id}", "type": "parking_flood", "severity": "warning",
                           "title": f"{lot} may flood",
                           "message": f"Estimated flood levels are high at {lot}. {instead}",
                           "parking_id": trip["parking"]["planned"]["parking_id"]})

        delay = route.get("traffic_delay_minutes")
        if delay and delay >= self.settings.alert_traffic_delay_minutes:
            alerts.append({**base, "key": f"leave_earlier|{event_id}", "type": "leave_earlier", "severity": "info",
                           "title": f"Heavy traffic on the way to {name}",
                           "message": f"Traffic is heavier than usual. Leave by {leave}."})

        for c in route["closures"]:
            what = "closed" if c["full_closure"] else "under construction"
            alerts.append({**base, "key": f"closure|{event_id}|{c['closure_id']}", "type": "closure",
                           "severity": "info", "title": f"Road work on your route to {name}",
                           "message": f"{c['name']} is {what}. Your leave-by time already allows for it."})
        return alerts

    def _nws_alert(self, a: dict) -> dict:
        return {"key": f"nws|{a['id']}", "source": "nws", "type": "weather_warning",
                "severity": NWS_SEVERITY.get(a["severity"], "info"),
                "title": a["event"],  # the official name, e.g. "Flood Warning"; its headline is all dates and times
                "message": (f"The National Weather Service has issued this for your area (severity: "
                            f"{a['severity'].lower()}). Avoid low-lying and flooded roads."
                            if a.get("severity") and a["severity"] != "Unknown" else
                            "The National Weather Service has issued this for your area. "
                            "Avoid low-lying and flooded roads."),
                "event_id": None, "segment_id": None, "parking_id": None, "expires_at": a.get("ends")}

    def _tide_alert(self, t: dict, at: datetime) -> dict | None:
        if not t["above_minor"]:
            return None
        return {"key": f"tide|{at.astimezone(MIAMI):%Y-%m-%d}", "source": "tide", "type": "tide",
                "severity": "critical" if t["above_moderate"] else "warning",
                "title": "High tide in Biscayne Bay",
                "message": (f"Estimated water levels in Biscayne Bay are at the "
                            f"{'moderate' if t['above_moderate'] else 'minor'} flood level. Low-lying bayfront "
                            f"streets (Brickell, near the Miami River) may flood even without rain."),
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
