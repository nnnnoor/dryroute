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
    "elev_p10": 0.25,           # inverted: low elevation = high risk. p10, not min: min catches water pixels at bridge ends
    "flood_report_days": 0.20,  # scaled by percentile within jurisdiction (RISK_PCT_WITHIN)
    "pct_impervious": 0.10,
    "sink_p90": 0.10,           # depression depth; p90 not max: single deep pixels (lake/pit edges)
}
RISK_INVERT = {"elev_p10"}
# scale by a known range instead of data min/max (clipped). elev_p10: <= 0.5 m -> max risk, >= 5 m -> none;
# a data min/max would be stretched to ~11 m by motorway embankments
# sink_p90: 0 m -> 0, >= 1 m (~99th pct) -> 1; data max (~2.9 m, quarry/lake edges) would squeeze the rest
RISK_FIXED_RANGE = {"fema_risk_level": (0, 3), "elev_p10": (0.5, 5.0), "sink_p90": (0.0, 1.0)}
FILL_ZERO = ["flood_report_days", "drain_issue_days", "drains_per_100m"]  # counts/rates: missing means none
# Percentile scaling (replaces min/max): feature -> group column. Edges with 0 get 0; edges with > 0
# get their percentile rank (pandas rank(method="average", pct=True)) among the edges with > 0 in the
# same group, so (0, 1]. Used for 311 because the city logs far more reports per road than the county.
RISK_PCT_WITHIN = {"flood_report_days": "jurisdiction"}
# Final override: bridge-deck edges get risk_score = min(risk_score, cap). Raw features untouched.
BRIDGE_RISK_CAP = 0.1

# Step 9: Atlas target (MONGO_URI in .env has no db name, so it's set here)
MONGO_DB = "flood"
MONGO_COLLECTION = "segments"

# Step 3: spacing (m) of elevation samples along each edge (DEM is 10 m; min 5 samples per edge)
ELEV_SAMPLE_M = 10

# Step 5: 311 reports (sources/types chosen 2026-09-26; see CLAUDE.md Step 5)
REPORTS_SINCE = "2022-01-01"
REPORT_SNAP_M = 30          # sjoin_nearest max distance to a non-bridge edge (UTM m)
COUNTY_311_YEARS = [2022, 2023]  # data_311_2024 needs a token (not public); 2025+ don't exist
FLOOD_TYPES = {  # scored -> flood_report_days
    "city": ["COMPWSF"],                                   # STORM FLOOD/ DRAINAGE
    "county": ["FLOODING / STANDING WATER - LOCALIZED"],
}
DRAIN_TYPES = {  # not scored -> drain_issue_days
    "city": [],
    "county": ["DRAIN CLOGGED / CLEANING", "DRAIN - REPAIR", "RER DRAINAGE CANALS FLOOD COMPLAINT",
               "CANAL - BLOCKED"],
}

# Step 6: Miami-Dade "Stormwater Point" TYPE values that are surface inlets (where road water enters).
# Excluded: MANHOLE (pipe access), CROSS SECTION (canal survey), DRAINAGE WELL / VERTICAL FRENCH DRAIN
# (disposal, not intake), structures, pumps, valves, etc. Not scored (direction is ambiguous).
INLET_TYPES = ["CATCH BASIN", "RISER CATCH BASIN", "YARD DRAIN"]
DRAIN_BUFFER_M = 50

# Step 10: road closures / construction for routing (NOT part of risk_score). Loaded to Mongo
# collection MONGO_CLOSURES; the backend filters start <= now <= end at request time.
MONGO_CLOSURES = "closures"
FL511_EVENT_TYPES = ["roadwork", "closures"]   # FL511 EventType values kept (needs FL511_API_KEY in .env)
CLOSURE_SNAP_M = 30          # point events: nearest edges within this distance (UTM m)
CLOSURE_BUFFER_M = 20        # line/polygon events: buffer, then edges with >= CLOSURE_MIN_OVERLAP of length inside
CLOSURE_MIN_OVERLAP = 0.5
CLOSURE_BEARING_TOL = 60     # directional events: keep edges within +-this many degrees of the direction
# Miami-Dade Utility Coordination (planned construction; weak proxy: planned windows, not lane closures)
UCC_LAYERS = [  # MDPublisher FeatureServer names sharing the PRJNAME/GENPRJSTAT/STARTDATE/ENDDATE schema
    "UtilCoordRoadway_gdb", "UtilCoordPaving_gdb", "FDOTCorridor_gdb", "MDCPWArea_gdb", "MDCPWCorridor_gdb",
    "MDCPWSite_gdb", "UtilCoordBridge_gdb", "UtilCoordStormwater_gdb", "UtilCoordWater_gdb", "UtilCoordSewer_gdb",
    "UtilCoordReclaimed_gdb", "UtilCoordGas_gdb", "UtilCoordCable_gdb", "UtilCoordPower_gdb", "UtilCoordTransit_gdb",
    "WASDWater_gdb", "WASDSewer_gdb", "WASDReclaimed_gdb", "UtilCoordMiscellaneous_gdb", "MiamiCIPArea_gdb",
    "CoralGablesCIPSite_gdb",
]
UCC_EXCLUDE_AGENCY_STATUS = ["Const.Complete", "Closed"]  # GENPRJSTAT says Construction but the work is done
UCC_MAX_WINDOW_DAYS = 730    # longer windows are multi-year program spans (FDOT median ~7 yr), not work zones
# FDOT entries in Utility Coordination carry fiscal-year windows (Jul 1 -> Jun 30), not work dates, and
# FDOT's actual lane/road closures come from FL511. So FDOT is taken from FL511 only.
UCC_EXCLUDE_AGENCIES = ["FDOT"]
