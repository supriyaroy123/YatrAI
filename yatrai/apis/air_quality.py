"""
WAQI (World Air Quality Index) API wrapper.
Fetches live AQI, PM2.5, PM10, and dominant pollutant for a location.
"""

import requests
from yatrai.config import WAQI_URL, WAQI_TOKEN, API_TIMEOUT, get_aqi_category


def get_aqi(lat: float, lon: float) -> dict:
    """
    Get live Air Quality Index for a coordinate pair.

    Args:
        lat: Latitude in decimal degrees.
        lon: Longitude in decimal degrees.

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

    # WAQI requires geo:latitude;longitude/ format
    url = f"{WAQI_URL}:{lat};{lon}/"
    params = {"token": WAQI_TOKEN}

    try:
        resp = requests.get(url, params=params, timeout=API_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()

        if data.get("status") != "ok":
            return defaults

        info = data["data"]
        aqi_val = info.get("aqi", -1)

        # Some stations return a string (e.g., "-") instead of an integer
        if isinstance(aqi_val, str):
            aqi_val = -1

        iaqi = info.get("iaqi", {})

        if aqi_val >= 0:
            category, color = get_aqi_category(aqi_val)
        else:
            category, color = "Unavailable", "#999999"

        return {
            "aqi": aqi_val,
            "pm25": iaqi.get("pm25", {}).get("v"),
            "pm10": iaqi.get("pm10", {}).get("v"),
            "dominant_pollutant": info.get("dominentpol", "unknown"),
            "category": category,
            "color": color,
        }
    except Exception as e:
        print(f"[WAQI] Error: {e}")
        return defaults
