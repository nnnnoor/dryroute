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

    # Risk labels (docs/api-contracts.md Conventions): low < risk_medium <= medium < risk_high <= high
    risk_medium: float = 0.35
    risk_high: float = 0.6
    risk_stale_hours: float = 3       # older "live" ML runs fall back to the static score
    segments_risk_limit: int = 5000   # max features per /segments/risk response

    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:5173"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
