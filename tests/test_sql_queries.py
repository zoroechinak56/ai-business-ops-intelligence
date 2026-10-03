"""Tests that SQL KPI queries and Power BI views execute on SQLite."""

from pathlib import Path

from sqlalchemy import Engine, create_engine, text

from src.analytics.run_sql import QUERY_FILES, VIEW_NAMES, run_analytics
from src.ingestion.load_db import (
    _build_dim_date,
    _execute_sql_file,
)
from tests.test_load_db import _clean_frames


def _create_populated_database(engine: Engine) -> None:
    """Create the project schema and insert one related row per source table."""
    project_root = Path(__file__).resolve().parents[1]
    schema_dir = project_root / "sql" / "schema"
    with engine.begin() as connection:
        connection.exec_driver_sql("BEGIN")
        _execute_sql_file(connection, schema_dir / "01_schema.sql")
        _execute_sql_file(connection, schema_dir / "02_dim_date.sql")
        for table_name, frame in _clean_frames().items():
            frame.to_sql(table_name, connection, if_exists="append", index=False)
        _build_dim_date().to_sql("dim_date", connection, if_exists="append", index=False)


def test_all_kpi_queries_and_views_execute_in_sqlite() -> None:
    """Each numbered query runs and all required views are created."""
    engine = create_engine("sqlite:///:memory:")
    try:
        _create_populated_database(engine)
        results = run_analytics(engine, sample_rows=2)

        assert tuple(results["queries"]) == QUERY_FILES
        assert all(len(rows) <= 2 for rows in results["queries"].values())
        assert tuple(results["views"]) == VIEW_NAMES
        with engine.connect() as connection:
            created_views = connection.execute(
                text(
                    "SELECT name FROM sqlite_master WHERE type = 'view' "
                    "ORDER BY name"
                )
            ).scalars().all()
            assert set(created_views) == set(VIEW_NAMES)
            assert connection.execute(
                text("SELECT COUNT(*) FROM vw_orders_enriched")
            ).scalar_one() == 1
            rfm_scores = connection.execute(
                text(
                    "SELECT recency_score, frequency_score, monetary_score "
                    "FROM vw_customer_rfm"
                )
            ).one()
            assert all(1 <= score <= 5 for score in rfm_scores)
    finally:
        engine.dispose()


def test_queries_and_views_can_be_rerun_idempotently() -> None:
    """Repeated analytics runs replace views and do not change result shapes."""
    engine = create_engine("sqlite:///:memory:")
    try:
        _create_populated_database(engine)
        first = run_analytics(engine)
        second = run_analytics(engine)

        assert first["queries"].keys() == second["queries"].keys()
        assert first["views"] == second["views"]
    finally:
        engine.dispose()


def test_rfm_recency_uses_most_recent_customer_order() -> None:
    """Recency measures time since the most recent, not oldest, order."""
    engine = create_engine("sqlite:///:memory:")
    try:
        _create_populated_database(engine)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO orders "
                    "(order_id, customer_id, warehouse_id, employee_id, order_date, "
                    "order_notes, order_status) VALUES "
                    "('ORD-2', 'CUST-1', 'WH-A', 'EMP-1', '2025-01-10', "
                    "'Second order', 'fulfilled')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO order_items "
                    "(order_item_id, order_id, product_id, quantity, unit_price, "
                    "discount_code) VALUES "
                    "('ITEM-2', 'ORD-2', 'SKU-1', 1, 10, 'NONE')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO orders "
                    "(order_id, customer_id, warehouse_id, employee_id, order_date, "
                    "order_notes, order_status) VALUES "
                    "('ORD-3', 'CUST-1', 'WH-A', 'EMP-1', '2025-01-31', "
                    "'Unfulfilled order', 'unfulfilled')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO order_items "
                    "(order_item_id, order_id, product_id, quantity, unit_price, "
                    "discount_code) VALUES "
                    "('ITEM-3', 'ORD-3', 'SKU-1', 100, 999, 'NONE')"
                )
            )

        results = run_analytics(engine, sample_rows=10)
        rfm_rows = results["queries"]["09_customer_rfm.sql"]
        customer = next(row for row in rfm_rows if row["customer_id"] == "CUST-1")
        assert customer["recency_days"] == 0
        assert customer["frequency"] == 2
        assert customer["monetary"] == 30
        inventory_turnover = results["queries"]["07_inventory_turnover.sql"]
        product_row = next(row for row in inventory_turnover if row["product_id"] == "SKU-1")
        assert product_row["units_sold"] == 3
        with engine.connect() as connection:
            view_recency = connection.execute(
                text(
                    "SELECT recency_days FROM vw_customer_rfm "
                    "WHERE customer_id = 'CUST-1'"
                )
            ).scalar_one()
            assert view_recency == 0
    finally:
        engine.dispose()
