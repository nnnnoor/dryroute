import config

OUTPUT = config.ROOT / "artifacts" / "ml"
WEATHER_CACHE = config.RAW / "ml_weather_archive.json"
TIMEZONE = "America/New_York"
LATITUDE, LONGITUDE = 25.76, -80.29
WINDOWS = {"city": ("2022-10-01", "2024-08-09"), "county": ("2022-01-01", "2023-12-31")}
TRAIN_END = "2023-06-30"
VALIDATION_END = "2023-09-30"
SEED = 42
MODEL_VERSION = "daily_report_v1"
