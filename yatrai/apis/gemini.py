"""
Gemini API travel assistant summary generator.
"""
import requests
import json
import os

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

def generate_travel_summary(
    origin: str,
    destination: str,
    vehicle_type: str,
    traffic_level: str,
    confidence: float,
    eta_minutes: float,
    accident_risk: str,
    aqi: int,
    temp_c: float,
    rain_mm: float,
    visibility_km: float,
    departure_time: str,
    fuel_needed_liters: float = None,
    fuel_cost_rupees: float = None,
    traffic_impact_percent: int = None,
) -> dict:
    """
    Generate a natural-language travel summary, recommendation, safety alert, weather alert,
    and fuel cost insight using the Gemini 1.5 Flash API.
    """
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
    
    fuel_info = ""
    if fuel_needed_liters is not None and fuel_cost_rupees is not None:
        fuel_info = f"""
- Fuel Needed: {fuel_needed_liters:.2f} Liters
- Estimated Fuel Cost: ₹{fuel_cost_rupees}
- Traffic Congestion Fuel Impact: +{traffic_impact_percent}% fuel consumption due to traffic
"""

    prompt = f"""
You are YatrAI, an AI Travel Assistant for Indian road commuters.
Analyze the following travel data and generate a JSON response.

TRAVEL DATA:
- Origin: {origin}
- Destination: {destination}
- Vehicle Type: {vehicle_type}
- Traffic Level: {traffic_level}
- Traffic Congestion Confidence: {confidence:.2f}
- Estimated Travel Time: {eta_minutes:.1f} minutes
- Road Accident Risk: {accident_risk}
- Air Quality Index (AQI): {aqi}
- Destination Temperature: {temp_c:.1f}°C
- Rainfall: {rain_mm:.1f} mm
- Visibility: {visibility_km:.1f} km
- Departure Time: {departure_time}
{fuel_info}

 response MUST be a valid JSON object with the following keys:
1. "summary": A concise, natural-language, professional travel summary (approx 2-3 sentences). Example: "Heavy congestion is currently detected on the route from Gandhinagar to Ahmedabad. Estimated travel time is approximately 1 hour 25 minutes. Accident risk is moderate due to rainfall and reduced visibility. Air quality remains moderate. Travelers are advised to allow extra travel time and drive cautiously."
2. "travel_recommendation": A practical recommendation for the route.
3. "safety_recommendation": Advice on driving style, precautions, or safety gear.
4. "weather_alert": A brief warning about rainfall or visibility if applicable, otherwise an empty string.
5. "fuel_insight": An AI-generated fuel insight. Example: "Heavy traffic is increasing fuel consumption by approximately 25% compared to free-flow conditions. Consider delaying departure or selecting an alternative route to reduce fuel costs."

IMPORTANT: Do not return any markdown tags or backticks (like ```json). Return ONLY the raw JSON string.
"""

    headers = {"Content-Type": "application/json"}
    payload = {
        "contents": [{
            "parts": [{
                "text": prompt
            }]
        }]
    }

    fallback_response = {
        "summary": f"Travel from {origin} to {destination} via {vehicle_type} is expected to take {eta_minutes:.1f} minutes. Traffic is {traffic_level} and road safety risk is {accident_risk}.",
        "travel_recommendation": "Plan ahead and allocate extra travel time.",
        "safety_recommendation": "Drive defensively, follow traffic rules, and wear your seatbelt/helmet.",
        "weather_alert": "Watch out for local weather conditions." if rain_mm > 0 or visibility_km < 5 else "",
        "fuel_insight": f"Heavy traffic may increase fuel consumption. Consider driving during off-peak hours to save fuel." if traffic_level in ("Heavy", "Gridlock") else "Traffic impact on fuel consumption is minimal."
    }

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=3)
        resp.raise_for_status()
        result = resp.json()
        
        # Extract text content
        text_content = result["candidates"][0]["content"]["parts"][0]["text"].strip()
        
        # Clean up any potential markdown code blocks
        if text_content.startswith("```"):
            # strip markdown lines
            lines = text_content.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines[-1].startswith("```"):
                lines = lines[:-1]
            text_content = "\n".join(lines).strip()
            
        data = json.loads(text_content)
        # Ensure all required keys exist
        for key in ["summary", "travel_recommendation", "safety_recommendation", "weather_alert", "fuel_insight"]:
            if key not in data:
                data[key] = fallback_response[key]
        return data
    except Exception as e:
        print(f"[Gemini API] Error: {e}")
        return fallback_response

