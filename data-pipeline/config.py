"""Shared config: bbox, CRS values, and paths. Every pipeline script imports from here."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent

BBOX = (-80.39, 25.72, -80.18, 25.80)   # (west, south, east, north): FIU -> Brickell
CRS_WGS = "EPSG:4326"
CRS_UTM = "EPSG:32617"

RAW = ROOT / "data" / "raw"
FEAT = ROOT / "data" / "features"
PROC = ROOT / "data" / "processed"
CHECKS = ROOT / "data" / "checks"

# Step 1: max Hausdorff distance (m, UTM) for u->v and v->u edges to count as one physical street
TWIN_TOL_M = 1.0

# Step 8: risk_score = weighted mean of 0-1 scaled features (weights renormalized over the
# features present for each row). Placeholder until the ML model replaces it.
RISK_WEIGHTS = {
    "fema_risk_level": 0.35,
    "elev_min": 0.25,           # inverted: low elevation = high risk
    "flood_reports_311": 0.20,
    "pct_impervious": 0.10,
    "sink_depth": 0.10,
}
RISK_INVERT = {"elev_min"}
RISK_FIXED_RANGE = {"fema_risk_level": (0, 3)}  # scale by known range instead of data min/max
FILL_ZERO = ["flood_reports_311", "drains_per_100m"]  # counts/rates: missing means none
# Final override: bridge-deck edges get risk_score = min(risk_score, cap). Raw features untouched.
BRIDGE_RISK_CAP = 0.1
