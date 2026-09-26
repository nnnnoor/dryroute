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

## Layout

- `app/main.py`: app, startup (loads data + graph), CORS
- `app/config.py`: settings
- `app/db/store.py`: data access (`LocalStore` now, Mongo later), same interface either way
- `app/services/road_graph.py`: routing graph from `graph.graphml` (edge ids = segment ids)
- `app/services/flood_risk.py`: live risk per road, read from the ML job's `risk_scores` (static score as fallback)
- `app/services/route_planner.py`: usual vs flood-safer route on the graph (closures + risk-weighted costs)
- `app/services/parking.py`: FIU lot flood hazard (scaled by rain) and safer alternatives
- `app/api/`: endpoints (`risk.py`: `/weather`, `/segments/risk`, `/demo/scenario`; `routes.py`: `/routes`; `parking.py`: `/parking`; `calendar.py`: `/calendar/*`)
- `app/services/trips.py`: a trip = route + parking check (used by `/routes` and the calendar)
- `app/services/calendar_sync.py`: calendar events -> FIU building -> lot -> leave-by time
- `app/integrations/fake_calendar.py`: fake Google Calendar from `fixtures/calendar.json` (weekly schedule)
- `fixtures/`: fake data in the same shape as the real sources (closures, calendar, user profile);
  `fiu_buildings.json` (FIU building codes, from OSM via `scripts/build_fiu_buildings.py`)
