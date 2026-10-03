"""Tests for source-data validation using small in-memory DataFrames."""

import json
from pathlib import Path

import pandas as pd

from src.validation.validator import validate_csv_directory, validate_tables, write_report


def _valid_product_tables() -> dict[str, pd.DataFrame]:
    """Return small valid product and supplier frames for isolated checks."""
    return {
        "suppliers": pd.DataFrame(
            {
                "supplier_id": ["SUP-001"],
                "supplier_name": ["Supplier X"],
                "contact_email": ["contact@example.com"],
                "country": ["US"],
                "supplier_tier": ["preferred"],
            }
        ),
        "products": pd.DataFrame(
            {
                "product_id": ["SKU-0001", "SKU-0002"],
                "product_name": ["Widget One", "Widget Two"],
                "sku": ["SKU-0001", "SKU-0002"],
                "sku_group": ["A", "Y"],
                "category": ["Home", "Electronics"],
                "supplier_id": ["SUP-001", "SUP-001"],
                "unit_price": [10.0, 20.0],
                "description": ["First product", "Second product"],
            }
        ),
    }


def _issues_for(report: dict[str, object], table: str) -> dict[str, dict[str, object]]:
    """Index one table's issues by their stable issue code."""
    tables = report["tables"]
    assert isinstance(tables, dict)
    table_report = tables[table]
    assert isinstance(table_report, dict)
    issues = table_report["issues"]
    assert isinstance(issues, list)
    return {str(issue["code"]): issue for issue in issues}


def test_detects_null_duplicate_type_range_fk_and_casing_issues() -> None:
    """Invalid product records are classified without modifying their input."""
    tables = _valid_product_tables()
    products = tables["products"]
    products.loc[0, "product_id"] = "SKU-0002"
    products.loc[0, "product_name"] = 42
    products.loc[0, "supplier_id"] = None
    products.loc[1, "supplier_id"] = "SUP-MISSING"
    products.loc[0, "unit_price"] = -1.0
    products.loc[1, "category"] = "hOME"
    products.loc[0, "description"] = None

    report = validate_tables(tables)
    issues = _issues_for(report, "products")

    assert issues["null_required"]["severity"] == "reject"
    assert issues["duplicate_primary_key"]["severity"] == "reject"
    assert issues["invalid_type"]["severity"] == "reject"
    assert issues["foreign_key_not_found"]["severity"] == "reject"
    assert issues["negative_value"]["severity"] == "fix"
    assert issues["inconsistent_category_casing"]["severity"] == "fix"
    assert issues["null_optional"]["severity"] == "warn"
    assert issues["duplicate_primary_key"]["sample_rows"] == [1, 2]
    assert products.loc[0, "unit_price"] == -1.0
    assert products.loc[1, "category"] == "hOME"


def test_missing_required_columns_are_rejects() -> None:
    """A missing source column is reported as a structural rejection."""
    report = validate_tables({"products": pd.DataFrame({"product_id": ["SKU-1"]})})
    issues = _issues_for(report, "products")

    assert issues["missing_column"]["severity"] == "reject"
    assert issues["missing_column"]["count"] == 7


def test_delivery_date_order_and_foreign_keys_are_reported() -> None:
    """Delivery temporal and relationship constraints are checked together."""
    tables = {
        "orders": pd.DataFrame(
            {
                "order_id": ["ORD-1"],
                "customer_id": ["CUST-1"],
                "warehouse_id": ["WH-A"],
                "employee_id": ["EMP-1"],
                "order_date": ["2025-01-01"],
                "order_notes": [None],
                "order_status": ["fulfilled"],
            }
        ),
        "suppliers": _valid_product_tables()["suppliers"],
        "customers": pd.DataFrame(
            {
                "customer_id": ["CUST-1"],
                "customer_name": ["Example Customer"],
                "email": ["customer@example.com"],
                "phone": [None],
                "city": ["Example City"],
                "state": ["CA"],
                "signup_date": ["2024-01-01"],
                "customer_segment": ["consumer"],
            }
        ),
        "employees": pd.DataFrame(
            {
                "employee_id": ["EMP-1"],
                "employee_name": ["Example Employee"],
                "email": ["employee@example.com"],
                "phone": [None],
                "role": ["picker"],
                "hire_date": ["2020-01-01"],
            }
        ),
        "warehouses": pd.DataFrame(
            {
                "warehouse_id": ["WH-A"],
                "warehouse_name": ["Warehouse A"],
                "city": ["Example City"],
                "region": ["Northeast"],
                "capacity_units": [100],
            }
        ),
        "deliveries": pd.DataFrame(
            {
                "delivery_id": ["DEL-1", "DEL-2"],
                "order_id": ["ORD-1", "ORD-MISSING"],
                "supplier_id": ["SUP-001", "SUP-001"],
                "warehouse_id": ["WH-A", "WH-A"],
                "shipped_date": ["2025-01-03", "2025-01-03"],
                "delivered_date": ["2025-01-02", None],
                "promised_delivery_date": ["2025-01-10", "2025-01-10"],
                "supplier_late": [False, True],
                "delivery_status": ["delivered", "delivered"],
                "tracking_number": ["TRK-1", "TRK-2"],
            }
        ),
    }
    tables["orders"].loc[0, "customer_id"] = "CUST-MISSING"

    report = validate_tables(tables)
    delivery_issues = _issues_for(report, "deliveries")
    order_issues = _issues_for(report, "orders")

    assert delivery_issues["delivered_before_shipped"]["severity"] == "fix"
    assert delivery_issues["delivered_before_shipped"]["count"] == 1
    assert delivery_issues["foreign_key_not_found"]["severity"] == "reject"
    assert "null_optional" not in delivery_issues
    assert order_issues["foreign_key_not_found"]["count"] == 1


def test_csv_directory_report_writes_json_and_reports_missing_files(
    tmp_path: Path,
) -> None:
    """The file-based entry point reports absent files and saves JSON output."""
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    pd.DataFrame({"product_id": ["SKU-1"]}).to_csv(
        raw_dir / "products.csv", index=False
    )

    report = validate_csv_directory(raw_dir)
    output_path = tmp_path / "processed" / "validation_report.json"
    write_report(report, output_path)

    saved_report = json.loads(output_path.read_text(encoding="utf-8"))
    assert saved_report["data_modified"] is False
    assert "missing_file" in _issues_for(saved_report, "customers")
    assert saved_report["totals"]["severity_counts"]["reject"] > 0
