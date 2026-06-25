"""
LightGBM congestion classifier trained on the VANET dataset with Optuna tuning.

Training pipeline:
  1. Load vanet_traffic_data.csv
  2. Encode target labels (Free-flow / Moderate / Heavy / Gridlock)
  3. 80/20 stratified split → StandardScaler
  4. Optuna 100-trial hyperparameter search (5-fold stratified CV, f1_macro)
  5. Retrain best model on full training set
  6. Save model + scaler + encoder + training medians + Optuna study
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
    VANET_DATASET,
    CONGESTION_ALL_FEATURES,
    CONGESTION_V2X_FEATURES,
    CONGESTION_LABELS,
    CONGESTION_MODEL_PATH,
    CONGESTION_SCALER_PATH,
    CONGESTION_ENCODER_PATH,
    TRAINING_MEDIANS_PATH,
    OPTUNA_STUDY_PATH,
    OPTUNA_N_TRIALS,
    OPTUNA_CV_FOLDS,
    OPTUNA_TIMEOUT,
    LGBM_PARAM_SPACE,
)

# Suppress Optuna info logs during search
optuna.logging.set_verbosity(optuna.logging.WARNING)
warnings.filterwarnings("ignore", category=UserWarning)


# ── Training ──────────────────────────────────────────────────────────

def train_congestion_model() -> dict:
    """
    Full training pipeline for the congestion classifier.

    Returns:
        dict with {accuracy, f1_macro, best_trial, classification_report}.

    Raises:
        FileNotFoundError: If the VANET dataset CSV is missing.
    """
    print("[Congestion] Loading dataset …")
    df = pd.read_csv(VANET_DATASET)
    print(f"  Rows: {len(df):,}  |  Columns: {df.shape[1]}")

    # ── Features & target ────────────────────────────────────────────
    X = df[CONGESTION_ALL_FEATURES].copy()
    y = df["label"].copy()

    le = LabelEncoder()
    le.fit(CONGESTION_LABELS)          # deterministic ordering
    y_encoded = le.transform(y)

    # ── Train / test split ───────────────────────────────────────────
    X_train, X_test, y_train, y_test = train_test_split(
        X, y_encoded, test_size=0.2, stratify=y_encoded, random_state=42
    )

    # ── StandardScaler ───────────────────────────────────────────────
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_test_scaled = scaler.transform(X_test)

    # ── Save training medians for V2X features (used at inference) ──
    training_medians = {}
    for feat in CONGESTION_V2X_FEATURES:
        training_medians[feat] = float(X_train[feat].median())
    print(f"  V2X training medians saved for {len(training_medians)} features")

    # ── Optuna hyperparameter search ─────────────────────────────────
    print(f"[Congestion] Running Optuna ({OPTUNA_N_TRIALS} trials, "
          f"{OPTUNA_CV_FOLDS}-fold CV) …")

    def objective(trial):
        params = {
            "boosting_type": "gbdt",
            "objective": "multiclass",
            "num_class": len(CONGESTION_LABELS),
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

    study = optuna.create_study(direction="maximize", study_name="congestion_lgbm")
    study.optimize(objective, n_trials=OPTUNA_N_TRIALS, timeout=OPTUNA_TIMEOUT)

    best_params = study.best_trial.params
    print(f"  Best trial #{study.best_trial.number}  |  f1_macro = {study.best_value:.4f}")
    print(f"  Best params: {best_params}")

    # ── Final model with best hyperparameters ────────────────────────
    final_params = {
        "boosting_type": "gbdt",
        "objective": "multiclass",
        "num_class": len(CONGESTION_LABELS),
        "metric": "multi_logloss",
        "verbosity": -1,
        "n_jobs": -1,
        **best_params,
    }
    model = lgb.LGBMClassifier(**final_params)
    model.fit(X_train_scaled, y_train)

    # ── Evaluation ───────────────────────────────────────────────────
    y_pred = model.predict(X_test_scaled)
    accuracy = accuracy_score(y_test, y_pred)
    f1 = f1_score(y_test, y_pred, average="macro")
    report = classification_report(
        y_test, y_pred, target_names=le.classes_, digits=4
    )
    print(f"\n[Congestion] Test Accuracy: {accuracy:.4f}  |  F1-macro: {f1:.4f}")
    print(report)

    # ── Persist artefacts ────────────────────────────────────────────
    joblib.dump(model, CONGESTION_MODEL_PATH)
    joblib.dump(scaler, CONGESTION_SCALER_PATH)
    joblib.dump(le, CONGESTION_ENCODER_PATH)
    joblib.dump(training_medians, TRAINING_MEDIANS_PATH)
    joblib.dump(study, OPTUNA_STUDY_PATH)
    print(f"[Congestion] Artefacts saved to {CONGESTION_MODEL_PATH.parent}")

    return {
        "accuracy": accuracy,
        "f1_macro": f1,
        "best_trial": study.best_trial.number,
        "best_params": best_params,
        "classification_report": report,
    }


# ── Loading ───────────────────────────────────────────────────────────

def load_congestion_model() -> tuple:
    """
    Load saved congestion model artefacts.

    Returns:
        (model, scaler, label_encoder, training_medians)

    Raises:
        FileNotFoundError: If any required file is missing.
    """
    model = joblib.load(CONGESTION_MODEL_PATH)
    scaler = joblib.load(CONGESTION_SCALER_PATH)
    le = joblib.load(CONGESTION_ENCODER_PATH)
    medians = joblib.load(TRAINING_MEDIANS_PATH)
    return model, scaler, le, medians


# ── Inference ─────────────────────────────────────────────────────────

def predict_congestion(features_df: pd.DataFrame) -> dict:
    """
    Predict congestion level from a single-row feature DataFrame.

    Args:
        features_df: DataFrame with columns matching CONGESTION_ALL_FEATURES.

    Returns:
        dict with {level, confidence, probabilities}.
    """
    model, scaler, le, _ = load_congestion_model()

    scaled = scaler.transform(features_df[CONGESTION_ALL_FEATURES])
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
    }
