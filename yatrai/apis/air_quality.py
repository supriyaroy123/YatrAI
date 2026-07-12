"""
Open-Meteo Air Quality API wrapper.
Fetches live AQI, PM2.5, PM10, and dominant pollutant for a location.
Replaces WAQI due to demo token limitations.
"""

import requests
from yatrai.config import API_TIMEOUT, get_aqi_category

def get_aqi(lat: float, lon: float, location_name: str = None) -> dict:
    """
    Get live Air Quality Index for a coordinate pair using Open-Meteo.

    Args:
        lat: Latitude in decimal degrees.
        lon: Longitude in decimal degrees.
        location_name: Optional location/city name (unused in Open-Meteo, kept for signature compatibility).

    Returns:
        dict with {aqi, pm25, pm10, dominant_pollutant, category, color}.
        Returns safe defaults when the API is unavailable.
    """
    defaults = {
        "aqi": -1,
        "pm25": None,
        "pm10": None,
        "dominant_pollutant": "unknown",
        "category": "Unavailable",
        "color": "#999999",
    }

    url = "https://air-quality-api.open-meteo.com/v1/air-quality"
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "us_aqi,pm10,pm2_5"
    }

    try:
        resp = requests.get(url, params=params, timeout=API_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()

        current = data.get("current", {})
        aqi_val = current.get("us_aqi", -1)
        pm10 = current.get("pm10")
        pm25 = current.get("pm2_5")

        if aqi_val is None:
            aqi_val = -1

        if aqi_val >= 0:
            category, color = get_aqi_category(int(aqi_val))
        else:
            category, color = "Unavailable", "#999999"

        dominant = "unknown"
        if pm10 is not None and pm25 is not None:
            dominant = "pm10" if pm10 > pm25 else "pm25"

        return {
            "aqi": int(aqi_val) if aqi_val >= 0 else -1,
            "pm25": pm25,
            "pm10": pm10,
            "dominant_pollutant": dominant,
            "category": category,
            "color": color,
        }
    except Exception as e:
        print(f"[Open-Meteo AQI] Error: {e}")
        return defaults
