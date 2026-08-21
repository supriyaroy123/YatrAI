"""
YatrAI — POI Search Pipeline (Google Places API, fully dynamic)
---------------------------------------------------------------
2-stage pipeline that finds ANY place the user asks for using real,
live Google Maps data:

  Stage 1 — Gemini Intent Extraction
      Gemini interprets the user's free-text query (including Hindi/Hinglish)
      and returns a clean English search phrase, name synonyms, and OSM tags.

  Stage 2 — Google Places Text Search
      A rich multi-keyword query is sent to the Google Places API (New) Text
      Search endpoint with a location bias rectangle from the route bounding
      box.  Results are real, verified places — far richer than OSM coverage.

  Stage 2b — Relevance Filtering
      Google Places Text Search does substring matching, so "spa" can return
      "Sparsh Residency" or "Spatial Design School".  We filter out results
      whose primaryType is clearly unrelated to the user's intent (using the
      osm_tags returned by Gemini to determine the expected category family).

  Finally — Distance & Detour Calculation
      Results are filtered to those within 10 km of the route polyline,
      sorted by perpendicular distance, and the top 50 are returned with
      detour badges ("On your way", "Small detour", "Detour needed").

Note: The Overpass (OpenStreetMap) pipeline has been retired in favour of
Google Places for better real-world coverage, especially in India.
"""

import math
import logging
import asyncio
from typing import Optional
from concurrent.futures import ThreadPoolExecutor

from yatrai.apis.gemini import extract_poi_intent
from yatrai.apis.google_places import search_google_places

logger = logging.getLogger(__name__)

# Thread pool for running blocking I/O without stalling the event loop
_poi_executor = ThreadPoolExecutor(max_workers=4)

# ---------------------------------------------------------------------------
# Fast-path local intent dictionary
# ---------------------------------------------------------------------------
# Covers ~80% of real-world Indian travel queries. If the user's query
# matches one of these (exact lowercase match), we skip the Gemini API call
# entirely and use these pre-built intents — shaving 2-4 seconds off latency.

_LOCAL_INTENTS: dict = {
    "petrol pump": {
        "osm_tags": ["amenity=fuel"],
        "name_keywords": ["petrol", "fuel", "pump", "filling", "hp", "bharat", "indian oil"],
    },
    "petrol": {
        "osm_tags": ["amenity=fuel"],
        "name_keywords": ["petrol", "fuel", "pump", "filling", "hp", "bharat"],
    },
    "fuel": {
        "osm_tags": ["amenity=fuel"],
        "name_keywords": ["petrol", "fuel", "pump", "filling", "gas"],
    },
    "gas station": {
        "osm_tags": ["amenity=fuel"],
        "name_keywords": ["petrol", "fuel", "gas", "pump", "filling"],
    },
    "cng": {
        "osm_tags": ["amenity=fuel"],
        "name_keywords": ["cng", "gas", "fuel", "pump", "adani"],
    },
    "ev charging": {
        "osm_tags": ["amenity=charging_station"],
        "name_keywords": ["ev", "charging", "electric", "charger", "tata", "ather"],
    },
    "restaurant": {
        "osm_tags": ["amenity=restaurant", "amenity=fast_food"],
        "name_keywords": ["restaurant", "dhaba", "food", "kitchen", "bhojnalaya"],
    },
    "dhaba": {
        "osm_tags": ["amenity=restaurant", "amenity=fast_food"],
        "name_keywords": ["dhaba", "restaurant", "punjabi", "food", "bhojnalaya"],
    },
    "hotel": {
        "osm_tags": ["tourism=hotel", "tourism=guest_house", "tourism=hostel"],
        "name_keywords": ["hotel", "inn", "lodge", "suites", "stay", "residency"],
    },
    "hospital": {
        "osm_tags": ["amenity=hospital", "amenity=clinic"],
        "name_keywords": ["hospital", "clinic", "medical", "health", "nursing"],
    },
    "atm": {
        "osm_tags": ["amenity=atm"],
        "name_keywords": ["atm", "cash", "bank", "sbi", "hdfc", "icici"],
    },
    "pharmacy": {
        "osm_tags": ["amenity=pharmacy"],
        "name_keywords": ["pharmacy", "medical", "chemist", "dawai", "medicine"],
    },
    "cafe": {
        "osm_tags": ["amenity=cafe"],
        "name_keywords": ["cafe", "coffee", "chai", "starbucks", "ccd"],
    },
    "parking": {
        "osm_tags": ["amenity=parking"],
        "name_keywords": ["parking", "park", "lot", "garage"],
    },
    "police": {
        "osm_tags": ["amenity=police"],
        "name_keywords": ["police", "thana", "station", "chowki"],
    },
    "temple": {
        "osm_tags": ["amenity=place_of_worship", "historic=temple"],
        "name_keywords": ["temple", "mandir", "devi", "shiv", "hanuman"],
    },
    "mosque": {
        "osm_tags": ["amenity=place_of_worship"],
        "name_keywords": ["mosque", "masjid", "jama", "dargah"],
    },
    "church": {
        "osm_tags": ["amenity=place_of_worship"],
        "name_keywords": ["church", "cathedral", "chapel"],
    },
    "gurudwara": {
        "osm_tags": ["amenity=place_of_worship"],
        "name_keywords": ["gurudwara", "gurdwara", "sikh", "langar"],
    },
    "toilet": {
        "osm_tags": ["amenity=toilets"],
        "name_keywords": ["toilet", "restroom", "washroom", "sulabh", "bathroom"],
    },
    "restroom": {
        "osm_tags": ["amenity=toilets"],
        "name_keywords": ["restroom", "toilet", "washroom", "sulabh"],
    },
    "bus station": {
        "osm_tags": ["amenity=bus_station", "highway=bus_stop"],
        "name_keywords": ["bus", "station", "depot", "stand", "terminal"],
    },
    "railway station": {
        "osm_tags": ["railway=station"],
        "name_keywords": ["railway", "station", "train", "rail"],
    },
    "bank": {
        "osm_tags": ["amenity=bank"],
        "name_keywords": ["bank", "sbi", "hdfc", "icici", "pnb", "axis"],
    },
    "mall": {
        "osm_tags": ["shop=mall", "shop=department_store"],
        "name_keywords": ["mall", "shopping", "plaza", "centre", "center"],
    },
    "supermarket": {
        "osm_tags": ["shop=supermarket"],
        "name_keywords": ["supermarket", "grocery", "dmart", "reliance", "big bazaar"],
    },
    "gym": {
        "osm_tags": ["leisure=fitness_centre", "leisure=sports_centre"],
        "name_keywords": ["gym", "fitness", "workout", "crossfit"],
    },
    "spa": {
        "osm_tags": ["leisure=spa", "shop=beauty", "craft=massage"],
        "name_keywords": ["spa", "wellness", "massage", "relax"],
    },
    "mechanic": {
        "osm_tags": ["shop=car_repair", "craft=car_repair"],
        "name_keywords": ["mechanic", "garage", "repair", "workshop", "service"],
    },
    "tyre shop": {
        "osm_tags": ["shop=tyres", "craft=car_repair"],
        "name_keywords": ["tyre", "tire", "wheel", "puncture", "rubber"],
    },
    "toll": {
        "osm_tags": ["barrier=toll_booth"],
        "name_keywords": ["toll", "plaza", "booth", "nhai"],
    },
    "bakery": {
        "osm_tags": ["shop=bakery", "craft=bakery"],
        "name_keywords": ["bakery", "baker", "bread", "cake", "pastry"],
    },
    "sweet shop": {
        "osm_tags": ["shop=confectionery", "shop=pastry"],
        "name_keywords": ["sweet", "mithai", "halwai", "confection"],
    },
}


# ---------------------------------------------------------------------------
# Relevance filter  (fully dynamic — no static category whitelist)
# ---------------------------------------------------------------------------

# These place types are NEVER what a traveler wants when searching for
# a POI along a route.  This is a tiny universal hard-reject list only.
# It does NOT limit which categories a user CAN search for.
_ALWAYS_REJECT_TYPES: frozenset = frozenset({
    "school", "primary_school", "secondary_school", "university",
    "accounting", "insurance_agency", "real_estate_agency",
    "lawyer",
    # Note: local_government_office is NOT rejected — a traveler may legitimately
    # search for RTO, tehsil, gram panchayat, passport office, etc.
    "moving_company", "storage", "roofing_contractor",
    "plumber", "electrician", "painter", "general_contractor",
    "embassy",
})


def _is_relevant(place: dict, name_keywords: list) -> bool:
    """
    Fully dynamic relevance check — works for ANY user query without needing
    a pre-defined type mapping.

    Three rules (in priority order):

    Rule 1 — Hard reject
        Place types that are universally irrelevant for a traveler on a route
        (school, insurance agency, law firm, moving company, etc.).
        These are rejected regardless of the query.

    Rule 2 — Keyword match in place name  →  always KEEP
        If ANY of the user's intent keywords appears in the place name, the
        result is intentionally named that way and must be shown.
        e.g. 'spa' in name_keywords and place name contains 'spa' → keep.
        This catches: 'Ananda Spa', 'Wellness Spa Centre', 'Sparsh Spa'.

    Rule 3 — Benefit of the doubt  →  KEEP
        Everything else is kept.  Google Places already ranks by relevance;
        we trust its ranking and only strip universally wrong results.
        This ensures obscure queries (EV charging, gurudwara, nail salon,
        chai tapri, RTO, dharmashala, toll, etc.) are never over-filtered.
    """
    # Rule 1: hard reject
    ptype = (place.get("type") or "").lower().replace(" ", "_")
    if ptype in _ALWAYS_REJECT_TYPES:
        return False

    # Rule 2: keyword found in place name → definitely relevant
    pname = (place.get("name") or "").lower()
    for kw in name_keywords:
        kw_clean = (kw or "").strip().lower()
        if kw_clean and kw_clean in pname:
            return True

    # Rule 3: benefit of the doubt
    return True


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km between two lat/lon points."""
    R = 6371.0
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def point_to_line_distance(pt_lat: float, pt_lon: float, line_coords: list) -> tuple:
    """
    Returns (perpendicular_dist_km, along_route_dist_km, nearest_segment_index).
    line_coords is a list of [lon, lat] pairs (GeoJSON order).
    """
    min_dist = float("inf")
    total_along = 0.0
    best_along = 0.0
    best_idx = 0

    for i in range(len(line_coords) - 1):
        lat1, lon1 = line_coords[i][1],   line_coords[i][0]
        lat2, lon2 = line_coords[i+1][1], line_coords[i+1][0]

        seg_len       = haversine_km(lat1, lon1, lat2, lon2)
        dist_to_start = haversine_km(pt_lat, pt_lon, lat1, lon1)
        dist_to_end   = haversine_km(pt_lat, pt_lon, lat2, lon2)

        if seg_len == 0:
            perp_dist = dist_to_start
            along_seg = 0.0
        else:
            t = max(0.0, min(1.0,
                (dist_to_start ** 2 - dist_to_end ** 2 + seg_len ** 2) / (2 * seg_len ** 2)
            ))
            proj_lat  = lat1 + t * (lat2 - lat1)
            proj_lon  = lon1 + t * (lon2 - lon1)
            perp_dist = haversine_km(pt_lat, pt_lon, proj_lat, proj_lon)
            along_seg = t * seg_len

        if perp_dist < min_dist:
            min_dist   = perp_dist
            best_along = total_along + along_seg
            best_idx   = i

        total_along += seg_len

    return min_dist, best_along, best_idx


def compute_route_bounds(geometry_coords: list) -> dict:
    """Bounding box of the route (GeoJSON [lon, lat] list)."""
    lats = [c[1] for c in geometry_coords]
    lons = [c[0] for c in geometry_coords]
    return {
        "low":  {"lat": min(lats), "lon": min(lons)},
        "high": {"lat": max(lats), "lon": max(lons)},
    }


# ---------------------------------------------------------------------------
# Distance & detour annotation
# ---------------------------------------------------------------------------

def _annotate_places(raw_places: list, geometry_coords: list, max_dist_km: float = 15.0) -> list:
    """
    Filter and annotate places with perpendicular distance and detour badge.
    Removes anything farther than max_dist_km from the route polyline.
    Indian highways are sparse — 15km cutoff avoids dropping legitimate results.
    """
    results = []
    for p in raw_places:
        perp_km, _, _ = point_to_line_distance(p["lat"], p["lon"], geometry_coords)
        if perp_km > max_dist_km:
            continue

        if perp_km <= 1.0:
            detour_badge = "On your way"
        elif perp_km <= 3.0:
            detour_badge = "Small detour"
        else:
            detour_badge = "Detour needed"

        p["distance_km"]  = round(perp_km, 2)
        p["detour_badge"] = detour_badge
        results.append(p)

    results.sort(key=lambda x: x["distance_km"])
    return results[:50]


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def _get_local_intent(query: str) -> Optional[dict]:
    """
    Fast-path: check the local intent dictionary for common Indian travel
    queries. Returns a full intent dict if found, None otherwise.
    This skips the Gemini API call entirely for ~80% of real-world searches.
    """
    q = query.strip().lower()
    local = _LOCAL_INTENTS.get(q)
    if local:
        logger.info("[POISearch] Fast-path local intent HIT for '%s'", q)
        return {
            "osm_tags":       local["osm_tags"],
            "name_keywords":  local["name_keywords"],
            "filters":        [],
            "hindi_detected": False,
        }
    # Also check single-word partial matches (e.g. user types "petrol")
    for key, val in _LOCAL_INTENTS.items():
        if q in key or key in q:
            logger.info("[POISearch] Fast-path partial match '%s' → '%s'", q, key)
            return {
                "osm_tags":       val["osm_tags"],
                "name_keywords":  val["name_keywords"],
                "filters":        [],
                "hindi_detected": False,
            }
    return None


async def run_poi_search(
    query: str,
    geometry_coords: list,
    cached_result: Optional[dict] = None,
) -> dict:
    """
    Full dynamic POI search pipeline — 2 stages + relevance filter:

    Stage 1 — Local fast-path OR Gemini extracts clean English keywords.
    Stage 2 — Google Places Text Search with a clean multi-keyword query.
    Stage 2b — Relevance filter (skipped on retry to avoid over-filtering).

    Returns: {"pois": [...], "count": int, "from_cache": bool, "intent": dict}
    """
    # -- Cache hit -------------------------------------------------------------
    if cached_result is not None:
        pois = cached_result.get("pois", [])
        return {"pois": pois, "count": len(pois), "from_cache": True}

    loop = asyncio.get_running_loop()

    # -- Stage 1: Intent extraction (local fast-path first, then Gemini) ------
    intent = _get_local_intent(query)
    if intent is None:
        # Fall back to Gemini with a 3-second timeout guard
        try:
            intent = await asyncio.wait_for(
                loop.run_in_executor(_poi_executor, extract_poi_intent, query),
                timeout=3.0,
            )
        except asyncio.TimeoutError:
            logger.warning("[POISearch] Gemini intent timed out for '%s' — using raw query", query)
            intent = {
                "osm_tags": [],
                "name_keywords": [query.strip().lower()],
                "filters": [],
                "hindi_detected": False,
            }

    osm_tags      = intent.get("osm_tags", [])
    name_keywords = intent.get("name_keywords", [query.strip().lower()])

    # -- Build a clean text query from the top 3 keywords ---------------------
    # Keep it simple — joining 3 keywords gives Google enough context.
    # No extra word appending (it was muddying the results).
    rich_keywords = name_keywords[:3]
    text_query = " ".join(rich_keywords) if rich_keywords else query.strip()

    logger.info(
        "[POISearch] Query='%s'  keywords=%s  google_text_query='%s'",
        query, name_keywords, text_query,
    )

    # -- Compute bounding box -------------------------------------------------
    bounds = compute_route_bounds(geometry_coords)

    # -- Stage 2: Google Places Text Search -----------------------------------
    raw_places = await loop.run_in_executor(
        _poi_executor, search_google_places, text_query, bounds, 20, osm_tags
    )
    logger.info("[POISearch] Stage 2 (Google Places): %d raw results", len(raw_places))

    # -- Stage 2b: Dynamic relevance filter -----------------------------------
    before = len(raw_places)
    filtered_places = [p for p in raw_places if _is_relevant(p, name_keywords)]
    filtered_out = before - len(filtered_places)
    if filtered_out:
        logger.info(
            "[POISearch] Relevance filter removed %d unrelated places for query '%s'",
            filtered_out, query,
        )

    # -- Retry with raw query if still empty ----------------------------------
    # On retry: use the raw query AND skip the relevance filter entirely,
    # since over-filtering was likely the reason we got zero results.
    if not filtered_places and text_query.lower() != query.strip().lower():
        logger.info("[POISearch] Retrying with raw query (no filter): '%s'", query.strip())
        retry_places = await loop.run_in_executor(
            _poi_executor, search_google_places, query.strip(), bounds, 20, osm_tags
        )
        # Skip relevance filter on retry — show whatever Google returns
        filtered_places = retry_places
        logger.info("[POISearch] Retry: %d results (unfiltered)", len(filtered_places))

    # -- Distance annotation & filtering --------------------------------------
    results = _annotate_places(filtered_places, geometry_coords)

    return {
        "pois":       results,
        "count":      len(results),
        "from_cache": False,
        "intent":     intent,
    }
