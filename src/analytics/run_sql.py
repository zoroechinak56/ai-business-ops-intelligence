"""Execute SQL analytics queries and create the Power BI reporting views."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine
from sqlalchemy.exc import SQLAlchemyError

from src.config import settings
from src.logger import setup_logging


QUERY_FILES = tuple(f"{index:02d}_{name}.sql" for index, name in enumerate(
    (
        "fulfilment_rate",
        "on_time_delivery",
        "sla_breach",
        "warehouse_ranking",
        "supplier_delay",
        "stockout_by_sku_group",
        "inventory_turnover",
        "customer_cohorts",
        "customer_rfm",
        "support_tickets",
    ),
    start=1,
))

VIEW_FILES = (
    "vw_orders_enriched.sql",
    "vw_monthly_kpis.sql",
    "vw_warehouse_performance.sql",
    "vw_supplier_performance.sql",
    "vw_inventory_status.sql",
    "vw_customer_rfm.sql",
)

VIEW_NAMES = tuple(Path(filename).stem for filename in VIEW_FILES)
LOGGER = logging.getLogger(__name__)


def _read_sql(path: Path) -> str:
    """Read one SQL file and reject an empty definition."""
    sql = path.read_text(encoding="utf-8").strip()
    if not sql:
        raise ValueError(f"SQL file is empty: {path}")
    return sql


def _drop_existing_views(engine: Engine, view_names: tuple[str, ...]) -> None:
    """Drop old views in reverse dependency order before recreating them."""
    with engine.begin() as connection:
        for view_name in reversed(view_names):
            connection.exec_driver_sql(f"DROP VIEW IF EXISTS {view_name}")


def run_analytics(
    engine: Engine,
    query_dir: Path | None = None,
    views_dir: Path | None = None,
    sample_rows: int = 5,
) -> dict[str, Any]:
    """Execute every query, recreate views, and return printed-row samples."""
    project_root = Path(__file__).resolve().parents[2]
    query_path = query_dir or project_root / "sql" / "kpi_queries"
    views_path = views_dir or project_root / "sql" / "views"
    if sample_rows < 0:
        raise ValueError("sample_rows must be non-negative.")

    query_results: dict[str, list[dict[str, object]]] = {}
    for filename in QUERY_FILES:
        sql_path = query_path / filename
        sql = _read_sql(sql_path)
        with engine.connect() as connection:
            result = connection.exec_driver_sql(sql)
            query_results[filename] = [
                dict(row._mapping) for row in result.fetchmany(sample_rows)
            ]

    missing_views = [filename for filename in VIEW_FILES if not (views_path / filename).is_file()]
    if missing_views:
        raise FileNotFoundError(
            "Missing SQL view files: " + ", ".join(missing_views)
        )

    _drop_existing_views(engine, VIEW_NAMES)
    with engine.begin() as connection:
        for filename in VIEW_FILES:
            connection.exec_driver_sql(_read_sql(views_path / filename))

    return {
        "queries": query_results,
        "views": list(VIEW_NAMES),
    }


def _database_url(project_root: Path) -> str:
    """Use configured PostgreSQL or the same SQLite fallback as the loader."""
    if settings.database_url:
        return settings.database_url
    processed_dir = settings.data_processed_dir or project_root / "data" / "processed"
    processed_dir.mkdir(parents=True, exist_ok=True)
    database_path = (processed_dir / "business_ops.sqlite").as_posix()
    return f"sqlite:///{database_path}"


def main() -> None:
    """Run all analytics against the configured business operations database."""
    setup_logging()
    project_root = Path(__file__).resolve().parents[2]
    database_url = _database_url(project_root)
    engine = create_engine(database_url, future=True)
    try:
        results = run_analytics(engine)
    except (SQLAlchemyError, OSError, ValueError):
        LOGGER.exception("SQL analytics execution failed.")
        raise
    finally:
        engine.dispose()

    for filename, rows in results["queries"].items():
        print(f"\n{filename}")
        if not rows:
            print("(no rows)")
            continue
        for row in rows:
            print(row)

    print("\nCreated Power BI views:")
    for view_name in results["views"]:
        print(f"- {view_name}")


if __name__ == "__main__":
    main()
