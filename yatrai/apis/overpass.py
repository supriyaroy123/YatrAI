"""
YatrAI — Overpass API client
Supports:
  1. Multi-tag union query  — all OSM tags in a single request
  2. Name-regex fallback    — search by name~"keyword" when tags return nothing
"""
import requests
import logging
import re

logger = logging.getLogger(__name__)

_OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
    "https://z.overpass-api.de/api/interpreter",
]
_HEADERS = {"User-Agent": "YatrAI/1.0 (Student Project; route-intelligence-india)"}


def _bbox(bounds: dict, buffer: float = 0.05) -> tuple:
    """Return (min_lat, min_lon, max_lat, max_lon) with a buffer."""
    return (
        bounds["low"]["lat"]  - buffer,
        bounds["low"]["lon"]  - buffer,
        bounds["high"]["lat"] + buffer,
        bounds["high"]["lon"] + buffer,
    )


def _parse_elements(elements: list) -> list:
    """Convert raw Overpass elements into clean dicts."""
    results = []
    for el in elements:
        lat = el.get("lat") or el.get("center", {}).get("lat")
        lon = el.get("lon") or el.get("center", {}).get("lon")
        if lat is None or lon is None:
            continue
        tags = el.get("tags", {})
        name = (
            tags.get("name")
            or tags.get("brand")
            or tags.get("operator")
            or tags.get("name:en")
            or "Unnamed Location"
        )
        # Derive a human-readable type label from the most specific tag
        type_label = _derive_type_label(tags)
        results.append({
            "name":    name,
            "lat":     lat,
            "lon":     lon,
            "type":    type_label,
            "address": tags.get("addr:full", ""),
            "tags":    tags,
        })
    return results


def _derive_type_label(tags: dict) -> str:
    """
    Pick the most descriptive human-readable label from OSM tags.
    Priority: shop > leisure > tourism > healthcare > craft > amenity
    """
    for key in ("shop", "leisure", "tourism", "healthcare", "craft", "amenity", "highway", "natural"):
        val = tags.get(key)
        if val:
            return val.replace("_", " ").title()
    return "Place"


import concurrent.futures

# Persistent, thread-safe background executor
_executor = concurrent.futures.ThreadPoolExecutor(max_workers=12)


def _query_single_endpoint(url: str, query: str, timeout: float) -> list:
    """Query a single Overpass endpoint, returns elements or raises an exception."""
    resp = requests.post(
        url,
        data={"data": query},
        headers=_HEADERS,
        timeout=timeout
    )
    if resp.status_code == 200:
        return resp.json().get("elements", [])
    elif resp.status_code == 429:
        raise Exception("Rate-limited (HTTP 429)")
    else:
        raise Exception(f"HTTP status {resp.status_code}")


def _run_query(query: str) -> list:
    """Execute an Overpass QL query against all known endpoints in parallel, returning the first success."""
    timeout_seconds = 4.5
    futures = {
        _executor.submit(_query_single_endpoint, url, query, timeout_seconds): url
        for url in _OVERPASS_ENDPOINTS
    }
    
    first_success = None
    for future in concurrent.futures.as_completed(futures):
        url = futures[future]
        try:
            elements = future.result()
            if first_success is None:
                first_success = elements
                clean_query = query.strip().replace("\n", " ")
                logger.info("[Overpass] %s -> %d elements via %s (fastest success)", clean_query[:60], len(elements), url)
                break
        except Exception as e:
            logger.warning("[Overpass] Endpoint %s failed: %s", url, e)
            
    if first_success is not None:
        return first_success

    logger.error("[Overpass] All parallel endpoints failed or timed out.")
    return []


# ── Public API ────────────────────────────────────────────────────────────────

def search_overpass_multi(tags: list, bounds: dict) -> list:
    """
    Query Overpass with MULTIPLE OSM tags in a single union request.
    This is the primary search method — equivalent to Google Maps searching
    across all possible place categories at once.

    tags:   List of "key=value" strings, e.g. ["leisure=spa", "shop=beauty", "craft=massage"]
    bounds: {"low": {"lat":..,"lon":..}, "high": {"lat":..,"lon":..}}

    Returns a deduplicated list of place dicts.
    """
    if not tags:
        return []

    min_lat, min_lon, max_lat, max_lon = _bbox(bounds)
    bb = f"{min_lat},{min_lon},{max_lat},{max_lon}"

    # Build union of all tag queries — nodes + ways + relations
    union_parts = []
    for tag in tags:
        tag = tag.strip()
        if "=" not in tag:
            continue
        key, val = tag.split("=", 1)
        # Escape any special chars in val for Overpass QL
        val_esc = re.sub(r'["\n]', '', val)
        union_parts.append(f'  node["{key}"="{val_esc}"]({bb});')
        union_parts.append(f'  way["{key}"="{val_esc}"]({bb});')
        union_parts.append(f'  relation["{key}"="{val_esc}"]({bb});')

    if not union_parts:
        return []

    query = f"""
[out:json][timeout:25];
(
{chr(10).join(union_parts)}
);
out center tags;
"""
    elements = _run_query(query)
    results = _parse_elements(elements)

    # Deduplicate by (name, lat-rounded, lon-rounded) to avoid duplicates
    # from the same place tagged as both node + way
    seen = set()
    deduped = []
    for r in results:
        key = (r["name"].lower(), round(r["lat"], 4), round(r["lon"], 4))
        if key not in seen:
            seen.add(key)
            deduped.append(r)

    logger.info("[Overpass] Multi-tag search: %d unique results for tags=%s", len(deduped), tags)
    return deduped


def search_overpass_by_name(keywords, bounds: dict) -> list:
    """
    Fallback: search OpenStreetMap by name using a case-insensitive OR-regex.
    Catches places that are correctly named but use unusual/missing tags.

    keywords: a single string OR a list of strings.
              e.g. ["cloth","textile","garment","fabric","fashion","kapda"]
              All are joined into one OR-regex: 'cloth|textile|garment|...'
    """
    # Normalise: accept both str and list
    if isinstance(keywords, str):
        keywords = [keywords]
    keywords = [k.strip() for k in keywords if isinstance(k, str) and k.strip()]
    if not keywords:
        return []

    min_lat, min_lon, max_lat, max_lon = _bbox(bounds)
    bb = f"{min_lat},{min_lon},{max_lat},{max_lon}"

    # Build safe OR-regex: escape special regex chars, join with |
    def _safe(w):
        return re.sub(r"[^\w\s]", "", w).strip()

    safe_words = [_safe(k) for k in keywords if _safe(k)]
    if not safe_words:
        return []

    regex = "|".join(safe_words)   # e.g. "cloth|textile|garment|fabric|fashion"

    query = f"""
[out:json][timeout:20];
(
  node["name"~"{regex}",i]({bb});
  way["name"~"{regex}",i]({bb});
  relation["name"~"{regex}",i]({bb});
);
out center tags;
"""
    elements = _run_query(query)
    results = _parse_elements(elements)
    logger.info("[Overpass] Name-OR-regex '%s': %d results", regex, len(results))
    return results


def search_overpass_dynamic(tag: str, bounds: dict) -> list:
    """
    Legacy single-tag interface — kept for backward compatibility.
    Delegates to search_overpass_multi with a single tag.
    """
    if "=" not in tag:
        tag = f"amenity={tag}"
    return search_overpass_multi([tag], bounds)
