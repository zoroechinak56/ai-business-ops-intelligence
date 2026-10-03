"""Export Power BI views and analytical tables as CSV files."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.exc import SQLAlchemyError

from src.config import settings
from src.logger import setup_logging


EXPORT_OBJECTS = (
    "vw_orders_enriched",
    "vw_monthly_kpis",
    "vw_warehouse_performance",
    "vw_supplier_performance",
    "vw_inventory_status",
    "vw_customer_rfm",
    "customer_segments",
    "anomalies",
    "order_features",
    "stat_tests",
    "dim_date",
    "warehouses",
    "suppliers",
    "products",
    "inventory_snapshots",
)
ROOT_CAUSE_COLUMNS = (
    "kpi",
    "period_a",
    "period_b",
    "value_a",
    "value_b",
    "change",
    "dimension",
    "segment",
    "rate_a",
    "rate_b",
    "orders_a",
    "orders_b",
    "contribution_pp",
    "share_of_drop",
    "rate_vs_overall",
    "share_of_unfulfilled",
    "p_value",
    "dimension_rank",
    "overall_rank",
)
LOGGER = logging.getLogger(__name__)


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _database_url(project_root: Path) -> str:
    """Return configured database URL or the project SQLite fallback."""
    if settings.database_url:
        return settings.database_url
    processed_dir = settings.data_processed_dir or project_root / "data" / "processed"
    processed_dir.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{(processed_dir / 'business_ops.sqlite').as_posix()}"


def _root_cause_path(project_root: Path) -> Path:
    processed_dir = settings.data_processed_dir or project_root / "data" / "processed"
    return processed_dir / "root_cause_latest.json"


def _read_root_cause(path: Path) -> pd.DataFrame:
    """Flatten root-cause metadata onto one row per reported factor."""
    with path.open(encoding="utf-8") as input_file:
        report = json.load(input_file)
    if not isinstance(report, dict):
        raise ValueError(f"Root-cause report must be a JSON object: {path}")
    required = {
        "kpi",
        "period_a",
        "period_b",
        "value_a",
        "value_b",
        "change",
        "top_factors",
    }
    missing = required - report.keys()
    if missing:
        raise ValueError(
            "Root-cause report is missing fields: " + ", ".join(sorted(missing))
        )
    factors = report["top_factors"]
    if not isinstance(factors, list):
        raise ValueError("Root-cause report top_factors must be a list.")

    rows: list[dict[str, Any]] = []
    for factor in factors:
        if not isinstance(factor, dict):
            raise ValueError("Root-cause report factors must be JSON objects.")
        row = {
            "kpi": report["kpi"],
            "period_a": report["period_a"],
            "period_b": report["period_b"],
            "value_a": report["value_a"],
            "value_b": report["value_b"],
            "change": report["change"],
        }
        row.update({column: factor.get(column) for column in ROOT_CAUSE_COLUMNS[6:]})
        rows.append(row)
    return pd.DataFrame(rows, columns=ROOT_CAUSE_COLUMNS)


def export_powerbi_data(
    engine: Engine,
    output_dir: Path,
    root_cause_path: Path,
) -> dict[str, int]:
    """Export all required database objects and the flattened root-cause report."""
    inspector = inspect(engine)
    missing_objects = [
        object_name
        for object_name in EXPORT_OBJECTS
        if not inspector.has_table(object_name)
    ]
    if missing_objects:
        raise RuntimeError(
            "Required Power BI database objects are missing: "
            + ", ".join(missing_objects)
            + ". Rebuild the database and run the SQL/Python analytics first."
        )
    if not root_cause_path.is_file():
        raise FileNotFoundError(
            f"Missing root-cause report: {root_cause_path}. "
            "Run src.analytics.root_cause first."
        )

    with engine.connect() as connection:
        frames = {
            object_name: pd.read_sql_query(
                text(f"SELECT * FROM {object_name}"), connection
            )
            for object_name in EXPORT_OBJECTS
        }
    frames["root_cause"] = _read_root_cause(root_cause_path)

    output_dir.mkdir(parents=True, exist_ok=True)
    row_counts: dict[str, int] = {}
    for object_name, frame in frames.items():
        csv_path = output_dir / f"{object_name}.csv"
        frame.to_csv(csv_path, index=False, encoding="utf-8")
        row_counts[object_name] = len(frame)
        LOGGER.info("Exported %s rows to %s", len(frame), csv_path)
    return row_counts


def main() -> None:
    """Export the current database model for Power BI Desktop."""
    setup_logging()
    project_root = _project_root()
    output_dir = project_root / "powerbi" / "data"
    engine = create_engine(_database_url(project_root), future=True)
    try:
        row_counts = export_powerbi_data(
            engine,
            output_dir,
            _root_cause_path(project_root),
        )
    except (OSError, SQLAlchemyError, ValueError, RuntimeError):
        LOGGER.exception("Power BI CSV export failed.")
        raise
    finally:
        engine.dispose()

    print(f"Power BI CSV export: {output_dir}")
    for object_name, row_count in row_counts.items():
        print(f"{object_name}.csv: {row_count:,} rows")


if __name__ == "__main__":
    main()
