"""
YatrAI — POI Search Pipeline (Google Maps-style, fully dynamic)
----------------------------------------------------------------
3-stage pipeline that finds ANY place the user asks for:

  Stage 1 — Gemini Tag Extraction
      Gemini acts as an OSM expert and returns ALL possible key=value tags
      for the user's query, e.g. "spa" → ["leisure=spa","shop=beauty","craft=massage"]

  Stage 2 — Multi-tag Overpass Union Query
      All tags are searched in a SINGLE Overpass request (fast, complete).

  Stage 3 — Name-regex Fallback
      If Stage 2 returns 0 results, Overpass searches by name~"keyword" regex,
      catching places tagged unusually but correctly named (e.g. "Ananda Spa"
      tagged as tourism=attraction).

  Finally — Distance & Detour Calculation
      Results are filtered to those within 10 km of the route polyline,
      sorted by perpendicular distance, and the top 50 are returned.
"""

import math
import logging
import asyncio
from typing import Optional

from yatrai.apis.gemini import extract_poi_intent
from yatrai.apis.overpass import search_overpass_multi, search_overpass_by_name

logger = logging.getLogger(__name__)


# ── Geometry helpers ──────────────────────────────────────────────────────────

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


# ── Distance & detour annotation ──────────────────────────────────────────────

def _annotate_places(raw_places: list, geometry_coords: list, max_dist_km: float = 10.0) -> list:
    """
    Filter and annotate places with perpendicular distance and detour badge.
    Removes anything farther than max_dist_km from the route polyline.
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


# ── Main pipeline ─────────────────────────────────────────────────────────────

async def run_poi_search(
    query: str,
    geometry_coords: list,
    cached_result: Optional[dict] = None,
) -> dict:
    """
    Full dynamic POI search pipeline — 3 stages:

    Stage 1 — Gemini extracts ALL possible OSM tags + name synonym keywords
    Stage 2 — Single Overpass union query for all tags simultaneously
    Stage 3 — OR-regex name search fallback using synonym keywords
              (catches 'Fashion World', 'Kapda Store', 'Ananda Spa', etc.)

    Returns: {"pois": [...], "count": int, "from_cache": bool, "intent": dict}
    """
    # ── Cache hit ─────────────────────────────────────────────────
    if cached_result is not None:
        pois = cached_result.get("pois", [])
        return {"pois": pois, "count": len(pois), "from_cache": True}

    loop = asyncio.get_running_loop()

    # ── Stage 1: Gemini intent extraction ─────────────────────────
    intent        = await loop.run_in_executor(None, extract_poi_intent, query)
    osm_tags      = intent.get("osm_tags", [])
    name_keywords = intent.get("name_keywords", [query.strip().lower()])

    logger.info(
        "[POISearch] Query='%s' → osm_tags=%s  name_keywords=%s",
        query, osm_tags, name_keywords
    )

    # ── Compute bounding box ───────────────────────────────────────
    bounds = compute_route_bounds(geometry_coords)

    raw_places = []

    # ── Stage 2: Multi-tag union Overpass query ────────────────────
    if osm_tags:
        raw_places = await loop.run_in_executor(
            None, search_overpass_multi, osm_tags, bounds
        )
        logger.info("[POISearch] Stage 2 (multi-tag): %d raw results", len(raw_places))

    # ── Stage 3: OR-regex name fallback ───────────────────────────
    # Fire when Stage 2 found nothing. We search OSM by name using ALL
    # synonym keywords as one OR-regex so 'Fashion World', 'Textile Palace',
    # 'Kapda Store', 'Ananda Spa' are all matched in a single query.
    if not raw_places and name_keywords:
        logger.info(
            "[POISearch] Stage 3 (name OR-regex) keywords=%s",
            name_keywords
        )
        raw_places = await loop.run_in_executor(
            None, search_overpass_by_name, name_keywords, bounds
        )
        logger.info("[POISearch] Stage 3: %d raw results", len(raw_places))

    # ── Distance annotation & filtering ───────────────────────────
    results = _annotate_places(raw_places, geometry_coords)

    return {
        "pois":       results,
        "count":      len(results),
        "from_cache": False,
        "intent":     intent,
    }
