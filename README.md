# 🚦 YatrAI — Smart Traffic Intelligence for Indian Roads

An end-to-end ML-powered traffic intelligence system that predicts **congestion**, **travel time**, **accident risk**, and **air quality** for any route in India — from just 3 inputs: origin, destination, and vehicle type.

## 🎯 What It Does

```Shell
User types:  Gandhinagar → Ahmedabad, Car
                    ↓
System auto-builds 26 features from 4 free APIs
                    ↓
3 ML models run in parallel
                    ↓
User sees: Traffic level, ETA, Accident risk, AQI
```

## 🧠 Three Models

| Model         | Algorithm                      | Dataset                      | Predicts                                |
| ------------- | ------------------------------ | ---------------------------- | --------------------------------------- |
| Congestion    | LightGBM + Optuna (100 trials) | VANET (195K rows)            | Free-flow / Moderate / Heavy / Gridlock |
| Accident Risk | LightGBM / Rule-based fallback | US Accidents (7.7M rows)     | Low / Medium / High                     |
| Travel Time   | Hybrid Physics + ML            | OSRM + Congestion prediction | ETA in minutes                          |

## 🌐 Four Real-Time APIs (All Free)

| API        | Provides                                          | Cost              |
| ---------- | ------------------------------------------------- | ----------------- |
| Nominatim  | Place name → GPS coordinates                     | Free              |
| OSRM       | Road distance + base travel time + route geometry | Free              |
| Open-Meteo | Live temperature, rain, visibility                | Free              |
| WAQI       | Live Air Quality Index                            | Free (demo token) |

## 🔧 Feature Bridging (Core Innovation)

The model was trained on 26 features including V2X sensor data (RSSI, packet loss, comm delay). A real user never provides these. YatrAI solves this through **feature bridging**:

- **Computable features** (speed, density, flow) → derived from OSRM + time-of-day heuristics
- **V2X sensor features** → imputed with training medians (saved once during training)
- **Weather features** → direct from Open-Meteo API
- **Time features** → from server clock

## 🚀 Quick Start

### 1. Install Dependencies

```bash
cd YatrAI
pip install -r requirements.txt
```

### 2. Train the Congestion Model

```bash
python train.py
```

This runs Optuna hyperparameter search (100 trials) on the VANET dataset.

### 3. Start the Server

```bash
python app.py
```

Open **http://localhost:8000** in your browser.

### 4. (Optional) Train Accident Model

Download the [US Accidents dataset](https://www.kaggle.com/datasets/sobhanmoosavi/us-accidents) from Kaggle, place `US_Accidents_March23.csv` in the parent directory, and re-run `python train.py`.

## 📁 Project Structure

```
YatrAI/
├── app.py                          # FastAPI backend
├── train.py                        # Training pipeline
├── requirements.txt
├── yatrai/
│   ├── config.py                   # Central configuration
│   ├── feature_engineering.py      # 26-feature builder (feature bridging)
│   ├── congestion_model.py         # LightGBM + Optuna congestion classifier
│   ├── accident_model.py           # Accident risk (ML + rule-based fallback)
│   ├── travel_time.py              # Hybrid physics + ML travel time
│   ├── shap_explainer.py           # SHAP TreeExplainer
│   ├── drift_detection.py          # SQLite logging + PSI drift detection
│   └── apis/
│       ├── geocoding.py            # Nominatim
│       ├── routing.py              # OSRM
│       ├── weather.py              # Open-Meteo
│       └── air_quality.py          # WAQI
├── frontend/
│   ├── index.html                  # Premium dark UI
│   ├── style.css                   # Glassmorphism design system
│   └── script.js                   # Leaflet map + API calls
├── models/                         # Saved model artifacts (auto-created)
└── predictions.db                  # SQLite prediction log (auto-created)
```

## 🔍 SHAP Explainability

Every prediction comes with SHAP feature importance — showing *why* the model predicted what it did. This turns the model from a black box into something a transport authority could trust.

## 📊 MLOps

- **Prediction logging** — every prediction stored in SQLite
- **Drift detection** — PSI monitoring of feature distributions
- **Auto-retraining** — triggered when drift exceeds threshold

## 🛠️ Tech Stack

| Layer          | Technology                     |
| -------------- | ------------------------------ |
| ML             | LightGBM, scikit-learn, Optuna |
| Explainability | SHAP                           |
| Backend        | FastAPI, Uvicorn               |
| Frontend       | HTML/CSS/JS, Leaflet.js        |
| Monitoring     | Evidently, APScheduler         |
| Storage        | SQLite, joblib                 |

## 📜 License

This project is for educational purposes.
