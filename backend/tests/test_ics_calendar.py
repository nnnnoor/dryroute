"""Calendar import from an iCal link or .ics file. No network: link downloads are faked."""
import time
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.integrations import ics_calendar as ics

MIAMI_WEEK = (datetime(2026, 9, 28, 4, 0, tzinfo=timezone.utc),   # Mon Sep 28, midnight Miami
              datetime(2026, 10, 3, 4, 0, tzinfo=timezone.utc))   # Sat Oct 3

# What a student's calendar export looks like: a weekly class (one day skipped), a daily online class,
# a floating-time event (no zone), an all-day event, a cancelled event.
SCHEDULE = b"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//DryRoute test//EN
BEGIN:VEVENT
UID:cop3530@fiu.edu
SUMMARY:COP 3530 Data Structures
LOCATION:PC 213
DTSTART;TZID=America/New_York:20260824T090000
DTEND;TZID=America/New_York:20260824T101500
RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR
EXDATE;TZID=America/New_York:20260930T090000
END:VEVENT
BEGIN:VEVENT
UID:enc1101@fiu.edu
SUMMARY:ENC 1101 Writing
LOCATION:Online (Zoom)
DTSTART;TZID=America/New_York:20260824T130000
DTEND;TZID=America/New_York:20260824T140000
RRULE:FREQ=DAILY
END:VEVENT
BEGIN:VEVENT
UID:study@fiu.edu
SUMMARY:Study group
LOCATION:GL 100
DTSTART:20260929T150000
DTEND:20260929T160000
END:VEVENT
BEGIN:VEVENT
UID:holiday@fiu.edu
SUMMARY:No classes
DTSTART;VALUE=DATE:20261002
END:VEVENT
BEGIN:VEVENT
UID:cancelled@fiu.edu
SUMMARY:Cancelled lab
LOCATION:PC 213
STATUS:CANCELLED
DTSTART;TZID=America/New_York:20260929T120000
DTEND;TZID=America/New_York:20260929T130000
END:VEVENT
END:VCALENDAR
"""


def week_events():
    return ics.events_between(ics.parse(SCHEDULE), *MIAMI_WEEK)


def test_weekly_class_expands_and_skips_exdate():
    cop = [e for e in week_events() if e["summary"] == "COP 3530 Data Structures"]
    assert [e["start"]["dateTime"][:10] for e in cop] == ["2026-09-28", "2026-09-29", "2026-10-01", "2026-10-02"]
    first = datetime.fromisoformat(cop[0]["start"]["dateTime"])
    assert first == datetime(2026, 9, 28, 13, 0, tzinfo=timezone.utc)  # 9:00 Miami (EDT)
    assert cop[0]["location"] == "PC 213" and cop[0]["id"] != cop[1]["id"]
    assert cop[0]["id"] == week_events()[0]["id"]  # stable across reads (alert ids depend on it)


def test_floating_all_day_and_cancelled():
    by_name = {}
    for e in week_events():
        by_name.setdefault(e["summary"], e)
    study = datetime.fromisoformat(by_name["Study group"]["start"]["dateTime"])
    assert study == datetime(2026, 9, 29, 19, 0, tzinfo=timezone.utc)  # no zone -> Miami time
    assert by_name["No classes"]["start"] == {"date": "2026-10-02"}
    assert "Cancelled lab" not in by_name


@pytest.mark.parametrize("data", [b"", b"hello", b"<html>Sign in</html>", b"BEGIN:VEVENT\nEND:VEVENT\n"])
def test_not_a_calendar(data):
    with pytest.raises(ValueError):
        ics.parse(data)


def test_links():
    assert ics.normalize_url(" webcal://calendar.google.com/x/basic.ics") == "https://calendar.google.com/x/basic.ics"
    for bad in ("ftp://example.com/a.ics", "calendar.ics", "javascript:alert(1)"):
        with pytest.raises(ValueError):
            ics.normalize_url(bad)
    for private in ("http://127.0.0.1/a.ics", "http://localhost:8000/a.ics", "http://10.0.0.5/a.ics",
                    "http://169.254.169.254/latest/meta-data", "http://[::1]/a.ics"):
        with pytest.raises(ValueError, match="private"):
            ics.fetch(private, 1000, 1)


@pytest.fixture
def browser(client):
    b = TestClient(client.app)  # its own cookie jar: a separate student
    yield b
    b.close()


def test_upload_connects_this_browser_only(client, browser):
    r = browser.post("/calendar/ics/upload", content=SCHEDULE, headers={"Content-Type": "text/calendar"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["connected"] is True and body["source"] == "ics"
    assert body["events_next_7_days"] > 0  # the daily/weekly classes repeat forever
    assert "httponly" in r.headers["set-cookie"].lower()
    assert browser.get("/calendar/connection").json()["source"] == "ics"
    names = {e["event_name"] for e in browser.get("/calendar/events", params={"hours": 168}).json()}
    assert {"COP 3530 Data Structures", "ENC 1101 Writing"} <= names
    assert "Hackathon team meeting" not in names  # the demo schedule's, not this student's
    # another browser (no cookie) still gets the demo schedule
    demo_names = {e["event_name"] for e in client.get("/calendar/events", params={"hours": 168}).json()}
    assert "Hackathon team meeting" in demo_names and "ENC 1101 Writing" not in demo_names


def test_imported_class_gets_a_leave_by_once_home_is_set(browser):
    browser.post("/calendar/ics/upload", content=SCHEDULE)
    home = {"label": "Shenandoah", "lat": 25.75322, "lon": -80.24144}
    assert browser.put("/me", json={"home": home}).status_code == 200
    n = browser.get("/calendar/next-event")
    assert n.status_code == 200
    event = n.json()
    assert event["event_name"] in {"COP 3530 Data Structures", "Study group"}  # in person, not the Zoom class
    assert event["trip"] is not None and event["recommended_departure"]


def test_bad_upload(browser):
    r = browser.post("/calendar/ics/upload", content=b"<html>not a calendar</html>")
    assert r.status_code == 400 and "iCal" in r.json()["detail"]
    assert browser.get("/calendar/connection").json()["connected"] is False


def test_link_import_and_refresh_keeps_last_good_copy(client, browser, monkeypatch):
    calls = []
    def fake_fetch(url, max_bytes, timeout_s):
        calls.append(url)
        return SCHEDULE
    monkeypatch.setattr(ics, "fetch", fake_fetch)
    r = browser.post("/calendar/ics/url", json={"url": "webcal://calendar.example.edu/student.ics"})
    assert r.status_code == 200, r.text
    assert calls == ["https://calendar.example.edu/student.ics"]

    key = browser.cookies.get("dryroute_calendar")
    connection = client.app.state.calendar_sessions.sessions[key]
    assert connection["ics"]["url"] == "https://calendar.example.edu/student.ics"

    def down(url, max_bytes, timeout_s):
        calls.append(url)
        raise ValueError("offline")
    monkeypatch.setattr(ics, "fetch", down)
    connection["ics"]["fetched_at"] = time.time() - 3600  # due for a refresh
    events = browser.get("/calendar/events", params={"hours": 168}).json()
    assert len(calls) == 2 and any(e["event_name"] == "COP 3530 Data Structures" for e in events)


def test_link_errors(browser):
    assert browser.post("/calendar/ics/url", json={"url": "http://127.0.0.1/cal.ics"}).status_code == 400
    assert browser.post("/calendar/ics/url", json={"url": "not a link"}).status_code == 400
    assert browser.post("/calendar/ics/url", json={}).status_code == 422
