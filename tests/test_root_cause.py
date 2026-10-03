"""Tests for fulfilment root-cause analysis using small in-memory DataFrames."""

import json
from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import create_engine

from src.analytics import root_cause
from src.analytics.root_cause import (
    _dimension_factors,
    decompose_fulfilment,
    explain_kpi_change,
    prepare_order_facts,
    save_result,
)


def _tables() -> dict[str, pd.DataFrame]:
    """Build two monthly samples with known warehouse/supplier/group failures."""
    orders: list[dict[str, object]] = []
    deliveries: list[dict[str, object]] = []
    order_items: list[dict[str, object]] = []
    assignments = (
        ("WH-A", "SUP-X", "Y", "North"),
        ("WH-B", "SUP-Y", "A", "South"),
    )
    order_number = 0
    for month, failure_count in ((5, 1), (6, 5)):
        for index in range(10):
            order_number += 1
            warehouse_id, supplier_id, sku_group, _ = assignments[index % 2]
            order_id = f"ORD-{order_number:03d}"
            order_date = f"2025-{month:02d}-{index + 1:02d}"
            fulfilled = index >= failure_count
            orders.append(
                {
                    "order_id": order_id,
                    "warehouse_id": warehouse_id,
                    "order_date": order_date,
                    "order_status": "fulfilled" if fulfilled else "unfulfilled",
                }
            )
            deliveries.append(
                {
                    "delivery_id": f"DEL-{order_number:03d}",
                    "order_id": order_id,
                    "supplier_id": supplier_id,
                }
            )
            order_items.append(
                {
                    "order_item_id": f"ITEM-{order_number:03d}",
                    "order_id": order_id,
                    "product_id": f"SKU-{sku_group}",
                }
            )

    return {
        "orders": pd.DataFrame(orders),
        "deliveries": pd.DataFrame(deliveries),
        "order_items": pd.DataFrame(order_items),
        "products": pd.DataFrame(
            {
                "product_id": ["SKU-Y", "SKU-A"],
                "sku_group": ["Y", "A"],
            }
        ),
        "warehouses": pd.DataFrame(
            {
                "warehouse_id": ["WH-A", "WH-B"],
                "region": ["North", "South"],
            }
        ),
    }


def test_prepare_order_facts_assigns_exclusive_dimension_segments() -> None:
    """Order fact preparation produces one row and four dimensions per order."""
    facts = prepare_order_facts(_tables())

    assert len(facts) == 20
    assert facts["order_id"].is_unique
    assert set(("warehouse", "supplier", "sku_group", "region")).issubset(facts.columns)
    assert facts["sku_group"].value_counts().to_dict() == {"A": 10, "Y": 10}


def test_decomposition_reconciles_to_total_fulfilment_change() -> None:
    """Segment contribution sums reconcile within every mutually exclusive dimension."""
    result = decompose_fulfilment(_tables(), "2025-05", "2025-06")

    assert result["kpi"] == "fulfilment"
    assert result["value_a"] == pytest.approx(0.9)
    assert result["value_b"] == pytest.approx(0.5)
    assert result["change"] == pytest.approx(-0.4)
    assert len(result["top_factors"]) <= 9
    dimensions = {"warehouse", "supplier", "sku_group"}
    assert dimensions == {factor["dimension"] for factor in result["top_factors"]}
    assert all(
        "rate_vs_overall" in factor
        and "share_of_unfulfilled" in factor
        and factor["share_of_unfulfilled"] == factor["share_of_delayed"]
        for factor in result["top_factors"]
    )
    for dimension in dimensions:
        rows = [
            factor
            for factor in result["top_factors"]
            if factor["dimension"] == dimension
        ]
        assert sum(factor["contribution_pp"] for factor in rows) == pytest.approx(
            result["change"] * 100
        )
        assert sum(factor["share_of_drop"] for factor in rows) == pytest.approx(
            100
        )
    assert all(0 <= factor["p_value"] <= 1 for factor in result["top_factors"])
    assert all(
        0 <= factor["share_of_unfulfilled"] <= 1
        for factor in result["top_factors"]
    )
    assert result["top_factors"][0]["dimension_rank"] >= 1
    assert result["top_factors"][0]["overall_rank"] == 1


def test_factors_rank_by_volume_weighted_contribution() -> None:
    """A smaller raw rate decline can rank below a weighted mix effect."""
    rows: list[dict[str, object]] = []
    for month in (5, 6):
        segments = (
            (("X", 90, 81), ("Y", 10, 9))
            if month == 5
            else (("X", 10, 10), ("Y", 90, 54))
        )
        for segment, order_count, fulfilled_count in segments:
            for index in range(order_count):
                rows.append(
                    {
                        "order_date": pd.Timestamp(
                            year=2025, month=month, day=index % 28 + 1
                        ),
                        "fulfilled": index < fulfilled_count,
                        "warehouse": segment,
                    }
                )
    facts = pd.DataFrame(rows)

    factors = _dimension_factors(
        facts,
        "warehouse",
        pd.Period("2025-05", freq="M"),
        pd.Period("2025-06", freq="M"),
        value_a=0.9,
        value_b=0.64,
    )

    assert factors[0]["segment"] == "X"
    assert factors[0]["rate_b"] - factors[0]["rate_a"] > 0
    assert factors[0]["contribution_pp"] < factors[1]["contribution_pp"]
    assert sum(factor["contribution_pp"] for factor in factors) == pytest.approx(
        -26
    )
    assert factors[0]["rate_vs_overall"] == pytest.approx(0.36)


def test_decomposition_validates_periods_and_supported_kpi() -> None:
    """Unsupported KPIs, malformed periods, and absent data fail clearly."""
    with pytest.raises(ValueError, match="Only the fulfilment"):
        explain_kpi_change("revenue", "2025-05", "2025-06")
    with pytest.raises(ValueError, match="expected YYYY-MM"):
        decompose_fulfilment(_tables(), "May 2025", "2025-06")
    with pytest.raises(ValueError, match="different months"):
        decompose_fulfilment(_tables(), "2025-05", "2025-05")


def test_explain_kpi_change_reads_sqlite_and_saves_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The public API reads its database and serializes the structured result."""
    database_path = tmp_path / "root_cause.sqlite"
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        with engine.begin() as connection:
            for table_name, frame in _tables().items():
                frame.to_sql(table_name, connection, index=False)
    finally:
        engine.dispose()
    monkeypatch.setattr(
        root_cause,
        "settings",
        replace(
            root_cause.settings,
            database_url=f"sqlite:///{database_path.as_posix()}",
        ),
    )

    result = explain_kpi_change("fulfilment", "2025-05", "2025-06")
    report_path = tmp_path / "root_cause_latest.json"
    save_result(result, report_path)
    saved = json.loads(report_path.read_text(encoding="utf-8"))

    assert saved["period_a"] == "2025-05"
    assert saved["period_b"] == "2025-06"
    assert saved["top_factors"]
