"""Vercel entrypoint for the backend (backend/README.md, Deploy): the FastAPI app in backend/app."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "backend"))

from app.main import app  # noqa: E402,F401
