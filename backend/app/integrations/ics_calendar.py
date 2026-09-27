"""Calendar from an iCal (.ics) link or file: no Google sign-in needed (docs/api-contracts.md, /calendar/ics/*).

Every calendar app can share one: Google Calendar's "Secret address in iCal format", Outlook's "Publish a
calendar", Apple's public calendar link, Canvas's calendar feed. POST /calendar/ics/url fetches a link
(https:// or webcal://) and re-fetches it every ics_refresh_minutes, so edits show up; POST /calendar/ics/upload
takes a file's contents as the request body. Either one connects this browser with source "ics", the same
kind of session as Google (integrations/google_calendar.py), so /calendar/*, /alerts and /me use it.

Weekly classes are one event with a repeat rule; recurring_ical_events expands them into dated events,
honoring skipped days (EXDATE) and moved ones (RECURRENCE-ID). Times without a zone are Miami time.
The backend only fetches public internet addresses (never localhost or private networks).
"""
import hashlib
import ipaddress
import socket
import time
from datetime import date, datetime, timedelta
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import httpx
import icalendar
import recurring_ical_events
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.integrations.google_calendar import COOKIE, check_origin, configured, set_cookie
from app.timeutil import iso_utc

router = APIRouter(prefix="/calendar/ics")
MIAMI = ZoneInfo("America/New_York")
MAX_REDIRECTS = 3


# ---------------------------------------------------------------- reading a calendar
def parse(data: bytes) -> icalendar.Calendar:
    """The calendar in `data`, or ValueError with a message a student can act on."""
    if not data.strip():
        raise ValueError("The calendar file is empty.")
    try:
        cal = icalendar.Calendar.from_ical(data)
    except Exception:
        raise ValueError("That isn't an iCal (.ics) calendar. Use the calendar's iCal link or an .ics file.") from None
    if cal.name != "VCALENDAR":
        raise ValueError("That isn't an iCal (.ics) calendar. Use the calendar's iCal link or an .ics file.")
    return cal


def _when(value) -> dict:
    """icalendar start/end -> Google Calendar API shape: {"dateTime"} for timed events, {"date"} for all-day."""
    if isinstance(value, datetime):
        return {"dateTime": (value if value.tzinfo else value.replace(tzinfo=MIAMI)).isoformat()}
    if isinstance(value, date):
        return {"date": value.isoformat()}
    raise ValueError("bad event time")


def events_between(cal: icalendar.Calendar, time_min: datetime, time_max: datetime) -> list[dict]:
    """Events starting in [time_min, time_max), soonest first, in the fields the Google Calendar API
    returns (id, summary, location, start, end), so CalendarService treats them like any other source."""
    events = []
    for e in recurring_ical_events.of(cal).between(time_min, time_max):
        if str(e.get("STATUS", "")).upper() == "CANCELLED" or "DTSTART" not in e:
            continue
        try:
            start = _when(e["DTSTART"].dt)
            end = _when(e["DTEND"].dt) if "DTEND" in e else start
        except ValueError:
            continue
        if "dateTime" in start and datetime.fromisoformat(start["dateTime"]) < time_min:
            continue  # between() also returns events that started earlier and are still going
        uid = str(e.get("UID", "")) or f"{e.get('SUMMARY', '')}"
        events.append({
            "id": "ics_" + hashlib.sha1(f"{uid}|{start.get('dateTime') or start.get('date')}".encode()).hexdigest()[:12],
            "summary": str(e.get("SUMMARY", "")) or None,
            "location": str(e.get("LOCATION", "")) or None,
            "start": start, "end": end,
        })
    return sorted(events, key=lambda ev: ev["start"].get("dateTime") or ev["start"]["date"])


# ---------------------------------------------------------------- fetching a link
def normalize_url(url: str) -> str:
    url = url.strip()
    if url.lower().startswith("webcal://"):
        url = "https://" + url[len("webcal://"):]
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Paste the calendar's iCal link (it starts with https:// or webcal://).")
    return url


def _check_public(url: str) -> None:
    """Refuse links to localhost, private networks and other non-internet addresses."""
    host = urlparse(url).hostname
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(host, None)}
    except socket.gaierror:
        raise ValueError("Couldn't find that calendar's website. Check the link.") from None
    for address in addresses:
        if not ipaddress.ip_address(address.split("%")[0]).is_global:
            raise ValueError("That link points to a private address. Use the calendar's public iCal link.")


def fetch(url: str, max_bytes: int, timeout_s: float) -> bytes:
    """Download a calendar link. Redirects are followed by hand so each hop is checked."""
    url = normalize_url(url)
    with httpx.Client(timeout=timeout_s, follow_redirects=False,
                      headers={"User-Agent": "DryRoute/0.1 (calendar import)"}) as client:
        for _ in range(MAX_REDIRECTS + 1):
            _check_public(url)
            with client.stream("GET", url) as r:
                if r.is_redirect:
                    url = normalize_url(urljoin(url, r.headers["location"]))
                    continue
                if r.status_code in (401, 403, 404):
                    raise ValueError("The calendar link didn't work (it may be private or expired). "
                                     "Copy the iCal link again.")
                r.raise_for_status()
                data = b""
                for chunk in r.iter_bytes():
                    data += chunk
                    if len(data) > max_bytes:
                        raise ValueError("That calendar is too big to import.")
                return data
    raise ValueError("The calendar link redirects too many times.")


# ---------------------------------------------------------------- calendar source
class IcsCalendar:
    """Calendar source for a browser connected with source "ics". A link is re-fetched when older than
    ics_refresh_minutes; if that fails, the last good copy is used ("couldn't refresh" isn't "no classes")."""

    def __init__(self, settings, connection, lock):
        self.settings, self.connection, self.lock = settings, connection, lock

    def calendar(self) -> icalendar.Calendar:
        with self.lock:
            ics = self.connection["ics"]
            stale = time.time() - ics["fetched_at"] > self.settings.ics_refresh_minutes * 60
            if ics["url"] and stale:
                try:
                    data = fetch(ics["url"], self.settings.ics_max_bytes, self.settings.ics_timeout_s)
                    parse(data)
                    ics["data"] = data
                except (ValueError, httpx.HTTPError):
                    pass
                ics["fetched_at"] = time.time()  # retry after another interval either way
            return parse(ics["data"])

    def list_events(self, time_min: datetime, time_max: datetime) -> list[dict]:
        return events_between(self.calendar(), time_min, time_max)


# ---------------------------------------------------------------- endpoints
class LinkIn(BaseModel):
    url: str = Field(min_length=1, max_length=2000)


def _connect(request: Request, response: Response, data: bytes, url: str | None) -> dict:
    """Validate the calendar, then connect this browser to it (replacing any earlier calendar)."""
    state = request.app.state
    try:
        cal = parse(data)
        now = datetime.now(MIAMI)
        week = events_between(cal, now, now + timedelta(days=7))
    except ValueError as e:
        raise HTTPException(400, str(e))
    upcoming = [e for e in week if "dateTime" in e["start"]]

    manager = state.calendar_sessions
    old = manager.sessions.pop(request.cookies.get(COOKIE), None)
    profile = old["profile"] if old and old["source"] != "disconnected" else None
    key = manager.create("ics", profile=profile)
    manager.sessions[key]["ics"] = {"data": data, "url": url, "fetched_at": time.time()}
    set_cookie(response, state.settings, COOKIE, key)
    return {
        "connected": True, "source": "ics", "google_configured": configured(state.settings),
        "events_next_7_days": len(upcoming),
        "upcoming": [{"event_name": e["summary"], "start_time": iso_utc(datetime.fromisoformat(e["start"]["dateTime"])), "location": e["location"],
                      "building_found": state.calendar.resolve(e["location"]) is not None}
                     for e in upcoming[:5]],
    }


@router.post("/url")
def import_link(body: LinkIn, request: Request, response: Response):
    """Connect a calendar by its iCal link. The backend downloads it now and every ~30 min after."""
    check_origin(request)
    s = request.app.state.settings
    try:
        url = normalize_url(body.url)
        data = fetch(url, s.ics_max_bytes, s.ics_timeout_s)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except httpx.HTTPError:
        raise HTTPException(502, "Couldn't download that calendar right now. Check the link and try again.")
    return _connect(request, response, data, url)


@router.post("/upload")
async def upload(request: Request, response: Response):
    """Connect a calendar from an .ics file: send the file itself as the request body."""
    check_origin(request)
    max_bytes = request.app.state.settings.ics_max_bytes
    data = b""
    async for chunk in request.stream():
        data += chunk
        if len(data) > max_bytes:
            raise HTTPException(413, "That calendar file is too big to import.")
    return _connect(request, response, data, None)
