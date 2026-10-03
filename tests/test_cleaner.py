"""Tests for read-only-source cleaning and rejected-row quarantine."""

import json
from pathlib import Path

import pandas as pd

from src.cleaning.cleaner import (
    OPTIONAL_DEFAULTS,
    clean_csv_directory,
    clean_tables,
)
from src.validation.validator import validate_tables


def _source_tables() -> dict[str, pd.DataFrame]:
    """Create a small relational data set with targeted cleaning defects."""
    return {
        "suppliers": pd.DataFrame(
            {
                "supplier_id": ["SUP-1"],
                "supplier_name": ["Supplier One"],
                "contact_email": [None],
                "country": ["US"],
                "supplier_tier": ["preferred"],
            }
        ),
        "warehouses": pd.DataFrame(
            {
                "warehouse_id": ["WH-A"],
                "warehouse_name": ["Warehouse A"],
                "city": ["Example City"],
                "region": [None],
                "capacity_units": [100],
            }
        ),
        "customers": pd.DataFrame(
            {
                "customer_id": ["CUST-1"],
                "customer_name": ["  Example Customer  "],
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
        "products": pd.DataFrame(
            {
                "product_id": ["SKU-1", "SKU-2", "SKU-3"],
                "product_name": ["Widget One", "Widget Two", "Widget Three"],
                "sku": ["SKU-1", "SKU-2", "SKU-3"],
                "sku_group": ["A", "B", "C"],
                "category": ["  hOME  ", "Electronics", "Home"],
                "supplier_id": ["SUP-1", "SUP-1", "SUP-MISSING"],
                "unit_price": [10.0, -2.0, 15.0],
                "description": [None, "Good product", "Orphan supplier"],
            }
        ),
        "orders": pd.DataFrame(
            {
                "order_id": ["ORD-1", "ORD-2", "ORD-3", "ORD-3"],
                "customer_id": ["CUST-1", "CUST-1", "CUST-MISSING", "CUST-1"],
                "warehouse_id": ["WH-A"] * 4,
                "employee_id": ["EMP-1"] * 4,
                "order_date": [
                    " 2025-01-01 ",
                    "2025-01-02",
                    "2025-01-03",
                    "2025-01-04",
                ],
                "order_notes": [None, "  note  ", "bad customer", "duplicate"],
                "order_status": ["fulfilled"] * 4,
            }
        ),
        "order_items": pd.DataFrame(
            {
                "order_item_id": ["ITEM-1", "ITEM-2", "ITEM-3"],
                "order_id": ["ORD-1", "ORD-2", "ORD-3"],
                "product_id": ["SKU-1", "SKU-2", "SKU-1"],
                "quantity": [2, -1, 1],
                "unit_price": [10.0, 20.0, 10.0],
                "discount_code": [None, "  SAVE  ", "NONE"],
            }
        ),
        "deliveries": pd.DataFrame(
            {
                "delivery_id": ["DEL-1", "DEL-2", "DEL-3"],
                "order_id": ["ORD-1", "ORD-2", "ORD-3"],
                "supplier_id": ["SUP-1"] * 3,
                "warehouse_id": ["WH-A"] * 3,
                "shipped_date": [
                    "2025-01-02",
                    "2025-01-03",
                    "2025-01-04",
                ],
                "delivered_date": [
                    "2025-01-03",
                    "2025-01-02",
                    "2025-01-05",
                ],
                "promised_delivery_date": [
                    "2025-01-10",
                    "2025-01-10",
                    "2025-01-10",
                ],
                "supplier_late": [False, True, False],
                "delivery_status": ["delivered"] * 3,
                "tracking_number": [None, "TRK-2", "TRK-3"],
            }
        ),
        "inventory_snapshots": pd.DataFrame(
            {
                "snapshot_id": ["INV-1"],
                "snapshot_date": ["2025-01-06"],
                "product_id": ["SKU-1"],
                "warehouse_id": ["WH-A"],
                "supplier_id": ["SUP-1"],
                "quantity_on_hand": [10],
                "reorder_point": [5],
                "stockout_flag": [False],
                "notes": [None],
            }
        ),
        "support_tickets": pd.DataFrame(
            {
                "ticket_id": ["TKT-1", "TKT-2"],
                "customer_id": ["CUST-1", "CUST-MISSING"],
                "ticket_date": ["2025-01-05", "2025-01-06"],
                "issue_type": ["Return", "Order status"],
                "priority": ["low", "high"],
                "resolution_status": ["resolved", "open"],
                "resolution_notes": [None, "Orphan customer"],
            }
        ),
    }


def _rejection_reasons_for(
    frame: pd.DataFrame, key_column: str, key: str
) -> list[str]:
    """Collect quarantine reasons for rows matching a source primary key."""
    rejected = frame.loc[frame[key_column].eq(key), "reject_reason"]
    return [str(reason) for reason in rejected]


def test_cleaning_fixes_safe_fields_and_rejects_unrecoverable_rows() -> None:
    """Recoverable text/null defects are fixed and hard defects quarantined."""
    source = _source_tables()
    before = source["products"].copy(deep=True)
    input_report = validate_tables(source)

    clean, rejected, summary = clean_tables(source, input_report)

    assert clean["products"]["category"].tolist() == ["Home"]
    assert clean["products"]["description"].tolist() == ["Not provided"]
    assert _rejection_reasons_for(rejected["products"], "product_id", "SKU-2") == [
        "negative_value:unit_price"
    ]
    assert _rejection_reasons_for(rejected["products"], "product_id", "SKU-3") == [
        "orphan_foreign_key:supplier_id"
    ]
    assert source["products"].equals(before)
    assert summary["tables"]["products"] == {
        "rows_before": 3,
        "rows_after": 1,
        "fixed": 1,
        "rejected": 2,
    }


def test_duplicate_drop_orphan_rejection_and_order_cascade() -> None:
    """Duplicate keys keep the first row and rejected orders cascade to children."""
    source = _source_tables()
    duplicate_valid_order = source["orders"].iloc[[1]].copy()
    duplicate_valid_order.loc[:, "order_notes"] = "duplicate key"
    source["orders"] = pd.concat(
        [source["orders"], duplicate_valid_order], ignore_index=True
    )
    clean, rejected, summary = clean_tables(source, validate_tables(source))

    assert clean["orders"]["order_id"].tolist() == ["ORD-1", "ORD-2"]
    order_reasons = _rejection_reasons_for(rejected["orders"], "order_id", "ORD-3")
    assert order_reasons == ["orphan_foreign_key:customer_id", "duplicate_primary_key:order_id"]
    assert _rejection_reasons_for(rejected["orders"], "order_id", "ORD-2") == [
        "duplicate_primary_key:order_id"
    ]
    assert "negative_value:quantity" in _rejection_reasons_for(
        rejected["order_items"], "order_item_id", "ITEM-2"
    )[0]
    assert "orphan_foreign_key:product_id" in _rejection_reasons_for(
        rejected["order_items"], "order_item_id", "ITEM-2"
    )[0]
    assert _rejection_reasons_for(
        rejected["order_items"], "order_item_id", "ITEM-3"
    ) == ["cascade_parent_order_rejected"]
    assert _rejection_reasons_for(
        rejected["deliveries"], "delivery_id", "DEL-2"
    ) == ["delivered_before_shipped"]
    assert _rejection_reasons_for(
        rejected["deliveries"], "delivery_id", "DEL-3"
    ) == ["cascade_parent_order_rejected"]
    assert _rejection_reasons_for(
        rejected["support_tickets"], "ticket_id", "TKT-2"
    ) == ["orphan_foreign_key:customer_id"]
    assert summary["post_clean_validation"]["severity_counts"]["reject"] == 0


def test_defaults_are_documented_and_clean_tables_have_valid_foreign_keys() -> None:
    """Every optional string receives its declared default; clean output validates."""
    source = _source_tables()
    clean, _, summary = clean_tables(source, validate_tables(source))

    for table_name, defaults in OPTIONAL_DEFAULTS.items():
        for column, default in defaults.items():
            if table_name in clean:
                assert clean[table_name][column].notna().all()
                assert default in clean[table_name][column].tolist()
    assert clean["orders"]["order_notes"].tolist() == [
        "Not provided",
        "note",
    ]
    assert clean["orders"]["order_date"].iloc[0] == pd.Timestamp("2025-01-01")
    assert summary["post_clean_validation"]["severity_counts"]["reject"] == 0
    assert summary["post_clean_validation"]["issue_count"] == 0


def test_directory_cleaning_writes_outputs_without_changing_raw_csvs(
    tmp_path: Path,
) -> None:
    """The file workflow writes clean/quarantine/report outputs and preserves raw."""
    raw_dir = tmp_path / "raw"
    processed_dir = tmp_path / "processed"
    raw_dir.mkdir()
    source = _source_tables()
    for table_name, frame in source.items():
        frame.to_csv(raw_dir / f"{table_name}.csv", index=False)
    original_bytes = {
        table_name: (raw_dir / f"{table_name}.csv").read_bytes()
        for table_name in source
    }
    validation_path = processed_dir / "validation_report.json"
    validation_path.parent.mkdir(parents=True)
    validation_path.write_text(
        json.dumps(validate_tables(source)), encoding="utf-8"
    )

    summary = clean_csv_directory(raw_dir, validation_path, processed_dir)

    for table_name in source:
        assert (raw_dir / f"{table_name}.csv").read_bytes() == original_bytes[
            table_name
        ]
        assert (processed_dir / "clean" / f"{table_name}.csv").is_file()
        rejected_path = processed_dir / "rejected" / f"{table_name}.csv"
        assert rejected_path.is_file()
        assert "reject_reason" in pd.read_csv(rejected_path).columns
    summary_path = processed_dir / "cleaning_summary.json"
    assert json.loads(summary_path.read_text(encoding="utf-8"))["totals"] == (
        summary["totals"]
    )
