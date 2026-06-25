"""
LightGBM accident risk classifier with rule-based fallback.

Two operational modes:
  1. **Full model** — trained on US Accidents dataset.
     Derives risk_level from Severity (1-2→Low, 3→Medium, 4→High).
  2. **Rule-based fallback** — always works, no data needed.
     Heuristically scores risk from weather, congestion, and time of day.
"""

import warnings
import numpy as np
import pandas as pd
import joblib
import lightgbm as lgb
import optuna
from sklearn.model_selection import train_test_split, StratifiedKFold, cross_val_score
from sklearn.preprocessing import LabelEncoder, StandardScaler
from sklearn.metrics import classification_report, accuracy_score, f1_score

from yatrai.config import (
    US_ACCIDENTS_DATASET,
    ACCIDENT_ALL_FEATURES,
    ACCIDENT_WEATHER_FEATURES,
    ACCIDENT_ROAD_FEATURES,
    ACCIDENT_TIME_FEATURES,
    ACCIDENT_LOCATION_FEATURES,
    SEVERITY_TO_RISK,
    RISK_LABELS,
    ACCIDENT_MODEL_PATH,
    ACCIDENT_SCALER_PATH,
    ACCIDENT_ENCODER_PATH,
    OPTUNA_N_TRIALS,
    OPTUNA_CV_FOLDS,
    OPTUNA_TIMEOUT,
    LGBM_PARAM_SPACE,
)

optuna.logging.set_verbosity(optuna.logging.WARNING)
warnings.filterwarnings("ignore", category=UserWarning)


# Training 

def train_accident_model() -> dict:
    """
    Train the accident risk classifier on US Accidents data.

    Steps:
      1. Load CSV, sample 500K rows for speed.
      2. Derive risk_level from Severity column.
      3. Engineer time features from Start_Time.
      4. Handle missing values (median for numeric, False for booleans).
      5. Optuna 100-trial search, stratified CV, f1_macro.
      6. Retrain best model on full training set; save artefacts.

    Returns:
        dict with {accuracy, f1_macro, best_trial, classification_report}.
    """
    print("[Accident] Loading dataset …")
    df = pd.read_csv(US_ACCIDENTS_DATASET)
    print(f"  Raw rows: {len(df):,}")

    # Sample for speed
    if len(df) > 500_000:
        df = df.sample(n=500_000, random_state=42).reset_index(drop=True)
        print(f"  Sampled to {len(df):,} rows")

    # Derive risk_level 
    df["risk_level"] = df["Severity"].map(SEVERITY_TO_RISK)
    df = df.dropna(subset=["risk_level"]).reset_index(drop=True)

    # Time features from Start_Time 
    df["Start_Time"] = pd.to_datetime(df["Start_Time"], errors="coerce")
    df["hour"] = df["Start_Time"].dt.hour
    df["day_of_week"] = df["Start_Time"].dt.dayofweek          # 0=Mon .. 6=Sun
    df["is_weekend"] = (df["day_of_week"] >= 5).astype(int)
    df["is_night"] = ((df["hour"] < 6) | (df["hour"] >= 22)).astype(int)
    df["is_rush_hour"] = (
        ((df["hour"] >= 7) & (df["hour"] <= 10))
        | ((df["hour"] >= 17) & (df["hour"] <= 20))
    ).astype(int)

    # Sunrise_Sunset_Night derived feature 
    df["Sunrise_Sunset_Night"] = (
        df["Sunrise_Sunset"].str.strip().str.lower() == "night"
    ).astype(int)

    # Handle missing values 
    # Numeric weather features → fill with median
    for col in ACCIDENT_WEATHER_FEATURES:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
            df[col] = df[col].fillna(df[col].median())

    # Boolean road features → fill with False (0)
    for col in ACCIDENT_ROAD_FEATURES:
        if col in df.columns:
            df[col] = df[col].fillna(False).astype(int)

    # Location features → fill with median
    for col in ACCIDENT_LOCATION_FEATURES:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
            df[col] = df[col].fillna(df[col].median())

    # Features & target
    # Ensure all expected columns exist
    for col in ACCIDENT_ALL_FEATURES:
        if col not in df.columns:
            df[col] = 0

    X = df[ACCIDENT_ALL_FEATURES].copy()
    y = df["risk_level"].copy()

    le = LabelEncoder()
    le.fit(RISK_LABELS)
    y_encoded = le.transform(y)

    # Train / test split 
    X_train, X_test, y_train, y_test = train_test_split(
        X, y_encoded, test_size=0.2, stratify=y_encoded, random_state=42
    )

    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_test_scaled = scaler.transform(X_test)

    # Optuna 
    print(f"[Accident] Running Optuna ({OPTUNA_N_TRIALS} trials, "
          f"{OPTUNA_CV_FOLDS}-fold CV) …")

    def objective(trial):
        params = {
            "boosting_type": "gbdt",
            "objective": "multiclass",
            "num_class": len(RISK_LABELS),
            "metric": "multi_logloss",
            "verbosity": -1,
            "n_jobs": -1,
            "num_leaves": trial.suggest_int(
                "num_leaves", *LGBM_PARAM_SPACE["num_leaves"]
            ),
            "max_depth": trial.suggest_int(
                "max_depth", *LGBM_PARAM_SPACE["max_depth"]
            ),
            "learning_rate": trial.suggest_float(
                "learning_rate", *LGBM_PARAM_SPACE["learning_rate"], log=True
            ),
            "n_estimators": trial.suggest_int(
                "n_estimators", *LGBM_PARAM_SPACE["n_estimators"]
            ),
            "min_child_samples": trial.suggest_int(
                "min_child_samples", *LGBM_PARAM_SPACE["min_child_samples"]
            ),
            "subsample": trial.suggest_float(
                "subsample", *LGBM_PARAM_SPACE["subsample"]
            ),
            "colsample_bytree": trial.suggest_float(
                "colsample_bytree", *LGBM_PARAM_SPACE["colsample_bytree"]
            ),
            "reg_alpha": trial.suggest_float(
                "reg_alpha", *LGBM_PARAM_SPACE["reg_alpha"], log=True
            ),
            "reg_lambda": trial.suggest_float(
                "reg_lambda", *LGBM_PARAM_SPACE["reg_lambda"], log=True
            ),
        }
        model = lgb.LGBMClassifier(**params)
        cv = StratifiedKFold(n_splits=OPTUNA_CV_FOLDS, shuffle=True, random_state=42)
        scores = cross_val_score(
            model, X_train_scaled, y_train,
            cv=cv, scoring="f1_macro", n_jobs=-1,
        )
        return scores.mean()

    study = optuna.create_study(direction="maximize", study_name="accident_lgbm")
    study.optimize(objective, n_trials=OPTUNA_N_TRIALS, timeout=OPTUNA_TIMEOUT)

    best_params = study.best_trial.params
    print(f"  Best trial #{study.best_trial.number}  |  f1_macro = {study.best_value:.4f}")

    # Final model 
    final_params = {
        "boosting_type": "gbdt",
        "objective": "multiclass",
        "num_class": len(RISK_LABELS),
        "metric": "multi_logloss",
        "verbosity": -1,
        "n_jobs": -1,
        **best_params,
    }
    model = lgb.LGBMClassifier(**final_params)
    model.fit(X_train_scaled, y_train)

    y_pred = model.predict(X_test_scaled)
    accuracy = accuracy_score(y_test, y_pred)
    f1 = f1_score(y_test, y_pred, average="macro")
    report = classification_report(y_test, y_pred, target_names=le.classes_, digits=4)

    print(f"\n[Accident] Test Accuracy: {accuracy:.4f}  |  F1-macro: {f1:.4f}")
    print(report)

    #  Save 
    joblib.dump(model, ACCIDENT_MODEL_PATH)
    joblib.dump(scaler, ACCIDENT_SCALER_PATH)
    joblib.dump(le, ACCIDENT_ENCODER_PATH)
    print(f"[Accident] Artefacts saved to {ACCIDENT_MODEL_PATH.parent}")

    return {
        "accuracy": accuracy,
        "f1_macro": f1,
        "best_trial": study.best_trial.number,
        "best_params": best_params,
        "classification_report": report,
    }


# Loading 

def load_accident_model() -> tuple:
    """
    Load saved accident model artefacts.

    Returns:
        (model, scaler, label_encoder)

    Raises:
        FileNotFoundError: If any required file is missing.
    """
    model = joblib.load(ACCIDENT_MODEL_PATH)
    scaler = joblib.load(ACCIDENT_SCALER_PATH)
    le = joblib.load(ACCIDENT_ENCODER_PATH)
    return model, scaler, le


# Inference — Full Model 

def predict_accident_risk(
    features_df: pd.DataFrame,
    congestion_level: str = "Moderate",
    hour: int = 12,
) -> dict:
    """
    Predict accident risk using the trained model, with automatic
    fallback to the rule-based estimator if the model isn't available.

    Args:
        features_df: Single-row DataFrame with ACCIDENT_ALL_FEATURES columns.
                      Can also be a weather dict (triggers fallback).
        congestion_level: Current congestion level string.
        hour: Current hour (0-23).

    Returns:
        dict with {level, confidence, probabilities/risk_score, model_type}.
    """
    # If a plain dict is passed (weather data), use the fallback
    if isinstance(features_df, dict):
        return predict_accident_risk_fallback(features_df, congestion_level, hour)

    try:
        model, scaler, le = load_accident_model()
    except FileNotFoundError:
        # No trained model available — cannot predict without weather dict
        return {
            "level": "Unknown",
            "confidence": 0.0,
            "risk_score": 0.0,
            "reasons": ["Accident model not trained"],
            "model_type": "unavailable",
        }

    scaled = scaler.transform(features_df[ACCIDENT_ALL_FEATURES])
    proba = model.predict_proba(scaled)[0]
    pred_idx = int(np.argmax(proba))
    level = le.inverse_transform([pred_idx])[0]
    confidence = float(proba[pred_idx])

    probabilities = {
        label: round(float(p), 4)
        for label, p in zip(le.classes_, proba)
    }

    return {
        "level": level,
        "confidence": round(confidence, 4),
        "probabilities": probabilities,
        "model_type": "lightgbm",
    }


# Inference — Rule-Based Fallback 

def predict_accident_risk_fallback(
    weather_data: dict,
    congestion_level: str,
    hour: int,
) -> dict:
    """
    Heuristic accident risk estimator that works without a trained model.

    Scores risk from weather (rain, visibility), time of day (night,
    rush hour), and current congestion level.

    Args:
        weather_data: dict with keys rain_mm, visibility_km, etc.
        congestion_level: Current congestion level string.
        hour: Current hour (0-23).

    Returns:
        dict with {level, confidence, risk_score, reasons, model_type}.
    """
    risk_score = 0.0
    reasons = []

    # Weather contribution 
    rain = weather_data.get("rain_mm", 0)
    vis = weather_data.get("visibility_km", 10)

    if rain > 0:
        risk_score += 0.3
        reasons.append(f"Rain ({rain:.1f}mm)")
    if vis < 5:
        risk_score += 0.2
        reasons.append(f"Low visibility ({vis:.1f}km)")

    # Time contribution 
    is_night = hour < 6 or hour >= 22
    if is_night:
        risk_score += 0.15
        reasons.append("Night driving")

    # Congestion contribution 
    if congestion_level in ("Heavy", "Gridlock"):
        risk_score += 0.2
        reasons.append(f"{congestion_level} traffic")

    # Rush hour 
    is_rush = any(s <= hour <= e for s, e in [(7, 10), (17, 20)])
    if is_rush:
        risk_score += 0.15
        reasons.append("Rush hour")

    risk_score = min(risk_score, 1.0)

    if risk_score < 0.3:
        level = "Low"
    elif risk_score < 0.6:
        level = "Medium"
    else:
        level = "High"

    confidence = 0.6  # lower confidence for rule-based

    return {
        "level": level,
        "confidence": confidence,
        "risk_score": round(risk_score, 2),
        "reasons": reasons,
        "model_type": "rule-based",
    }