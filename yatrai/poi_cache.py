"""
YatrAI — Firestore POI Cache
------------------------------
Caches Overpass API results per route+category in Firestore so that
repeat searches on the same route are near-instant (300 ms target).

Cache key design:
    sha256( sorted(route_point_strings) + "||" + normalized_tag_string )

This key is stable across re-runs and is category-scoped, so a user
searching "petrol" and then "hotel" on the same route gets two separate
cache documents with their own TTLs.

Cache document schema (Firestore):
    {
        "key":        "<sha256 hex>",
        "pois":       [ {osm_id, name, lat, lon, type, tags, ...}, ... ],
        "embeddings": "<base64-encoded pickled numpy array>",
        "created_at": <Firestore server timestamp>,
        "expires_at": <datetime>,
    }

Fallback:
    If Firestore is unavailable (no credentials), falls back to a
    simple in-memory dict (per-process, cleared on restart).
    All public functions work identically in both modes.
"""

import hashlib
import logging
import pickle
import base64
from datetime import datetime, timedelta, timezone
from typing import Optional

from yatrai.config import POI_CACHE_TTL_HOURS, POI_CACHE_COLLECTION

logger = logging.getLogger(__name__)

# In-memory fallback cache: key -> {"pois": [...], "embeddings": bytes, "expires_at": datetime}
_memory_cache: dict = {}


# ── Cache Key ─────────────────────────────────────────────────────────────────

def make_cache_key(route_coords: list, query: str) -> str:
    """
    Creates a unique hash for a route + query combination.
    Samples the route heavily to avoid minor GPS jitter causing cache misses.
    """
    if not route_coords:
        return "empty_route"

    sampled = route_coords[::20]
    if len(sampled) < 2:
        sampled = [route_coords[0], route_coords[-1]]

    coord_str = "_".join(f"{c[0]:.2f},{c[1]:.2f}" for c in sampled)
    
    # Clean and normalize the query
    q = str(query).lower().strip()
    
    raw = f"{coord_str}_{q}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


# ── Public API ────────────────────────────────────────────────────────────────

def get_cached_pois(cache_key: str) -> Optional[dict]:
    """
    Retrieves a cached POI set by key.

    Returns:
        dict with {"pois": [...], "embeddings": ndarray} on cache hit
        None on miss or if the entry has expired
    """
    # Try Firestore first
    try:
        db = _get_db()
        if db is not None:
            return _firestore_get(db, cache_key)
    except Exception as e:
        logger.warning("[POICache] Firestore get failed: %s — checking memory cache", e)

    # Fallback: in-memory
    return _memory_get(cache_key)


def set_cached_pois(cache_key: str, pois: list[dict]) -> None:
    """
    Stores POI results in the cache.

    Args:
        cache_key:   Key from make_cache_key()
        pois:        Deduplicated list of POI dicts
    """
    expires_at = datetime.now(tz=timezone.utc) + timedelta(hours=POI_CACHE_TTL_HOURS)

    doc = {
        "key":        cache_key,
        "pois":       pois,
        "expires_at": expires_at,
        "created_at": datetime.now(tz=timezone.utc),
    }

    # Write to Firestore (best-effort)
    try:
        db = _get_db()
        if db is not None:
            _firestore_set(db, cache_key, doc)
            logger.info("[POICache] Cached %d POIs to Firestore (key=...%s)", len(pois), cache_key[-8:])
            return
    except Exception as e:
        logger.warning("[POICache] Firestore set failed: %s — writing to memory cache", e)

    # Fallback: in-memory
    _memory_set(cache_key, doc)
    logger.info("[POICache] Cached %d POIs to memory (key=...%s)", len(pois), cache_key[-8:])


# Firestore helpers 

def _get_db():
    """Lazy import of the Firestore client to avoid circular imports."""
    from yatrai.firebase_admin_init import get_firestore_client
    return get_firestore_client()


def _firestore_get(db, cache_key: str) -> Optional[dict]:
    """Reads one document from Firestore and deserialises it."""
    doc_ref = db.collection(POI_CACHE_COLLECTION).document(cache_key)
    doc = doc_ref.get()

    if not doc.exists:
        return None

    data = doc.to_dict()

    # Check TTL
    expires_str = data.get("expires_at", "")
    if expires_str:
        try:
            expires_at = datetime.fromisoformat(expires_str)
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if datetime.now(tz=timezone.utc) > expires_at:
                logger.debug("[POICache] Firestore entry expired (key=...%s)", cache_key[-8:])
                doc_ref.delete()   # clean up expired doc
                return None
        except Exception:
            pass  # if we can't parse the date, treat as valid

    # Deserialise embeddings
    embeddings = _decode_embeddings(data.get("embeddings", ""))
    if embeddings is None:
        return None  # corrupt entry

    logger.info("[POICache] Firestore HIT — %d POIs (key=...%s)", len(data["pois"]), cache_key[-8:])
    return {"pois": data["pois"], "embeddings": embeddings}


def _firestore_set(db, cache_key: str, doc: dict) -> None:
    """Writes one document to Firestore."""
    db.collection(POI_CACHE_COLLECTION).document(cache_key).set(doc)


# ── In-memory cache helpers 

def _memory_get(cache_key: str) -> Optional[dict]:
    """Reads from the in-memory fallback cache."""
    entry = _memory_cache.get(cache_key)
    if entry is None:
        return None

    # TTL check
    expires_str = entry.get("expires_at", "")
    try:
        expires_at = datetime.fromisoformat(expires_str)
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if datetime.now(tz=timezone.utc) > expires_at:
            _memory_cache.pop(cache_key, None)
            return None
    except Exception:
        pass

    embeddings = _decode_embeddings(entry.get("embeddings", ""))
    if embeddings is None:
        return None

    logger.info("[POICache] Memory HIT — %d POIs (key=...%s)", len(entry["pois"]), cache_key[-8:])
    return {"pois": entry["pois"], "embeddings": embeddings}


def _memory_set(cache_key: str, doc: dict) -> None:
    """Writes to the in-memory fallback cache and evicts old entries."""
    _memory_cache[cache_key] = doc
    # Keep memory lean: evict expired entries if cache grows large
    if len(_memory_cache) > 100:
        now = datetime.now(tz=timezone.utc)
        stale = []
        for k, v in _memory_cache.items():
            try:
                exp = datetime.fromisoformat(v.get("expires_at", ""))
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=timezone.utc)
                if now > exp:
                    stale.append(k)
            except Exception:
                pass
        for k in stale:
            _memory_cache.pop(k, None)


# ── Serialisation helpers ─────────────────────────────────────────────────────

def _decode_embeddings(blob: str):
    """
    Deserialises a base64+pickle encoded numpy array.
    Returns None on any decoding error.
    """
    if not blob:
        return None
    try:
        return pickle.loads(base64.b64decode(blob.encode("utf-8")))
    except Exception as e:
        logger.error("[POICache] Failed to decode embeddings: %s", e)
        return None
