import asyncio
import os
from fastapi.testclient import TestClient
from app import app

client = TestClient(app)

print("Starting comprehensive backend test...")

payload = {
    "origin": "Gandhinagar",
    "destination": "Ahmedabad",
    "vehicle_type": "Car",
    "departure_time": "9:38 PM",
    "fuel_mode": "avg",
    "origin_lat": None,
    "origin_lon": None,
    "dest_lat": None,
    "dest_lon": None
}

print(f"\nSending POST request to /predict with payload: {payload}")
response = client.post("/predict", json=payload)

print(f"\nStatus Code: {response.status_code}")
if response.status_code == 200:
    print("SUCCESS! The backend processed the request without any 500 crashes.")
    data = response.json()
    print("Trip Duration:", data.get("duration_mins"), "minutes")
    print("Distance:", data.get("distance_km"), "km")
else:
    print("Request failed!")
    print("Response Body:", response.text)

print("\nTesting /api/config endpoint...")
conf_resp = client.get("/api/config")
print(f"Status: {conf_resp.status_code}")
if conf_resp.status_code == 200:
    print("Config loaded successfully.")
else:
    print(conf_resp.text)
