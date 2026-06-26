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

from fastapi import FastAPI, HTTPException
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
    print("\n" + "="*50)
    print("  YatrAI is running!")
    print("  Open http://localhost:8000 in your browser")
    print("="*50 + "\n")


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




class AQIRequest(BaseModel):
    city: str


#  Endpoints 
@app.get("/")
async def serve_frontend():
    index_path = FRONTEND_DIR / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return {"message": "YatrAI API is running. Frontend not found at /frontend/"}


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


@app.post("/predict")
async def predict(request: PredictRequest):
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
    loop = asyncio.get_running_loop()

    # Step 1: Geocode origin and destination concurrently in separate threads
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

    # Step 7.5: Generate AI Travel Summary using Gemini
    from yatrai.apis.gemini import generate_travel_summary
    ai_summary = await loop.run_in_executor(
        None,
        generate_travel_summary,
        request.origin,
        request.destination,
        request.vehicle_type,
        congestion_result["level"],
        congestion_result.get("confidence", 0.5),
        travel_result["eta_minutes"],
        accident_result["level"],
        aqi_data.get("aqi", -1),
        weather_data.get("temp_c", 30.0),
        weather_data.get("rain_mm", 0.0),
        weather_data.get("visibility_km", 10.0),
        formatted_departure,
        fuel_result["fuel_needed_liters"],
        fuel_result["fuel_cost_rupees"],
        fuel_result["traffic_impact_percent"],
        sustainability_result["co2_emission_kg"],
    )
    
    # Inject AI sustainability insight
    sustainability_result["sustainability_insight"] = ai_summary.get("sustainability_insight", "")

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
