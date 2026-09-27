"""Check the dashboard's demo homes against the real ML runs in Atlas: from each home, the demo test class
(POST /demo/test-event, a class in PC) should be clear on "live" and get a flood alert on "storm".

The homes come from frontend/src/pages/CalendarPage.tsx (DEMO_HOMES), plus the Atlas demo user's home. The
storm run is fixed, but "live" follows the real weather: run this on demo morning. It only reads Atlas (the
shared demo switch never moves). Local mode's fake storm floods far more roads, so only this check counts.

Run from backend/ (MONGO_URI from backend/.env, else data-pipeline/.env; optional building code, default PC):
    .venv/Scripts/python scripts/check_demo_homes.py [BUILDING]
"""
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import dotenv_values

BACKEND = Path(__file__).resolve().parents[1]
PRESETS = BACKEND.parent / "frontend" / "src" / "pages" / "CalendarPage.tsx"
HOME = re.compile(r"\{ label: '([^']+)', lat: ([-\d.]+), lon: ([-\d.]+), note: '([^']*)' \}")

sys.path.insert(0, str(BACKEND))
if not (os.environ.get("MONGO_URI") or dotenv_values(BACKEND / ".env").get("MONGO_URI")):
    os.environ["MONGO_URI"] = dotenv_values(BACKEND.parent / "data-pipeline" / ".env").get("MONGO_URI") or ""
os.environ.update(DATA_BACKEND="mongo", STATIC_DATA="files", TOMTOM_API_KEY="", LIVE_CONDITIONS_ENABLED="false")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.services.route_planner import worst_street  # noqa: E402


def outcome(state, lat: float, lon: float, building: dict, scenario: str) -> str:
    """What the dashboard shows for the test class from this home: "clear", or its alerts."""
    event = {"start": {"dateTime": (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()}}
    user = {**state.store.get_user(), "home": {"lat": lat, "lon": lon}}
    plan = state.calendar._trip_plan(event, building, user, lat, lon)  # same lot and arrival as the dashboard
    with state.risk.using(scenario):
        trip = state.trips.plan(lat, lon, **plan["dest"], arrive_by=plan["arrive_by"])
    if not trip["coverage"]["origin_in_area"]:
        return "outside the mapped area"
    alerts = []
    if trip["compromised"]:
        alerts.append(f"{trip['recommendation']['action']} ({worst_street(trip['usual']) or 'flooded route'})")
    if trip["parking"] and trip["parking"]["hazardous"]:
        alerts.append("lot may flood")
    return "; ".join(alerts) or "clear"


def main() -> int:
    code = (sys.argv[1] if len(sys.argv) > 1 else "PC").upper()
    homes = [(label, float(lat), float(lon)) for label, lat, lon, _ in HOME.findall(PRESETS.read_text(encoding="utf-8"))]
    with TestClient(app):
        state = app.state
        if code not in state.calendar.buildings:
            print(f"Unknown FIU building code {code!r}")
            return 2
        building = {"code": code, **state.calendar.buildings[code]}
        demo = state.store.get_user()["home"]
        homes.append((f"Atlas demo user ({demo['label']})", demo["lat"], demo["lon"]))
        with state.risk.using("live"):
            w = state.risk.weather()
        print(f"live run {w['computed_at']} ({w['model_version']}, rain: {w['rain_level']}{', STALE' if w['stale'] else ''})")
        print(f"test class in {code} ({building['name']})\n")
        failed = 0
        for label, lat, lon in homes:
            live, storm = (outcome(state, lat, lon, building, s) for s in ("live", "storm"))
            ok = live == "clear" and storm not in ("clear", "outside the mapped area")
            failed += not ok
            print(f"{'OK  ' if ok else 'FAIL'} {label:34} live: {live:34} storm: {storm}")
    print("\nAll homes show the storm alert." if not failed else
          f"\n{failed} home(s) won't show the live -> storm change: pick others (clear on live, alert on storm).")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
