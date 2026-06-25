"""
OSRM (Open Source Routing Machine) API wrapper.
Gets road distance, duration, and turn-by-turn geometry between two points.
"""

import requests
from yatrai.config import (
    OSRM_URL, API_TIMEOUT, ROUTING_API_KEY, OPENROUTESERVICE_URL
)


def get_route(origin: tuple, destination: tuple) -> dict:
    """
    Get driving route between two coordinate pairs using OpenRouteService or OSRM fallback.

    Args:
        origin: (lat, lon) tuple for the start point.
        destination: (lat, lon) tuple for the end point.

    Returns:
        dict with {distance_km, duration_min, geometry, steps} on success,
        or None on failure.
    """
    # Attempt OpenRouteService first if key is available
    if ROUTING_API_KEY:
        headers = {
            "Authorization": ROUTING_API_KEY,
            "Content-Type": "application/json",
        }
        # ORS expects longitude, latitude order
        body = {
            "coordinates": [
                [origin[1], origin[0]],
                [destination[1], destination[0]]
            ]
        }
        try:
            resp = requests.post(
                OPENROUTESERVICE_URL, json=body, headers=headers, timeout=API_TIMEOUT
            )
            resp.raise_for_status()
            data = resp.json()

            if data.get("features"):
                feature = data["features"][0]
                geometry = feature.get("geometry", {})
                properties = feature.get("properties", {})
                segments = properties.get("segments", [{}])
                segment = segments[0]

                steps = []
                for step in segment.get("steps", []):
                    steps.append({
                        "instruction": step.get("instruction", step.get("name", "")),
                        "distance_m": step.get("distance", 0.0),
                        "duration_s": step.get("duration", 0.0),
                    })

                return {
                    "distance_km": round(segment.get("distance", 0.0) / 1000, 2),
                    "duration_min": round(segment.get("duration", 0.0) / 60, 2),
                    "geometry": geometry,
                    "steps": steps,
                }
        except Exception as e:
            print(f"[OpenRouteService] Error getting route: {e}. Falling back to OSRM...")

    # Fallback to OSRM
    # OSRM expects lon,lat order
    origin_str = f"{origin[1]},{origin[0]}"
    dest_str = f"{destination[1]},{destination[0]}"
    url = f"{OSRM_URL}/{origin_str};{dest_str}"

    params = {
        "overview": "full",
        "geometries": "geojson",
        "steps": "true",
    }

    try:
        resp = requests.get(url, params=params, timeout=API_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()

        if data.get("code") != "Ok" or not data.get("routes"):
            return None

        route = data["routes"][0]
        legs = route.get("legs", [{}])

        steps = []
        for leg in legs:
            for step in leg.get("steps", []):
                steps.append({
                    "instruction": step.get("name", ""),
                    "distance_m": step.get("distance", 0),
                    "duration_s": step.get("duration", 0),
                })

        return {
            "distance_km": round(route["distance"] / 1000, 2),
            "duration_min": round(route["duration"] / 60, 2),
            "geometry": route["geometry"],
            "steps": steps,
        }
    except Exception as e:
        print(f"[OSRM] Error getting route: {e}")
        return None
