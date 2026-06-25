"""
Feature Bridging Module — the heart of YatrAI's inference pipeline.

Converts 3 user inputs (origin, destination, vehicle type) into the
26-feature vector the congestion model expects, and the 20-feature vector
the accident model needs — using only free API data and training medians.

Feature groups and their sources:
  TRAFFIC   ← derived from OSRM distance/duration + time-of-day heuristics
  SIGNAL    ← defaults (no real sensor feed)
  WEATHER   ← direct from Open-Meteo API
  V2X       ← imputed with training medians (no real V2X network)
  DERIVED   ← recalculated from the above using VANET formulas
"""

import random
import numpy as np
import pandas as pd
from datetime import datetime

from yatrai.config import (
    CONGESTION_ALL_FEATURES,
    CONGESTION_V2X_FEATURES,
    ACCIDENT_ALL_FEATURES,
    DEFAULT_FREE_FLOW_SPEED,
    MAX_DENSITY,
    RUSH_HOUR_RANGES,
    NIGHT_HOURS,
    DENSITY_MULTIPLIERS,
)


# ── Congestion Feature Builder ────────────────────────────────────────

def build_congestion_features(
    route_data: dict,
    weather_data: dict,
    training_medians: dict,
    current_hour: int = None,
) -> pd.DataFrame:
    """
    Build a single-row DataFrame with all 26 congestion model features
    from live API data and saved training medians.

    Args:
        route_data: dict from OSRM with {distance_km, duration_min, …}.
        weather_data: dict from Open-Meteo with {temp_c, rain_mm, visibility_km, …}.
        training_medians: dict of V2X feature medians from training data.
        current_hour: Hour of day (0-23). Defaults to server clock.

    Returns:
        pd.DataFrame with one row and columns in CONGESTION_ALL_FEATURES order.
    """
    if current_hour is None:
        current_hour = datetime.now().hour

    features = {}

    # ── 1. TRAFFIC FEATURES ──────────────────────────────────────────

    distance_km = route_data.get("distance_km", 10.0)
    duration_min = route_data.get("duration_min", 15.0)
    duration_hr = max(duration_min / 60.0, 1e-6)

    # Average speed from OSRM (free-flow routing)
    avg_speed = distance_km / duration_hr
    features["avg_speed_kmph"] = avg_speed

    # Density estimate from time-of-day heuristics
    base_density = 40.0  # vehicles/km baseline for Indian urban roads
    is_rush = any(s <= current_hour <= e for s, e in RUSH_HOUR_RANGES)
    night_start, night_end = NIGHT_HOURS
    is_night = current_hour >= night_start or current_hour < night_end

    if is_rush:
        density_multiplier = DENSITY_MULTIPLIERS["rush_hour"]
    elif is_night:
        density_multiplier = DENSITY_MULTIPLIERS["night"]
    else:
        density_multiplier = DENSITY_MULTIPLIERS["daytime"]

    density = base_density * density_multiplier
    features["density_veh_per_km"] = density

    # Wait time increases with density (quadratic relationship)
    features["avg_wait_time_s"] = (density / MAX_DENSITY) ** 2 * 120.0

    # Occupancy as percentage of max density, capped at 100
    features["occupancy_pct"] = min((density / MAX_DENSITY) * 100.0, 100.0)

    # Fundamental traffic flow equation: flow = speed × density
    flow = avg_speed * density
    features["flow_veh_per_hr"] = flow

    # Queue forms when density exceeds threshold
    features["queue_length_veh"] = density * 0.3 if density > 50 else 0.0

    # Steady-state assumption — small random acceleration
    features["avg_accel_ms2"] = random.uniform(-0.5, 0.5)

    # ── 2. SIGNAL FEATURES ───────────────────────────────────────────

    features["heading_deg"] = random.uniform(0, 360)   # not meaningful
    features["signal_state_num"] = 1.0                  # default: green
    features["incident_num"] = 0.0                      # no incident

    # ── 3. WEATHER FEATURES (direct from API) ────────────────────────

    features["temp_c"] = weather_data.get("temp_c", 30.0)
    features["visibility_km"] = weather_data.get("visibility_km", 10.0)
    features["rain_intensity_mmph"] = weather_data.get("rain_mm", 0.0)

    # ── 4. V2X FEATURES → imputed with training medians ──────────────

    for feat in CONGESTION_V2X_FEATURES:
        features[feat] = training_medians.get(feat, 0.0)

    # ── 5. DERIVED FEATURES (recalculated from above) ────────────────

    features["speed_density_ratio"] = (
        features["avg_speed_kmph"] / (features["density_veh_per_km"] + 1e-6)
    )

    features["congestion_pressure"] = (
        features["density_veh_per_km"]
        * features["avg_wait_time_s"]
        / (features["avg_speed_kmph"] + 1e-6)
    )

    features["wireless_congestion_intensity"] = (
        features.get("channel_busy_ratio_pct", 0.0)
        * features.get("packet_loss_pct", 0.0)
        / 100.0
    )

    features["throughput_per_queued_vehicle"] = (
        features["flow_veh_per_hr"]
        / (features["queue_length_veh"] + 1e-6)
    )

    features["acceleration_directionality"] = (
        abs(features["avg_accel_ms2"])
        * (features["heading_deg"] / 360.0)
    )

    temp = features["temp_c"]
    vis = features["visibility_km"]
    rain = features["rain_intensity_mmph"]
    features["weather_factor"] = (
        (1.0 - vis / 20.0)
        * (1.0 + rain / 10.0)
        * (1.0 + abs(temp - 25.0) / 50.0)
    )

    # ── Build DataFrame in exact column order ────────────────────────
    df = pd.DataFrame([features])
    df = df[CONGESTION_ALL_FEATURES]
    return df


# ── Accident Feature Builder ─────────────────────────────────────────

def build_accident_features(
    weather_data: dict,
    origin_coords: tuple,
    hour: int = None,
) -> pd.DataFrame:
    """
    Build a single-row DataFrame with accident model features from
    live weather data and location.

    Args:
        weather_data: dict from Open-Meteo with temp_c, rain_mm, etc.
        origin_coords: (lat, lon) tuple.
        hour: Hour of day (0-23). Defaults to server clock.

    Returns:
        pd.DataFrame with one row and columns in ACCIDENT_ALL_FEATURES order.
    """
    if hour is None:
        hour = datetime.now().hour

    now = datetime.now()
    features = {}

    # ── Weather features (unit conversions) ──────────────────────────
    temp_c = weather_data.get("temp_c", 30.0)
    features["Temperature(F)"] = temp_c * 9.0 / 5.0 + 32.0

    features["Humidity(%)"] = weather_data.get("humidity", 50.0)

    # Pressure: default to average sea-level (29.92 inHg)
    features["Pressure(in)"] = 29.92

    # Visibility: km → miles
    vis_km = weather_data.get("visibility_km", 10.0)
    features["Visibility(mi)"] = vis_km * 0.621371

    # Wind speed: km/h → mph
    wind_kmh = weather_data.get("wind_speed_kmh", 10.0)
    features["Wind_Speed(mph)"] = wind_kmh * 0.621371

    # Precipitation: mm → inches
    rain_mm = weather_data.get("rain_mm", 0.0)
    features["Precipitation(in)"] = rain_mm * 0.0393701

    # ── Road features (default to False — unknown) ───────────────────
    road_features = [
        "Crossing", "Junction", "Traffic_Signal", "Amenity",
        "Bump", "Give_Way", "No_Exit", "Roundabout",
        "Station", "Stop", "Traffic_Calming",
    ]
    for feat in road_features:
        features[feat] = 0

    # ── Time features ────────────────────────────────────────────────
    features["hour"] = hour
    features["day_of_week"] = now.weekday()  # 0=Mon .. 6=Sun
    features["is_weekend"] = 1 if now.weekday() >= 5 else 0
    features["is_night"] = 1 if (hour < 6 or hour >= 22) else 0
    features["is_rush_hour"] = 1 if any(
        s <= hour <= e for s, e in [(7, 10), (17, 20)]
    ) else 0

    # ── Location features ────────────────────────────────────────────
    features["Start_Lat"] = origin_coords[0] if origin_coords else 28.6139
    features["Start_Lng"] = origin_coords[1] if origin_coords else 77.2090

    # ── Sunrise/Sunset derived ───────────────────────────────────────
    features["Sunrise_Sunset_Night"] = features["is_night"]

    # ── Build DataFrame in exact column order ────────────────────────
    df = pd.DataFrame([features])
    df = df[ACCIDENT_ALL_FEATURES]
    return df
