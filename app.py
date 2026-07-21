"""
YatrAI — FastAPI Backend
Serves predictions and static frontend.
"""
import sys
import os
import json
import sqlite3
from datetime import datetime
from pathlib import Path

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(__file__))

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional

from yatrai.config import (
    FRONTEND_DIR, DB_PATH, CONGESTION_MODEL_PATH, ACCIDENT_MODEL_PATH,
    CONGESTION_ALL_FEATURES, CONGESTION_LABELS, RISK_LABELS,
    TRAINING_MEDIANS_PATH, MODEL_DIR,
)
from yatrai.apis.geocoding import geocode
from yatrai.apis.routing import get_route
from yatrai.apis.weather import get_weather
from yatrai.apis.air_quality import get_aqi
from yatrai.feature_engineering import build_congestion_features, build_accident_features
from yatrai.travel_time import estimate_travel_time
from yatrai.drift_detection import log_prediction, get_prediction_stats
from yatrai.fuel_calculator import calculate_fuel
from yatrai.sustainability import calculate_sustainability
from yatrai.apis.gemini import generate_travel_summary
# POI chatbot imports
from yatrai.poi_search import run_poi_search
from yatrai.poi_cache import make_cache_key, get_cached_pois, set_cached_pois
from yatrai.firebase_admin_init import get_firestore_client  # initialise on startup


# Simple in-memory prediction cache: key -> (result_dict, timestamp)
_predict_cache: dict = {}
_CACHE_TTL_SECONDS = 300  # 5 minutes


# App Setup 
app = FastAPI(
    title="YatrAI",
    description="End-to-end Traffic Intelligence for Indian Roads",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

#  Global Model State 
congestion_model = None
congestion_scaler = None
congestion_encoder = None
training_medians = None
accident_model = None
accident_scaler = None
accident_encoder = None
shap_explainer = None


def load_models():
    """Load trained models on startup."""
    global congestion_model, congestion_scaler, congestion_encoder
    global training_medians, accident_model, accident_scaler, accident_encoder
    global shap_explainer
    
    import joblib
    
    # Load congestion model
    if CONGESTION_MODEL_PATH.exists():
        from yatrai.congestion_model import load_congestion_model
        congestion_model, congestion_scaler, congestion_encoder, training_medians = load_congestion_model()
        print("[OK] Congestion model loaded")
        
        # Initialize SHAP explainer
        try:
            from yatrai.shap_explainer import ShapExplainer
            shap_explainer = ShapExplainer(congestion_model, CONGESTION_ALL_FEATURES)
            print("[OK] SHAP explainer initialized")
        except Exception as e:
            print(f"[!] SHAP explainer failed: {e}")
    else:
        print("[!!] Congestion model not found -- run train.py first")
    
    # Load accident model
    if ACCIDENT_MODEL_PATH.exists():
        from yatrai.accident_model import load_accident_model
        accident_model, accident_scaler, accident_encoder = load_accident_model()
        print("[OK] Accident risk model loaded")
    else:
        print("[!!] Accident model not found -- using rule-based fallback")


@app.on_event("startup")
async def startup_event():
    load_models()
    # Initialize SQLite
    _init_db()
    # Warm up Firebase Admin SDK (non-blocking — logs outcome)
    get_firestore_client()
    # Pre-warm embedding model so first POI search is fast
    # Run in background thread so it doesn't delay server startup
    import asyncio
    loop = asyncio.get_running_loop()
    loop.run_in_executor(None, _warm_embedding_model)
    print("\n" + "="*50)
    print("  YatrAI is running!")
    print("  Open http://localhost:8000 in your browser")
    print("="*50 + "\n")


def _warm_embedding_model():
    """No-op: embedding model was removed from the POI pipeline (now uses Overpass directly)."""
    print("[OK] POI pipeline ready (Overpass + Gemini, no local embedding model needed)")



def _init_db():
    """Create predictions table if not exists."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            origin TEXT,
            destination TEXT,
            vehicle_type TEXT,
            congestion_level TEXT,
            congestion_confidence REAL,
            accident_risk TEXT,
            eta_minutes REAL,
            aqi INTEGER,
            model_version TEXT,
            features_json TEXT
        )
    """)
    conn.commit()
    conn.close()


# Request/Response Models 
class PredictRequest(BaseModel):
    origin: str
    destination: str
    vehicle_type: str = "Car"
    departure_time: Optional[str] = None
    fuel_mode: str = "average"
    custom_mileage: Optional[float] = None
    # Optional pre-resolved coordinates from frontend (Google Maps)
    # When provided, backend skips Nominatim geocoding for a major speed boost
    origin_lat: Optional[float] = None
    origin_lon: Optional[float] = None
    dest_lat: Optional[float] = None
    dest_lon: Optional[float] = None




class AQIRequest(BaseModel):
    city: str


#  Endpoints 
@app.api_route("/", methods=["GET", "HEAD"])
async def serve_frontend():
    index_path = FRONTEND_DIR / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return {"message": "YatrAI API is running. Frontend not found at /frontend/"}


@app.get("/login")
async def serve_login():
    login_path = FRONTEND_DIR / "login.html"
    if login_path.exists():
        return FileResponse(str(login_path))
    raise HTTPException(status_code=404, detail="Login page not found")


@app.get("/history")
async def serve_history():
    history_path = FRONTEND_DIR / "history.html"
    if history_path.exists():
        return FileResponse(str(history_path))
    raise HTTPException(status_code=404, detail="History page not found")


@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "models": {
            "congestion": congestion_model is not None,
            "accident": accident_model is not None,
            "shap": shap_explainer is not None,
        },
    }


@app.get("/model-info")
async def model_info():
    info = {
        "version": "1.0.0",
        "congestion_model": {
            "loaded": congestion_model is not None,
            "algorithm": "LightGBM",
            "features": len(CONGESTION_ALL_FEATURES),
            "classes": CONGESTION_LABELS,
            "tuning": "Optuna (100 trials)",
        },
        "accident_model": {
            "loaded": accident_model is not None,
            "algorithm": "LightGBM" if accident_model else "Rule-based fallback",
            "classes": RISK_LABELS,
        },
        "apis": ["Nominatim", "OSRM", "Open-Meteo", "WAQI"],
    }
    return info


@app.get("/api/config")
async def get_frontend_config():
    """Serves the public API keys and Firebase config needed by the frontend."""
    return {
        "GOOGLE_MAPS_API_KEY": os.environ.get("GOOGLE_MAPS_API_KEY", ""),
        "FIREBASE_API_KEY": os.environ.get("FIREBASE_API_KEY", ""),
        "FIREBASE_AUTH_DOMAIN": os.environ.get("FIREBASE_AUTH_DOMAIN", ""),
        "FIREBASE_PROJECT_ID": os.environ.get("FIREBASE_PROJECT_ID", ""),
        "FIREBASE_STORAGE_BUCKET": os.environ.get("FIREBASE_STORAGE_BUCKET", ""),
        "FIREBASE_MESSAGING_SENDER_ID": os.environ.get("FIREBASE_MESSAGING_SENDER_ID", ""),
        "FIREBASE_APP_ID": os.environ.get("FIREBASE_APP_ID", ""),
        "FIREBASE_MEASUREMENT_ID": os.environ.get("FIREBASE_MEASUREMENT_ID", "")
    }

@app.post("/predict")
async def predict(request: PredictRequest, background_tasks: BackgroundTasks):
    """Main prediction endpoint — the heart of YatrAI."""
    now = datetime.now()
    
    # Parse departure time
    departure_hour = now.hour
    departure_minute = now.minute
    if request.departure_time:
        try:
            if ":" in request.departure_time:
                parts = request.departure_time.split(":")
                departure_hour = int(parts[0])
                # Check for AM/PM in the second part if present
                minute_str = parts[1].strip().lower()
                if "pm" in minute_str and departure_hour < 12:
                    departure_hour += 12
                elif "am" in minute_str and departure_hour == 12:
                    departure_hour = 0
                
                # Extract digits only for minute
                minute_digits = "".join(c for c in minute_str if c.isdigit())
                if minute_digits:
                    departure_minute = int(minute_digits)
        except Exception as e:
            print(f"Error parsing departure time: {e}")
            
    departure_time_str = f"{departure_hour:02d}:{departure_minute:02d}"
    errors = []
    
    import asyncio
    import time as _time
    loop = asyncio.get_running_loop()

    # In-memory cache lookup
    cache_key = (
        request.origin.strip().lower(),
        request.destination.strip().lower(),
        departure_hour,
        request.vehicle_type,
    )
    cached = _predict_cache.get(cache_key)
    if cached:
        result_dict, cached_at = cached
        if _time.monotonic() - cached_at < _CACHE_TTL_SECONDS:
            return result_dict

    # Step 1: Geocode origin and destination
    # Fast path: use pre-resolved coordinates from frontend (Google Maps Geocoder)
    # This skips the slow Nominatim API call entirely
    if (
        request.origin_lat is not None and request.origin_lon is not None
        and request.dest_lat is not None and request.dest_lon is not None
    ):
        origin_geo = {
            "lat": request.origin_lat,
            "lon": request.origin_lon,
            "display_name": request.origin,
        }
        dest_geo = {
            "lat": request.dest_lat,
            "lon": request.dest_lon,
            "display_name": request.destination,
        }
    else:
        # Fallback: geocode via Nominatim (slower, only if no coords given)
        origin_task = loop.run_in_executor(None, geocode, request.origin)
        dest_task = loop.run_in_executor(None, geocode, request.destination)
        origin_geo, dest_geo = await asyncio.gather(origin_task, dest_task)

        if not origin_geo:
            raise HTTPException(status_code=400, detail=f"Could not find location: {request.origin}")
        if not dest_geo:
            raise HTTPException(status_code=400, detail=f"Could not find location: {request.destination}")
    
    origin_coords = (origin_geo["lat"], origin_geo["lon"])
    dest_coords = (dest_geo["lat"], dest_geo["lon"])
    
    # Step 2-4: Get route, weather, and AQI concurrently in separate threads
    route_task = loop.run_in_executor(None, get_route, origin_coords, dest_coords)
    weather_task = loop.run_in_executor(None, get_weather, dest_coords[0], dest_coords[1])
    aqi_task = loop.run_in_executor(None, get_aqi, dest_coords[0], dest_coords[1], dest_geo["display_name"])
    
    route_data, weather_data, aqi_data = await asyncio.gather(route_task, weather_task, aqi_task)

    if not route_data:
        raise HTTPException(status_code=502, detail="Could not calculate route. Try different locations.")
    
    # Step 5: Congestion prediction
    congestion_result = {"level": "Moderate", "confidence": 0.5, "probabilities": {}}
    explanation = []
    
    if congestion_model is not None and training_medians is not None:
        try:
            features_df = build_congestion_features(
                route_data=route_data,
                weather_data=weather_data,
                training_medians=training_medians,
                current_hour=departure_hour,
            )
            # Scale features
            import numpy as np
            features_scaled = congestion_scaler.transform(features_df)
            
            # Predict
            proba = congestion_model.predict_proba(features_scaled)[0]
            pred_idx = int(np.argmax(proba))
            pred_label = congestion_encoder.inverse_transform([pred_idx])[0]
            
            congestion_result = {
                "level": pred_label,
                "confidence": round(float(proba[pred_idx]), 3),
                "probabilities": {
                    congestion_encoder.inverse_transform([i])[0]: round(float(p), 3)
                    for i, p in enumerate(proba)
                },
            }
            
            # SHAP explanation
            if shap_explainer:
                try:
                    explanation = shap_explainer.explain(features_df)
                except Exception as e:
                    errors.append(f"SHAP: {e}")
        except Exception as e:
            errors.append(f"Congestion model: {e}")
    else:
        errors.append("Congestion model not loaded")
    
    # Step 6: Accident risk prediction
    if accident_model is not None:
        try:
            from yatrai.accident_model import predict_accident_risk
            accident_features = build_accident_features(
                weather_data=weather_data,
                origin_coords=origin_coords,
                hour=departure_hour,
            )
            accident_result = predict_accident_risk(
                accident_features,
                congestion_level=congestion_result["level"],
                hour=departure_hour,
            )
        except Exception as e:
            errors.append(f"Accident model: {e}")
            from yatrai.accident_model import predict_accident_risk_fallback
            accident_result = predict_accident_risk_fallback(
                weather_data, congestion_result["level"], departure_hour
            )
    else:
        from yatrai.accident_model import predict_accident_risk_fallback
        accident_result = predict_accident_risk_fallback(
            weather_data, congestion_result["level"], departure_hour
        )
        
    # Night-time safety warning check for undeveloped areas
    from yatrai.travel_time import get_city_density_factor
    density_factor = get_city_density_factor(origin_geo["display_name"], dest_geo["display_name"])
    is_night_hours = departure_hour < 6 or departure_hour >= 19
    if density_factor == 1.00 and is_night_hours:
        if accident_result.get("level") == "Low":
            accident_result["level"] = "Medium"
        reasons = accident_result.setdefault("reasons", [])
        if "Less streetlights at night in undeveloped area" not in reasons:
            reasons.insert(0, "Less streetlights at night in undeveloped area")
    
    # Step 7: Travel time estimation
    travel_result = estimate_travel_time(
        base_duration_min=route_data["duration_min"],
        congestion_level=congestion_result["level"],
        vehicle_type=request.vehicle_type,
        rain_mm=weather_data.get("rain_mm", 0.0),
        visibility_km=weather_data.get("visibility_km", 10.0),
        departure_hour=departure_hour,
        departure_minute=departure_minute,
        origin_name=origin_geo["display_name"],
        destination_name=dest_geo["display_name"],
    )
    
    # Calculate predicted arrival time
    try:
        from datetime import time, timedelta
        dep_time = time(hour=departure_hour, minute=departure_minute)
        dep_datetime = datetime.combine(now.date(), dep_time)
        arr_datetime = dep_datetime + timedelta(minutes=travel_result["eta_minutes"])
        
        formatted_departure = dep_datetime.strftime("%I:%M %p")
        formatted_arrival = arr_datetime.strftime("%I:%M %p")
    except Exception:
        formatted_departure = f"{departure_hour:02d}:{departure_minute:02d}"
        formatted_arrival = "Unknown"
        
    travel_result["departure_time"] = formatted_departure
    travel_result["arrival_time"] = formatted_arrival

    # Step 7.2: Fuel cost estimation
    fuel_result = calculate_fuel(
        distance_km=route_data["distance_km"],
        vehicle_type=request.vehicle_type,
        congestion_level=congestion_result["level"],
        rain_mm=weather_data.get("rain_mm", 0.0),
        visibility_km=weather_data.get("visibility_km", 10.0),
        fuel_mode=request.fuel_mode,
        custom_mileage=request.custom_mileage,
    )

    # Step 7.3: Sustainability calculation
    sustainability_result = calculate_sustainability(
        fuel_needed_liters=fuel_result["fuel_needed_liters"],
        distance_km=route_data["distance_km"],
        vehicle_type=request.vehicle_type,
        congestion_level=congestion_result["level"],
        rain_mm=weather_data.get("rain_mm", 0.0),
        visibility_km=weather_data.get("visibility_km", 10.0),
        mileage_used=fuel_result["mileage_used"],
    )

    # Step 7.5: Set placeholders for AI Travel Summary (handled asynchronously on frontend)
    ai_summary = {
        "summary": "Generating AI travel summary...",
        "travel_recommendation": "Analyzing optimal route conditions...",
        "safety_recommendation": "Checking safety factors...",
        "weather_alert": "",
        "fuel_insight": "Assessing congestion fuel impact...",
        "sustainability_insight": "Analyzing carbon offset..."
    }
    sustainability_result["sustainability_insight"] = "Analyzing carbon offset..."

    # Step 8: Build response
    response = {
        "origin": {
            "name": request.origin,
            "display_name": origin_geo["display_name"],
            "lat": origin_coords[0],
            "lon": origin_coords[1],
        },
        "destination": {
            "name": request.destination,
            "display_name": dest_geo["display_name"],
            "lat": dest_coords[0],
            "lon": dest_coords[1],
        },
        "vehicle_type": request.vehicle_type,
        "congestion": congestion_result,
        "travel_time": travel_result,
        "accident_risk": accident_result,
        "aqi": aqi_data,
        "weather": weather_data,
        "route": {
            "distance_km": route_data["distance_km"],
            "geometry": route_data["geometry"],
        },
        "explanation": explanation,
        "ai_summary": ai_summary,
        "fuel_estimation": fuel_result,
        "sustainability_analytics": sustainability_result,
        "model_version": "v1.0",
        "timestamp": now.isoformat(),
    }
    
    if errors:
        response["warnings"] = errors

    # Cache the result
    import time as _time2
    _predict_cache[cache_key] = (response, _time2.monotonic())
    # Evict stale cache entries occasionally (keep dict lean)
    if len(_predict_cache) > 200:
        cutoff = _time2.monotonic() - _CACHE_TTL_SECONDS
        stale = [k for k, (_, ts) in _predict_cache.items() if ts < cutoff]
        for k in stale:
            _predict_cache.pop(k, None)

    # Step 9: Log prediction
    try:
        log_prediction({
            "timestamp": now.isoformat(),
            "origin": request.origin,
            "destination": request.destination,
            "vehicle_type": request.vehicle_type,
            "congestion_level": congestion_result["level"],
            "congestion_confidence": congestion_result["confidence"],
            "accident_risk": accident_result.get("level", "Unknown"),
            "eta_minutes": travel_result["eta_minutes"],
            "aqi": aqi_data.get("aqi", -1),
            "model_version": "v1.0",
        })
    except Exception:
        pass  # Don't fail prediction if logging fails
    return response


class InsightsRequest(BaseModel):
    origin: str
    destination: str
    vehicle_type: str = "Car"
    congestion_level: str = "Moderate"
    confidence: float = 0.5
    eta_minutes: float = 60.0
    accident_risk: str = "Low"
    aqi: int = -1
    temp_c: float = 30.0
    rain_mm: float = 0.0
    visibility_km: float = 10.0
    departure_time: Optional[str] = None
    fuel_needed_liters: float = 0.0
    fuel_cost_rupees: float = 0.0
    traffic_impact_percent: float = 0.0
    co2_emission_kg: float = 0.0


@app.post("/predict/insights")
async def predict_insights(request: InsightsRequest):
    """
    Async endpoint for AI-generated travel insights via Gemini.
    Call this AFTER /predict returns so the main result loads instantly.
    """
    import asyncio
    loop = asyncio.get_running_loop()
    try:
        ai_summary = await asyncio.wait_for(
            loop.run_in_executor(
                None,
                generate_travel_summary,
                request.origin,
                request.destination,
                request.vehicle_type,
                request.congestion_level,
                request.confidence,
                request.eta_minutes,
                request.accident_risk,
                request.aqi,
                request.temp_c,
                request.rain_mm,
                request.visibility_km,
                request.departure_time or "Now",
                request.fuel_needed_liters,
                request.fuel_cost_rupees,
                request.traffic_impact_percent,
                request.co2_emission_kg,
            ),
            timeout=10.0
        )
    except asyncio.TimeoutError:
        ai_summary = {
            "summary": "AI summary unavailable — connection timed out.",
            "travel_recommendation": "Check live traffic before departure.",
            "safety_recommendation": "Drive safely and follow traffic rules.",
            "weather_alert": "",
            "fuel_insight": "Plan fuel stops ahead on long routes.",
            "sustainability_insight": "Consider carpooling or public transit for eco-friendly travel."
        }
    return ai_summary


@app.get("/aqi/{city}")
async def get_city_aqi(city: str):
    """Get live AQI for a city."""
    geo = geocode(city)
    if not geo:
        raise HTTPException(status_code=404, detail=f"City not found: {city}")
    aqi_data = get_aqi(geo["lat"], geo["lon"], geo["display_name"])
    return {"city": city, **aqi_data}


@app.get("/stats")
async def prediction_stats():
    """Get prediction statistics."""
    return get_prediction_stats()


# ── POI Search Models & Endpoint ──────────────────────────────────────────────

class POISearchRequest(BaseModel):
    """
    Request body for the POI search endpoint.
    route_geometry is the GeoJSON geometry object from the /predict response.
    """
    route_geometry: dict          # GeoJSON geometry from /predict (type + coordinates)
    origin_lat: float             # User's starting point latitude
    origin_lon: float             # User's starting point longitude
    query: str                    # Free-text query: e.g. "petrol pump", "vegetarian restaurant"
    radius_km: float = 2.0        # Search radius (km) around each sampled route point


@app.post("/poi-search")
async def poi_search(request: POISearchRequest):
    """
    Route-based POI search endpoint using Gemini + Google Places API v1.
    """
    import asyncio
    from yatrai.poi_search import run_poi_search
    from yatrai.poi_cache import make_cache_key, get_cached_pois, set_cached_pois
    
    # ── Step 1: Extract coordinate list from GeoJSON geometry ────────
    geometry = request.route_geometry
    geom_type = geometry.get("type", "")
    coords = geometry.get("coordinates", [])

    if geom_type == "LineString":
        route_coords = coords  # [[lon, lat], ...]
    elif geom_type == "MultiLineString":
        # Flatten multiple segments into one list
        route_coords = [c for segment in coords for c in segment]
    else:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported geometry type '{geom_type}'. Expected LineString."
        )

    if not route_coords:
        raise HTTPException(status_code=400, detail="Route geometry has no coordinates.")

    # ── Step 2: Generate stable cache key ───────────────────────────
    cache_key = make_cache_key(route_coords, request.query)

    # ── Step 3: Check Firestore cache ────────────────────────────────
    cached = get_cached_pois(cache_key)
    
    # ── Step 4: Run Pipeline ─────────────────────────────────────────
    result = await run_poi_search(
        query=request.query,
        geometry_coords=route_coords,
        cached_result=cached
    )

    pois = result["pois"]
    from_cache = result["from_cache"]

    # ── Step 5: Write to Firestore cache (only on cache miss) ───────
    if not from_cache and pois:
        try:
            loop = asyncio.get_running_loop()
            loop.run_in_executor(None, set_cached_pois, cache_key, pois)
        except Exception as e:
            print(f"[POI] Cache write failed (non-fatal): {e}")

    # ── Step 6: Build and return response ───────────────────────────
    if not pois:
        return {
            "pois": [],
            "count": 0,
            "from_cache": from_cache,
            "message": f"No results found for '{request.query}' along this route.",
        }

    return {
        "pois": pois,
        "count": len(pois),
        "from_cache": from_cache,
        "query": request.query
    }




#  Serve Frontend Static Files 
if FRONTEND_DIR.exists():
    app.mount("/frontend", StaticFiles(directory=str(FRONTEND_DIR)), name="frontend")


#  Run
if __name__ == "__main__":
    import uvicorn
    # Disable reload mode in production (e.g. on Render) to save memory
    is_production = os.environ.get("RENDER", "false").lower() == "true" or "PORT" in os.environ
    reload_mode = not is_production
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=reload_mode)
