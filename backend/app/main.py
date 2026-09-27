"""DryRoute API. Run from backend/: uvicorn app.main:app --reload --port 8000"""
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api import alerts, calendar, dashboard, me, parking, risk, routes
from app.config import Settings, get_settings
from app.db.store import make_store
from app.integrations.fake_calendar import FakeCalendar
from app.integrations.google_calendar import CalendarSessions, router as google_router
from app.integrations.live_conditions import LiveConditions
from app.integrations.tomtom import TomTomTraffic
from app.services.alerts import AlertService
from app.services.calendar_sync import CalendarService
from app.services.dashboard import DashboardService
from app.services.flood_risk import RiskService
from app.services.parking import ParkingService
from app.services.road_graph import RoadNetwork
from app.services.route_planner import RoutePlanner
from app.services.trips import TripPlanner


def make_calendar_source(settings: Settings):
    # Real Google sources are selected per request after OAuth, never shared globally.
    return FakeCalendar(settings.fixtures_dir / "calendar.json")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    store = make_store(settings)
    network = RoadNetwork(settings.data_dir / "graph.graphml")
    network.check_matches(store.segments.index)
    app.state.settings = settings
    app.state.calendar_sessions = CalendarSessions()
    app.state.store = store
    app.state.network = network
    app.state.risk = RiskService(store, settings)
    traffic = TomTomTraffic(settings.tomtom_api_key, settings.traffic_timeout_s,
                            settings.traffic_cache_minutes) if settings.tomtom_api_key else None
    app.state.planner = RoutePlanner(network, store, app.state.risk, settings, traffic)
    app.state.parking = ParkingService(store, app.state.risk, settings)
    app.state.trips = TripPlanner(app.state.planner, app.state.parking, store)
    app.state.calendar = CalendarService(make_calendar_source(settings), store, app.state.trips, app.state.parking,
                                         settings, settings.fixtures_dir / "fiu_buildings.json")
    live = LiveConditions() if settings.live_conditions_enabled else None
    app.state.alerts = AlertService(store, app.state.calendar, app.state.parking, live, settings)
    app.state.dashboard = DashboardService(store, app.state.trips, app.state.risk, settings)
    yield


settings = get_settings()
app = FastAPI(title="DryRoute API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)
app.include_router(risk.router)
app.include_router(routes.router)
app.include_router(parking.router)
app.include_router(calendar.router)
app.include_router(google_router)
app.include_router(alerts.router)
app.include_router(dashboard.router)
app.include_router(me.router)


@app.get("/health")
def health(request: Request):
    s = request.app.state
    return {
        "status": "ok",
        "data_backend": s.settings.data_backend,
        "fake_calendar": s.settings.use_fake_calendar,
        "traffic": "tomtom" if s.planner.traffic else "off (free-flow)",
        "live_conditions": "nws + tide" if s.alerts.live else "off",
        "segments": len(s.store.segments),
        "graph_nodes": s.network.G.number_of_nodes(),
        "graph_edges": s.network.G.number_of_edges(),
        "parking_lots": len(s.store.parking),
        "hotspots": len(s.store.hotspots),
        "active_closures": len(s.store.active_closures()),
    }
