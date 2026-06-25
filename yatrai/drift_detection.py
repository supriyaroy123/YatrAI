"""
Prediction logging to SQLite + PSI-based drift detection.

Logs every inference prediction to a local SQLite database and provides
Population Stability Index (PSI) calculation to detect feature drift
between training and production distributions.
"""

import json
import sqlite3
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

from yatrai.config import (
    DB_PATH,
    PSI_THRESHOLD,
    MIN_PREDICTIONS_FOR_DRIFT,
    CONGESTION_ALL_FEATURES,
)


# Database Initialization 
def _get_connection() -> sqlite3.Connection:
    """Get a SQLite connection, creating the predictions table if needed."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS predictions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp       TEXT    NOT NULL,
            origin          TEXT,
            destination     TEXT,
            vehicle_type    TEXT,
            congestion_level    TEXT,
            congestion_confidence REAL,
            accident_risk   TEXT,
            eta_minutes     REAL,
            aqi             INTEGER,
            model_version   TEXT,
            features_json   TEXT
        )
    """)
    conn.commit()
    return conn


# Prediction Logging 

def log_prediction(prediction_data: dict) -> int:
    """
    Log a single prediction to the SQLite database.

    Args:
        prediction_data: dict with keys matching the table columns.
            Required: origin, destination, vehicle_type.
            Optional: congestion_level, congestion_confidence,
                      accident_risk, eta_minutes, aqi,
                      model_version, features (dict or DataFrame row).

    Returns:
        The row ID of the inserted record.
    """
    conn = _get_connection()

    # Serialise the feature vector if present
    features = prediction_data.get("features")
    if features is not None:
        if isinstance(features, pd.DataFrame):
            features_json = features.iloc[0].to_dict()
        elif isinstance(features, dict):
            features_json = features
        else:
            features_json = {}
        # Convert numpy types to native Python for JSON
        features_json = {
            k: float(v) if isinstance(v, (np.floating, np.integer)) else v
            for k, v in features_json.items()
        }
        features_str = json.dumps(features_json)
    else:
        features_str = None

    cursor = conn.execute(
        """
        INSERT INTO predictions
            (timestamp, origin, destination, vehicle_type,
             congestion_level, congestion_confidence, accident_risk,
             eta_minutes, aqi, model_version, features_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            datetime.utcnow().isoformat(),
            prediction_data.get("origin", ""),
            prediction_data.get("destination", ""),
            prediction_data.get("vehicle_type", ""),
            prediction_data.get("congestion_level"),
            prediction_data.get("congestion_confidence"),
            prediction_data.get("accident_risk"),
            prediction_data.get("eta_minutes"),
            prediction_data.get("aqi"),
            prediction_data.get("model_version", "1.0"),
            features_str,
        ),
    )
    conn.commit()
    row_id = cursor.lastrowid
    conn.close()
    return row_id


# PSI Calculation 

def _calculate_psi(expected: np.ndarray, actual: np.ndarray, bins: int = 10) -> float:
    """
    Calculate Population Stability Index between two distributions.

    PSI < 0.1  → no significant shift
    PSI 0.1–0.2 → moderate shift (monitor)
    PSI > 0.2  → significant shift (retrain)

    Args:
        expected: 1-D array of training distribution values.
        actual: 1-D array of production distribution values.
        bins: Number of histogram bins.

    Returns:
        PSI value (float).
    """
    # Create bins from the expected distribution
    breakpoints = np.linspace(
        min(expected.min(), actual.min()),
        max(expected.max(), actual.max()),
        bins + 1,
    )

    expected_counts, _ = np.histogram(expected, bins=breakpoints)
    actual_counts, _ = np.histogram(actual, bins=breakpoints)

    # Convert to proportions, avoiding zero
    expected_pct = (expected_counts + 1e-6) / (expected_counts.sum() + 1e-6 * bins)
    actual_pct = (actual_counts + 1e-6) / (actual_counts.sum() + 1e-6 * bins)

    psi = np.sum((actual_pct - expected_pct) * np.log(actual_pct / expected_pct))
    return float(psi)


# Drift Detection 
def check_drift(training_features_df: pd.DataFrame) -> dict:
    """
    Compare recent production predictions against training distribution
    using PSI.

    Args:
        training_features_df: DataFrame of training features (used as
            the reference distribution).

    Returns:
        dict with {drift_detected, total_psi, drifted_features, details}.
        Returns None if there aren't enough production predictions yet.
    """
    conn = _get_connection()
    rows = conn.execute(
        "SELECT features_json FROM predictions WHERE features_json IS NOT NULL "
        "ORDER BY id DESC LIMIT ?",
        (MIN_PREDICTIONS_FOR_DRIFT * 2,),
    ).fetchall()
    conn.close()

    if len(rows) < MIN_PREDICTIONS_FOR_DRIFT:
        return None  # not enough data yet

    # Parse stored feature vectors
    prod_features = []
    for (fj,) in rows:
        try:
            prod_features.append(json.loads(fj))
        except (json.JSONDecodeError, TypeError):
            continue

    if len(prod_features) < MIN_PREDICTIONS_FOR_DRIFT:
        return None

    prod_df = pd.DataFrame(prod_features)

    # Compute PSI per feature
    drifted = []
    details = {}
    total_psi = 0.0

    for col in CONGESTION_ALL_FEATURES:
        if col not in prod_df.columns or col not in training_features_df.columns:
            continue

        expected = training_features_df[col].dropna().values
        actual = prod_df[col].dropna().values

        if len(expected) == 0 or len(actual) == 0:
            continue

        psi = _calculate_psi(expected, actual)
        total_psi += psi
        details[col] = round(psi, 4)

        if psi > PSI_THRESHOLD:
            drifted.append(col)

    drift_detected = len(drifted) > 0

    result = {
        "drift_detected": drift_detected,
        "total_psi": round(total_psi, 4),
        "drifted_features": drifted,
        "features_checked": len(details),
        "predictions_analysed": len(prod_features),
        "details": details,
    }

    if drift_detected:
        result["alert"] = {
            "message": f"Drift detected in {len(drifted)} feature(s): {', '.join(drifted)}",
            "severity": "high" if len(drifted) > 3 else "medium",
            "recommendation": "Consider retraining the congestion model.",
        }

    return result


# Statistics 

def get_prediction_stats() -> dict:
    """
    Return summary statistics from the prediction log.

    Returns:
        dict with {total_predictions, last_24h, congestion_distribution,
                   accident_distribution, avg_eta}.
    """
    conn = _get_connection()

    total = conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0]

    cutoff = (datetime.utcnow() - timedelta(hours=24)).isoformat()
    last_24h = conn.execute(
        "SELECT COUNT(*) FROM predictions WHERE timestamp > ?", (cutoff,)
    ).fetchone()[0]

    congestion_rows = conn.execute(
        "SELECT congestion_level, COUNT(*) FROM predictions "
        "WHERE congestion_level IS NOT NULL GROUP BY congestion_level"
    ).fetchall()
    congestion_dist = {row[0]: row[1] for row in congestion_rows}

    accident_rows = conn.execute(
        "SELECT accident_risk, COUNT(*) FROM predictions "
        "WHERE accident_risk IS NOT NULL GROUP BY accident_risk"
    ).fetchall()
    accident_dist = {row[0]: row[1] for row in accident_rows}

    avg_eta_row = conn.execute(
        "SELECT AVG(eta_minutes) FROM predictions WHERE eta_minutes IS NOT NULL"
    ).fetchone()
    avg_eta = round(avg_eta_row[0], 1) if avg_eta_row[0] is not None else None

    conn.close()

    return {
        "total_predictions": total,
        "last_24h": last_24h,
        "congestion_distribution": congestion_dist,
        "accident_distribution": accident_dist,
        "avg_eta_minutes": avg_eta,
    }
