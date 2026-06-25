"""
Nominatim (OpenStreetMap) geocoding API wrapper.
Converts place names to geographic coordinates with built-in rate limiting.
"""

import requests
import time
from yatrai.config import (
    NOMINATIM_URL, NOMINATIM_USER_AGENT, NOMINATIM_DELAY, API_TIMEOUT,
    GEOCODING_API_KEY, OPENCAGE_URL
)

_last_request_time = 0


def geocode(place_name: str) -> dict:
    """
    Convert a place name to geographic coordinates using OpenCage or Nominatim fallback.

    Args:
        place_name: Human-readable place name (e.g., "Connaught Place, Delhi").

    Returns:
        dict with {lat, lon, display_name} on success, or None on failure.
    """
    global _last_request_time

    # Attempt OpenCage first if key is available
    if GEOCODING_API_KEY:
        params = {
            "key": GEOCODING_API_KEY,
            "q": place_name,
            "limit": 1,
            "countrycode": "IN",
        }
        headers = {"User-Agent": NOMINATIM_USER_AGENT}
        try:
            resp = requests.get(
                OPENCAGE_URL, params=params, headers=headers, timeout=API_TIMEOUT
            )
            resp.raise_for_status()
            data = resp.json()

            if data.get("results"):
                result = data["results"][0]
                return {
                    "lat": float(result["geometry"]["lat"]),
                    "lon": float(result["geometry"]["lng"]),
                    "display_name": result["formatted"],
                }
        except Exception as e:
            print(f"[OpenCage] Error geocoding '{place_name}': {e}. Falling back to Nominatim...")

    # Enforce Nominatim's 1-request-per-second rate limit
    elapsed = time.time() - _last_request_time
    if elapsed < NOMINATIM_DELAY:
        time.sleep(NOMINATIM_DELAY - elapsed)

    params = {
        "q": place_name,
        "format": "json",
        "limit": 1,
        "countrycodes": "in",  # restrict to India
    }
    headers = {"User-Agent": NOMINATIM_USER_AGENT}

    try:
        resp = requests.get(
            NOMINATIM_URL, params=params, headers=headers, timeout=API_TIMEOUT
        )
        _last_request_time = time.time()
        resp.raise_for_status()
        data = resp.json()

        if not data:
            return None

        return {
            "lat": float(data[0]["lat"]),
            "lon": float(data[0]["lon"]),
            "display_name": data[0]["display_name"],
        }
    except Exception as e:
        print(f"[Nominatim] Error geocoding '{place_name}': {e}")
        return None
