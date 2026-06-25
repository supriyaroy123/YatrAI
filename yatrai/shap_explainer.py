"""
SHAP TreeExplainer wrapper for LightGBM models.
Provides per-prediction feature importance explanations.
"""

import shap
import numpy as np


class ShapExplainer:
    """Wraps shap.TreeExplainer to produce top-N feature impact rankings."""

    def __init__(self, model, feature_names: list):
        """
        Args:
            model: A trained LightGBM model (or any tree-based model).
            feature_names: Ordered list of feature column names.
        """
        self.explainer = shap.TreeExplainer(model)
        self.feature_names = feature_names

    def explain(self, features_df, top_n: int = 5) -> list:
        """
        Compute SHAP values for a single prediction and return the
        top-N most impactful features.

        Args:
            features_df: A single-row DataFrame with model features.
            top_n: Number of top features to return.

        Returns:
            List of dicts: [{feature, impact}, ...] sorted by descending impact.
        """
        shap_values = self.explainer.shap_values(features_df)

        # shap_values can be:
        #   - list of 2D arrays (one per class) — older SHAP
        #   - 3D numpy array (n_samples, n_features, n_classes) — newer SHAP
        #   - 2D numpy array (n_samples, n_features) — binary/regression

        if isinstance(shap_values, list):
            # List of arrays: aggregate by mean of absolute values across classes
            combined = np.abs(np.array(shap_values)).mean(axis=0)
        elif isinstance(shap_values, np.ndarray) and shap_values.ndim == 3:
            # 3D array: (n_samples, n_features, n_classes)
            combined = np.abs(shap_values).mean(axis=2)
        elif isinstance(shap_values, np.ndarray):
            combined = np.abs(shap_values)
        else:
            return []

        # Handle single-row (shape may be (1, n_features) or (n_features,))
        if combined.ndim > 1:
            importance = combined[0]
        else:
            importance = combined

        # Ensure importance is a 1D array of correct length
        importance = np.asarray(importance).flatten()
        if len(importance) != len(self.feature_names):
            # Fallback: truncate or pad
            importance = importance[:len(self.feature_names)]

        # Rank and extract top-N
        indices = np.argsort(importance)[::-1][:top_n]

        explanations = []
        for idx in indices:
            idx = int(idx)
            explanations.append({
                "feature": self.feature_names[idx],
                "impact": round(float(importance[idx]), 4),
            })
        return explanations

