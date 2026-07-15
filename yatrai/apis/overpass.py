"""
YatrAI — Overpass API Client
------------------------------
Fetches Points of Interest from OpenStreetMap via the Overpass API.

Key design decisions:
  - Uses the blocking `requests` library (called from asyncio thread pool)
  - Primary endpoint: overpass-api.de (most stable, global)
  - Fallback mirror: overpass.karte.io (Japanese CDN, good for Asia)
  - QUERY_INTENT_MAP: maps free-text keywords → OSM tag filters so the
    user can type "petrol" and we know to query amenity=fuel

Stage in the pipeline:
  route coords → [sample points] → THIS MODULE → raw POI dicts
                                   (for each sampled point, in parallel)
"""

import logging
import requests
from typing import Optional

from yatrai.config import (
    OVERPASS_URL, OVERPASS_FALLBACK_URL, OVERPASS_TIMEOUT
)

logger = logging.getLogger(__name__)

# ── Query Intent Map ──────────────────────────────────────────────────────────
# Maps normalized user query keywords → list of Overpass tag filters.
# Each filter is a string like "amenity=fuel" or "tourism=hotel".
# When multiple tags are present they are OR-ed in the Overpass query.
QUERY_INTENT_MAP: dict[str, list[str]] = {
    # Fuel / petrol
    "fuel":              ["amenity=fuel"],
    "petrol":            ["amenity=fuel"],
    "petrol pump":       ["amenity=fuel"],
    "gas":               ["amenity=fuel"],
    "gas station":       ["amenity=fuel"],
    "cng":               ["amenity=fuel"],
    "ev":                ["amenity=charging_station"],
    "charging":          ["amenity=charging_station"],
    "ev charging":       ["amenity=charging_station"],
    "electric":          ["amenity=charging_station"],

    # Food
    "restaurant":        ["amenity=restaurant"],
    "food":              ["amenity=restaurant", "amenity=fast_food", "amenity=food_court"],
    "eat":               ["amenity=restaurant", "amenity=fast_food"],
    "lunch":             ["amenity=restaurant", "amenity=fast_food", "amenity=cafe"],
    "dinner":            ["amenity=restaurant"],
    "breakfast":         ["amenity=cafe", "amenity=restaurant"],
    "vegetarian":        ["amenity=restaurant", "amenity=fast_food"],
    "veg":               ["amenity=restaurant", "amenity=fast_food"],
    "dhaba":             ["amenity=restaurant"],
    "cafe":              ["amenity=cafe"],
    "coffee":            ["amenity=cafe"],
    "tea":               ["amenity=cafe"],
    "fast food":         ["amenity=fast_food"],
    "snacks":            ["amenity=fast_food", "amenity=cafe"],
    "bakery":            ["shop=bakery", "amenity=cafe"],
    "sweet":             ["shop=confectionery", "amenity=restaurant"],
    "juice":             ["amenity=cafe", "amenity=fast_food"],
    "biryani":           ["amenity=restaurant"],
    "pizza":             ["amenity=fast_food", "amenity=restaurant"],
    "burger":            ["amenity=fast_food"],

    # Accommodation
    "hotel":             ["tourism=hotel", "tourism=guest_house", "tourism=motel"],
    "accommodation":     ["tourism=hotel", "tourism=guest_house", "tourism=hostel"],
    "stay":              ["tourism=hotel", "tourism=guest_house"],
    "lodge":             ["tourism=hotel", "tourism=guest_house"],
    "motel":             ["tourism=motel"],
    "resort":            ["tourism=resort"],
    "hostel":            ["tourism=hostel"],
    "guesthouse":        ["tourism=guest_house"],
    "oyo":               ["tourism=hotel", "tourism=guest_house"],
    "dharamshala":       ["tourism=hostel", "tourism=guest_house"],

    # Medical
    "hospital":          ["amenity=hospital"],
    "clinic":            ["amenity=clinic", "amenity=hospital"],
    "medical":           ["amenity=hospital", "amenity=clinic", "amenity=pharmacy"],
    "doctor":            ["amenity=clinic", "amenity=hospital"],
    "pharmacy":          ["amenity=pharmacy"],
    "chemist":           ["amenity=pharmacy"],
    "medicine":          ["amenity=pharmacy"],
    "veterinary":        ["amenity=veterinary"],
    "vet":               ["amenity=veterinary"],
    "dentist":           ["amenity=dentist"],
    "dispensary":        ["amenity=pharmacy", "amenity=clinic"],

    # Finance
    "atm":               ["amenity=atm"],
    "bank":              ["amenity=bank", "amenity=atm"],
    "cash":              ["amenity=atm"],
    "money":             ["amenity=atm", "amenity=bank"],
    "exchange":          ["amenity=bureau_de_change"],
    "insurance":         ["office=insurance"],

    # Education  ← THE MISSING CATEGORY (fixes 'education center' query)
    "school":            ["amenity=school"],
    "college":           ["amenity=college"],
    "university":        ["amenity=university"],
    "education":         ["amenity=school", "amenity=college", "amenity=university",
                          "amenity=library", "amenity=language_school"],
    "education center":  ["amenity=school", "amenity=college", "amenity=university",
                          "amenity=language_school", "amenity=training"],
    "coaching":          ["amenity=school", "amenity=language_school", "amenity=training"],
    "tuition":           ["amenity=school", "amenity=language_school"],
    "institute":         ["amenity=college", "amenity=university", "amenity=school"],
    "library":           ["amenity=library"],
    "training":          ["amenity=training", "amenity=language_school"],
    "language school":   ["amenity=language_school"],
    "kindergarten":      ["amenity=kindergarten"],
    "nursery":           ["amenity=kindergarten"],
    "playschool":        ["amenity=kindergarten"],

    # Sports & Recreation
    "gym":               ["leisure=fitness_centre", "leisure=sports_centre"],
    "fitness":           ["leisure=fitness_centre"],
    "stadium":           ["leisure=stadium", "leisure=sports_centre"],
    "cricket":           ["leisure=pitch", "leisure=stadium"],
    "football":          ["leisure=pitch", "leisure=stadium"],
    "swimming":          ["leisure=swimming_pool", "leisure=sports_centre"],
    "pool":              ["leisure=swimming_pool"],
    "sports":            ["leisure=sports_centre", "leisure=pitch", "leisure=stadium"],
    "playground":        ["leisure=playground"],

    # Rest & Facilities
    "toilet":            ["amenity=toilets"],
    "restroom":          ["amenity=toilets"],
    "washroom":          ["amenity=toilets"],
    "rest area":         ["highway=rest_area", "amenity=toilets"],
    "parking":           ["amenity=parking"],
    "car park":          ["amenity=parking"],

    # Emergency & Safety
    "police":            ["amenity=police"],
    "police station":    ["amenity=police"],
    "fire":              ["amenity=fire_station"],
    "fire station":      ["amenity=fire_station"],
    "emergency":         ["amenity=hospital", "amenity=police", "amenity=fire_station"],

    # Shopping
    "shop":              ["shop=supermarket", "shop=convenience"],
    "grocery":           ["shop=supermarket", "shop=convenience"],
    "supermarket":       ["shop=supermarket"],
    "mall":              ["shop=mall"],
    "market":            ["amenity=marketplace", "shop=supermarket"],
    "cloth":             ["shop=clothes"],
    "clothing":          ["shop=clothes"],
    "electronics":       ["shop=electronics"],
    "mobile":            ["shop=mobile_phone"],
    "medical store":     ["amenity=pharmacy"],
    "hardware":          ["shop=hardware"],

    # Government / Public
    "post office":       ["amenity=post_office"],
    "post":              ["amenity=post_office"],
    "government":        ["office=government"],
    "municipality":      ["office=government"],

    # Worship / Religious
    "temple":            ["amenity=place_of_worship"],
    "mandir":            ["amenity=place_of_worship"],
    "mosque":            ["amenity=place_of_worship"],
    "masjid":            ["amenity=place_of_worship"],
    "church":            ["amenity=place_of_worship"],
    "gurudwara":         ["amenity=place_of_worship"],
    "worship":           ["amenity=place_of_worship"],
    "prayer":            ["amenity=place_of_worship"],

    # Tourist / Attractions
    "tourist":           ["tourism=attraction", "tourism=viewpoint", "tourism=museum"],
    "viewpoint":         ["tourism=viewpoint"],
    "museum":            ["tourism=museum"],
    "monument":          ["tourism=monument", "tourism=attraction"],
    "zoo":               ["tourism=zoo"],
    "amusement":         ["tourism=theme_park", "leisure=amusement_arcade"],
    "park":              ["leisure=park", "leisure=nature_reserve"],
    "garden":            ["leisure=garden", "leisure=park"],
    "beach":             ["natural=beach"],
    "waterfall":         ["waterway=waterfall", "tourism=attraction"],

    # Transport
    "bus stop":          ["highway=bus_stop"],
    "bus stand":         ["amenity=bus_station"],
    "taxi":              ["amenity=taxi"],
    "auto":              ["amenity=taxi"],
    "railway":           ["railway=station"],
    "train":             ["railway=station"],
    "metro":             ["station=subway", "railway=station"],
    "airport":           ["aeroway=aerodrome"],
}

# ── Broad Semantic Fallback Tags ──────────────────────────────────────────────
# Used when the user query doesn't match any keyword in QUERY_INTENT_MAP.
# Fetches a diverse mix of POI types so that FAISS can semantically rank
# the most relevant ones (e.g., "education center" → schools will rank higher
# than restaurants when these diverse results are re-ranked by MiniLM).
DEFAULT_TAGS = [
    "amenity=school", "amenity=college", "amenity=university",
    "amenity=restaurant", "amenity=hospital", "amenity=pharmacy",
    "amenity=fuel", "tourism=hotel", "amenity=atm",
    "amenity=police", "amenity=cafe", "amenity=place_of_worship",
    "shop=supermarket", "leisure=park", "amenity=parking",
]


def map_query_to_tags(query: str) -> tuple[list[str], bool]:
    """
    Maps a free-text user query to a list of OSM tag strings.

    Strategy:
      1. Exact phrase match (longest match first) → returns specific tags
      2. Any keyword found inside the query string
      3. Broad semantic fallback → fetches 15 diverse POI types so FAISS
         can pick the semantically closest ones from the actual results

    Args:
        query: Raw user input, e.g. "quiet vegetarian restaurant near highway"

    Returns:
        Tuple of (tag_list, matched_intent: bool)
        matched_intent=False means the broad fallback was used.
    """
    q = query.lower().strip()

    # Try longest phrase matches first (avoids "gas" matching "gas station" incompletely)
    sorted_keys = sorted(QUERY_INTENT_MAP.keys(), key=len, reverse=True)
    for key in sorted_keys:
        if key in q:
            logger.debug("[Overpass] Query '%s' matched intent key '%s'", q, key)
            return QUERY_INTENT_MAP[key], True

    # No keyword matched — use broad fallback so FAISS can still find relevant results
    logger.warning(
        "[Overpass] No intent matched for query '%s' — using broad semantic fallback", q
    )
    return DEFAULT_TAGS, False


def build_overpass_query(lat: float, lon: float, radius_m: int, tags: list[str]) -> str:
    """
    Builds an OverpassQL query string that finds all OSM nodes/ways
    matching ANY of the given tag=value filters within `radius_m` metres
    of (lat, lon).

    Example output (for tags=["amenity=fuel", "amenity=atm"]):
        [out:json][timeout:15];
        (
          node["amenity"="fuel"](around:2000,12.34,56.78);
          node["amenity"="atm"](around:2000,12.34,56.78);
        );
        out body;

    Args:
        lat, lon:  Centre of the search circle
        radius_m:  Search radius in metres
        tags:      List of "key=value" strings from QUERY_INTENT_MAP

    Returns:
        OverpassQL query string ready to POST
    """
    # Build one node query block per tag filter
    node_lines = []
    for tag in tags:
        if "=" in tag:
            key, value = tag.split("=", 1)
            node_lines.append(
                f'  node["{key}"="{value}"](around:{radius_m},{lat},{lon});'
            )
        else:
            # Bare key without value — less common but handle gracefully
            node_lines.append(
                f'  node["{tag}"](around:{radius_m},{lat},{lon});'
            )

    union_body = "\n".join(node_lines)
    query = (
        f"[out:json][timeout:{OVERPASS_TIMEOUT}];\n"
        f"(\n{union_body}\n);\n"
        f"out body;"
    )
    return query


def fetch_pois_overpass(
    lat: float,
    lon: float,
    radius_m: int,
    tags: list[str],
) -> list[dict]:
    """
    Calls the Overpass API for POIs near a single point.
    Tries the primary endpoint first, falls back to the mirror on failure.

    This is a synchronous (blocking) function — it is called from an
    asyncio thread pool via run_in_executor so it does not block the event loop.

    Args:
        lat, lon:   Centre coordinates
        radius_m:   Search radius in metres
        tags:       OSM tag filters (from map_query_to_tags)

    Returns:
        List of POI dicts: {osm_id, name, lat, lon, type, tags}
        Empty list on error or zero results (never raises).
    """
    query = build_overpass_query(lat, lon, radius_m, tags)

    for url in (OVERPASS_URL, OVERPASS_FALLBACK_URL):
        try:
            resp = requests.post(
                url,
                data={"data": query},
                timeout=OVERPASS_TIMEOUT,
                headers={"User-Agent": "YatrAI/1.0 (route-poi-search)"},
            )
            resp.raise_for_status()
            data = resp.json()

            elements = data.get("elements", [])
            pois = []
            for el in elements:
                # Only process node-type elements with valid coordinates
                if el.get("type") != "node":
                    continue
                el_lat = el.get("lat")
                el_lon = el.get("lon")
                if el_lat is None or el_lon is None:
                    continue

                el_tags = el.get("tags", {})
                name = el_tags.get("name") or el_tags.get("name:en") or "Unnamed"

                # Derive a human-readable POI type from the first matched tag
                poi_type = _infer_poi_type(el_tags)

                pois.append({
                    "osm_id":  el["id"],
                    "name":    name,
                    "lat":     el_lat,
                    "lon":     el_lon,
                    "type":    poi_type,
                    "tags":    el_tags,
                })

            if not pois:
                logger.debug("[Overpass] Zero results at (%.4f, %.4f) from %s", lat, lon, url)
            else:
                logger.debug("[Overpass] %d POIs at (%.4f, %.4f)", len(pois), lat, lon)

            return pois  # success on primary — no need to try fallback

        except Exception as e:
            logger.warning("[Overpass] Failed at %s for (%.4f, %.4f): %s", url, lat, lon, e)
            # loop continues to fallback URL

    # Both endpoints failed
    logger.error("[Overpass] Both endpoints failed for (%.4f, %.4f) — returning empty", lat, lon)
    return []


def build_overpass_bbox_query(min_lat: float, min_lon: float, max_lat: float, max_lon: float, tags: list[str]) -> str:
    """
    Builds an OverpassQL query string that finds all OSM nodes/ways
    matching ANY of the given tag=value filters within a bounding box.
    """
    node_lines = []
    bbox_str = f"{min_lat},{min_lon},{max_lat},{max_lon}"
    for tag in tags:
        if "=" in tag:
            key, value = tag.split("=", 1)
            node_lines.append(f'  node["{key}"="{value}"]({bbox_str});')
        else:
            node_lines.append(f'  node["{tag}"]({bbox_str});')

    union_body = "\n".join(node_lines)
    query = (
        f"[out:json][timeout:{OVERPASS_TIMEOUT}];\n"
        f"(\n{union_body}\n);\n"
        f"out body;"
    )
    return query


def fetch_pois_overpass_bbox(
    min_lat: float,
    min_lon: float,
    max_lat: float,
    max_lon: float,
    tags: list[str],
) -> list[dict]:
    """
    Calls the Overpass API for POIs within a bounding box.
    Used to replace multiple circular queries with a single query covering the route.
    """
    query = build_overpass_bbox_query(min_lat, min_lon, max_lat, max_lon, tags)

    for url in (OVERPASS_URL, OVERPASS_FALLBACK_URL):
        try:
            resp = requests.post(
                url,
                data={"data": query},
                timeout=OVERPASS_TIMEOUT,
                headers={"User-Agent": "YatrAI/1.0 (route-poi-search-bbox)"},
            )
            resp.raise_for_status()
            data = resp.json()

            elements = data.get("elements", [])
            pois = []
            for el in elements:
                if el.get("type") != "node":
                    continue
                el_lat = el.get("lat")
                el_lon = el.get("lon")
                if el_lat is None or el_lon is None:
                    continue

                el_tags = el.get("tags", {})
                name = el_tags.get("name") or el_tags.get("name:en") or "Unnamed"
                poi_type = _infer_poi_type(el_tags)

                pois.append({
                    "osm_id":  el["id"],
                    "name":    name,
                    "lat":     el_lat,
                    "lon":     el_lon,
                    "type":    poi_type,
                    "tags":    el_tags,
                })

            if not pois:
                logger.debug("[Overpass] Zero results in bbox [%.4f, %.4f, %.4f, %.4f] from %s", min_lat, min_lon, max_lat, max_lon, url)
            else:
                logger.debug("[Overpass] %d POIs in bbox [%.4f, %.4f, %.4f, %.4f]", len(pois), min_lat, min_lon, max_lat, max_lon)

            return pois

        except Exception as e:
            logger.warning("[Overpass] Failed at %s for bbox: %s", url, e)

    logger.error("[Overpass] Both endpoints failed for bbox — returning empty")
    return []


def _infer_poi_type(tags: dict) -> str:
    """
    Infers a clean display label for a POI from its OSM tag dictionary.
    Prioritises the most user-meaningful type.
    """
    amenity  = tags.get("amenity",  "")
    tourism  = tags.get("tourism",  "")
    shop     = tags.get("shop",     "")
    highway  = tags.get("highway",  "")
    leisure  = tags.get("leisure",  "")
    office   = tags.get("office",   "")
    railway  = tags.get("railway",  "")
    aeroway  = tags.get("aeroway",  "")
    natural_ = tags.get("natural",  "")

    amenity_map = {
        # Fuel
        "fuel":             "Petrol Pump",
        "charging_station": "EV Charging",
        # Food
        "restaurant":       "Restaurant",
        "fast_food":        "Fast Food",
        "food_court":       "Food Court",
        "cafe":             "Café",
        # Medical
        "hospital":         "Hospital",
        "clinic":           "Clinic",
        "pharmacy":         "Pharmacy",
        "dentist":          "Dentist",
        "veterinary":       "Veterinary",
        # Finance
        "atm":              "ATM",
        "bank":             "Bank",
        "bureau_de_change": "Currency Exchange",
        # Education
        "school":           "School",
        "college":          "College",
        "university":       "University",
        "library":          "Library",
        "language_school":  "Language School",
        "training":         "Training Centre",
        "kindergarten":     "Kindergarten",
        # Facilities
        "toilets":          "Restroom",
        "parking":          "Parking",
        "place_of_worship": "Place of Worship",
        # Emergency / Government
        "police":           "Police Station",
        "fire_station":     "Fire Station",
        "post_office":      "Post Office",
        # Transport
        "bus_station":      "Bus Station",
        "taxi":             "Taxi Stand",
        # Other
        "marketplace":      "Market",
    }

    tourism_map = {
        "hotel":       "Hotel",
        "guest_house": "Guest House",
        "hostel":      "Hostel",
        "motel":       "Motel",
        "resort":      "Resort",
        "attraction":  "Tourist Attraction",
        "viewpoint":   "Viewpoint",
        "museum":      "Museum",
        "monument":    "Monument",
        "zoo":         "Zoo",
        "theme_park":  "Amusement Park",
    }

    shop_map = {
        "supermarket":  "Supermarket",
        "convenience":  "Convenience Store",
        "mall":         "Mall",
        "bakery":       "Bakery",
        "confectionery":"Sweet Shop",
        "clothes":      "Clothing Store",
        "electronics":  "Electronics Store",
        "mobile_phone": "Mobile Store",
        "hardware":     "Hardware Store",
    }

    leisure_map = {
        "park":             "Park",
        "nature_reserve":   "Nature Reserve",
        "garden":           "Garden",
        "fitness_centre":   "Gym",
        "sports_centre":    "Sports Centre",
        "stadium":          "Stadium",
        "swimming_pool":    "Swimming Pool",
        "pitch":            "Sports Ground",
        "playground":       "Playground",
        "amusement_arcade": "Arcade",
    }

    if amenity in amenity_map:
        return amenity_map[amenity]
    if tourism in tourism_map:
        return tourism_map[tourism]
    if shop in shop_map:
        return shop_map[shop]
    if leisure in leisure_map:
        return leisure_map[leisure]
    if highway == "rest_area":
        return "Rest Area"
    if highway == "bus_stop":
        return "Bus Stop"
    if railway == "station":
        return "Railway Station"
    if aeroway == "aerodrome":
        return "Airport"
    if natural_ == "beach":
        return "Beach"
    if office == "government":
        return "Government Office"
    if office == "insurance":
        return "Insurance Office"

    # Generic fallback: use the raw tag value, capitalised
    raw = amenity or tourism or shop or highway or leisure or office or railway
    return raw.replace("_", " ").title() if raw else "Point of Interest"

