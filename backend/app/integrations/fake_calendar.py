"""Fake Google Calendar: a weekly class schedule (fixtures/calendar.json) expanded into events with the
same fields the Google Calendar API returns (id, summary, location, start.dateTime, end.dateTime).
The real GoogleCalendar (integrations/google_calendar.py, once OAuth exists) has the same list_events."""
import json
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

DAYS = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]  # Google/iCal weekday codes, Monday first


class FakeCalendar:
    def __init__(self, path: Path):
        cfg = json.loads(path.read_text(encoding="utf-8"))
        self.tz = ZoneInfo(cfg["timezone"])
        self.classes = cfg["classes"]

    def list_events(self, time_min: datetime, time_max: datetime) -> list[dict]:
        """Events starting in [time_min, time_max), soonest first."""
        events = []
        day = time_min.astimezone(self.tz).date()
        while day <= time_max.astimezone(self.tz).date():
            for i, c in enumerate(self.classes):
                if DAYS[day.weekday()] not in c["days"]:
                    continue
                start = datetime.combine(day, time.fromisoformat(c["start"]), self.tz)
                end = datetime.combine(day, time.fromisoformat(c["end"]), self.tz)
                if time_min <= start < time_max:
                    events.append({
                        "id": f"fake{i}_{day:%Y%m%d}",
                        "summary": c["summary"],
                        "location": c["location"],
                        "start": {"dateTime": start.isoformat()},
                        "end": {"dateTime": end.isoformat()},
                    })
            day += timedelta(days=1)
        return sorted(events, key=lambda e: e["start"]["dateTime"])
