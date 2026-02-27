"""Correlation engine and value prediction model for VAULT.

Learns which visual features predict high auction bids using scikit-learn.
"""

import json
import pickle
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.model_selection import train_test_split, cross_val_score
from sklearn.preprocessing import LabelEncoder
from sklearn.metrics import mean_absolute_error, r2_score

from vault.config import get_config, get_base_dir
from vault.logger import setup_logger
from storage.database import Database

log = setup_logger("vault.trainer")

# All possible item categories for one-hot encoding
ALL_CATEGORIES = [
    "furniture", "electronics", "clothing", "boxes", "appliances",
    "bikes", "tools", "toys", "sports_equipment", "artwork",
    "musical_instruments", "office_supplies", "kitchenware",
    "outdoor_equipment", "luggage", "other",
]

ALL_BOX_TYPES = [
    "uhaul_branded", "plastic_totes", "garbage_bags",
    "misc_cardboard", "moving_boxes", "storage_bins", "none_visible",
]

CONDITION_MAP = {
    "excellent": 5, "good": 4, "fair": 3, "poor": 2, "very_poor": 1,
}

CLOTHING_VOLUME_MAP = {
    "none": 0, "small_amount": 1, "moderate": 2,
    "large_amount": 3, "primarily_clothing": 4,
}


class ValuePredictor:
    """Trains and uses a model to predict storage unit auction values."""

    def __init__(self):
        cfg = get_config()["training"]
        self.test_split = cfg["test_split"]
        self.random_state = cfg["random_state"]
        self.min_samples = cfg["min_samples"]
        self.top_quartile_threshold = cfg["top_quartile_threshold"]
        self.model_path = get_base_dir() / cfg["model_path"]

        self.model = None
        self.feature_names = None
        self.db = Database()

    def _build_feature_dataframe(self, records):
        """Convert raw database records into a feature DataFrame for training."""
        rows = []
        for r in records:
            row = {}

            # Numeric features
            row["packing_quality_score"] = r.get("packing_quality_score", 5)
            row["fill_percentage"] = r.get("fill_percentage", 50)
            row["clothing_visible"] = r.get("clothing_visible", 0)

            # Encode condition
            condition = r.get("condition_assessment", "fair")
            row["condition_score"] = CONDITION_MAP.get(condition, 3)

            # Encode clothing volume
            clothing_vol = r.get("clothing_volume_estimate", "none")
            row["clothing_volume_score"] = CLOTHING_VOLUME_MAP.get(clothing_vol, 0)

            # One-hot encode item categories
            try:
                categories = json.loads(r.get("item_categories", "[]"))
            except (json.JSONDecodeError, TypeError):
                categories = []
            for cat in ALL_CATEGORIES:
                row[f"cat_{cat}"] = 1 if cat in categories else 0

            # One-hot encode box types
            try:
                box_types = json.loads(r.get("box_types", "[]"))
            except (json.JSONDecodeError, TypeError):
                box_types = []
            for bt in ALL_BOX_TYPES:
                row[f"box_{bt}"] = 1 if bt in box_types else 0

            # Count of high-value indicators
            try:
                hv = json.loads(r.get("high_value_indicators", "[]"))
            except (json.JSONDecodeError, TypeError):
                hv = []
            row["num_high_value_indicators"] = len(hv)

            # Count of brand names
            try:
                brands = json.loads(r.get("brand_names", "[]"))
            except (json.JSONDecodeError, TypeError):
                brands = []
            row["num_brands"] = len(brands)

            # Target variable
            row["final_bid"] = r.get("final_bid", 0)

            rows.append(row)

        return pd.DataFrame(rows)

    def train(self):
        """Train the value prediction model on all available data."""
        log.info("Starting model training")

        with self.db as db:
            records = db.get_all_features_with_bids()

        if len(records) < self.min_samples:
            log.warning(
                f"Only {len(records)} samples available (minimum: {self.min_samples}). "
                "Need more data before training will be meaningful."
            )
            if len(records) < 5:
                log.error("Not enough data to train. Scrape and analyze more auctions first.")
                return None

        log.info(f"Training on {len(records)} auction records")

        df = self._build_feature_dataframe(records)
        feature_cols = [c for c in df.columns if c != "final_bid"]
        X = df[feature_cols]
        y = df["final_bid"]

        self.feature_names = feature_cols

        # Train/test split
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=self.test_split, random_state=self.random_state
        )

        # Gradient boosting regressor
        self.model = GradientBoostingRegressor(
            n_estimators=200,
            max_depth=4,
            learning_rate=0.1,
            min_samples_split=5,
            random_state=self.random_state,
        )

        self.model.fit(X_train, y_train)

        # Evaluate
        train_pred = self.model.predict(X_train)
        test_pred = self.model.predict(X_test)

        train_mae = mean_absolute_error(y_train, train_pred)
        test_mae = mean_absolute_error(y_test, test_pred)
        r2 = r2_score(y_test, test_pred)

        # Cross-validation
        cv_scores = cross_val_score(
            self.model, X, y, cv=min(5, len(records)), scoring="r2"
        )

        log.info(f"Training MAE: ${train_mae:.2f}")
        log.info(f"Test MAE: ${test_mae:.2f}")
        log.info(f"R² score: {r2:.3f}")
        log.info(f"Cross-val R² (mean): {cv_scores.mean():.3f}")

        # Feature importance
        importances = self.model.feature_importances_
        importance_pairs = sorted(
            zip(feature_cols, importances), key=lambda x: x[1], reverse=True
        )

        log.info("Top 10 features by importance:")
        for name, imp in importance_pairs[:10]:
            log.info(f"  {name}: {imp:.4f}")

        # Save model
        self.model_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.model_path, "wb") as f:
            pickle.dump({
                "model": self.model,
                "feature_names": self.feature_names,
            }, f)
        log.info(f"Model saved to {self.model_path}")

        # Save run to database
        results = {
            "num_samples": len(records),
            "accuracy": round(r2, 4),
            "top_features": json.dumps(importance_pairs[:15]),
            "model_path": str(self.model_path),
            "notes": f"MAE: ${test_mae:.2f}, R²: {r2:.3f}, CV R²: {cv_scores.mean():.3f}",
        }

        with self.db as db:
            db.save_model_run(results)

        # Generate report
        report = self._generate_report(
            importance_pairs, df, r2, test_mae, cv_scores.mean()
        )

        return report

    def _generate_report(self, importance_pairs, df, r2, mae, cv_r2):
        """Generate the JSON analysis report."""
        # Top indicators
        top_indicators = [
            {"feature": name, "importance": round(float(imp), 4)}
            for name, imp in importance_pairs[:15]
        ]

        # Average bid by key features
        avg_by_feature = {}

        # By condition
        for cond_name, cond_val in CONDITION_MAP.items():
            subset = df[df["condition_score"] == cond_val]
            if len(subset) > 0:
                avg_by_feature[f"condition_{cond_name}"] = round(
                    subset["final_bid"].mean(), 2
                )

        # By category presence
        for cat in ALL_CATEGORIES:
            col = f"cat_{cat}"
            if col in df.columns:
                present = df[df[col] == 1]
                if len(present) > 0:
                    avg_by_feature[f"has_{cat}"] = round(
                        present["final_bid"].mean(), 2
                    )

        # By clothing visibility
        with_clothing = df[df["clothing_visible"] == 1]
        without_clothing = df[df["clothing_visible"] == 0]
        if len(with_clothing) > 0:
            avg_by_feature["with_clothing"] = round(with_clothing["final_bid"].mean(), 2)
        if len(without_clothing) > 0:
            avg_by_feature["without_clothing"] = round(without_clothing["final_bid"].mean(), 2)

        # High vs low packing quality
        high_quality = df[df["packing_quality_score"] >= 7]
        low_quality = df[df["packing_quality_score"] <= 3]
        if len(high_quality) > 0:
            avg_by_feature["high_packing_quality"] = round(high_quality["final_bid"].mean(), 2)
        if len(low_quality) > 0:
            avg_by_feature["low_packing_quality"] = round(low_quality["final_bid"].mean(), 2)

        report = {
            "top_indicators": top_indicators,
            "avg_bid_by_feature": avg_by_feature,
            "model_accuracy": {
                "r2_score": round(r2, 4),
                "mean_absolute_error": round(mae, 2),
                "cross_val_r2": round(cv_r2, 4),
            },
            "dataset_size": len(df),
            "avg_bid_overall": round(df["final_bid"].mean(), 2),
            "median_bid": round(df["final_bid"].median(), 2),
        }

        # Save report
        from vault.config import get_reports_dir
        reports_dir = get_reports_dir()
        reports_dir.mkdir(parents=True, exist_ok=True)
        report_path = reports_dir / "latest_report.json"
        with open(report_path, "w") as f:
            json.dump(report, f, indent=2)
        log.info(f"Report saved to {report_path}")

        return report

    def load_model(self):
        """Load a previously trained model from disk."""
        if not self.model_path.exists():
            log.error(f"No trained model found at {self.model_path}. Run training first.")
            return False

        with open(self.model_path, "rb") as f:
            data = pickle.load(f)

        self.model = data["model"]
        self.feature_names = data["feature_names"]
        log.info(f"Model loaded from {self.model_path}")
        return True

    def predict_value(self, features_dict):
        """Predict auction value for a set of features.

        Args:
            features_dict: Raw features dict from vision analysis (same format
                          as stored in unit_features table).

        Returns:
            Dict with predicted_value and confidence info.
        """
        if self.model is None:
            if not self.load_model():
                return None

        # Build a single-row DataFrame matching training features
        row = {}
        row["packing_quality_score"] = features_dict.get("packing_quality_score", 5)
        row["fill_percentage"] = features_dict.get("fill_percentage", 50)
        row["clothing_visible"] = 1 if features_dict.get("clothing_visible") else 0

        condition = features_dict.get("condition_assessment", "fair")
        row["condition_score"] = CONDITION_MAP.get(condition, 3)

        clothing_vol = features_dict.get("clothing_volume_estimate", "none")
        row["clothing_volume_score"] = CLOTHING_VOLUME_MAP.get(clothing_vol, 0)

        categories = features_dict.get("item_categories", [])
        if isinstance(categories, str):
            try:
                categories = json.loads(categories)
            except json.JSONDecodeError:
                categories = []
        for cat in ALL_CATEGORIES:
            row[f"cat_{cat}"] = 1 if cat in categories else 0

        box_types = features_dict.get("box_types", [])
        if isinstance(box_types, str):
            try:
                box_types = json.loads(box_types)
            except json.JSONDecodeError:
                box_types = []
        for bt in ALL_BOX_TYPES:
            row[f"box_{bt}"] = 1 if bt in box_types else 0

        hv = features_dict.get("high_value_indicators", [])
        if isinstance(hv, str):
            try:
                hv = json.loads(hv)
            except json.JSONDecodeError:
                hv = []
        row["num_high_value_indicators"] = len(hv)

        brands = features_dict.get("brand_names", [])
        if isinstance(brands, str):
            try:
                brands = json.loads(brands)
            except json.JSONDecodeError:
                brands = []
        row["num_brands"] = len(brands)

        # Ensure all expected features are present
        df = pd.DataFrame([row])
        for col in self.feature_names:
            if col not in df.columns:
                df[col] = 0
        df = df[self.feature_names]

        prediction = self.model.predict(df)[0]

        # Estimate a rough confidence range (using training data std as proxy)
        # In a real system you'd use prediction intervals from the ensemble
        margin = max(prediction * 0.25, 20)  # 25% or at least $20

        return {
            "predicted_value": round(float(prediction), 2),
            "value_range_low": round(max(0, float(prediction - margin)), 2),
            "value_range_high": round(float(prediction + margin), 2),
        }
