"""Statistical comparisons for warehouse processing and supplier reliability."""

from __future__ import annotations

import math

import pandas as pd
from scipy.stats import chi2_contingency, mannwhitneyu


STAT_TEST_COLUMNS = (
    "test_name",
    "statistic",
    "p_value",
    "effect_size",
    "effect_measure",
    "sample_size",
    "group_a",
    "group_a_n",
    "group_b",
    "group_b_n",
)


def run_statistical_tests(
    order_features: pd.DataFrame,
    deliveries: pd.DataFrame,
) -> pd.DataFrame:
    """Compare Warehouse A processing time and supplier late-delivery rates.

    Warehouse effect size is Cliff's delta (equivalent to rank-biserial
    correlation for the Mann-Whitney statistic), positive when Warehouse A
    tends to have longer processing times. Supplier effect size is Cramer's V.
    """
    required_features = {"warehouse_id", "processing_days"}
    missing_features = required_features - set(order_features.columns)
    if missing_features:
        raise ValueError(
            "Order features are missing columns: "
            + ", ".join(sorted(missing_features))
        )
    required_deliveries = {"supplier_id", "supplier_late"}
    missing_deliveries = required_deliveries - set(deliveries.columns)
    if missing_deliveries:
        raise ValueError(
            "Deliveries are missing columns: "
            + ", ".join(sorted(missing_deliveries))
        )

    warehouse = order_features.loc[
        :, ["warehouse_id", "processing_days"]
    ].copy()
    warehouse["processing_days"] = pd.to_numeric(
        warehouse["processing_days"], errors="coerce"
    )
    warehouse = warehouse.dropna(subset=["warehouse_id", "processing_days"])
    warehouse_a = warehouse.loc[
        warehouse["warehouse_id"].eq("WH-A"), "processing_days"
    ].to_numpy()
    other_warehouses = warehouse.loc[
        ~warehouse["warehouse_id"].eq("WH-A"), "processing_days"
    ].to_numpy()
    if not len(warehouse_a) or not len(other_warehouses):
        raise ValueError(
            "Mann-Whitney comparison requires Warehouse A and other warehouses."
        )

    mann_whitney = mannwhitneyu(
        warehouse_a, other_warehouses, alternative="two-sided", method="auto"
    )
    cliff_delta = 2 * float(mann_whitney.statistic) / (
        len(warehouse_a) * len(other_warehouses)
    ) - 1
    warehouse_result = {
        "test_name": "warehouse_a_vs_other_processing_days",
        "statistic": float(mann_whitney.statistic),
        "p_value": float(mann_whitney.pvalue),
        "effect_size": cliff_delta,
        "effect_measure": "cliffs_delta",
        "sample_size": len(warehouse_a) + len(other_warehouses),
        "group_a": "WH-A",
        "group_a_n": len(warehouse_a),
        "group_b": "other_warehouses",
        "group_b_n": len(other_warehouses),
    }

    supplier_data = deliveries.loc[
        :, ["supplier_id", "supplier_late"]
    ].dropna()
    contingency = pd.crosstab(
        supplier_data["supplier_id"], supplier_data["supplier_late"]
    )
    if contingency.shape[0] < 2 or contingency.shape[1] < 2:
        raise ValueError(
            "Supplier chi-square comparison requires multiple suppliers "
            "and both late/on-time observations."
        )
    chi_square, p_value, _, _ = chi2_contingency(contingency, correction=False)
    total_observations = int(contingency.to_numpy().sum())
    dimension_count = min(contingency.shape[0] - 1, contingency.shape[1] - 1)
    cramer_v = math.sqrt(chi_square / (total_observations * dimension_count))
    supplier_result = {
        "test_name": "supplier_vs_late_delivery",
        "statistic": float(chi_square),
        "p_value": float(p_value),
        "effect_size": cramer_v,
        "effect_measure": "cramers_v",
        "sample_size": total_observations,
        "group_a": "all_suppliers",
        "group_a_n": contingency.shape[0],
        "group_b": "late_status",
        "group_b_n": contingency.shape[1],
    }

    return pd.DataFrame(
        [warehouse_result, supplier_result],
        columns=STAT_TEST_COLUMNS,
    )
