"""
YatrAI — POI Search Pipeline
------------------------------
Implements the full route-based POI search pipeline:

    Route GeoJSON  →  Sample points every 5 km
                   →  Parallel Overpass queries  (asyncio.gather)
                   →  Deduplicate by OSM ID
                   →  Compute distance from origin (haversine)
                   →  Embed all POI texts with MiniLM  (pre-computed)
                   →  FAISS flat-L2 search  (query-time, ~5 ms)
                   →  Return top-K ranked POIs

Stage-by-stage commentary is included for the college viva explanation.
"""

import re
import math
import logging
import asyncio
from typing import Optional

import numpy as np

from yatrai.config import (
    POI_SAMPLE_INTERVAL_KM, POI_DEFAULT_RADIUS_KM,
    POI_MAX_RESULTS, EMBEDDING_MODEL_NAME,
)
from yatrai.apis.overpass import fetch_pois_overpass, map_query_to_tags, QUERY_INTENT_MAP

logger = logging.getLogger(__name__)


# ── Fix 1: Smart Query Parser ────────────────────────────────────────────────
# Splits natural-language POI queries into (category, location_anchor).
# Examples:
#   "ATM near Sector 27"      → category="ATM",        anchor="Sector 27"
#   "restaurants in Sector 15" → category="restaurants", anchor="Sector 15"
#   "petrol pump"              → category="petrol pump", anchor=None
#   "Sector 27, Gandhinagar"   → category=None,          anchor="Sector 27, Gandhinagar"

# Prepositions that separate a category from a location anchor
_LOCATION_PREPS = re.compile(
    r'\b(?:near|in|at|around|close\s+to|next\s+to|by|nearby|from)\b',
    re.IGNORECASE,
)

# Pattern for sector-style locations (common in Indian cities)
_SECTOR_LOCATION_RE = re.compile(
    r'\bsector\s+\d{1,2}\b',
    re.IGNORECASE,
)


def parse_poi_query(query: str) -> dict:
    """
    Parses a user's POI search query to extract two parts:
      - category:  What they want (ATM, restaurant, etc.)
      - anchor:    Where they want it (Sector 27, a specific location)

    Returns:
        {
            "category":      str or None,
            "anchor":        str or None,
            "original_query": str,
            "has_category":  bool,
        }
    """
    q = query.strip()
    if not q:
        return {"category": None, "anchor": None, "original_query": q, "has_category": False}

    q_lower = q.lower()

    # ── Strategy 1: Split on prepositions ──
    # "ATM near Sector 27" → ["ATM", "Sector 27"]
    prep_match = _LOCATION_PREPS.search(q)
    if prep_match:
        before = q[:prep_match.start()].strip()
        after  = q[prep_match.end():].strip()

        # Determine which side is the category and which is the anchor
        # Check if "before" matches a known POI category
        before_is_category = _is_poi_category(before)
        after_is_category  = _is_poi_category(after)

        if before_is_category and not after_is_category:
            return {
                "category": before,
                "anchor": after if after else None,
                "original_query": q,
                "has_category": True,
            }
        elif after_is_category and not before_is_category:
            # Rare but handles "near sector 27, ATM"
            return {
                "category": after,
                "anchor": before if before else None,
                "original_query": q,
                "has_category": True,
            }
        elif before_is_category and after_is_category:
            # Both look like categories — use the longer one as category
            return {
                "category": before,
                "anchor": after,
                "original_query": q,
                "has_category": True,
            }
        else:
            # Neither is a clear category — "something near somewhere"
            # Treat "before" as category attempt, "after" as anchor
            return {
                "category": before if before else None,
                "anchor": after if after else None,
                "original_query": q,
                "has_category": bool(before),
            }

    # ── Strategy 2: Check if entire query is just a location (no category) ──
    # e.g., "Sector 27, Gandhinagar"
    sector_match = _SECTOR_LOCATION_RE.search(q)
    if sector_match:
        # Check if there's a POI category keyword alongside the sector
        # Remove the sector part and see if anything meaningful remains
        without_sector = _SECTOR_LOCATION_RE.sub('', q)
        # Also remove city names and commas
        cleaned = re.sub(
            r'\b(gandhinagar|ahmedabad|gujarat|india)\b', '', without_sector,
            flags=re.IGNORECASE
        )
        cleaned = re.sub(r'[,\s]+', ' ', cleaned).strip()

        if cleaned and _is_poi_category(cleaned):
            return {
                "category": cleaned,
                "anchor": q_lower,  # Full query as anchor (includes sector)
                "original_query": q,
                "has_category": True,
            }
        else:
            # Pure location, no category
            return {
                "category": None,
                "anchor": q,
                "original_query": q,
                "has_category": False,
            }

    # ── Strategy 3: Entire query is a category (no location anchor) ──
    return {
        "category": q,
        "anchor": None,
        "original_query": q,
        "has_category": True,
    }


def _is_poi_category(text: str) -> bool:
    """
    Checks if text looks like a POI category (matches any keyword in QUERY_INTENT_MAP).
    Uses the same longest-match-first strategy as map_query_to_tags.
    """
    t = text.lower().strip()
    if not t:
        return False

    sorted_keys = sorted(QUERY_INTENT_MAP.keys(), key=len, reverse=True)
    for key in sorted_keys:
        if key in t:
            return True

    return False


# ── Lazy-loaded embedding model singleton ─────────────────────────────────────
# We load the MiniLM model ONCE at first use so startup is fast.
# Subsequent calls reuse the already-loaded model (no re-loading cost).
_embedding_model = None


def get_embedding_model():
    """
    Returns the sentence-transformer model (lazy singleton).
    Model: all-MiniLM-L6-v2 (~90 MB, CPU-only).
    Loads in ~2–3 seconds on first call; returns instantly thereafter.
    """
    global _embedding_model
    if _embedding_model is None:
        logger.info("[POISearch] Loading embedding model '%s'...", EMBEDDING_MODEL_NAME)
        from sentence_transformers import SentenceTransformer
        _embedding_model = SentenceTransformer(EMBEDDING_MODEL_NAME)
        logger.info("[POISearch] Embedding model loaded.")
    return _embedding_model


# ── Stage 1: Route Sampling ───────────────────────────────────────────────────

def sample_route_points(
    geometry_coords: list,
    interval_km: float = POI_SAMPLE_INTERVAL_KM,
) -> list[tuple[float, float]]:
    """
    Samples (lat, lon) points along a GeoJSON polyline at a fixed
    interval (default 5 km) using cumulative haversine distance.

    Why: Overpass API only searches within a radius of a single point.
    By sampling every 5 km, we ensure we cover long routes without
    missing POIs in the middle sections.

    Args:
        geometry_coords: List of [lon, lat] pairs from GeoJSON geometry
        interval_km:     Spacing between sampled points in kilometres

    Returns:
        List of (lat, lon) tuples; always includes the first point.
    """
    if not geometry_coords:
        return []

    sampled = []
    accumulated_km = 0.0
    prev_lat, prev_lon = geometry_coords[0][1], geometry_coords[0][0]

    # Always include the start point
    sampled.append((prev_lat, prev_lon))

    for coord in geometry_coords[1:]:
        curr_lat, curr_lon = coord[1], coord[0]
        seg_km = haversine_km(prev_lat, prev_lon, curr_lat, curr_lon)
        accumulated_km += seg_km

        if accumulated_km >= interval_km:
            sampled.append((curr_lat, curr_lon))
            accumulated_km = 0.0  # reset after each sample

        prev_lat, prev_lon = curr_lat, curr_lon

    logger.info("[POISearch] Sampled %d points from %d coords (interval=%.1f km)",
                len(sampled), len(geometry_coords), interval_km)
    return sampled


# ── Stage 2: Parallel Overpass Calls ─────────────────────────────────────────

async def fetch_all_pois(
    sample_points: list[tuple[float, float]],
    tags: list[str],
    radius_km: float = POI_DEFAULT_RADIUS_KM,
) -> list[dict]:
    """
    Fires Overpass API requests for ALL sampled points CONCURRENTLY.

    Why asyncio.gather + run_in_executor:
        - Overpass calls are network-bound (~1–3 s each)
        - Sequential calls would take N × 3 s = 30+ s for a 10-point route
        - Parallel calls complete in max(individual_latencies) ≈ 3–5 s total
        - run_in_executor offloads the blocking requests.post to a thread pool

    Args:
        sample_points: (lat, lon) tuples from sample_route_points()
        tags:          OSM tag filters from map_query_to_tags()
        radius_km:     Search radius in km around each point

    Returns:
        Flat list of all POI dicts from all points (duplicates present)
    """
    radius_m = int(radius_km * 1000)
    loop = asyncio.get_running_loop()

    # Create one coroutine per sampled point
    tasks = [
        loop.run_in_executor(
            None,                     # default thread pool
            fetch_pois_overpass,
            lat, lon, radius_m, tags
        )
        for lat, lon in sample_points
    ]

    # Fire all concurrently and wait for all to finish
    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_pois = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger.warning("[POISearch] Point %d raised: %s", i, result)
        elif result:
            all_pois.extend(result)

    logger.info("[POISearch] Overpass returned %d raw POIs from %d points",
                len(all_pois), len(sample_points))
    return all_pois


# ── Stage 3: Deduplication ────────────────────────────────────────────────────

def deduplicate_pois(pois: list[dict]) -> list[dict]:
    """
    Removes duplicate POIs that appear in multiple search radii.

    Primary key:  OSM node ID (guaranteed globally unique)
    Fallback:     Rounded coordinates (4 dp ≈ 11 m precision) for POIs
                  that may appear under different IDs in different queries.

    Args:
        pois: Flat list with possible duplicates

    Returns:
        Deduplicated list in original order (first occurrence kept)
    """
    seen_ids:    set = set()
    seen_coords: set = set()
    unique: list[dict] = []

    for poi in pois:
        osm_id = poi.get("osm_id")
        coord_key = (round(poi["lat"], 4), round(poi["lon"], 4))

        if osm_id and osm_id in seen_ids:
            continue
        if coord_key in seen_coords:
            continue

        if osm_id:
            seen_ids.add(osm_id)
        seen_coords.add(coord_key)
        unique.append(poi)

    logger.info("[POISearch] Deduplication: %d → %d unique POIs", len(pois), len(unique))
    return unique


# ── Stage 4: Distance Calculation ────────────────────────────────────────────

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Computes the great-circle distance between two lat/lon points (km).
    Uses the Haversine formula: accurate to within ~0.3% for distances < 1000 km.
    """
    R = 6371.0  # Earth radius in km
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1))
         * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def point_to_line_distance(poi_lat: float, poi_lon: float, polyline_coords: list) -> tuple[float, float, float]:
    """
    Computes the shortest perpendicular distance (in km) from a point
    to a polyline, and the along-route distance (in km) to the projection point.
    Returns: (perp_dist_km, along_route_km, total_route_km)
    """
    if not polyline_coords or len(polyline_coords) < 2:
        return 999.0, 0.0, 0.0
        
    R = 6371.0
    lat0 = math.radians(poi_lat)
    lon0 = math.radians(poi_lon)
    
    min_dist_sq = float('inf')
    best_along_route = 0.0
    
    # Pre-compute cos(lat) for projection to cartesian
    cos_lat0 = math.cos(lat0)
    
    px = lon0 * cos_lat0
    py = lat0
    
    cumulative_dist = 0.0
    total_dist = 0.0
    
    for i in range(len(polyline_coords) - 1):
        lon1_deg, lat1_deg = polyline_coords[i]
        lon2_deg, lat2_deg = polyline_coords[i+1]
        
        x1 = math.radians(lon1_deg) * cos_lat0
        y1 = math.radians(lat1_deg)
        x2 = math.radians(lon2_deg) * cos_lat0
        y2 = math.radians(lat2_deg)
        
        dx = x2 - x1
        dy = y2 - y1
        l2 = dx*dx + dy*dy
        seg_len_km = math.sqrt(l2) * R
        
        if l2 == 0:
            t = 0.0
        else:
            t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / l2))
            
        proj_x = x1 + t * dx
        proj_y = y1 + t * dy
        
        dist_sq = (px - proj_x)**2 + (py - proj_y)**2
        
        if dist_sq < min_dist_sq:
            min_dist_sq = dist_sq
            best_along_route = cumulative_dist + (t * seg_len_km)
            
        cumulative_dist += seg_len_km
        
    total_dist = cumulative_dist
            
    return math.sqrt(min_dist_sq) * R, best_along_route, total_dist



def attach_distances(pois: list[dict], origin_lat: float, origin_lon: float) -> list[dict]:
    """
    Adds a `distance_km` field to each POI (haversine from origin).
    Used for the "X km from start" label in the UI.
    """
    for poi in pois:
        poi["distance_km"] = round(
            haversine_km(origin_lat, origin_lon, poi["lat"], poi["lon"]), 2
        )
    return pois


# ── Stage 5: Embedding ────────────────────────────────────────────────────────

def build_poi_text(poi: dict) -> str:
    """
    Constructs a short text description of a POI for embedding.
    Combines name + type + relevant address tags into one sentence.

    Example: "HP Petrol Pump Petrol Pump NH-8 Gurugram Haryana"
    """
    tags = poi.get("tags", {})
    parts = [
        poi.get("name", ""),
        poi.get("type", ""),
        tags.get("brand", ""),
        tags.get("operator", ""),
        tags.get("addr:street", ""),
        tags.get("addr:city", ""),
        tags.get("addr:state", ""),
    ]
    return " ".join(p for p in parts if p).strip()


def embed_pois(pois: list[dict]) -> Optional[np.ndarray]:
    """
    Embeds all POI texts with the MiniLM model.

    Called ONCE per cache miss (pre-computation) — not at query time.
    Returns a numpy array of shape (N, 384) — 384 is MiniLM's output dim.

    Returns None if embedding fails (e.g., no POIs to embed).
    """
    if not pois:
        return None

    model = get_embedding_model()
    texts = [build_poi_text(p) for p in pois]

    # show_progress_bar=False to keep server logs clean
    embeddings = model.encode(texts, show_progress_bar=False, convert_to_numpy=True)
    logger.info("[POISearch] Embedded %d POIs → shape %s", len(pois), embeddings.shape)
    return embeddings


# ── Stage 6: FAISS Vector Search ─────────────────────────────────────────────

def rank_pois(
    query: str,
    pois: list[dict],
    embeddings: np.ndarray,
    polyline_coords: list,
    total_route_km: float,
    top_k: int = POI_MAX_RESULTS,
) -> list[dict]:
    """
    Ranks POIs by combining FAISS semantic similarity, distance from route, 
    and OSM tag richness. Applies zone balancing and deduplication.
    """
    import faiss

    if embeddings is None or len(pois) == 0:
        return []

    # Step 1: Embed the user query
    model = get_embedding_model()
    query_vec = model.encode([query], show_progress_bar=False, convert_to_numpy=True)

    # Step 2: Build FAISS index and search ALL points to get similarities
    dim = embeddings.shape[1]
    index = faiss.IndexFlatL2(dim)
    index.add(embeddings.astype("float32"))
    
    distances, indices = index.search(query_vec.astype("float32"), len(pois))
    
    # Find max tags for normalization
    max_tags = max((len(p.get("tags", {})) for p in pois), default=1)
    
    scored_pois = []
    
    for i, idx in enumerate(indices[0]):
        if idx < 0 or idx >= len(pois): 
            continue
        poi = pois[idx]
        l2_dist = distances[0][i]
        
        # Convert L2 distance to a 0-1 similarity score
        faiss_sim = 1.0 / (1.0 + l2_dist)
        
        # Calculate perpendicular and along-route distance
        perp_km, along_km, _ = point_to_line_distance(poi["lat"], poi["lon"], polyline_coords)
        
        # Fix B2: Filter out POIs more than 5km from route
        if perp_km > 5.0:
            continue
            
        # Distance score mapping
        if perp_km <= 1.0:
            dist_score = 1.0
        elif perp_km <= 3.0:
            dist_score = 0.7
        else:
            dist_score = 0.4
            
        # Normalized tag count
        tag_count = len(poi.get("tags", {}))
        tag_score = tag_count / max_tags
        
        # Fix B1: Final Score computation
        final_score = (faiss_sim * 0.50) + (dist_score * 0.35) + (tag_score * 0.15)
        
        poi["_final_score"] = final_score
        poi["_along_km"] = along_km
        poi["detour_km"] = round(perp_km, 2)
        scored_pois.append(poi)
        
    # Sort by final score descending
    scored_pois.sort(key=lambda x: x["_final_score"], reverse=True)
    
    # Fix B3: Deduplicate (same category + within 200m -> keep higher score)
    deduped = []
    for p in scored_pois:
        is_dup = False
        for d in deduped:
            if p["type"] == d["type"]:
                dist_between = haversine_km(p["lat"], p["lon"], d["lat"], d["lon"])
                if dist_between <= 0.2:
                    is_dup = True
                    break
        if not is_dup:
            deduped.append(p)
            
    # Fix B4: Zone balancing (split route into 3 equal zones, max 4 results per zone)
    zone_length = total_route_km / 3.0 if total_route_km > 0 else 1.0
    zone_counts = {0: 0, 1: 0, 2: 0}
    
    final_results = []
    for p in deduped:
        if len(final_results) >= top_k:
            break
            
        zone_idx = min(2, int(p["_along_km"] / zone_length))
        if zone_counts[zone_idx] < 4:
            final_results.append(p)
            zone_counts[zone_idx] += 1
            
    logger.info("[POISearch] Ranked, deduplicated, and zone-balanced %d POIs down to %d for query '%s'", len(pois), len(final_results), query)
    
    # Clean up temporary keys
    for p in final_results:
        p.pop("_final_score", None)
        p.pop("_along_km", None)
        
    return final_results


# ── Top-level pipeline function ───────────────────────────────────────────────

# Fix 3: Radii to try in order (progressive widening)
async def run_poi_search(
    geometry_coords: list,
    origin_lat: float,
    origin_lon: float,
    query: str,
    radius_km: float = POI_DEFAULT_RADIUS_KM,
    cache_key: Optional[str] = None,
    cached_result: Optional[dict] = None,
    anchor_point: Optional[tuple[float, float]] = None,
) -> dict:
    """
    Orchestrates the full POI search pipeline.
    Called from the FastAPI /poi-search endpoint.

    If cached_result is provided (cache hit), skips Overpass and Embedding
    and goes straight to FAISS ranking.

    Returns:
        {
            "pois":       [...ranked POI dicts...],
            "embeddings": ndarray,  # for caching
            "count":      int,
            "from_cache": bool,
        }
    """
    # Calculate total route length for zone balancing
    total_route_km = sum(
        haversine_km(geometry_coords[i][1], geometry_coords[i][0], geometry_coords[i+1][1], geometry_coords[i+1][0])
        for i in range(len(geometry_coords)-1)
    ) if len(geometry_coords) > 1 else 1.0

    # Stage 1: Map query text to OSM tags
    tags, query_matched = map_query_to_tags(query)
    logger.info("[POISearch] Query '%s' → tags: %s", query, tags)

    # ── Cache HIT path (fast) ──────────────────────────────────────────
    if cached_result is not None:
        pois       = cached_result["pois"]
        embeddings = cached_result["embeddings"]

        # Filter the cache by requested tags to prevent master-cache bleed (e.g. returning hospitals for pharmacy)
        if query_matched:
            filtered_pois = []
            filtered_indices = []
            for i, p in enumerate(pois):
                ptags = p.get("tags", {})
                match = False
                for t in tags:
                    if "=" in t:
                        k, v = t.split("=", 1)
                        if ptags.get(k) == v:
                            match = True
                            break
                    elif t in ptags:
                        match = True
                        break
                if match:
                    filtered_pois.append(p)
                    filtered_indices.append(i)
                    
            if not filtered_pois:
                logger.info("[POISearch] Cache has 0 matches for requested tags %s. Falling back to Overpass.", tags)
                cached_result = None
            else:
                pois = filtered_pois
                if embeddings is not None:
                    import numpy as np
                    embeddings = embeddings[filtered_indices]

    if cached_result is not None:
        attach_distances(pois, origin_lat, origin_lon)
        ranked = rank_pois(query, pois, embeddings, geometry_coords, total_route_km)
        return {"pois": ranked, "embeddings": embeddings, "count": len(ranked), "from_cache": True}

    # ── Cache MISS path (slower, first search on this route) ──────────

    # Stage 2: Fetch POIs (Bounding Box for Route, Circular for Anchor)
    from yatrai.apis.overpass import fetch_pois_overpass_bbox
    import asyncio
    
    loop = asyncio.get_running_loop()

    if anchor_point is not None:
        logger.info("[POISearch] Using anchor point (%.4f, %.4f) with 10km radius", anchor_point[0], anchor_point[1])
        # Fix A2: Always query at 10km for anchor searches
        raw_pois = await loop.run_in_executor(
            None, fetch_pois_overpass, anchor_point[0], anchor_point[1], 10000, tags
        )
    else:
        # Fix A1: Bounding box for the route
        if not geometry_coords:
            return {"pois": [], "embeddings": None, "count": 0, "from_cache": False}
            
        lons = [c[0] for c in geometry_coords]
        lats = [c[1] for c in geometry_coords]
        
        # Buffer by ~0.09 degrees (approx 10km padding)
        min_lon = min(lons) - 0.09
        max_lon = max(lons) + 0.09
        min_lat = min(lats) - 0.09
        max_lat = max(lats) + 0.09
        
        logger.info("[POISearch] Using bounding box fetch for route")
        raw_pois = await loop.run_in_executor(
            None, fetch_pois_overpass_bbox, min_lat, min_lon, max_lat, max_lon, tags
        )

    if not raw_pois:
        logger.warning("[POISearch] Zero POIs returned for query '%s'", query)
        return {"pois": [], "embeddings": None, "count": 0, "from_cache": False}

    # Stage 3: Deduplicate by OSM ID / rounded coordinates
    unique_pois = deduplicate_pois(raw_pois)

    # Stage 4: Compute distance from origin for each POI (for display)
    attach_distances(unique_pois, origin_lat, origin_lon)

    # Stage 5: Pre-compute embeddings (stored in cache)
    embeddings = embed_pois(unique_pois)
    if embeddings is None:
        return {"pois": unique_pois, "embeddings": None, "count": len(unique_pois), "from_cache": False}

    # Stage 6: Rank POIs (FAISS + Math)
    ranked = rank_pois(query, unique_pois, embeddings, geometry_coords, total_route_km)

    return {
        "pois":       ranked,
        "embeddings": embeddings,   
        "all_pois":   unique_pois,  # full list stored in cache for future queries
        "count":      len(ranked),
        "from_cache": False,
    }


def pre_warm_poi_cache_sync(geometry_coords: list):
    """
    Synchronous wrapper to run the async pre-warm task.
    Suitable for calling via FastAPI BackgroundTasks.
    """
    import asyncio
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_pre_warm_poi_cache(geometry_coords))
    except RuntimeError:
        asyncio.run(_pre_warm_poi_cache(geometry_coords))


async def _pre_warm_poi_cache(geometry_coords: list):
    """
    Background task that fetches ALL major OSM categories at once,
    embeds them, and stores them in Firestore under a "MASTER" route key.
    """
    from yatrai.poi_cache import make_cache_key, get_cached_pois, set_cached_pois
    from yatrai.apis.overpass import fetch_pois_overpass_bbox
    import asyncio

    if not geometry_coords:
        return

    # The requested master categories
    master_tags = [
        "amenity=fuel", "amenity=restaurant", "tourism=hotel", 
        "amenity=atm", "amenity=hospital", "amenity=pharmacy", "amenity=parking"
    ]
    
    # Check if already cached
    master_key = make_cache_key(geometry_coords, ["MASTER_ALL"])
    if get_cached_pois(master_key) is not None:
        logger.info("[POICache] Master cache already exists for this route.")
        return

    logger.info("[POICache] Pre-warming master cache...")

    # Fetch via bounding box
    lons = [c[0] for c in geometry_coords]
    lats = [c[1] for c in geometry_coords]
    min_lon, max_lon = min(lons) - 0.09, max(lons) + 0.09
    min_lat, max_lat = min(lats) - 0.09, max(lats) + 0.09
    
    loop = asyncio.get_running_loop()
    raw_pois = await loop.run_in_executor(
        None, fetch_pois_overpass_bbox, min_lat, min_lon, max_lat, max_lon, master_tags
    )

    if not raw_pois:
        logger.info("[POICache] Pre-warm found no POIs.")
        return

    unique_pois = deduplicate_pois(raw_pois)
    embeddings = embed_pois(unique_pois)
    
    if embeddings is not None:
        # Cache to Firestore in background thread
        await loop.run_in_executor(None, set_cached_pois, master_key, unique_pois, embeddings)
        logger.info("[POICache] Master cache pre-warmed with %d POIs.", len(unique_pois))


