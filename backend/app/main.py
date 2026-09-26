"""DryRoute API. Run from backend/: uvicorn app.main:app --reload --port 8000"""
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api import calendar, parking, risk, routes
from app.config import Settings, get_settings
from app.db.store import make_store
from app.integrations.fake_calendar import FakeCalendar
from app.services.calendar_sync import CalendarService
from app.services.flood_risk import RiskService
from app.services.parking import ParkingService
from app.services.road_graph import RoadNetwork
from app.services.route_planner import RoutePlanner
from app.services.trips import TripPlanner


def make_calendar_source(settings: Settings):
    if settings.use_fake_calendar:
        return FakeCalendar(settings.fixtures_dir / "calendar.json")
    raise NotImplementedError("Google Calendar is not wired up yet; use USE_FAKE_CALENDAR=true")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    store = make_store(settings)
    network = RoadNetwork(settings.data_dir / "graph.graphml")
    network.check_matches(store.segments.index)
    app.state.settings = settings
    app.state.store = store
    app.state.network = network
    app.state.risk = RiskService(store, settings)
    app.state.planner = RoutePlanner(network, store, app.state.risk, settings)
    app.state.parking = ParkingService(store, app.state.risk, settings)
    app.state.trips = TripPlanner(app.state.planner, app.state.parking, store)
    app.state.calendar = CalendarService(make_calendar_source(settings), store, app.state.trips, app.state.parking,
                                         settings, settings.fixtures_dir / "fiu_buildings.json")
    yield


settings = get_settings()
app = FastAPI(title="DryRoute API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(risk.router)
app.include_router(routes.router)
app.include_router(parking.router)
app.include_router(calendar.router)


@app.get("/health")
def health(request: Request):
    s = request.app.state
    return {
        "status": "ok",
        "data_backend": s.settings.data_backend,
        "fake_calendar": s.settings.use_fake_calendar,
        "segments": len(s.store.segments),
        "graph_nodes": s.network.G.number_of_nodes(),
        "graph_edges": s.network.G.number_of_edges(),
        "parking_lots": len(s.store.parking),
        "hotspots": len(s.store.hotspots),
        "active_closures": len(s.store.active_closures()),
    }
