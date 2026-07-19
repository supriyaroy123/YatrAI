import sys
from dotenv import load_dotenv
load_dotenv()
from yatrai.apis.geocoding import geocode

print("Geocoding Gandhinagar...")
try:
    res = geocode("Gandhinagar")
    print("Result:", res)
except Exception as e:
    print("Error:", e)
