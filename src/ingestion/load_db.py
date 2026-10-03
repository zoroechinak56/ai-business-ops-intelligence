"""Rebuild the relational database from standardized clean CSV files."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
import json
import logging
from pathlib import Path

import pandas as pd
from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError

from src.config import settings
from src.logger import setup_logging
from src.validation.validator import TABLE_SCHEMAS


LOAD_ORDER = (
    "suppliers",
    "warehouses",
    "customers",
    "employees",
    "products",
    "orders",
    "order_items",
    "deliveries",
    "inventory_snapshots",
    "support_tickets",
)
DATE_RANGE_START = date(2025, 1, 1)
DATE_RANGE_END = date(2025, 6, 30)
DATE_TABLE_NAME = "dim_date"
LOGGER = logging.getLogger(__name__)


def _enable_sqlite_foreign_keys(engine: Engine) -> None:
    """Enable foreign-key enforcement on every SQLite DBAPI connection."""
    if engine.dialect.name != "sqlite":
        return

    def enable_foreign_keys(
        dbapi_connection: object, _connection_record: object
    ) -> None:
        del _connection_record
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    if not event.contains(engine, "connect", enable_foreign_keys):
        event.listen(engine, "connect", enable_foreign_keys)


def _execute_sql_file(connection: Connection, sql_path: Path) -> None:
    """Execute simple semicolon-delimited DDL from a project schema file."""
    statements: list[str] = []
    statement_lines: list[str] = []
    for line in sql_path.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("--"):
            continue
        statement_lines.append(line)
        if ";" in line:
            statement = "\n".join(statement_lines).strip()
            statement_lines = []
            if statement:
                statements.extend(
                    part.strip() for part in statement.split(";") if part.strip()
                )
    remainder = "\n".join(statement_lines).strip()
    if remainder:
        statements.extend(part.strip() for part in remainder.split(";") if part.strip())

    for statement in statements:
        connection.exec_driver_sql(statement)


def _build_dim_date() -> pd.DataFrame:
    """Build an inclusive six-month calendar with stable ISO week attributes."""
    dates = pd.date_range(DATE_RANGE_START, DATE_RANGE_END, freq="D")
    return pd.DataFrame(
        {
            "date": dates.date,
            "year": dates.year,
            "month": dates.month,
            "week": dates.isocalendar().week.astype(int),
            "weekday": dates.isocalendar().day.astype(int),
            "is_weekend": dates.isocalendar().day.isin((6, 7)),
        }
    )


def _read_clean_csv(clean_dir: Path, table_name: str) -> pd.DataFrame:
    """Load one clean CSV, parsing schema-declared date columns explicitly."""
    csv_path = clean_dir / f"{table_name}.csv"
    if not csv_path.is_file():
        raise FileNotFoundError(f"Missing clean table file: {csv_path}")
    frame = pd.read_csv(csv_path)

    for column, spec in TABLE_SCHEMAS[table_name].items():
        if spec.kind == "date":
            parsed = pd.to_datetime(frame[column], errors="raise")
            frame[column] = parsed.dt.date
    return frame


def _check_expected_counts(
    row_counts: Mapping[str, int],
    cleaning_summary: Mapping[str, object],
) -> None:
    """Fail if a table's loaded count differs from the cleaning summary."""
    table_summaries = cleaning_summary.get("tables")
    if not isinstance(table_summaries, Mapping):
        raise ValueError("Cleaning summary must contain a 'tables' mapping.")

    missing = set(LOAD_ORDER) - set(table_summaries)
    if missing:
        raise ValueError(
            "Cleaning summary is missing tables: " + ", ".join(sorted(missing))
        )

    mismatches: list[str] = []
    for table_name in LOAD_ORDER:
        table_summary = table_summaries[table_name]
        if not isinstance(table_summary, Mapping) or "rows_after" not in table_summary:
            raise ValueError(
                f"Cleaning summary for '{table_name}' has no rows_after count."
            )
        expected_count = int(table_summary["rows_after"])
        actual_count = row_counts[table_name]
        if actual_count != expected_count:
            mismatches.append(
                f"{table_name}: expected {expected_count}, loaded {actual_count}"
            )
    if mismatches:
        raise RuntimeError("Database row-count mismatch: " + "; ".join(mismatches))


def rebuild_database(
    engine: Engine,
    clean_dir: Path,
    cleaning_summary: Mapping[str, object],
    schema_dir: Path | None = None,
) -> dict[str, int]:
    """Drop/recreate all tables and load clean CSVs in foreign-key order."""
    schema_path = schema_dir or Path(__file__).resolve().parents[2] / "sql" / "schema"
    _enable_sqlite_foreign_keys(engine)
    frames = {
        table_name: _read_clean_csv(clean_dir, table_name)
        for table_name in LOAD_ORDER
    }

    with engine.begin() as connection:
        if engine.dialect.name == "sqlite":
            connection.exec_driver_sql("BEGIN")
        _execute_sql_file(connection, schema_path / "01_schema.sql")
        _execute_sql_file(connection, schema_path / "02_dim_date.sql")

        for table_name in LOAD_ORDER:
            frames[table_name].to_sql(
                table_name,
                connection,
                if_exists="append",
                index=False,
                chunksize=5_000,
            )

        _build_dim_date().to_sql(
            DATE_TABLE_NAME,
            connection,
            if_exists="append",
            index=False,
            chunksize=500,
        )

        row_counts = {
            table_name: int(
                connection.execute(
                    text(f"SELECT COUNT(*) FROM {table_name}")
                ).scalar_one()
            )
            for table_name in LOAD_ORDER
        }
        row_counts[DATE_TABLE_NAME] = int(
            connection.execute(
                text(f"SELECT COUNT(*) FROM {DATE_TABLE_NAME}")
            ).scalar_one()
        )
        _check_expected_counts(row_counts, cleaning_summary)

    return row_counts


def _read_cleaning_summary(path: Path) -> dict[str, object]:
    """Read the required cleaning summary JSON object."""
    with path.open(encoding="utf-8") as summary_file:
        summary = json.load(summary_file)
    if not isinstance(summary, dict):
        raise ValueError(f"Cleaning summary is not a JSON object: {path}")
    return summary


def _database_url(project_root: Path) -> str:
    """Choose the configured PostgreSQL URL or a local SQLite fallback."""
    if settings.database_url:
        return settings.database_url
    processed_dir = settings.data_processed_dir or project_root / "data" / "processed"
    processed_dir.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{(processed_dir / 'business_ops.sqlite').as_posix()}"


def main() -> None:
    """Rebuild the configured database and print verified row counts."""
    setup_logging()
    project_root = Path(__file__).resolve().parents[2]
    processed_dir = settings.data_processed_dir or project_root / "data" / "processed"
    clean_dir = processed_dir / "clean"
    summary_path = processed_dir / "cleaning_summary.json"
    cleaning_summary = _read_cleaning_summary(summary_path)
    database_url = _database_url(project_root)
    engine = create_engine(database_url, future=True)

    try:
        row_counts = rebuild_database(engine, clean_dir, cleaning_summary)
    except (SQLAlchemyError, OSError, ValueError, RuntimeError):
        LOGGER.exception("Database rebuild failed.")
        raise
    finally:
        dialect_name = engine.dialect.name
        engine.dispose()

    print(f"Database rebuilt using {dialect_name}.")
    for table_name, count in row_counts.items():
        print(f"{table_name:<24} {count:>10,}")
    LOGGER.info("Rebuilt database with %d source tables.", len(LOAD_ORDER))


if __name__ == "__main__":
    main()
