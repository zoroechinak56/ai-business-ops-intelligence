"""Database rebuild tests using an in-memory SQLite database."""

from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from src.ingestion.load_db import LOAD_ORDER, rebuild_database


def _clean_frames() -> dict[str, pd.DataFrame]:
    """Build one valid related record for each clean source table."""
    return {
        "suppliers": pd.DataFrame(
            {
                "supplier_id": ["SUP-1"],
                "supplier_name": ["Supplier One"],
                "contact_email": ["supplier@example.com"],
                "country": ["US"],
                "supplier_tier": ["preferred"],
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
        "customers": pd.DataFrame(
            {
                "customer_id": ["CUST-1"],
                "customer_name": ["Example Customer"],
                "email": ["customer@example.com"],
                "phone": ["Unknown"],
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
                "phone": ["Unknown"],
                "role": ["picker"],
                "hire_date": ["2020-01-01"],
            }
        ),
        "products": pd.DataFrame(
            {
                "product_id": ["SKU-1"],
                "product_name": ["Widget One"],
                "sku": ["SKU-1"],
                "sku_group": ["A"],
                "category": ["Home"],
                "supplier_id": ["SUP-1"],
                "unit_price": [10.0],
                "description": ["Not provided"],
            }
        ),
        "orders": pd.DataFrame(
            {
                "order_id": ["ORD-1"],
                "customer_id": ["CUST-1"],
                "warehouse_id": ["WH-A"],
                "employee_id": ["EMP-1"],
                "order_date": ["2025-01-01"],
                "order_notes": ["Not provided"],
                "order_status": ["fulfilled"],
            }
        ),
        "order_items": pd.DataFrame(
            {
                "order_item_id": ["ITEM-1"],
                "order_id": ["ORD-1"],
                "product_id": ["SKU-1"],
                "quantity": [2],
                "unit_price": [10.0],
                "discount_code": ["NONE"],
            }
        ),
        "deliveries": pd.DataFrame(
            {
                "delivery_id": ["DEL-1"],
                "order_id": ["ORD-1"],
                "supplier_id": ["SUP-1"],
                "warehouse_id": ["WH-A"],
                "shipped_date": ["2025-01-02"],
                "delivered_date": ["2025-01-04"],
                "promised_delivery_date": ["2025-01-10"],
                "supplier_late": [False],
                "delivery_status": ["delivered"],
                "tracking_number": ["TRK-1"],
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
                "notes": ["Not provided"],
            }
        ),
        "support_tickets": pd.DataFrame(
            {
                "ticket_id": ["TKT-1"],
                "customer_id": ["CUST-1"],
                "ticket_date": ["2025-01-05"],
                "issue_type": ["Return"],
                "priority": ["low"],
                "resolution_status": ["resolved"],
                "resolution_notes": ["Not provided"],
            }
        ),
    }


def _write_clean_frames(clean_dir: Path) -> dict[str, pd.DataFrame]:
    """Write one clean CSV per table and return the fixture frames."""
    clean_dir.mkdir(parents=True, exist_ok=True)
    frames = _clean_frames()
    for table_name, frame in frames.items():
        frame.to_csv(clean_dir / f"{table_name}.csv", index=False)
    return frames


def _summary(frames: dict[str, pd.DataFrame]) -> dict[str, object]:
    """Create a cleaning-summary object with expected retained row counts."""
    return {
        "tables": {
            table_name: {"rows_after": len(frames[table_name])}
            for table_name in LOAD_ORDER
        }
    }


def test_rebuild_loads_tables_and_full_calendar_into_sqlite(tmp_path: Path) -> None:
    """All clean tables load and dim_date spans the required six months."""
    clean_dir = tmp_path / "clean"
    frames = _write_clean_frames(clean_dir)
    engine = create_engine("sqlite:///:memory:")

    row_counts = rebuild_database(engine, clean_dir, _summary(frames))

    assert all(row_counts[table_name] == 1 for table_name in LOAD_ORDER)
    assert row_counts["dim_date"] == 181
    with engine.connect() as connection:
        first_date, last_date = connection.execute(
            text("SELECT MIN(date), MAX(date) FROM dim_date")
        ).one()
        assert first_date == "2025-01-01"
        assert last_date == "2025-06-30"
        monday_weekday, sunday_weekend = connection.execute(
            text(
                "SELECT weekday, is_weekend FROM dim_date "
                "WHERE date = '2025-01-06'"
            )
        ).one()
        assert monday_weekday == 1
        assert sunday_weekend in (0, False)
    engine.dispose()


def test_rebuild_enforces_constraints_and_is_repeatable(tmp_path: Path) -> None:
    """Rebuilding drops previous rows and database checks reject invalid data."""
    clean_dir = tmp_path / "clean"
    frames = _write_clean_frames(clean_dir)
    engine = create_engine("sqlite:///:memory:")
    summary = _summary(frames)

    rebuild_database(engine, clean_dir, summary)
    rebuild_database(engine, clean_dir, summary)

    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO order_items "
                    "(order_item_id, order_id, product_id, quantity, unit_price, "
                    "discount_code) VALUES ('ITEM-BAD', 'ORD-1', 'SKU-1', 0, 1, 'NONE')"
                )
            )
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO deliveries "
                    "(delivery_id, order_id, supplier_id, warehouse_id, shipped_date, "
                    "delivered_date, promised_delivery_date, supplier_late, "
                    "delivery_status, tracking_number) VALUES "
                    "('DEL-BAD', 'ORD-1', 'SUP-1', 'WH-A', '2025-01-04', "
                    "'2025-01-03', '2025-01-10', 0, 'delivered', 'TRK-BAD')"
                )
            )
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO orders "
                    "(order_id, customer_id, warehouse_id, employee_id, order_date, "
                    "order_notes, order_status) VALUES "
                    "('ORD-BAD', 'CUST-MISSING', 'WH-A', 'EMP-1', '2025-01-01', "
                    "'test', 'fulfilled')"
                )
            )
    with engine.connect() as connection:
        loaded_orders = connection.execute(
            text("SELECT COUNT(*) FROM orders")
        ).scalar_one()
        assert loaded_orders == 1
    engine.dispose()


def test_row_count_mismatch_rolls_back_database_rebuild(tmp_path: Path) -> None:
    """A cleaning-summary mismatch fails the transaction without partial schema."""
    clean_dir = tmp_path / "clean"
    frames = _write_clean_frames(clean_dir)
    summary = _summary(frames)
    summary_tables = summary["tables"]
    assert isinstance(summary_tables, dict)
    summary_tables["orders"]["rows_after"] = 2
    engine = create_engine("sqlite:///:memory:")

    with pytest.raises(RuntimeError, match="row-count mismatch"):
        rebuild_database(engine, clean_dir, summary)

    with engine.connect() as connection:
        table_count = connection.execute(
            text(
                "SELECT COUNT(*) FROM sqlite_master "
                "WHERE type = 'table' AND name = 'orders'"
            )
        ).scalar_one()
    assert table_count == 0
    engine.dispose()
