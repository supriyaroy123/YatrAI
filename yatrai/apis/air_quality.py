"""
WAQI (World Air Quality Index) API wrapper.
Fetches live AQI, PM2.5, PM10, and dominant pollutant for a location.
"""

import requests
from yatrai.config import WAQI_URL, WAQI_TOKEN, API_TIMEOUT, get_aqi_category


def get_aqi_fallback_search(location_name: str) -> dict:
    """
    Search for AQI stations by parsing a location/display name and querying the WAQI Search API.
    """
    defaults = {
        "aqi": -1,
        "pm25": None,
        "pm10": None,
        "dominant_pollutant": "unknown",
        "category": "Unavailable",
        "color": "#999999",
    }
    if not location_name:
        return defaults
    
    parts = [p.strip() for p in location_name.split(",") if p.strip()]
    if not parts:
        return defaults
        
    candidates = []
    if len(parts) >= 2:
        candidates.append(parts[1])
    candidates.append(parts[0])
    if len(parts) >= 3:
        candidates.append(parts[2])
        
    for keyword in candidates:
        if len(keyword) < 3:
            continue
            
        search_url = "https://api.waqi.info/search/"
        try:
            resp = requests.get(search_url, params={"token": WAQI_TOKEN, "keyword": keyword}, timeout=API_TIMEOUT)
            resp.raise_for_status()
            res_json = resp.json()
            if res_json.get("status") == "ok":
                stations = res_json.get("data", [])
                if stations:
                    uid = stations[0].get("uid")
                    if uid:
                        feed_url = f"https://api.waqi.info/feed/@{uid}/"
                        feed_resp = requests.get(feed_url, params={"token": WAQI_TOKEN}, timeout=API_TIMEOUT)
                        feed_resp.raise_for_status()
                        feed_json = feed_resp.json()
                        if feed_json.get("status") == "ok":
                            info = feed_json["data"]
                            aqi_val = info.get("aqi", -1)
                            if isinstance(aqi_val, str):
                                aqi_val = -1
                            iaqi = info.get("iaqi", {})
                            if aqi_val >= 0:
                                from yatrai.config import get_aqi_category
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
            print(f"[WAQI Fallback] Search error for '{keyword}': {e}")
            
    return defaults


def get_aqi(lat: float, lon: float, location_name: str = None) -> dict:
    """
    Get live Air Quality Index for a coordinate pair.

    Args:
        lat: Latitude in decimal degrees.
        lon: Longitude in decimal degrees.
        location_name: Optional location/city name for search fallback.

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
            # Try keyword search fallback if location_name is available
            if location_name:
                return get_aqi_fallback_search(location_name)
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
        if location_name:
            return get_aqi_fallback_search(location_name)
        return defaults
