import requests
import logging

logger = logging.getLogger(__name__)

def search_overpass_dynamic(tag: str, bounds: dict) -> list[dict]:
    """
    Dynamically fetches POIs from Overpass API using a bounding box and a specific tag.
    Returns a list of clean dictionaries.
    
    tag: e.g. "amenity=cafe"
    bounds: {"low": {"lat": min_lat, "lon": min_lon}, "high": {"lat": max_lat, "lon": max_lon}}
    """
    if "=" not in tag:
        tag = f"amenity={tag}"
        
    key, val = tag.split("=", 1)
    
    # Add a small buffer to the bounding box (approx 5km)
    buffer = 0.05
    min_lat = bounds["low"]["lat"] - buffer
    min_lon = bounds["low"]["lon"] - buffer
    max_lat = bounds["high"]["lat"] + buffer
    max_lon = bounds["high"]["lon"] + buffer
    
    query = f"""
    [out:json][timeout:15];
    (
      node["{key}"="{val}"]({min_lat},{min_lon},{max_lat},{max_lon});
      way["{key}"="{val}"]({min_lat},{min_lon},{max_lat},{max_lon});
    );
    out center;
    """
    
    endpoints = [
        "https://overpass-api.de/api/interpreter",
        "https://lz4.overpass-api.de/api/interpreter",
        "https://z.overpass-api.de/api/interpreter"
    ]
    
    headers = {"User-Agent": "YatrAI/1.0 (Student Project)"}
    
    for url in endpoints:
        try:
            response = requests.post(url, data={'data': query}, headers=headers, timeout=25.0)
            
            if response.status_code == 200:
                data = response.json()
                elements = data.get("elements", [])
                
                results = []
                for el in elements:
                    lat = el.get("lat") or el.get("center", {}).get("lat")
                    lon = el.get("lon") or el.get("center", {}).get("lon")
                    tags = el.get("tags", {})
                    name = tags.get("name", tags.get("brand", "Unnamed Location"))
                    
                    if lat is None or lon is None:
                        continue
                        
                    results.append({
                        "name": name,
                        "lat": lat,
                        "lon": lon,
                        "type": val,
                        "tags": tags
                    })
                    
                logger.info(f"[Overpass] Found {len(results)} raw results for {tag} using {url}.")
                return results
            else:
                logger.warning(f"[Overpass] {url} failed with status {response.status_code}")
                
        except Exception as e:
            logger.warning(f"[Overpass] {url} exception: {e}")
            continue

    logger.error(f"[Overpass] All endpoints failed for {tag}.")
    return []
