"""Tests for Python analytics using small in-memory DataFrames."""

from datetime import date, timedelta

import pandas as pd
from sqlalchemy import create_engine, text

from src.analytics.anomaly_detection import detect_anomalies
from src.analytics.features import build_order_features
from src.analytics.run_analytics import run_analytics
from src.analytics.segmentation import build_customer_rfm, segment_customers
from src.analytics.statistics import run_statistical_tests


def _source_frames() -> dict[str, pd.DataFrame]:
    """Build six months of compact but relational operational test data."""
    orders: list[dict[str, object]] = []
    deliveries: list[dict[str, object]] = []
    order_items: list[dict[str, object]] = []
    warehouses = ("WH-A", "WH-B")
    suppliers = ("SUP-1", "SUP-2")

    for month in range(6):
        for day_index in range(1, 5):
            order_number = len(orders) + 1
            warehouse_id = warehouses[order_number % 2]
            supplier_id = suppliers[order_number % 2]
            order_date = date(2025, month + 1, day_index)
            processing_days = 12 if order_number == 1 else (
                4 if warehouse_id == "WH-A" else 1
            )
            fulfilled = order_number != 1
            order_id = f"ORD-{order_number:03d}"
            orders.append(
                {
                    "order_id": order_id,
                    "customer_id": f"CUST-{(order_number % 4) + 1}",
                    "warehouse_id": warehouse_id,
                    "employee_id": "EMP-1",
                    "order_date": order_date.isoformat(),
                    "order_notes": "test",
                    "order_status": "fulfilled" if fulfilled else "unfulfilled",
                }
            )
            shipped_date = order_date + timedelta(days=processing_days)
            delivered_date = shipped_date + timedelta(days=2)
            deliveries.append(
                {
                    "delivery_id": f"DEL-{order_number:03d}",
                    "order_id": order_id,
                    "supplier_id": supplier_id,
                    "warehouse_id": warehouse_id,
                    "shipped_date": shipped_date.isoformat(),
                    "delivered_date": delivered_date.isoformat(),
                    "promised_delivery_date": (
                        shipped_date + timedelta(days=1)
                    ).isoformat(),
                    "supplier_late": order_number % 3 == 0,
                    "delivery_status": "delivered",
                }
            )
            order_items.append(
                {
                    "order_item_id": f"ITEM-{order_number:03d}",
                    "order_id": order_id,
                    "product_id": "SKU-1",
                    "quantity": 2,
                    "unit_price": 10.0,
                    "discount_code": "NONE",
                }
            )

    customers = pd.DataFrame(
        {
            "customer_id": [f"CUST-{index}" for index in range(1, 5)],
            "customer_name": [f"Customer {index}" for index in range(1, 5)],
            "email": [f"customer{index}@example.com" for index in range(1, 5)],
            "phone": ["Unknown"] * 4,
            "city": ["Example City"] * 4,
            "state": ["CA"] * 4,
            "signup_date": ["2024-01-01"] * 4,
            "customer_segment": ["consumer"] * 4,
        }
    )
    support_tickets = pd.DataFrame(
        {
            "ticket_id": ["TKT-1", "TKT-2", "TKT-3"],
            "customer_id": ["CUST-1", "CUST-1", "CUST-2"],
            "ticket_date": ["2025-05-01", "2025-06-01", "2025-03-01"],
            "issue_type": ["Late delivery", "Late delivery", "Return"],
            "priority": ["high", "medium", "low"],
            "resolution_status": ["open", "resolved", "resolved"],
        }
    )
    return {
        "orders": pd.DataFrame(orders),
        "deliveries": pd.DataFrame(deliveries),
        "order_items": pd.DataFrame(order_items),
        "customers": customers,
        "support_tickets": support_tickets,
    }


def test_order_features_compute_processing_delivery_and_load_fields() -> None:
    """Each order gets date features, supplier reliability, and warehouse load."""
    frames = _source_frames()
    features = build_order_features(frames["orders"], frames["deliveries"])

    assert len(features) == len(frames["orders"])
    outlier = features.loc[features["order_id"].eq("ORD-001")].iloc[0]
    assert outlier["processing_days"] == 12
    assert outlier["is_late"]
    assert outlier["lead_time_gap"] == 1
    assert outlier["weekday"] == 3
    assert outlier["month"] == 1
    assert outlier["warehouse_load"] == 1
    assert features["supplier_late_rate"].between(0, 1).all()


def test_anomaly_detection_flags_processing_time_and_warehouse_day() -> None:
    """IQR/z-score and Isolation Forest anomalies are emitted in one output."""
    frames = _source_frames()
    features = build_order_features(frames["orders"], frames["deliveries"])
    anomalies = detect_anomalies(features, contamination=0.15)

    assert {"order_processing_time", "warehouse_day"}.issuperset(
        set(anomalies["anomaly_type"])
    )
    order_flags = anomalies.loc[
        anomalies["anomaly_type"].eq("order_processing_time")
    ]
    assert not order_flags.empty
    assert order_flags["order_id"].notna().all()
    assert order_flags["iqr_flag"].any() or order_flags["z_score_flag"].any()


def test_customer_rfm_segments_and_churn_score_are_computed() -> None:
    """K-Means labels customers and churn risk responds to decline and tickets."""
    frames = _source_frames()
    rfm = build_customer_rfm(
        frames["customers"], frames["orders"], frames["order_items"]
    )
    segments = segment_customers(
        rfm,
        frames["orders"],
        frames["support_tickets"],
        n_clusters=3,
    )

    assert len(segments) == 4
    assert segments["segment_label"].isin(
        {"Champions", "Loyal", "At Risk", "Lost"}
    ).all()
    assert segments["churn_risk_score"].between(0, 1).all()
    assert segments["cluster_id"].nunique() <= 3
    assert segments["ticket_count"].sum() == len(frames["support_tickets"])


def test_statistical_tests_return_expected_effect_sizes() -> None:
    """Mann-Whitney and chi-square tests include p-values and bounded effects."""
    features = pd.DataFrame(
        {
            "warehouse_id": ["WH-A"] * 4 + ["WH-B"] * 4,
            "processing_days": [10, 11, 12, 13, 1, 2, 3, 4],
        }
    )
    deliveries = pd.DataFrame(
        {
            "supplier_id": ["SUP-1"] * 4 + ["SUP-2"] * 4,
            "supplier_late": [True, True, True, False, False, False, False, True],
        }
    )

    results = run_statistical_tests(features, deliveries).set_index("test_name")

    warehouse_test = results.loc["warehouse_a_vs_other_processing_days"]
    assert warehouse_test["p_value"] < 0.1
    assert warehouse_test["effect_size"] == 1
    assert warehouse_test["effect_measure"] == "cliffs_delta"

    supplier_test = results.loc["supplier_vs_late_delivery"]
    assert 0 <= supplier_test["p_value"] <= 1
    assert 0 <= supplier_test["effect_size"] <= 1
    assert supplier_test["effect_measure"] == "cramers_v"


def test_analytics_runner_persists_all_output_tables() -> None:
    """The end-to-end analytics runner reads and writes SQLite tables."""
    engine = create_engine("sqlite:///:memory:")
    frames = _source_frames()
    try:
        with engine.begin() as connection:
            for table_name, frame in frames.items():
                frame.to_sql(table_name, connection, index=False)

        outputs = run_analytics(engine)

        with engine.connect() as connection:
            for table_name, frame in outputs.items():
                stored_count = connection.execute(
                    text(f"SELECT COUNT(*) FROM {table_name}")
                ).scalar_one()
                assert stored_count == len(frame)
        assert set(outputs) == {
            "anomalies",
            "customer_segments",
            "stat_tests",
            "order_features",
        }
    finally:
        engine.dispose()
