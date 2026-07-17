"""
YatrAI — POI Search Pipeline (Gemini + Google Places API v1)
------------------------------
Implements the zero-memory, highly accurate POI search pipeline:
    1. Gemini Intent Extraction
    2. Route Bounding Box Calculation
    3. Google Places API v1 (searchText)
    4. Distance & Detour Calculation
"""

import math
import logging
import asyncio
from typing import Optional

from yatrai.apis.gemini import extract_poi_intent
from yatrai.apis.overpass import search_overpass_dynamic

logger = logging.getLogger(__name__)

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance between two coords in km."""
    R = 6371.0
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def point_to_line_distance(pt_lat: float, pt_lon: float, line_coords: list) -> tuple[float, float, int]:
    """
    Returns (perpendicular_dist_km, along_route_dist_km, nearest_segment_index)
    """
    min_dist = float('inf')
    total_along = 0.0
    best_along = 0.0
    best_idx = 0

    for i in range(len(line_coords) - 1):
        lat1, lon1 = line_coords[i][1], line_coords[i][0]
        lat2, lon2 = line_coords[i+1][1], line_coords[i+1][0]
        
        seg_len = haversine_km(lat1, lon1, lat2, lon2)
        dist_to_start = haversine_km(pt_lat, pt_lon, lat1, lon1)
        dist_to_end = haversine_km(pt_lat, pt_lon, lat2, lon2)

        if seg_len == 0:
            perp_dist = dist_to_start
            along_seg = 0
        else:
            t = max(0, min(1, (dist_to_start**2 - dist_to_end**2 + seg_len**2) / (2 * seg_len**2)))
            proj_lat = lat1 + t * (lat2 - lat1)
            proj_lon = lon1 + t * (lon2 - lon1)
            perp_dist = haversine_km(pt_lat, pt_lon, proj_lat, proj_lon)
            along_seg = t * seg_len

        if perp_dist < min_dist:
            min_dist = perp_dist
            best_along = total_along + along_seg
            best_idx = i

        total_along += seg_len

    return min_dist, best_along, best_idx

def compute_route_bounds(geometry_coords: list) -> dict:
    """Computes the bounding box of the route line string."""
    lats = [c[1] for c in geometry_coords]
    lons = [c[0] for c in geometry_coords]
    return {
        "low": {"lat": min(lats), "lon": min(lons)},
        "high": {"lat": max(lats), "lon": max(lons)}
    }

async def run_poi_search(
    query: str,
    geometry_coords: list,
    cached_result: Optional[dict] = None
) -> dict:
    """
    Orchestrates the Gemini + Overpass API pipeline.
    """
    if cached_result is not None:
        return {
            "pois": cached_result["pois"],
            "count": len(cached_result["pois"]),
            "from_cache": True
        }

    # 1. Gemini Intent Extraction
    loop = asyncio.get_running_loop()
    intent = await loop.run_in_executor(None, extract_poi_intent, query)
    logger.info(f"[POISearch] Extracted Intent: {intent}")
    
    category_raw = intent.get("category", query.strip()).lower()
    
    # Robust mapping for common UI buttons -> OSM tags
    COMMON_TAGS = {
        "petrol pump": "amenity=fuel",
        "fuel": "amenity=fuel",
        "gas station": "amenity=fuel",
        "hospital": "amenity=hospital",
        "atm": "amenity=atm",
        "cafe": "amenity=cafe",
        "restaurant": "amenity=restaurant",
        "hotel": "tourism=hotel",
        "parking": "amenity=parking",
        "pharmacy": "amenity=pharmacy",
        "clinic": "amenity=clinic"
    }
    
    category_tag = COMMON_TAGS.get(category_raw, category_raw)
    
    # Fallback to amenity if Gemini just returned a raw word
    if "=" not in category_tag:
        category_tag = f"amenity={category_tag}"

    # 2. Compute Route Bounding Box
    bounds = compute_route_bounds(geometry_coords)

    # 3. Overpass API
    raw_places = await loop.run_in_executor(None, search_overpass_dynamic, category_tag, bounds)

    # 4. Distance & Detour Calculation
    results = []
    for p in raw_places:
        perp_km, _, _ = point_to_line_distance(p["lat"], p["lon"], geometry_coords)
        
        # Filter out anything wildly far away from the actual line
        if perp_km > 10.0:
            continue
            
        # Determine detour badge
        if perp_km <= 1.0:
            detour_badge = "On your way"
        elif perp_km <= 3.0:
            detour_badge = "Small detour"
        else:
            detour_badge = "Detour needed"

        p["distance_km"] = round(perp_km, 2)
        p["detour_badge"] = detour_badge
        results.append(p)

    # Sort by nearest distance first
    results.sort(key=lambda x: x["distance_km"])
    top_results = results[:50]

    return {
        "pois": top_results,
        "count": len(top_results),
        "from_cache": False,
        "intent": intent
    }
