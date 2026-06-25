"""
Open-Meteo weather API wrapper.
Fetches current weather conditions with sensible defaults for Indian cities.
"""

import requests
from yatrai.config import (
    OPEN_METEO_URL, API_TIMEOUT, WEATHER_API_KEY, OPENWEATHERMAP_URL
)


def get_weather(lat: float, lon: float) -> dict:
    """
    Get current weather for a coordinate pair using OpenWeatherMap or Open-Meteo fallback.

    Args:
        lat: Latitude in decimal degrees.
        lon: Longitude in decimal degrees.

    Returns:
        dict with {temp_c, rain_mm, visibility_km, wind_speed_kmh, humidity}.
        Falls back to sensible defaults on failure.
    """
    defaults = {
        "temp_c": 30.0,
        "rain_mm": 0.0,
        "visibility_km": 10.0,
        "wind_speed_kmh": 10.0,
        "humidity": 50.0,
    }

    # Attempt OpenWeatherMap if key is available
    if WEATHER_API_KEY:
        params = {
            "lat": lat,
            "lon": lon,
            "appid": WEATHER_API_KEY,
            "units": "metric",
        }
        try:
            resp = requests.get(OPENWEATHERMAP_URL, params=params, timeout=API_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            
            main = data.get("main", {})
            wind = data.get("wind", {})
            rain = data.get("rain", {})
            
            # OWM returns wind speed in m/s; convert to km/h
            wind_speed = wind.get("speed", defaults["wind_speed_kmh"] / 3.6) * 3.6
            # OWM returns rain in mm for last 1h or 3h
            rain_mm = rain.get("1h", rain.get("3h", defaults["rain_mm"]))
            
            return {
                "temp_c": main.get("temp", defaults["temp_c"]),
                "rain_mm": rain_mm,
                # OWM returns visibility in metres; convert to km
                "visibility_km": data.get("visibility", defaults["visibility_km"] * 1000) / 1000,
                "wind_speed_kmh": wind_speed,
                "humidity": main.get("humidity", defaults["humidity"]),
            }
        except Exception as e:
            print(f"[OpenWeatherMap] Error: {e}. Falling back to Open-Meteo...")

    # Fallback to Open-Meteo
    params = {
        "latitude": lat,
        "longitude": lon,
        "current_weather": "true",
        "current": "temperature_2m,relative_humidity_2m,rain,visibility,wind_speed_10m",
    }

    try:
        resp = requests.get(OPEN_METEO_URL, params=params, timeout=API_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        current = data.get("current", {})

        return {
            "temp_c": current.get("temperature_2m", defaults["temp_c"]),
            "rain_mm": current.get("rain", defaults["rain_mm"]),
            # Open-Meteo returns visibility in metres; convert to km
            "visibility_km": current.get(
                "visibility", defaults["visibility_km"] * 1000
            )
            / 1000,
            "wind_speed_kmh": current.get(
                "wind_speed_10m", defaults["wind_speed_kmh"]
            ),
            "humidity": current.get(
                "relative_humidity_2m", defaults["humidity"]
            ),
        }
    except Exception as e:
        print(f"[Open-Meteo] Error: {e}, using defaults")
        return defaults
