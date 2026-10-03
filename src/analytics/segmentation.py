"""Cluster customers using scaled RFM features and a transparent churn score."""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler


RFM_COLUMNS = ("recency_days", "frequency", "monetary")
SEGMENT_LABELS = ("Champions", "Loyal", "At Risk", "Lost")


def build_customer_rfm(
    customers: pd.DataFrame,
    orders: pd.DataFrame,
    order_items: pd.DataFrame,
) -> pd.DataFrame:
    """Aggregate fulfilled-order RFM measures for every customer."""
    required_customer = {"customer_id", "customer_name"}
    required_orders = {"order_id", "customer_id", "order_date", "order_status"}
    required_items = {"order_id", "quantity", "unit_price"}
    for label, frame, required in (
        ("customers", customers, required_customer),
        ("orders", orders, required_orders),
        ("order_items", order_items, required_items),
    ):
        missing = required - set(frame.columns)
        if missing:
            raise ValueError(
                f"{label} is missing columns: {', '.join(sorted(missing))}"
            )

    fulfilled = orders.loc[orders["order_status"].eq("fulfilled")].copy()
    fulfilled["order_date"] = pd.to_datetime(fulfilled["order_date"])
    order_value = (
        order_items.assign(
            line_value=pd.to_numeric(order_items["quantity"], errors="coerce")
            * pd.to_numeric(order_items["unit_price"], errors="coerce")
        )
        .groupby("order_id", as_index=False)["line_value"]
        .sum()
    )
    fulfilled = fulfilled.merge(
        order_value, on="order_id", how="left", validate="one_to_one"
    )
    latest_order_date = fulfilled["order_date"].max()
    if pd.isna(latest_order_date):
        rfm = pd.DataFrame(
            columns=["customer_id", *RFM_COLUMNS]
        )
    else:
        rfm = (
            fulfilled.groupby("customer_id", as_index=False)
            .agg(
                last_order_date=("order_date", "max"),
                frequency=("order_id", "nunique"),
                monetary=("line_value", "sum"),
            )
        )
        rfm["recency_days"] = (
            latest_order_date - rfm["last_order_date"]
        ).dt.total_seconds() / 86_400
        rfm = rfm.drop(columns="last_order_date")

    result = customers[["customer_id", "customer_name"]].merge(
        rfm, on="customer_id", how="left", validate="one_to_one"
    )
    result["recency_days"] = result["recency_days"].fillna(99_999)
    result["frequency"] = result["frequency"].fillna(0).astype(int)
    result["monetary"] = result["monetary"].fillna(0.0)
    return result


def _frequency_decline(
    orders: pd.DataFrame,
    customer_ids: pd.Series,
) -> pd.Series:
    """Compare early-period and recent average monthly order frequency."""
    dates = pd.to_datetime(orders["order_date"], errors="coerce")
    months = dates.dt.to_period("M")
    available_months = pd.PeriodIndex(months.dropna().unique()).sort_values()
    if len(available_months) == 0:
        return pd.Series(0.0, index=customer_ids.index)

    month_count = len(available_months)
    split = max(1, month_count // 2)
    early_months = set(available_months[:split])
    recent_months = set(available_months[split:])
    if not recent_months:
        recent_months = early_months

    frame = pd.DataFrame(
        {"customer_id": orders["customer_id"], "month": months}
    ).dropna(subset=["month"])
    early_counts = (
        frame.loc[frame["month"].isin(early_months)]
        .groupby("customer_id")
        .size()
    )
    recent_counts = (
        frame.loc[frame["month"].isin(recent_months)]
        .groupby("customer_id")
        .size()
    )
    early_avg = early_counts / len(early_months)
    recent_avg = recent_counts / len(recent_months)
    decline = (early_avg - recent_avg).clip(lower=0) / early_avg.clip(lower=1)
    return (
        customer_ids.map(decline).fillna(0).clip(lower=0, upper=1).astype(float)
    )


def segment_customers(
    customer_rfm: pd.DataFrame,
    orders: pd.DataFrame,
    support_tickets: pd.DataFrame,
    *,
    n_clusters: int = 4,
    random_state: int = 42,
) -> pd.DataFrame:
    """Scale RFM, assign K-Means clusters/labels, and score churn risk.

    Churn risk is 60% relative early-to-late order-frequency decline and 40%
    percentile rank of a customer's total ticket count.
    """
    required_rfm = {"customer_id", "customer_name", *RFM_COLUMNS}
    missing_rfm = required_rfm - set(customer_rfm.columns)
    if missing_rfm:
        raise ValueError(
            f"Customer RFM is missing columns: {', '.join(sorted(missing_rfm))}"
        )
    required_orders = {"customer_id", "order_date"}
    missing_orders = required_orders - set(orders.columns)
    if missing_orders:
        raise ValueError(
            f"Orders are missing columns: {', '.join(sorted(missing_orders))}"
        )
    required_tickets = {"customer_id"}
    missing_tickets = required_tickets - set(support_tickets.columns)
    if missing_tickets:
        raise ValueError(
            "Support tickets are missing columns: "
            + ", ".join(sorted(missing_tickets))
        )
    if n_clusters < 1:
        raise ValueError("n_clusters must be at least 1.")
    if customer_rfm["customer_id"].duplicated().any():
        raise ValueError("Customer RFM must contain one row per customer.")

    result = customer_rfm.copy().reset_index(drop=True)
    numeric_rfm = result.loc[:, RFM_COLUMNS].apply(pd.to_numeric, errors="coerce")
    if numeric_rfm.isna().any().any():
        raise ValueError("RFM feature values must be numeric and non-null.")
    cluster_features = numeric_rfm.copy()
    cluster_features["recency_days"] *= -1
    scaler = StandardScaler()
    scaled = scaler.fit_transform(cluster_features)
    distinct_count = np.unique(np.round(scaled, decimals=12), axis=0).shape[0]
    cluster_count = min(n_clusters, len(result), distinct_count)

    if cluster_count == 0:
        result["cluster_id"] = pd.Series(dtype="int64")
        result["segment_label"] = pd.Series(dtype="object")
    else:
        model = KMeans(
            n_clusters=cluster_count,
            random_state=random_state,
            n_init=10,
        )
        cluster_ids = model.fit_predict(scaled)
        result["cluster_id"] = cluster_ids
        result["segment_label"] = _label_clusters(
            cluster_ids, numeric_rfm, cluster_count
        )

    decline_score = _frequency_decline(orders, result["customer_id"])
    ticket_counts = support_tickets.groupby("customer_id").size()
    result["ticket_count"] = (
        result["customer_id"].map(ticket_counts).fillna(0).astype(int)
    )
    result["frequency_decline_score"] = decline_score
    if len(result):
        ticket_percentile = result["ticket_count"].rank(method="average", pct=True)
    else:
        ticket_percentile = pd.Series(dtype=float)
    result["churn_risk_score"] = (
        0.6 * result["frequency_decline_score"] + 0.4 * ticket_percentile
    ).clip(lower=0, upper=1)
    return result


def _label_clusters(
    cluster_ids: np.ndarray,
    rfm: pd.DataFrame,
    cluster_count: int,
) -> pd.Series:
    """Assign descriptive labels based on standardized cluster profiles."""
    cluster_means = rfm.assign(cluster_id=cluster_ids).groupby("cluster_id").mean()
    scaled_means = StandardScaler().fit_transform(cluster_means)
    profile = pd.DataFrame(
        scaled_means,
        index=cluster_means.index,
        columns=RFM_COLUMNS,
    )

    champions_score = (
        -profile["recency_days"]
        + profile["frequency"]
        + profile["monetary"]
    )
    lost_score = (
        profile["recency_days"]
        - profile["frequency"]
        - profile["monetary"]
    )
    label_map: dict[int, str] = {}
    champions_id = int(champions_score.idxmax())
    label_map[champions_id] = "Champions"

    remaining = [int(value) for value in profile.index if int(value) != champions_id]
    if cluster_count > 1:
        lost_id = int(lost_score.loc[remaining].idxmax())
        label_map[lost_id] = "Lost"
        remaining.remove(lost_id)
    if remaining:
        loyal_score = (
            profile.loc[remaining, "frequency"]
            - profile.loc[remaining, "recency_days"]
        )
        label_map[int(loyal_score.idxmax())] = "Loyal"
        remaining.remove(int(loyal_score.idxmax()))
    for cluster_id in remaining:
        label_map[cluster_id] = "At Risk"
    return pd.Series(cluster_ids, index=rfm.index).map(label_map)
