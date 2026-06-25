"""
YatrAI — Complete Training Pipeline

Trains the congestion and accident models sequentially.

Usage:
    python train.py
"""

import sys
import os

# Ensure the project root is on the Python path
sys.path.insert(0, os.path.dirname(__file__))

from yatrai.congestion_model import train_congestion_model
from yatrai.accident_model import train_accident_model
from yatrai.config import VANET_DATASET, US_ACCIDENTS_DATASET


def main():
    print("=" * 60)
    print("YatrAI — Training Pipeline")
    print("=" * 60)

    # Train Congestion Model 
    print("\n[1/2] Training Congestion Model (VANET dataset) …")
    if VANET_DATASET.exists():
        metrics = train_congestion_model()
        print(f"  Accuracy: {metrics['accuracy']:.4f}")
        print(f"  F1-macro: {metrics['f1_macro']:.4f}")
        print(f"  Best Optuna trial: {metrics['best_trial']}")
    else:
        print(f"  SKIP: {VANET_DATASET} not found")

    # Train Accident Model
    print("\n[2/2] Training Accident Risk Model (US Accidents dataset) …")
    if US_ACCIDENTS_DATASET.exists():
        metrics = train_accident_model()
        print(f"  Accuracy: {metrics['accuracy']:.4f}")
        print(f"  F1-macro: {metrics['f1_macro']:.4f}")
    else:
        print(
            "  SKIP: US Accidents dataset not found. "
            "Rule-based fallback will be used."
        )

    print("\n" + "=" * 60)
    print("Training complete!")
    print("=" * 60)


if __name__ == "__main__":
    main()
