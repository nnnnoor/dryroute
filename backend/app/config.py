"""Settings, read from environment variables or backend/.env. Every value has a local default."""
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", extra="ignore")

    # "local": committed pipeline files + backend/fixtures. "mongo": Atlas (needs MONGO_URI).
    data_backend: Literal["local", "mongo"] = "local"
    mongo_uri: str | None = None
    mongo_db: str = "flood"

    # Pipeline outputs (graph.graphml, segments.parquet, fiu_parking.geojson, ...), committed to git
    data_dir: Path = REPO_ROOT / "data-pipeline" / "data" / "processed"
    fixtures_dir: Path = BACKEND_DIR / "fixtures"

    use_fake_calendar: bool = True
    calendar_lookahead_days: int = 7    # how far ahead /calendar/next-event looks
    walk_m_per_min: float = 80          # walking speed from the lot to class
    walk_detour: float = 1.3            # real paths vs straight line
    max_lot_walk_m: float = 800         # no FIU lot this close to the building -> drive to the building itself

    # Risk labels (docs/api-contracts.md Conventions): low < risk_medium <= medium < risk_high <= high
    risk_medium: float = 0.35
    risk_high: float = 0.6
    risk_stale_hours: float = 3       # older "live" ML runs fall back to the static score
    segments_risk_limit: int = 5000   # max features per /segments/risk response

    # Routing. Safe-route edge cost = travel_time * (1 + route_risk_weight * risk),
    # times route_high_risk_penalty on high-risk edges (discouraged, never forbidden).
    route_risk_weight: float = 2.0
    route_high_risk_penalty: float = 5.0
    route_roadwork_factor: float = 1.5  # roadwork / planned construction: slower, still passable
    route_compromised_min_m: float = 200  # high-risk road on the usual route needed to call it compromised
    coverage_snap_m: float = 300        # a point farther than this from any mapped road is outside the area

    # Traffic (optional). With a key, each chosen route's drive time comes from TomTom (live traffic now,
    # predicted traffic for a future departure); without one, from speed limits (free-flow).
    tomtom_api_key: str | None = None
    traffic_timeout_s: float = 4.0
    traffic_cache_minutes: float = 10   # same route in the same 10-min slot reuses the answer (free-tier quota)

    # Parking. hazard = static parking_score (floods when it rains) x factor for the current rain level.
    # No usable ML run -> factor 1.0 (assume rain), same as the roads' static fallback.
    parking_rain_factor: dict[str, float] = {"none": 0.4, "light": 0.6, "moderate": 0.8, "heavy": 1.0}
    parking_alternatives: int = 3
    # Time from reaching the lot to being parked (added to the calendar's leave-by). Estimates.
    parking_search_minutes: dict[str, float] = {"garage": 5, "surface": 3, "street_side": 5}
    parking_peak_extra_minutes: float = 4   # weekday mornings, when lots fill for class
    parking_peak_hours: tuple[float, float] = (7.5, 11.0)  # Miami local time, 7:30-11:00
    # Lots rated and shown on the map, but never suggested to a student as somewhere to park
    parking_not_suggested: list[str] = ["Unnamed", "Loading Area", "Staff", "Compound"]

    # Alerts. Rebuilt when the app polls GET /alerts, at most every alerts_refresh_seconds.
    alerts_refresh_seconds: float = 60
    alert_lookahead_hours: float = 12        # trip alerts only for an event starting within this
    alert_traffic_delay_minutes: float = 10  # "leave earlier" when traffic adds at least this
    live_conditions_enabled: bool = True     # NWS flood alerts + Biscayne Bay tide (free, no key)

    # Dashboard. "Time saved" estimate: driving into a flooded road typically costs this many minutes
    # (crawling through water, turning back, detouring); a trip saves that share of it it avoided,
    # minus the safer route's extra minutes.
    flood_detour_minutes: float = 15

    # Local frontend: Vite dev (5173) and `vite preview` (4173), as localhost or 127.0.0.1; 3000 for other setups
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:4173",
                               "http://127.0.0.1:4173", "http://localhost:3000"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
