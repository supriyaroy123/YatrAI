"""
Fuel Cost Estimation Engine.
Calculates estimated fuel consumption and travel cost based on OSRM distance,
vehicle type, traffic conditions, weather conditions, and fuel mode.
"""
import os

def calculate_fuel(
    distance_km: float,
    vehicle_type: str,
    congestion_level: str,
    rain_mm: float = 0.0,
    fuel_mode: str = "average",
    custom_mileage: float = None,
) -> dict:
    """
    Calculate fuel needed (L) and cost (₹) based on factors:
    Final Fuel Needed = (Distance / Mileage) * Traffic_Fuel_Factor * Weather_Fuel_Factor
    """
    # 1. Mileage Mode
    mileage_dict = {
        "scooter": 50.0,
        "motorcycle": 45.0,
        "bike": 45.0, # fallback/alias
        "car": 15.0,
        "suv": 11.0,
        "auto rickshaw": 25.0,
        "auto": 25.0, # fallback/alias
        "bus": 4.0,
        "truck": 3.0,
    }
    
    vehicle_key = vehicle_type.lower().strip()
    default_mileage = mileage_dict.get(vehicle_key, 15.0)
    
    if fuel_mode == "custom" and custom_mileage is not None and custom_mileage > 0:
        mileage_used = custom_mileage
        mode_label = "Custom Mileage"
    else:
        mileage_used = default_mileage
        mode_label = "Estimated Average"

    # 2. Traffic Fuel Consumption Factor
    # Free Flow = 1.00, Moderate = 1.10, Heavy = 1.25, Gridlock = 1.45
    cong_normalized = congestion_level.lower().replace("-", " ")
    if "free" in cong_normalized:
        traffic_factor = 1.00
    elif "moderate" in cong_normalized:
        traffic_factor = 1.10
    elif "heavy" in cong_normalized:
        traffic_factor = 1.25
    elif "gridlock" in cong_normalized:
        traffic_factor = 1.45
    else:
        traffic_factor = 1.10  # Moderate default fallback

    # 3. Weather Fuel Factor
    # Normal Weather = 1.00, Light Rain = 1.05, Moderate Rain = 1.08, Heavy Rain = 1.12
    if rain_mm < 1.0:
        weather_factor = 1.00
    elif rain_mm < 5.0:
        weather_factor = 1.05
    elif rain_mm < 15.0:
        weather_factor = 1.08
    else:
        weather_factor = 1.12

    # 4. Calculation
    base_fuel = distance_km / mileage_used if mileage_used > 0 else 0
    final_fuel = base_fuel * traffic_factor * weather_factor
    
    # Get fuel price from environment
    fuel_price = float(os.environ.get("FUEL_PRICE", 105.0))
    fuel_cost = final_fuel * fuel_price
    
    # Traffic impact percent
    traffic_impact_pct = round((traffic_factor - 1.0) * 100)
    
    return {
        "distance_km": round(distance_km, 2),
        "vehicle_type": vehicle_type,
        "mileage_used": round(mileage_used, 1),
        "fuel_needed_liters": round(final_fuel, 2),
        "fuel_cost_rupees": round(fuel_cost),
        "fuel_price_per_liter": fuel_price,
        "fuel_mode": mode_label,
        "traffic_impact_percent": traffic_impact_pct,
        "factors": {
            "traffic": traffic_factor,
            "weather": weather_factor,
        }
    }
