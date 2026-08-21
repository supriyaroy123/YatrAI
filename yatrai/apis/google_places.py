"""
YatrAI — Google Places API (New) client
-----------------------------------------
Replaces the Overpass/OpenStreetMap pipeline for POI discovery.

Public function
    search_google_places(text_query, bounds, max_results) -> list[dict]

Each returned dict follows the YatrAI internal place schema:
    {
        "name":    str,          # displayName.text
        "lat":     float,        # location.latitude
        "lon":     float,        # location.longitude
        "address": str,          # formattedAddress
        "type":    str,          # primaryType (human-readable label)
        "tags": {                # mirrors the OSM-style tags dict
            "opening_hours": str | None,
            "phone":         str | None,
        }
    }
"""

import logging
import requests

from yatrai.config import (
    GOOGLE_MAPS_API_KEY,
    GOOGLE_PLACES_SEARCH_URL,
    GOOGLE_PLACES_FIELD_MASK,
    GOOGLE_PLACES_MAX_RESULTS,
)

logger = logging.getLogger(__name__)


_COMMON_KEYWORD_TAGS = {
    "petrol": ["amenity=fuel"],
    "fuel": ["amenity=fuel"],
    "gas": ["amenity=fuel"],
    "cng": ["amenity=fuel"],
    "diesel": ["amenity=fuel"],
    "pump": ["amenity=fuel"],
    "restaurant": ["amenity=restaurant", "amenity=food_court"],
    "food": ["amenity=restaurant", "amenity=food_court", "amenity=cafe"],
    "dhaba": ["amenity=restaurant"],
    "hotel": ["tourism=hotel", "tourism=motel", "tourism=guest_house"],
    "motel": ["tourism=hotel", "tourism=motel"],
    "resort": ["tourism=hotel", "tourism=resort"],
    "spa": ["leisure=spa", "shop=beauty", "craft=massage"],
    "massage": ["leisure=spa", "craft=massage"],
    "hospital": ["amenity=hospital", "amenity=clinic"],
    "clinic": ["amenity=clinic"],
    "pharmacy": ["amenity=pharmacy"],
    "chemist": ["amenity=pharmacy"],
    "doctor": ["amenity=doctors"],
    "atm": ["amenity=atm"],
    "bank": ["amenity=bank"],
    "cafe": ["amenity=cafe"],
    "café": ["amenity=cafe"],
    "coffee": ["amenity=cafe"],
    "parking": ["amenity=parking"],
    "metro": ["railway=station", "station=subway"],
    "railway": ["railway=station"],
    "train": ["railway=station"],
    "station": ["railway=station", "amenity=bus_station"],
    "bus": ["amenity=bus_station", "highway=bus_stop"],
    "toilet": ["amenity=toilets"],
    "restroom": ["amenity=toilets"],
    "temple": ["amenity=place_of_worship"],
    "worship": ["amenity=place_of_worship"],
    "mall": ["shop=mall", "shop=department_store"],
    "supermarket": ["shop=supermarket"],
    "grocery": ["shop=supermarket", "shop=convenience"],
    "shop": ["shop=yes"],
    "police": ["amenity=police"],
    "charging": ["amenity=charging_station"],
    "ev": ["amenity=charging_station"],
}


def _overpass_fallback(text_query: str, bounds: dict, osm_tags: list = None) -> list:
    """
    Transparently falls back to the Overpass (OpenStreetMap) pipeline when
    Google Places is unavailable (no API key, quota exceeded, network error).

    Returns an empty list if the Overpass module is not available (e.g. after
    migration to Google Places).
    """
    try:
        from yatrai.apis.overpass import search_overpass_multi, search_overpass_by_name
    except ImportError:
        logger.warning(
            "[GooglePlaces] Overpass module not available — cannot fall back. "
            "Ensure GOOGLE_MAPS_API_KEY is set in your environment."
        )
        return []

    # Map raw query to common OSM tags locally if tags were not resolved by Gemini
    if not osm_tags and text_query:
        local_tags = []
        tokens = text_query.strip().lower().split()
        for token in tokens:
            clean_token = "".join(c for c in token if c.isalnum())
            if clean_token in _COMMON_KEYWORD_TAGS:
                local_tags.extend(_COMMON_KEYWORD_TAGS[clean_token])
        if local_tags:
            osm_tags = list(set(local_tags))
            logger.info("[GooglePlaces] Mapped raw query '%s' locally to OSM tags %s", text_query, osm_tags)

    # 1. Try structured multi-tag search first (very fast, precise)
    if osm_tags:
        try:
            results = search_overpass_multi(osm_tags, bounds)
            if results:
                logger.info("[GooglePlaces] Overpass multi-tag fallback found %d results", len(results))
                return results
        except Exception as exc:
            logger.warning("[GooglePlaces] Overpass multi-tag fallback failed: %s", exc)

    # 2. Last resort: try regex-based name search
    try:
        keywords = text_query.strip().lower().split()
        results = search_overpass_by_name(keywords, bounds)
        logger.info(
            "[GooglePlaces] Overpass fallback for '%s' -> %d results",
            text_query, len(results),
        )
        return results
    except Exception as exc:
        logger.error("[GooglePlaces] Overpass fallback also failed: %s", exc)
        return []

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _make_location_bias(bounds: dict) -> dict:
    """
    Convert a YatrAI route bounding box into a Google Places locationBias
    rectangle.

    bounds format: {"low": {"lat": .., "lon": ..}, "high": {"lat": .., "lon": ..}}
    """
    return {
        "rectangle": {
            "low": {
                "latitude":  bounds["low"]["lat"],
                "longitude": bounds["low"]["lon"],
            },
            "high": {
                "latitude":  bounds["high"]["lat"],
                "longitude": bounds["high"]["lon"],
            },
        }
    }


def _parse_opening_hours(opening_hours):
    """
    Extract a compact, human-readable summary from the Places API
    regularOpeningHours object.
    Returns the first weekday description line (e.g. "Monday: 9:00 AM - 9:00 PM")
    or a plain "Open now" / "Closed now" indicator, or None if not available.
    """
    if not opening_hours:
        return None

    # weekdayDescriptions is a list of strings like ["Monday: 9:00 AM - 9:00 PM", ...]
    descriptions = opening_hours.get("weekdayDescriptions")
    if descriptions and isinstance(descriptions, list):
        return " | ".join(descriptions)

    # Fallback: open_now flag
    open_now = opening_hours.get("openNow")
    if open_now is True:
        return "Open now"
    if open_now is False:
        return "Closed now"

    return None


def _humanise_type(primary_type):
    """
    Convert a Google Places primaryType slug (e.g. "pizza_restaurant") into
    a readable label ("Pizza Restaurant").
    """
    if not primary_type:
        return "Place"
    return primary_type.replace("_", " ").title()


def _parse_places(response_json: dict) -> list:
    """
    Map the raw Google Places Text Search response to the YatrAI POI schema.
    """
    raw_places = response_json.get("places", [])
    results = []

    for place in raw_places:
        # -- Coordinates -------------------------------------------------------
        location = place.get("location", {})
        lat = location.get("latitude")
        lon = location.get("longitude")
        if lat is None or lon is None:
            continue  # skip places without coordinates

        # -- Name --------------------------------------------------------------
        display_name = place.get("displayName", {})
        name = display_name.get("text") or "Unnamed Location"

        # -- Address -----------------------------------------------------------
        address = place.get("formattedAddress", "")

        # -- Type --------------------------------------------------------------
        primary_type = place.get("primaryType")
        type_label = _humanise_type(primary_type)

        # -- Tags (opening hours + phone) -------------------------------------
        opening_hours_str = _parse_opening_hours(place.get("regularOpeningHours"))
        phone = place.get("nationalPhoneNumber") or None

        tags = {}
        if opening_hours_str:
            tags["opening_hours"] = opening_hours_str
        if phone:
            tags["phone"] = phone

        results.append({
            "name":    name,
            "lat":     lat,
            "lon":     lon,
            "address": address,
            "type":    type_label,
            "tags":    tags,
        })

    return results


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def search_google_places(
    text_query: str,
    bounds: dict,
    max_results: int = GOOGLE_PLACES_MAX_RESULTS,
    osm_tags: list = None,
) -> list:
    """
    Query Google Places API (New) - Text Search endpoint - and return a list
    of place dicts in the YatrAI internal schema.

    Args:
        text_query:  The free-text query (e.g. "petrol pump", "spa", "dhaba")
        bounds:      Route bounding box dict with "low"/"high" keys
        max_results: Maximum places to retrieve per request (API cap = 20)

    Returns:
        List of place dicts; empty list on error or if no API key is set.
    """
    api_key = GOOGLE_MAPS_API_KEY
    if not api_key:
        logger.warning(
            "[GooglePlaces] GOOGLE_MAPS_API_KEY is not set -- "
            "falling back to Overpass (OpenStreetMap)."
        )
        return _overpass_fallback(text_query, bounds, osm_tags)

    payload = {
        "textQuery":    text_query,
        "pageSize":     min(max_results, 20),   # hard cap enforced by Google
        "locationBias": _make_location_bias(bounds),
        "languageCode": "en",  # consistent English results for parsing
    }

    headers = {
        "Content-Type":     "application/json",
        "X-Goog-Api-Key":   api_key,
        "X-Goog-FieldMask": GOOGLE_PLACES_FIELD_MASK,
    }

    try:
        resp = requests.post(
            GOOGLE_PLACES_SEARCH_URL,
            json=payload,
            headers=headers,
            timeout=6.0,  # Google Places responds in <2s normally
        )

        if resp.status_code == 200:
            data = resp.json()
            places = _parse_places(data)
            logger.info(
                "[GooglePlaces] '%s' -> %d places returned",
                text_query, len(places),
            )
            return places

        # Log meaningful error details for billing/quota issues
        try:
            error_info = resp.json().get("error", {})
        except Exception:
            error_info = {}
        logger.warning(
            "[GooglePlaces] HTTP %d for query='%s': %s -- falling back to Overpass.",
            resp.status_code, text_query,
            error_info.get("message", resp.text[:200]),
        )
        return _overpass_fallback(text_query, bounds, osm_tags)

    except requests.exceptions.Timeout:
        logger.warning(
            "[GooglePlaces] Request timed out for query='%s' -- falling back to Overpass.",
            text_query,
        )
        return _overpass_fallback(text_query, bounds, osm_tags)
    except Exception as exc:
        logger.error(
            "[GooglePlaces] Unexpected error for query='%s': %s -- falling back to Overpass.",
            text_query, exc,
        )
        return _overpass_fallback(text_query, bounds, osm_tags)
