"""
Nominatim (OpenStreetMap) geocoding API wrapper.
Converts place names to geographic coordinates with built-in rate limiting.
"""

import re
import os
import logging
import requests
import time
from typing import Optional
from yatrai.config import (
    NOMINATIM_URL, NOMINATIM_USER_AGENT, NOMINATIM_DELAY, API_TIMEOUT,
    GEOCODING_API_KEY, OPENCAGE_URL
)

logger = logging.getLogger(__name__)
_last_request_time = 0


# ── Fix 2: Gandhinagar Sector Hardcoded Coordinates ──────────────────────────
# Nominatim returns inaccurate/missing results for "Sector X, Gandhinagar".
# These centroids were sourced from Google Maps for sectors 1-30.
GANDHINAGAR_SECTORS: dict[int, tuple[float, float]] = {
    1:  (23.2156, 72.6369),
    2:  (23.2181, 72.6444),
    3:  (23.2220, 72.6388),
    4:  (23.2248, 72.6466),
    5:  (23.2285, 72.6395),
    6:  (23.2310, 72.6470),
    7:  (23.2130, 72.6510),
    8:  (23.2165, 72.6570),
    9:  (23.2200, 72.6530),
    10: (23.2240, 72.6590),
    11: (23.2275, 72.6540),
    12: (23.2310, 72.6610),
    13: (23.2105, 72.6640),
    14: (23.2140, 72.6700),
    15: (23.2180, 72.6660),
    16: (23.2215, 72.6730),
    17: (23.2250, 72.6680),
    18: (23.2290, 72.6750),
    19: (23.2080, 72.6780),
    20: (23.2115, 72.6840),
    21: (23.2150, 72.6800),
    22: (23.2190, 72.6860),
    23: (23.2230, 72.6820),
    24: (23.2265, 72.6890),
    25: (23.2050, 72.6920),
    26: (23.2090, 72.6970),
    27: (23.2130, 72.6930),
    28: (23.2170, 72.6990),
    29: (23.2210, 72.6950),
    30: (23.2250, 72.7020),
}

# Regex to match "sector <number>" optionally followed by "gandhinagar"
_SECTOR_RE = re.compile(
    r'\bsector\s+(\d{1,2})\b',
    re.IGNORECASE,
)


def _try_gandhinagar_sector(place_name: str) -> Optional[dict]:
    """
    Checks if place_name refers to a Gandhinagar sector.
    Returns {lat, lon, display_name} if matched, else None.
    """
    q = place_name.lower()
    m = _SECTOR_RE.search(q)
    if not m:
        return None

    sector_num = int(m.group(1))

    # Only use hardcoded coords if "gandhinagar" is mentioned or implied
    # (i.e., the query is just "sector 27" without any other city)
    is_gandhinagar = "gandhinagar" in q
    # If no city is mentioned, assume Gandhinagar (since this is the known problem area)
    has_other_city = any(
        city in q for city in
        ["ahmedabad", "delhi", "mumbai", "bangalore", "chennai", "kolkata",
         "pune", "jaipur", "lucknow", "noida", "gurgaon", "chandigarh",
         "faridabad", "hyderabad"]
    )

    if (is_gandhinagar or not has_other_city) and sector_num in GANDHINAGAR_SECTORS:
        lat, lon = GANDHINAGAR_SECTORS[sector_num]
        logger.info("[Geocode] Hardcoded Gandhinagar Sector %d → (%.4f, %.4f)", sector_num, lat, lon)
        return {
            "lat": lat,
            "lon": lon,
            "display_name": f"Sector {sector_num}, Gandhinagar, Gujarat, India",
        }

    return None


def geocode(place_name: str) -> Optional[dict]:
    """
    Convert a place name to geographic coordinates using OpenCage or Nominatim fallback.
    Checks Gandhinagar sector hardcodes first.

    Args:
        place_name: Human-readable place name (e.g., "Connaught Place, Delhi").

    Returns:
        dict with {lat, lon, display_name} on success, or None on failure.
    """
    global _last_request_time
    
    # Fix 2: Try hardcoded Gandhinagar sectors first for ALL geocoding requests
    hardcoded = _try_gandhinagar_sector(place_name)
    if hardcoded:
        return hardcoded

    # Use Google Maps Geocoding API as primary fallback if key is available
    google_maps_key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if google_maps_key:
        params = {
            "address": place_name,
            "key": google_maps_key
        }
        try:
            resp = requests.get(
                "https://maps.googleapis.com/maps/api/geocode/json",
                params=params, timeout=API_TIMEOUT
            )
            resp.raise_for_status()
            data = resp.json()

            if data.get("status") == "OK" and data.get("results"):
                result = data["results"][0]
                return {
                    "lat": float(result["geometry"]["location"]["lat"]),
                    "lon": float(result["geometry"]["location"]["lng"]),
                    "display_name": result["formatted_address"],
                }
        except Exception as e:
            logger.error(f"[Google Geocoding] Error geocoding '{place_name}': {e}")

    # Fallback to Nominatim if Google Maps fails or key is missing
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
        logger.error(f"[Nominatim] Error geocoding '{place_name}': {e}")
        return None

def geocode_poi_anchor(anchor_text: str) -> Optional[dict]:
    """Deprecated: just calls geocode()"""
    return geocode(anchor_text)

