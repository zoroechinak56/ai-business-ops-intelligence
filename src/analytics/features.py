"""Engineer order-grain operational features from relational source frames."""

from __future__ import annotations

import pandas as pd


def build_order_features(
    orders: pd.DataFrame, deliveries: pd.DataFrame
) -> pd.DataFrame:
    """Return one operational feature row per order.

    ``lead_time_gap`` is actual delivered date minus promised delivery date in
    days (positive means late). ``warehouse_load`` counts orders assigned to a
    warehouse on the same calendar date.
    """
    required_order_columns = {
        "order_id",
        "warehouse_id",
        "order_date",
    }
    required_delivery_columns = {
        "order_id",
        "supplier_id",
        "shipped_date",
        "delivered_date",
        "promised_delivery_date",
        "supplier_late",
    }
    missing_orders = required_order_columns - set(orders.columns)
    missing_deliveries = required_delivery_columns - set(deliveries.columns)
    if missing_orders:
        raise ValueError(
            f"Orders are missing columns: {', '.join(sorted(missing_orders))}"
        )
    if missing_deliveries:
        raise ValueError(
            "Deliveries are missing columns: "
            + ", ".join(sorted(missing_deliveries))
        )
    if orders["order_id"].duplicated().any():
        raise ValueError("Orders must contain at most one row per order_id.")

    order_frame = orders.copy()
    delivery_frame = deliveries.copy()
    order_frame["order_date"] = pd.to_datetime(order_frame["order_date"])
    for column in ("shipped_date", "delivered_date", "promised_delivery_date"):
        delivery_frame[column] = pd.to_datetime(delivery_frame[column])

    delivery_summary = (
        delivery_frame.sort_values(["order_id", "shipped_date"])
        .drop_duplicates("order_id", keep="first")
        .loc[
            :,
            [
                "order_id",
                "supplier_id",
                "shipped_date",
                "delivered_date",
                "promised_delivery_date",
                "supplier_late",
            ],
        ]
    )
    features = order_frame.merge(
        delivery_summary, on="order_id", how="left", validate="one_to_one"
    )

    supplier_rates = delivery_frame.groupby("supplier_id", dropna=False)[
        "supplier_late"
    ].mean()
    features["supplier_late_rate"] = features["supplier_id"].map(supplier_rates)

    features["processing_days"] = (
        features["shipped_date"] - features["order_date"]
    ).dt.total_seconds() / 86_400
    features["is_late"] = (
        features["delivered_date"].isna()
        | features["delivered_date"].gt(features["promised_delivery_date"])
    )
    features["lead_time_gap"] = (
        features["delivered_date"] - features["promised_delivery_date"]
    ).dt.total_seconds() / 86_400
    features["weekday"] = features["order_date"].dt.weekday + 1
    features["month"] = features["order_date"].dt.month

    warehouse_daily_load = (
        order_frame.groupby(
            ["warehouse_id", order_frame["order_date"].dt.normalize()]
        )["order_id"]
        .count()
        .rename("warehouse_load")
        .reset_index()
        .rename(columns={"order_date": "load_date"})
    )
    features["_load_date"] = features["order_date"].dt.normalize()
    features = features.merge(
        warehouse_daily_load,
        left_on=["warehouse_id", "_load_date"],
        right_on=["warehouse_id", "load_date"],
        how="left",
        validate="many_to_one",
    ).drop(columns=["_load_date", "load_date"])

    return features
