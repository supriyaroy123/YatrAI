"""
Hybrid physics + congestion travel-time estimator.
Adjusts OSRM free-flow duration by congestion level, vehicle type, and rain.
This is NOT a trained model — purely formulaic.
"""

from yatrai.config import (
    CONGESTION_DELAY_FACTORS,
    VEHICLE_SPEED_FACTORS,
    RAIN_DELAY_PER_MM,
    MAX_RAIN_DELAY,
)


def is_rush_hour(hour: int, minute: int = 0) -> bool:
    """Check if departure time is in Indian rush hours (7-10 AM or 5-9 PM)."""
    mins = hour * 60 + minute
    morning_start = 7 * 60
    morning_end = 10 * 60
    evening_start = 17 * 60 # 5:00 PM
    evening_end = 21 * 60   # 9:00 PM
    return (morning_start <= mins <= morning_end) or (evening_start <= mins <= evening_end)


def get_city_density_factor(origin: str, destination: str) -> float:
    """Get the density factor based on city tier matching."""
    tier1 = ["bangalore", "bengaluru", "mumbai", "delhi", "hyderabad", "chennai", "kolkata", "pune"]
    tier2 = ["ahmedabad", "lucknow", "jaipur", "bhopal", "indore", "guwahati"]

    orig_lower = origin.lower() if origin else ""
    dest_lower = destination.lower() if destination else ""

    # Check Tier-1
    if any(city in orig_lower or city in dest_lower for city in tier1):
        return 1.20
    # Check Tier-2
    if any(city in orig_lower or city in dest_lower for city in tier2):
        return 1.10
    
    return 1.00


def estimate_travel_time(
    base_duration_min: float,
    congestion_level: str,
    vehicle_type: str,
    rain_mm: float = 0.0,
    visibility_km: float = 10.0,
    departure_hour: int = 12,
    departure_minute: int = 0,
    origin_name: str = "",
    destination_name: str = "",
) -> dict:
    """
    Estimate realistic travel time by layering Indian-road correction factors:
    Final ETA = Base_OSRM_ETA * Traffic_Factor * Vehicle_Factor * Rush_Hour_Factor * Weather_Factor * City_Density_Factor

    Special logic: If Rush Hour and Heavy congestion, apply an additional 1.10 factor.
    """
    # 1. Traffic Factor
    # Free Flow = 1.00, Moderate = 1.25, Heavy = 1.60, Gridlock = 2.20
    cong_normalized = congestion_level.lower().replace("-", " ")
    if "free" in cong_normalized:
        traffic_factor = 1.00
    elif "moderate" in cong_normalized:
        traffic_factor = 1.25
    elif "heavy" in cong_normalized:
        traffic_factor = 1.60
    elif "gridlock" in cong_normalized:
        traffic_factor = 2.20
    else:
        traffic_factor = 1.25  # default fallback

    # 2. Vehicle Factor
    # Bike/Scooter/Motorcycle = 0.90, Car/SUV = 1.00, Auto Rickshaw = 1.05, Bus = 1.20, Truck = 1.35
    veh_lower = vehicle_type.lower()
    if "bike" in veh_lower or "cycle" in veh_lower or "scooter" in veh_lower or "motorcycle" in veh_lower:
        vehicle_factor = 0.90
    elif "auto" in veh_lower:
        vehicle_factor = 1.05
    elif "bus" in veh_lower:
        vehicle_factor = 1.20
    elif "truck" in veh_lower or "lorry" in veh_lower:
        vehicle_factor = 1.35
    else:  # Car, SUV or default
        vehicle_factor = 1.00

    # 3. Rush Hour Factor
    # Morning Rush: 7:00 AM – 10:00 AM, Evening Rush: 5:00 PM – 9:00 PM
    # Rush Hour = 1.20, Normal = 1.00
    is_rush = is_rush_hour(departure_hour, departure_minute)
    rush_factor = 1.20 if is_rush else 1.00

    # 4. Weather Factor
    # Rainfall < 1 mm: 1.00
    # Rainfall 1–5 mm: 1.10
    # Rainfall 5–15 mm: 1.20
    # Rainfall > 15 mm: 1.35
    # Visibility < 2 km: Additional ×1.15
    if rain_mm < 1.0:
        weather_factor = 1.00
    elif rain_mm <= 5.0:
        weather_factor = 1.10
    elif rain_mm <= 15.0:
        weather_factor = 1.20
    else:
        weather_factor = 1.35

    if visibility_km < 2.0:
        weather_factor *= 1.15

    # 5. City Density Factor
    city_factor = get_city_density_factor(origin_name, destination_name)

    # 6. Special Rush Hour Logic: If Rush Hour AND Traffic Prediction = Heavy: Apply Additional Factor 1.10
    # Let's apply to Heavy and Gridlock
    special_rush_factor = 1.00
    if is_rush and (congestion_level.lower() in ("heavy", "gridlock")):
        special_rush_factor = 1.10

    # Calculate final travel time
    final_multiplier = traffic_factor * vehicle_factor * rush_factor * weather_factor * city_factor * special_rush_factor
    adjusted_time = base_duration_min * final_multiplier
    delay_min = adjusted_time - base_duration_min

    return {
        "eta_minutes": round(adjusted_time, 1),
        "base_minutes": round(base_duration_min, 1),
        "delay_minutes": round(delay_min, 1),
        "factors": {
            "traffic": {
                "level": congestion_level,
                "multiplier": round(traffic_factor, 2),
            },
            "vehicle": {
                "type": vehicle_type,
                "multiplier": round(vehicle_factor, 2),
            },
            "rush_hour": {
                "active": is_rush,
                "multiplier": round(rush_factor, 2),
            },
            "weather": {
                "rain_mm": rain_mm,
                "visibility_km": visibility_km,
                "multiplier": round(weather_factor, 2),
            },
            "city_density": {
                "multiplier": round(city_factor, 2),
            },
            "special_rush": {
                "active": (special_rush_factor > 1.0),
                "multiplier": round(special_rush_factor, 2),
            },
            "final_multiplier": round(final_multiplier, 3),
        },
    }



