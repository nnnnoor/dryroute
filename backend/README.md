# DryRoute backend (FastAPI)

API shapes: [docs/api-contracts.md](../docs/api-contracts.md). Interactive docs while running: http://localhost:8000/docs

## Run

```bash
cd backend
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt     # macOS/Linux: .venv/bin/python
.venv/Scripts/python -m uvicorn app.main:app --reload --port 8000
.venv/Scripts/python -m pytest                              # tests
```

No `.env` needed locally: by default it runs on the committed pipeline data
(`data-pipeline/data/processed/`) and fake fixtures (`fixtures/`). See `.env.example` for the switches
(`DATA_BACKEND=mongo`, `USE_FAKE_CALENDAR=false`, `CORS_ORIGINS`).

### Google Calendar

Follow [Google Calendar setup](../docs/google-calendar-setup.md) to enable the API, register the OAuth callback, and set backend credentials. The frontend setup button connects a private browser session; explicit demo calendars remain available without credentials. Connected sessions last one day: in memory locally (one backend worker), in Atlas with `DATA_BACKEND=mongo` (any number of server copies).

### Traffic (TomTom)

With `TOMTOM_API_KEY` in `backend/.env` (free key from developer.tomtom.com), each route's drive time and
leave-by time use TomTom's traffic for that exact path; the roads are still chosen by the backend.
Without a key, or if TomTom fails, times come from speed limits (`eta_source: "free_flow"`).
Check a real call: `DRYROUTE_TOMTOM_TESTS=1 .venv/Scripts/python -m pytest tests/test_tomtom_live.py -s`

### On MongoDB Atlas

Put `MONGO_URI` (password without the `< >`) and `DATA_BACKEND=mongo` in `backend/.env` (git-ignored).
The same data then comes from Atlas (db `flood`), plus the live `closures` and ML's `risk_runs` / `risk_scores`.
Until ML writes runs, "live" serves the static score (`/weather` says `stale: true`) and "storm" uses the
local fake storm. Tests always run offline; to also check Atlas:
`DRYROUTE_MONGO_TESTS=1 .venv/Scripts/python -m pytest tests/test_mongo.py`

### Deploy (Vercel)

Two Vercel projects from this repo: the backend (root directory: the repo root; Vercel reads `index.py`,
`pyproject.toml` and `vercel.json` there) and the frontend (root directory `frontend`, env
`VITE_API_BASE_URL=/api`). `frontend/vercel.json` forwards `/api/*` to the backend, so the phone only talks to
the frontend's address and the calendar cookie stays first-party (Safari blocks it across `*.vercel.app` sites).

Vercel runs many short-lived copies of the backend, so nothing that must last may live in server memory:
with `DATA_BACKEND=mongo`, calendar sessions (`calendar_sessions`, `oauth_pending`) and the demo switch
(`app_state`) are in Atlas. Backend env: `DATA_BACKEND=mongo`, `STATIC_DATA=files` (fast cold start),
`MONGO_URI`, `MONGO_DB`, `USE_FAKE_CALENDAR=true`, `FRONTEND_URL` and `CORS_ORIGINS` (the frontend's URL),
`CALENDAR_COOKIE_SECURE=true`, optionally `TOMTOM_API_KEY` and the `GOOGLE_*` values (redirect URI
`https://<frontend>/api/auth/google/callback`). Atlas Network Access must allow `0.0.0.0/0`.
Keep `pyproject.toml`'s packages in sync with `requirements.txt`; the bundle is ~465 of Vercel's 500 MB.
Try the entrypoint locally from the repo root: `backend/.venv/Scripts/python -m uvicorn index:app --port 8000`

Demo homes (the dashboard's demo controls, `DEMO_HOMES` in `frontend/src/pages/CalendarPage.tsx`) must be clear
on the ML "live" run and flood on its "storm" run; local mode's fake storm can't tell. Check them, and the Atlas
demo user's home, on demo morning: `.venv/Scripts/python scripts/check_demo_homes.py`

## Layout

- `app/main.py`: app, startup (loads data + graph), CORS
- `app/config.py`: settings
- `app/db/store.py`: data access interface + `LocalStore` (committed files + fixtures)
- `app/db/mongo.py`: `MongoStore`, the same interface on Atlas
- `app/services/road_graph.py`: routing graph from `graph.graphml` (edge ids = segment ids)
- `app/services/flood_risk.py`: live risk per road, read from the ML job's `risk_scores` (static score as fallback)
- `app/services/route_planner.py`: usual vs flood-safer route on the graph (closures + risk-weighted costs)
- `app/services/parking.py`: FIU lot flood hazard (scaled by rain) and safer alternatives
- `app/api/`: endpoints (`risk.py`: `/weather`, `/segments/risk`, `/demo/scenario`; `routes.py`: `/routes`; `parking.py`: `/parking`; `calendar.py`: `/calendar/*`; `alerts.py`: `/alerts*`; `dashboard.py`: `/trips`, `/dashboard`, `/demo/seed-trips`; `me.py`: `/me`)
- `app/services/trips.py`: a trip = route + parking check (used by `/routes` and the calendar)
- `app/services/calendar_sync.py`: calendar events -> FIU building -> lot -> leave-by time
- `app/integrations/fake_calendar.py`: fake Google Calendar from `fixtures/calendar.json` (weekly schedule)
- `app/integrations/tomtom.py`: traffic-aware travel time for a route the backend chose (optional)
- `app/integrations/live_conditions.py`: NWS flood alerts + Biscayne Bay tide (adapted from `data-pipeline/ml/live.py`)
- `app/services/alerts.py`: builds/updates/resolves alerts from the next trip, NWS and tide; saved via the store
- `app/services/dashboard.py`: records trips the student starts, sums them up for `/dashboard`, seeds a demo week
- `fixtures/`: fake data in the same shape as the real sources (closures, calendar, user profile);
  `fiu_buildings.json` (FIU building codes, from OSM via `scripts/build_fiu_buildings.py`)
