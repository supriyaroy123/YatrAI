"""
YatrAI Central Configuration
All constants, paths, API URLs, feature lists, and tuning parameters.
"""
import os
from pathlib import Path

# ─── Directory Paths ───────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent          # YatrAI/
DATA_DIR = BASE_DIR.parent                                  # Projects/ (where CSVs live)
MODEL_DIR = BASE_DIR / "models"
MODEL_ARCHIVE_DIR = MODEL_DIR / "archive"
FRONTEND_DIR = BASE_DIR / "frontend"
DB_PATH = BASE_DIR / "predictions.db"

# Load environment variables from .env if present
def _load_env():
    env_path = BASE_DIR / ".env"
    if env_path.exists():
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" in line:
                        k, v = line.split("=", 1)
                        os.environ[k.strip()] = v.strip()
        except Exception as e:
            print(f"[config] Warning: Failed to load .env file: {e}")

_load_env()

# Create directories if they don't exist
MODEL_DIR.mkdir(parents=True, exist_ok=True)
MODEL_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

# ─── Dataset Paths ─────────────────────────────────────────────────
VANET_DATASET = DATA_DIR / "vanet_traffic_data.csv"
US_ACCIDENTS_DATASET = DATA_DIR / "US_Accidents_March23.csv"
INDIAN_ROADS_DATASET = DATA_DIR / "indian_roads_dataset.csv"

# ─── Model File Paths ─────────────────────────────────────────────
CONGESTION_MODEL_PATH = MODEL_DIR / "congestion_model.joblib"
CONGESTION_SCALER_PATH = MODEL_DIR / "congestion_scaler.joblib"
CONGESTION_ENCODER_PATH = MODEL_DIR / "congestion_label_encoder.joblib"
TRAINING_MEDIANS_PATH = MODEL_DIR / "training_medians.joblib"
OPTUNA_STUDY_PATH = MODEL_DIR / "congestion_optuna_study.joblib"

ACCIDENT_MODEL_PATH = MODEL_DIR / "accident_model.joblib"
ACCIDENT_SCALER_PATH = MODEL_DIR / "accident_scaler.joblib"
ACCIDENT_ENCODER_PATH = MODEL_DIR / "accident_label_encoder.joblib"

#API Configuration 
# Nominatim (OpenStreetMap) - Free, no key needed
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search" 
NOMINATIM_USER_AGENT = "YatrAI/1.0 (traffic-intelligence-system)"

# OpenCage (Paid / Free tier geocoding, needs key)
GEOCODING_API_KEY = os.environ.get("GEOCODING_API_KEY", "0503af2aa0784d99bd00e550185065e6")
OPENCAGE_URL = "https://api.opencagedata.com/geocode/v1/json"

# OSRM (Open Source Routing Machine) - Free, no key needed
OSRM_URL = "https://router.project-osrm.org/route/v1/driving"

# OpenRouteService (OSM-based routing, needs key)
ROUTING_API_KEY = os.environ.get("ROUTING_API_KEY", "eyJvcmciOiI1YjNjZTM1OTc4NTExMTAwMDFjZjYyNDgiLCJpZCI6IjQxMDEyYWIxYjQ4MDQ4Y2ZiNDM4ZWFmYTFiNWY1ODU4IiwiaCI6Im11cm11cjY0In0=")
OPENROUTESERVICE_URL = "https://api.openrouteservice.org/v2/directions/driving-car/geojson"

# Open-Meteo - Free, no key needed
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

# OpenWeatherMap - Free / Paid weather, needs key
WEATHER_API_KEY = os.environ.get("WEATHER_API_KEY", "7b524f8959f5560bd382f84a1277cea3")
OPENWEATHERMAP_URL = "https://api.openweathermap.org/data/2.5/weather"

# WAQI (World Air Quality Index) - Free, needs token
WAQI_TOKEN = os.environ.get("WAQI_TOKEN", "82ac8dab37b1fece1a0e4c66db9f54cd4fd0ee45")
WAQI_URL = "https://api.waqi.info/feed/geo"

#  API Rate Limiting
NOMINATIM_DELAY = 1.0   # 1 request per second (Nominatim policy)
API_TIMEOUT = 10         # seconds

# Congestion Model Features
# Features used from the VANET dataset for training
CONGESTION_TRAFFIC_FEATURES = [
    "avg_speed_kmph",
    "density_veh_per_km",
    "avg_wait_time_s",
    "occupancy_pct",
    "flow_veh_per_hr",
    "queue_length_veh",
    "avg_accel_ms2",
]

CONGESTION_SIGNAL_FEATURES = [
    "heading_deg",
    "signal_state_num",
    "incident_num",
]

CONGESTION_WEATHER_FEATURES = [
    "temp_c",
    "visibility_km",
    "rain_intensity_mmph",
]

# V2X/IoT features — these get imputed with training medians at inference
CONGESTION_V2X_FEATURES = [
    "channel_busy_ratio_pct",
    "msg_rate_hz",
    "avg_comm_delay_ms",
    "rssi_dbm",
    "packet_loss_pct",
]

# Pre-engineered derived features in the VANET dataset
CONGESTION_DERIVED_FEATURES = [
    "speed_density_ratio",
    "congestion_pressure",
    "wireless_congestion_intensity",
    "throughput_per_queued_vehicle",
    "acceleration_directionality",
    "weather_factor",
]

# All features in the order the model expects them
CONGESTION_ALL_FEATURES = (
    CONGESTION_TRAFFIC_FEATURES
    + CONGESTION_SIGNAL_FEATURES
    + CONGESTION_WEATHER_FEATURES
    + CONGESTION_V2X_FEATURES
    + CONGESTION_DERIVED_FEATURES
)

# Congestion labels
CONGESTION_LABELS = ["Free-flow", "Moderate", "Heavy", "Gridlock"]

# Accident Model Features 
ACCIDENT_WEATHER_FEATURES = [
    "Temperature(F)", "Humidity(%)", "Pressure(in)",
    "Visibility(mi)", "Wind_Speed(mph)", "Precipitation(in)",
]

ACCIDENT_ROAD_FEATURES = [
    "Crossing", "Junction", "Traffic_Signal", "Amenity",
    "Bump", "Give_Way", "No_Exit", "Roundabout",
    "Station", "Stop", "Traffic_Calming",
]

ACCIDENT_TIME_FEATURES = [
    "hour", "day_of_week", "is_weekend", "is_night", "is_rush_hour",
]

ACCIDENT_LOCATION_FEATURES = [
    "Start_Lat", "Start_Lng",
]

ACCIDENT_ALL_FEATURES = (
    ACCIDENT_WEATHER_FEATURES
    + ACCIDENT_ROAD_FEATURES
    + ACCIDENT_TIME_FEATURES
    + ACCIDENT_LOCATION_FEATURES
    + ["Sunrise_Sunset_Night"]
)

# Severity mapping: Kaggle Severity 1-2 → Low, 3 → Medium, 4 → High
SEVERITY_TO_RISK = {1: "Low", 2: "Low", 3: "Medium", 4: "High"}
RISK_LABELS = ["Low", "Medium", "High"]

# Travel Time Configuration 
# Congestion delay multipliers (applied to OSRM free-flow time)
CONGESTION_DELAY_FACTORS = {
    "Free-flow": 1.0,
    "Moderate": 1.4,
    "Heavy": 1.9,
    "Gridlock": 2.6,
}

# Vehicle speed adjustment factors
VEHICLE_SPEED_FACTORS = {
    "Car": 1.0,
    "Bike": 0.8,
    "Truck": 1.3,
    "Auto": 0.9,
    "Bus": 1.2,
}

# Rain delay: ETA *= 1 + (rain_mm * RAIN_DELAY_PER_MM), capped
RAIN_DELAY_PER_MM = 0.05
MAX_RAIN_DELAY = 0.25   # max 25% extra

# ─── Feature Bridging — Heuristic Speed Model 
# Used to estimate traffic features from API data when real sensors
# aren't available. These are calibrated defaults for Indian roads.
DEFAULT_FREE_FLOW_SPEED = 60.0   # km/h on average Indian road
MAX_DENSITY = 150.0              # vehicles/km at standstill
RUSH_HOUR_RANGES = [(7, 10), (17, 20)]  # morning and evening peaks
NIGHT_HOURS = (22, 6)                    # 10 PM to 6 AM

# Density multipliers by time of day
DENSITY_MULTIPLIERS = {
    "rush_hour": 2.5,
    "daytime": 1.0,
    "night": 0.4,
}

# Optuna Tuning Configuration 
OPTUNA_N_TRIALS = 100
OPTUNA_CV_FOLDS = 5
OPTUNA_METRIC = "f1_macro"
OPTUNA_TIMEOUT = 1800  # 30 minutes max

# LightGBM search space
LGBM_PARAM_SPACE = {
    "num_leaves": (20, 300),
    "max_depth": (3, 12),
    "learning_rate": (0.01, 0.3),
    "n_estimators": (100, 1000),
    "min_child_samples": (5, 100),
    "subsample": (0.5, 1.0),
    "colsample_bytree": (0.5, 1.0),
    "reg_alpha": (1e-8, 10.0),
    "reg_lambda": (1e-8, 10.0),
}

#  Drift Detection Configuration 
PSI_THRESHOLD = 0.2        # trigger retraining if PSI exceeds this
DRIFT_CHECK_HOURS = 6      # check every N hours
MIN_PREDICTIONS_FOR_DRIFT = 100  # need at least this many predictions
ACCURACY_DROP_THRESHOLD = 0.05   # retrain if accuracy drops by 5%

# AQI Categories 
AQI_CATEGORIES = {
    (0, 50): ("Good", "#00e400"),
    (51, 100): ("Moderate", "#ffff00"),
    (101, 150): ("Unhealthy for Sensitive Groups", "#ff7e00"),
    (151, 200): ("Unhealthy", "#ff0000"),
    (201, 300): ("Very Unhealthy", "#8f3f97"),
    (301, 500): ("Hazardous", "#7e0023"),
}

def get_aqi_category(aqi_value: int) -> tuple:
    """Returns (category_name, color_hex) for a given AQI value."""
    for (low, high), (name, color) in AQI_CATEGORIES.items():
        if low <= aqi_value <= high:
            return name, color
    return "Hazardous", "#7e0023"
