import config

OUTPUT = config.ROOT / "artifacts" / "ml"
WEATHER_CACHE = config.RAW / "ml_weather_archive.json"
TIMEZONE = "America/New_York"
LATITUDE, LONGITUDE = 25.76, -80.29
WINDOWS = {"city": ("2022-10-01", "2024-08-09"), "county": ("2022-01-01", "2023-12-31")}
TRAIN_END = "2023-06-30"
VALIDATION_END = "2023-09-30"
SEED = 42
MODEL_VERSION = "daily_report_v2"
HISTORY_GAP_DAYS = 7  # skip the current storm's multi-day reports
HISTORY_MIN_EXPOSURE_DAYS = 30
TIDE_STATION = "8723214"  # NOAA CO-OPS Virginia Key, Biscayne Bay
NWS_USER_AGENT = "dryroute-shellhacks (flood-aware routing prototype)"
# Model selection: fit through the first date, validate the following quarter (development streets only).
ROLLING_WINDOWS = [("2022-12-31", "2023-01-01", "2023-03-31"), ("2023-03-31", "2023-04-01", "2023-06-30"),
                   ("2023-06-30", "2023-07-01", "2023-09-30")]
