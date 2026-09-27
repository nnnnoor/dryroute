"""Read-only Google Calendar integration, with isolated, expiring browser sessions.

Prototype storage: tokens and private user data stay in server memory for one day.
Use one worker; reconnect after a server restart. No tokens reach frontend storage.
"""
import base64
from copy import copy, deepcopy
import hashlib
import secrets
import threading
import time
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

router = APIRouter(prefix="/auth/google")
SCOPE = "https://www.googleapis.com/auth/calendar.events.readonly"
COOKIE = "dryroute_calendar"
STATE_COOKIE = "dryroute_oauth_state"


class CalendarSessions:
    def __init__(self):
        self.sessions = {}
        self.pending = {}
        self.lock = threading.RLock()

    def prune(self):
        now = time.time()
        with self.lock:
            for collection in (self.pending, self.sessions):
                for key in [key for key, value in collection.items() if value["expires"] <= now]:
                    del collection[key]

    def get(self, key):
        self.prune()
        return self.sessions.get(key)

    def create(self, source, tokens=None, profile=None):
        self.prune()
        key = secrets.token_urlsafe(32)
        self.sessions[key] = {
            "source": source, "tokens": tokens, "expires": time.time() + 86400,
            "profile": deepcopy(profile) if profile else {"name": None, "home": None,
                "preferred_parking_id": None, "arrival_buffer_minutes": 10},
            "alerts": {}, "trips": {}, "user_id": "calendar_" + secrets.token_hex(12),
        }
        return key


def configured(settings):
    return bool(settings.google_client_id and settings.google_client_secret)


def session(request, required=False):
    key = request.cookies.get(COOKIE)
    value = request.app.state.calendar_sessions.get(key)
    if required and key and not value:
        raise HTTPException(401, "Your calendar session expired. Please reconnect.")
    if required and value and value["source"] == "disconnected":
        raise HTTPException(401, "Connect a calendar to continue.")
    return value


def set_cookie(response, settings, name, value, age=86400, path="/"):
    response.set_cookie(name, value, max_age=age, httponly=True, secure=settings.calendar_cookie_secure,
                        samesite=settings.calendar_cookie_samesite, path=path)
    response.headers["Cache-Control"] = "no-store"


def check_origin(request):
    origin = request.headers.get("origin")
    if origin and origin not in request.app.state.settings.cors_origins:
        raise HTTPException(403, "This origin is not allowed to change the calendar connection.")


@router.get("/start")
def start(request: Request):
    settings = request.app.state.settings
    if not configured(settings):
        return RedirectResponse(settings.frontend_url.rstrip("/") + "/setup?calendar=not-configured", status_code=303)
    manager = request.app.state.calendar_sessions
    manager.prune()
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
    manager.pending[state] = {"expires": time.time() + 600, "verifier": verifier}
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
        "client_id": settings.google_client_id, "redirect_uri": settings.google_redirect_uri,
        "response_type": "code", "scope": SCOPE, "access_type": "offline", "prompt": "consent",
        "state": state, "code_challenge": challenge, "code_challenge_method": "S256",
    })
    response = RedirectResponse(url, status_code=303)
    set_cookie(response, settings, STATE_COOKIE, state, age=600, path="/auth/google")
    return response


@router.get("/callback")
def callback(request: Request, state: str = "", code: str = "", error: str = ""):
    settings = request.app.state.settings
    manager = request.app.state.calendar_sessions
    manager.prune()
    cookie_state = request.cookies.get(STATE_COOKIE, "")
    if not state or not cookie_state or not secrets.compare_digest(state, cookie_state):
        raise HTTPException(400, "Invalid Google authorization state. Start Connect Google Calendar again.")
    pending = manager.pending.pop(state, None)
    if pending is None:
        raise HTTPException(400, "Google authorization expired. Please reconnect.")
    outcome, tokens = ("denied" if error else "error"), None
    if code and not error:
        try:
            result = httpx.post("https://oauth2.googleapis.com/token", data={
                "client_id": settings.google_client_id, "client_secret": settings.google_client_secret,
                "redirect_uri": settings.google_redirect_uri, "code": code,
                "code_verifier": pending["verifier"], "grant_type": "authorization_code",
            }, timeout=15)
            result.raise_for_status()
            tokens = result.json()
            if not tokens.get("access_token") or SCOPE not in tokens.get("scope", "").split():
                tokens = None
                outcome = "scope-denied"
            else:
                tokens["expires_at"] = time.time() + float(tokens.get("expires_in", 3600))
                outcome = "connected"
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            tokens = None
    response = RedirectResponse(settings.frontend_url.rstrip("/") + "/setup?calendar=" + outcome, status_code=303)
    response.delete_cookie(STATE_COOKIE, path="/auth/google")
    response.headers["Cache-Control"] = "no-store"
    if tokens:
        manager.sessions.pop(request.cookies.get(COOKIE), None)
        set_cookie(response, settings, COOKIE, manager.create("google", tokens))
    return response


class GoogleCalendar:
    def __init__(self, settings, connection, lock):
        self.settings, self.connection, self.lock = settings, connection, lock

    def access_token(self):
        with self.lock:
            tokens = self.connection["tokens"]
            if not tokens:
                raise HTTPException(401, "Google Calendar authorization expired. Please reconnect.")
            if time.time() < tokens["expires_at"] - 60:
                return tokens["access_token"]
            if not tokens.get("refresh_token"):
                self.connection["tokens"] = None
                raise HTTPException(401, "Google Calendar authorization expired. Please reconnect.")
            response = httpx.post("https://oauth2.googleapis.com/token", data={
                "client_id": self.settings.google_client_id, "client_secret": self.settings.google_client_secret,
                "refresh_token": tokens["refresh_token"], "grant_type": "refresh_token",
            }, timeout=15)
            if response.status_code in (400, 401):
                self.connection["tokens"] = None
                raise HTTPException(401, "Google Calendar access expired or was revoked. Please reconnect.")
            response.raise_for_status()
            refreshed = response.json()
            if not refreshed.get("access_token"):
                raise ValueError("Missing access token")
            tokens.update(refreshed)
            tokens["expires_at"] = time.time() + float(refreshed.get("expires_in", 3600))
            return tokens["access_token"]

    def list_events(self, time_min, time_max):
        try:
            token = self.access_token()
            params = {"timeMin": time_min.isoformat(), "timeMax": time_max.isoformat(),
                      "singleEvents": "true", "orderBy": "startTime", "maxResults": 250}
            events = []
            with httpx.Client(timeout=15) as client:
                for _ in range(20):
                    response = client.get("https://www.googleapis.com/calendar/v3/calendars/primary/events",
                                          params=params, headers={"Authorization": "Bearer " + token})
                    if response.status_code == 401:
                        self.connection["tokens"] = None
                        raise HTTPException(401, "Google Calendar access expired. Please reconnect.")
                    if response.status_code == 403:
                        raise HTTPException(403, "Google Calendar access was refused. Check Calendar API enablement and read-only consent.")
                    response.raise_for_status()
                    data = response.json()
                    events.extend(e for e in data.get("items", []) if e.get("status") != "cancelled")
                    if not data.get("nextPageToken"):
                        return events
                    params["pageToken"] = data["nextPageToken"]
            raise HTTPException(502, "Too many calendar events. Request a shorter date range.")
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            raise HTTPException(502, "Google Calendar is temporarily unavailable. Please try again.") from None


class SessionStore:
    """Share public GIS data, isolate profiles, calendar-derived alerts, and trips."""
    def __init__(self, base, connection):
        self.base, self.connection = base, connection

    def __getattr__(self, name):
        return getattr(self.base, name)

    def get_user(self, user_id="demo"):
        return {**deepcopy(self.connection["profile"]), "user_id": self.connection["user_id"]}

    def save_user(self, user_id, user):
        self.connection["profile"] = deepcopy(user)

    def get_alerts(self, user_id):
        return deepcopy(list(self.connection["alerts"].values()))

    def save_alert(self, user_id, alert):
        self.connection["alerts"][alert["alert_id"]] = deepcopy(alert)

    def get_trips(self, user_id):
        return deepcopy(list(self.connection["trips"].values()))

    def save_trip(self, user_id, trip):
        self.connection["trips"][trip["trip_id"]] = deepcopy(trip)

    def delete_demo_trips(self, user_id):
        self.connection["trips"] = {k: v for k, v in self.connection["trips"].items() if not v.get("demo")}


def request_store(request):
    connection = session(request, required=True)
    return SessionStore(request.app.state.store, connection) if connection else request.app.state.store


def calendar_service(request):
    state = request.app.state
    connection = session(request, required=True)
    if not connection:
        if state.settings.use_fake_calendar:
            return state.calendar
        raise HTTPException(401, "Connect Google Calendar or choose the demo schedule first.")
    service = copy(state.calendar)
    service.store = SessionStore(state.store, connection)
    if connection["source"] == "google":
        service.source = GoogleCalendar(state.settings, connection, state.calendar_sessions.lock)
    elif connection["source"] == "ics":  # iCal link or file (integrations/ics_calendar.py)
        from app.integrations.ics_calendar import IcsCalendar
        service.source = IcsCalendar(state.settings, connection, state.calendar_sessions.lock)
    from app.api.demo import WithTestEvents  # POST /demo/test-event (demo only)
    service.source = WithTestEvents(service.source, connection)
    return service


def alerts_service(request):
    connection = session(request, required=True)
    if not connection:
        if not request.app.state.settings.use_fake_calendar:
            raise HTTPException(401, "Connect Google Calendar or choose the demo schedule first.")
        return request.app.state.alerts
    # Cache only within this session; never put Google events into the shared demo service.
    if "alert_service" not in connection:
        from app.services.alerts import AlertService
        state = request.app.state
        connection["alert_service"] = AlertService(request_store(request), calendar_service(request),
            state.parking, state.alerts.live, state.settings)
    return connection["alert_service"]


def dashboard_service(request):
    service = copy(request.app.state.dashboard)
    service.store = request_store(request)
    return service
