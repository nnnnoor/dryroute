"""Read-only Google Calendar integration, with isolated, expiring browser sessions.

A session (the connected calendar, Google tokens, and the student's profile, alerts and trips) lasts one day.
Locally it stays in server memory (CalendarSessions: one worker; reconnect after a server restart). With
DATA_BACKEND=mongo it is kept in Atlas (MongoCalendarSessions), so any server copy can answer: Vercel runs
many short-lived ones. No tokens reach frontend storage.
"""
import base64
from collections import OrderedDict
from copy import copy, deepcopy
from datetime import datetime, timezone
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
SESSION_SECONDS = 86400
PENDING_SECONDS = 600  # to finish Google's consent screen


def new_session(source, tokens=None, profile=None, **extra):
    return {
        "source": source, "tokens": tokens, "expires": time.time() + SESSION_SECONDS,
        "profile": deepcopy(profile) if profile else {"name": None, "home": None,
            "preferred_parking_id": None, "arrival_buffer_minutes": 10},
        "alerts": {}, "trips": {}, "user_id": "calendar_" + secrets.token_hex(12), **extra,
    }


class CalendarSessions:
    """Sessions in server memory (DATA_BACKEND=local, tests). Changes to a session dict are live."""

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

    def get(self, key, request=None):
        self.prune()
        return self.sessions.get(key)

    def pop(self, key, request=None):
        """Remove a session (reconnecting, disconnecting) and return it, or None."""
        return self.sessions.pop(key, None)

    def create(self, source, tokens=None, profile=None, **extra):
        self.prune()
        key = secrets.token_urlsafe(32)
        self.sessions[key] = new_session(source, tokens, profile, **extra)
        return key

    def add_pending(self, state, verifier):
        self.prune()
        self.pending[state] = {"expires": time.time() + PENDING_SECONDS, "verifier": verifier}

    def take_pending(self, state):
        """/start's {"verifier"} for this OAuth state, once; None if unknown or expired."""
        self.prune()
        return self.pending.pop(state, None)

    def save(self, request):
        """Nothing to write: changes are already in memory."""


class MongoCalendarSessions:
    """The same sessions in Atlas (DATA_BACKEND=mongo): collections calendar_sessions and oauth_pending,
    which a TTL index empties once `expires_at` passes. A request gets its own copy of each session it reads
    (kept on request.state); save() writes back the fields the request changed, before the response goes out
    (middleware in main.py). So code changes a session dict in place, the same in both modes."""

    def __init__(self, db):
        self.sessions, self.pending = db.calendar_sessions, db.oauth_pending
        for collection in (self.sessions, self.pending):
            collection.create_index("expires_at", expireAfterSeconds=0)
        self.lock = threading.RLock()  # orders token refreshes within this server copy only
        # (key, ics fetched_at) -> an imported iCal file (up to ics_max_bytes). Reads leave the file out and
        # take it from here, so it comes from Atlas once per server copy and version, not with every request.
        self._ics_files = OrderedDict()
        self._ics_lock = threading.Lock()

    @staticmethod
    def _read(request):
        """key -> (session, deep copy as read) for this request; {} outside a request (nothing saved)."""
        if request is None:
            return {}
        if not hasattr(request.state, "sessions_read"):
            request.state.sessions_read = {}
        return request.state.sessions_read

    def get(self, key, request=None):
        if not key:
            return None
        read = self._read(request)
        if key not in read:
            doc = self.sessions.find_one({"_id": key, "expires": {"$gt": time.time()}},
                                         {"_id": 0, "expires_at": 0, "ics.data": 0})
            if doc and "ics" in doc:
                doc["ics"]["data"] = self._ics_file(key, doc["ics"]["fetched_at"])
            read[key] = (doc, deepcopy(doc))  # the copy shares the file's bytes (immutable), so it's cheap
        return read[key][0]

    def _ics_file(self, key, version):
        with self._ics_lock:
            data = self._ics_files.get((key, version))
        if data is None:
            ics = (self.sessions.find_one({"_id": key}, {"ics": 1}) or {}).get("ics") or {}
            data = ics.get("data", b"")
            self._remember_ics(key, ics)
        return data

    def _remember_ics(self, key, ics):
        if "data" in ics:
            with self._ics_lock:
                self._ics_files[(key, ics["fetched_at"])] = ics["data"]
                while len(self._ics_files) > 16:
                    self._ics_files.popitem(last=False)

    def pop(self, key, request=None):
        if not key:
            return None
        self._read(request).pop(key, None)
        doc = self.sessions.find_one_and_delete({"_id": key}, {"_id": 0, "expires_at": 0})
        return doc if doc and doc["expires"] > time.time() else None

    def create(self, source, tokens=None, profile=None, **extra):
        key = secrets.token_urlsafe(32)
        doc = new_session(source, tokens, profile, **extra)
        self.sessions.insert_one({"_id": key, **doc, "expires_at": _utc(doc["expires"])})
        self._remember_ics(key, doc.get("ics") or {})
        return key

    def add_pending(self, state, verifier):
        expires = time.time() + PENDING_SECONDS
        self.pending.insert_one({"_id": state, "verifier": verifier, "expires": expires, "expires_at": _utc(expires)})

    def take_pending(self, state):
        doc = self.pending.find_one_and_delete({"_id": state})
        return doc if doc and doc["expires"] > time.time() else None

    def save(self, request):
        """Write back the top-level fields this request changed, so two requests changing different fields
        (e.g. alerts and the test class) don't overwrite each other."""
        for key, (doc, before) in self._read(request).items():
            if doc is None:
                continue
            changed = {k: v for k, v in doc.items() if k not in before or before[k] != v}
            removed = [k for k in before if k not in doc]
            update = {}
            if changed:
                update["$set"] = changed
            if removed:
                update["$unset"] = dict.fromkeys(removed, "")
            if update:
                self.sessions.update_one({"_id": key}, update)
            self._remember_ics(key, changed.get("ics") or {})  # a re-downloaded link


def _utc(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc)


def make_sessions(settings, store):
    return MongoCalendarSessions(store.db) if settings.data_backend == "mongo" else CalendarSessions()


def configured(settings):
    return bool(settings.google_client_id and settings.google_client_secret)


def session(request, required=False):
    key = request.cookies.get(COOKIE)
    value = request.app.state.calendar_sessions.get(key, request)
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
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
    request.app.state.calendar_sessions.add_pending(state, verifier)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
        "client_id": settings.google_client_id, "redirect_uri": settings.google_redirect_uri,
        "response_type": "code", "scope": SCOPE, "access_type": "offline", "prompt": "consent",
        "state": state, "code_challenge": challenge, "code_challenge_method": "S256",
    })
    response = RedirectResponse(url, status_code=303)
    # Path "/", not "/auth/google": behind the frontend's /api proxy (Vercel) the browser sees /api/auth/google
    set_cookie(response, settings, STATE_COOKIE, state, age=PENDING_SECONDS)
    return response


@router.get("/callback")
def callback(request: Request, state: str = "", code: str = "", error: str = ""):
    settings = request.app.state.settings
    manager = request.app.state.calendar_sessions
    cookie_state = request.cookies.get(STATE_COOKIE, "")
    if not state or not cookie_state or not secrets.compare_digest(state, cookie_state):
        raise HTTPException(400, "Invalid Google authorization state. Start Connect Google Calendar again.")
    pending = manager.take_pending(state)
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
    response.delete_cookie(STATE_COOKIE)
    response.headers["Cache-Control"] = "no-store"
    if tokens:
        manager.pop(request.cookies.get(COOKIE), request)
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
    # One per request, never the shared demo service (no Google events there). When its alerts were last
    # built is kept in the session, so the next request, on any server copy, knows whether they're due.
    from app.services.alerts import AlertService
    state = request.app.state
    return AlertService(request_store(request), calendar_service(request), state.parking, state.alerts.live,
                        state.settings, last_refresh=connection.setdefault("alerts_refreshed", {}))


def invalidate_alerts(connection):
    """Rebuild this session's alerts on its next poll (profile or test class changed)."""
    connection.pop("alerts_refreshed", None)


def dashboard_service(request):
    service = copy(request.app.state.dashboard)
    service.store = request_store(request)
    return service
