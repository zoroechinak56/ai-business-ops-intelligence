"""Detect outlier order processing times and unusual warehouse-days."""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest


ANOMALY_COLUMNS = (
    "anomaly_type",
    "order_id",
    "warehouse_id",
    "date",
    "processing_days",
    "warehouse_load",
    "late_rate",
    "iqr_flag",
    "z_score_flag",
    "isolation_forest_flag",
    "anomaly_score",
)


def detect_anomalies(
    order_features: pd.DataFrame,
    *,
    contamination: float = 0.05,
    random_state: int = 42,
    z_threshold: float = 3.0,
) -> pd.DataFrame:
    """Flag processing-time outliers and anomalous warehouse-day aggregates.

    Order-level rows are selected when either 1.5-IQR fences or the configured
    absolute z-score threshold is exceeded. Isolation Forest is applied to
    warehouse-day order volume, mean processing days, and late rate.
    """
    required = {
        "order_id",
        "warehouse_id",
        "order_date",
        "processing_days",
        "is_late",
        "warehouse_load",
    }
    missing = required - set(order_features.columns)
    if missing:
        raise ValueError(
            f"Order features are missing columns: {', '.join(sorted(missing))}"
        )
    if not 0 < contamination <= 0.5:
        raise ValueError("contamination must be greater than 0 and at most 0.5.")
    if z_threshold <= 0:
        raise ValueError("z_threshold must be positive.")

    features = order_features.copy()
    features["order_date"] = pd.to_datetime(features["order_date"])
    processing = pd.to_numeric(features["processing_days"], errors="coerce")
    valid_processing = processing.dropna()
    if valid_processing.empty:
        iqr_outlier = pd.Series(False, index=features.index)
        z_scores = pd.Series(np.nan, index=features.index, dtype=float)
    else:
        first_quartile = valid_processing.quantile(0.25)
        third_quartile = valid_processing.quantile(0.75)
        spread = third_quartile - first_quartile
        lower_fence = first_quartile - 1.5 * spread
        upper_fence = third_quartile + 1.5 * spread
        iqr_outlier = processing.lt(lower_fence) | processing.gt(upper_fence)
        standard_deviation = valid_processing.std(ddof=0)
        if standard_deviation == 0:
            z_scores = pd.Series(0.0, index=features.index)
        else:
            z_scores = (processing - valid_processing.mean()) / standard_deviation
        z_outlier = z_scores.abs().gt(z_threshold)
        iqr_outlier = iqr_outlier.fillna(False)

    z_outlier = z_scores.abs().gt(z_threshold).fillna(False)
    order_flags = iqr_outlier | z_outlier
    order_anomalies = pd.DataFrame(
        {
            "anomaly_type": "order_processing_time",
            "order_id": features["order_id"],
            "warehouse_id": features["warehouse_id"],
            "date": features["order_date"].dt.date,
            "processing_days": processing,
            "warehouse_load": features["warehouse_load"],
            "late_rate": features["is_late"].astype(float),
            "iqr_flag": iqr_outlier.astype(bool),
            "z_score_flag": z_outlier.astype(bool),
            "isolation_forest_flag": False,
            "anomaly_score": z_scores,
        }
    ).loc[order_flags]

    daily = (
        features.assign(
            date=features["order_date"].dt.date,
            processing_days=processing,
            late_rate=features["is_late"].astype(float),
        )
        .groupby(["warehouse_id", "date"], as_index=False)
        .agg(
            warehouse_load=("order_id", "count"),
            processing_days=("processing_days", "mean"),
            late_rate=("late_rate", "mean"),
        )
    )

    if len(daily) >= 5:
        model = IsolationForest(
            contamination=contamination,
            random_state=random_state,
            n_estimators=100,
        )
        model_features = daily[
            ["warehouse_load", "processing_days", "late_rate"]
        ].replace([np.inf, -np.inf], np.nan)
        valid_rows = model_features.notna().all(axis=1)
        daily["isolation_forest_flag"] = False
        daily["anomaly_score"] = np.nan
        if int(valid_rows.sum()) >= 5:
            valid_features = model_features.loc[valid_rows]
            daily.loc[valid_rows, "isolation_forest_flag"] = (
                model.fit_predict(valid_features) == -1
            )
            daily.loc[valid_rows, "anomaly_score"] = model.score_samples(
                valid_features
            )
        daily_anomalies = daily.loc[daily["isolation_forest_flag"]].copy()
    else:
        daily_anomalies = daily.iloc[0:0].copy()
        daily_anomalies["isolation_forest_flag"] = pd.Series(dtype=bool)
        daily_anomalies["anomaly_score"] = pd.Series(dtype=float)

    daily_anomalies.insert(0, "anomaly_type", "warehouse_day")
    daily_anomalies["order_id"] = None
    daily_anomalies["iqr_flag"] = False
    daily_anomalies["z_score_flag"] = False
    output_columns = list(ANOMALY_COLUMNS)
    anomalies = pd.concat(
        [
            order_anomalies.reindex(columns=output_columns),
            daily_anomalies.reindex(columns=output_columns),
        ],
        ignore_index=True,
    )
    return anomalies
