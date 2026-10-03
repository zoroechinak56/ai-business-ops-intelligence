"""Report source-data quality issues without changing raw CSV files."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
import json
import logging
from numbers import Real
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd

from src.config import settings
from src.logger import setup_logging


Severity = Literal["reject", "fix", "warn"]
ValueKind = Literal["string", "integer", "number", "boolean", "date"]


@dataclass(frozen=True, slots=True)
class ColumnSpec:
    """Expected scalar kind and nullability for one source column."""

    kind: ValueKind
    nullable: bool = False
    warn_if_null: bool = False


def _string(nullable: bool = False) -> ColumnSpec:
    return ColumnSpec("string", nullable, nullable)


def _integer(nullable: bool = False) -> ColumnSpec:
    return ColumnSpec("integer", nullable)


def _number(nullable: bool = False) -> ColumnSpec:
    return ColumnSpec("number", nullable)


def _boolean(nullable: bool = False) -> ColumnSpec:
    return ColumnSpec("boolean", nullable)


def _date(nullable: bool = False) -> ColumnSpec:
    return ColumnSpec("date", nullable)


TABLE_SCHEMAS: dict[str, dict[str, ColumnSpec]] = {
    "customers": {
        "customer_id": _string(),
        "customer_name": _string(),
        "email": _string(),
        "phone": _string(True),
        "city": _string(),
        "state": _string(),
        "signup_date": _date(),
        "customer_segment": _string(),
    },
    "products": {
        "product_id": _string(),
        "product_name": _string(),
        "sku": _string(),
        "sku_group": _string(),
        "category": _string(),
        "supplier_id": _string(),
        "unit_price": _number(),
        "description": _string(True),
    },
    "suppliers": {
        "supplier_id": _string(),
        "supplier_name": _string(),
        "contact_email": _string(True),
        "country": _string(),
        "supplier_tier": _string(),
    },
    "warehouses": {
        "warehouse_id": _string(),
        "warehouse_name": _string(),
        "city": _string(),
        "region": _string(True),
        "capacity_units": _integer(),
    },
    "employees": {
        "employee_id": _string(),
        "employee_name": _string(),
        "email": _string(),
        "phone": _string(True),
        "role": _string(),
        "hire_date": _date(),
    },
    "orders": {
        "order_id": _string(),
        "customer_id": _string(),
        "warehouse_id": _string(),
        "employee_id": _string(),
        "order_date": _date(),
        "order_notes": _string(True),
        "order_status": _string(),
    },
    "order_items": {
        "order_item_id": _string(),
        "order_id": _string(),
        "product_id": _string(),
        "quantity": _integer(),
        "unit_price": _number(),
        "discount_code": _string(True),
    },
    "deliveries": {
        "delivery_id": _string(),
        "order_id": _string(),
        "supplier_id": _string(),
        "warehouse_id": _string(),
        "shipped_date": _date(),
        "delivered_date": _date(True),
        "promised_delivery_date": _date(),
        "supplier_late": _boolean(),
        "delivery_status": _string(),
        "tracking_number": _string(True),
    },
    "inventory_snapshots": {
        "snapshot_id": _string(),
        "snapshot_date": _date(),
        "product_id": _string(),
        "warehouse_id": _string(),
        "supplier_id": _string(),
        "quantity_on_hand": _integer(),
        "reorder_point": _integer(),
        "stockout_flag": _boolean(),
        "notes": _string(True),
    },
    "support_tickets": {
        "ticket_id": _string(),
        "customer_id": _string(),
        "ticket_date": _date(),
        "issue_type": _string(),
        "priority": _string(),
        "resolution_status": _string(),
        "resolution_notes": _string(True),
    },
}

PRIMARY_KEYS = {
    "customers": "customer_id",
    "products": "product_id",
    "suppliers": "supplier_id",
    "warehouses": "warehouse_id",
    "employees": "employee_id",
    "orders": "order_id",
    "order_items": "order_item_id",
    "deliveries": "delivery_id",
    "inventory_snapshots": "snapshot_id",
    "support_tickets": "ticket_id",
}

FOREIGN_KEYS: dict[str, dict[str, tuple[str, str]]] = {
    "products": {"supplier_id": ("suppliers", "supplier_id")},
    "orders": {
        "customer_id": ("customers", "customer_id"),
        "warehouse_id": ("warehouses", "warehouse_id"),
        "employee_id": ("employees", "employee_id"),
    },
    "order_items": {
        "order_id": ("orders", "order_id"),
        "product_id": ("products", "product_id"),
    },
    "deliveries": {
        "order_id": ("orders", "order_id"),
        "supplier_id": ("suppliers", "supplier_id"),
        "warehouse_id": ("warehouses", "warehouse_id"),
    },
    "inventory_snapshots": {
        "product_id": ("products", "product_id"),
        "warehouse_id": ("warehouses", "warehouse_id"),
        "supplier_id": ("suppliers", "supplier_id"),
    },
    "support_tickets": {"customer_id": ("customers", "customer_id")},
}

NON_NEGATIVE_COLUMNS = {
    "products": ("unit_price",),
    "warehouses": ("capacity_units",),
    "order_items": ("quantity", "unit_price"),
    "inventory_snapshots": ("quantity_on_hand", "reorder_point"),
}

SEVERITIES: tuple[Severity, ...] = ("reject", "fix", "warn")
SAMPLE_LIMIT = 10
LOGGER = logging.getLogger(__name__)


def _is_missing(value: object) -> bool:
    """Return whether a scalar value is null."""
    return bool(pd.isna(value))


def _is_valid_value(value: object, kind: ValueKind) -> bool:
    """Check a non-null value against its declared source type."""
    if kind == "string":
        return isinstance(value, str)
    if kind == "integer":
        if isinstance(value, (bool, np.bool_)):
            return False
        if isinstance(value, (int, np.integer)):
            return True
        return isinstance(value, (float, np.floating)) and float(value).is_integer()
    if kind == "number":
        return isinstance(value, (Real, np.integer, np.floating)) and not isinstance(
            value, (bool, np.bool_)
        )
    if kind == "boolean":
        return isinstance(value, (bool, np.bool_))
    if kind == "date":
        if not isinstance(value, (str, date, datetime, pd.Timestamp, np.datetime64)):
            return False
        return not pd.isna(pd.to_datetime(value, errors="coerce"))
    return False


def _row_numbers(mask: pd.Series) -> list[int]:
    """Return up to ten 1-based CSV row numbers matching a mask."""
    return (np.flatnonzero(mask.to_numpy())[:SAMPLE_LIMIT] + 1).tolist()


def _add_issue(
    table_report: dict[str, object],
    *,
    code: str,
    severity: Severity,
    column: str | None,
    mask: pd.Series | None,
    count: int | None = None,
    message: str,
) -> None:
    """Append one aggregated issue to a table report."""
    affected_count = (
        count
        if count is not None
        else int(mask.sum())
        if mask is not None
        else 1
    )
    if affected_count <= 0:
        return

    issue: dict[str, object] = {
        "code": code,
        "severity": severity,
        "column": column,
        "count": affected_count,
        "message": message,
        "sample_rows": _row_numbers(mask) if mask is not None else [],
    }
    issues = table_report["issues"]
    assert isinstance(issues, list)
    issues.append(issue)


def _empty_table_report(row_count: int = 0) -> dict[str, object]:
    """Create a report entry before table validation."""
    return {
        "rows": row_count,
        "issue_count": 0,
        "severity_counts": {severity: 0 for severity in SEVERITIES},
        "issues": [],
    }


def _validate_frame(table_name: str, frame: pd.DataFrame) -> dict[str, object]:
    """Check one table's schema, nulls, primary key, ranges, and casing."""
    table_report = _empty_table_report(len(frame))
    schema = TABLE_SCHEMAS[table_name]
    available = set(frame.columns)

    missing_columns = [column for column in schema if column not in available]
    if missing_columns:
        _add_issue(
            table_report,
            code="missing_column",
            severity="reject",
            column=None,
            mask=None,
            count=len(missing_columns),
            message=(
                "Required columns are missing: " + ", ".join(missing_columns) + "."
            ),
        )

    for column, spec in schema.items():
        if column not in available:
            continue

        values = frame[column]
        null_mask = values.isna()
        if not spec.nullable:
            _add_issue(
                table_report,
                code="null_required",
                severity="reject",
                column=column,
                mask=null_mask,
                message=f"Required field '{column}' contains null values.",
            )
        elif spec.warn_if_null and bool(null_mask.any()):
            _add_issue(
                table_report,
                code="null_optional",
                severity="warn",
                column=column,
                mask=null_mask,
                message=f"Optional field '{column}' contains null values.",
            )

        invalid_type_mask = values.map(
            lambda value: False
            if _is_missing(value)
            else not _is_valid_value(value, spec.kind)
        )
        _add_issue(
            table_report,
            code="invalid_type",
            severity="reject",
            column=column,
            mask=invalid_type_mask,
            message=f"Column '{column}' contains values that are not {spec.kind}.",
        )

    primary_key = PRIMARY_KEYS[table_name]
    if primary_key in available:
        duplicate_mask = frame[primary_key].notna() & frame[primary_key].duplicated(
            keep=False
        )
        _add_issue(
            table_report,
            code="duplicate_primary_key",
            severity="reject",
            column=primary_key,
            mask=duplicate_mask,
            message=f"Primary key '{primary_key}' contains duplicates.",
        )

    for column in NON_NEGATIVE_COLUMNS.get(table_name, ()):
        if column not in available:
            continue
        numeric_values = pd.to_numeric(frame[column], errors="coerce")
        negative_mask = numeric_values.lt(0).fillna(False)
        _add_issue(
            table_report,
            code="negative_value",
            severity="fix",
            column=column,
            mask=negative_mask,
            message=f"Column '{column}' contains negative values.",
        )

    if table_name == "products" and "category" in available:
        category = frame["category"]
        inconsistent_mask = category.map(
            lambda value: isinstance(value, str) and value != value.title()
        )
        _add_issue(
            table_report,
            code="inconsistent_category_casing",
            severity="fix",
            column="category",
            mask=inconsistent_mask,
            message="Product categories should use consistent title casing.",
        )

    if (
        table_name == "deliveries"
        and {"shipped_date", "delivered_date"}.issubset(available)
    ):
        shipped_dates = pd.to_datetime(frame["shipped_date"], errors="coerce")
        delivered_dates = pd.to_datetime(frame["delivered_date"], errors="coerce")
        date_order_mask = (
            shipped_dates.notna()
            & delivered_dates.notna()
            & delivered_dates.lt(shipped_dates)
        )
        _add_issue(
            table_report,
            code="delivered_before_shipped",
            severity="fix",
            column="delivered_date",
            mask=date_order_mask,
            message="Delivered dates must not precede shipped dates.",
        )

    return table_report


def _validate_foreign_keys(
    table_name: str,
    frame: pd.DataFrame,
    tables: Mapping[str, pd.DataFrame],
    table_report: dict[str, object],
) -> None:
    """Report non-null child keys that do not exist in an available parent."""
    for column, (parent_name, parent_key) in FOREIGN_KEYS.get(table_name, {}).items():
        if column not in frame.columns or parent_name not in tables:
            continue
        parent = tables[parent_name]
        if parent_key not in parent.columns:
            continue
        values = frame[column]
        known_values = parent[parent_key].dropna()
        invalid_reference_mask = values.notna() & ~values.isin(known_values)
        _add_issue(
            table_report,
            code="foreign_key_not_found",
            severity="reject",
            column=column,
            mask=invalid_reference_mask,
            message=(
                f"Values in '{column}' are missing from "
                f"{parent_name}.{parent_key}."
            ),
        )


def _finalize_table_report(table_report: dict[str, object]) -> None:
    """Calculate affected-row counts by issue classification."""
    issues = table_report["issues"]
    assert isinstance(issues, list)
    counts: Counter[str] = Counter()
    for issue in issues:
        counts[str(issue["severity"])] += int(issue["count"])
    table_report["severity_counts"] = {
        severity: counts[severity] for severity in SEVERITIES
    }
    table_report["issue_count"] = sum(counts.values())


def validate_tables(
    tables: Mapping[str, pd.DataFrame],
) -> dict[str, object]:
    """Validate supplied in-memory tables and return a JSON-ready report.

    Only supplied tables are checked. Foreign-key checks run when their parent
    table is also supplied, which keeps this function useful for focused tests.
    """
    unknown_tables = set(tables) - set(TABLE_SCHEMAS)
    if unknown_tables:
        raise ValueError(f"Unknown source tables: {', '.join(sorted(unknown_tables))}")

    table_reports: dict[str, dict[str, object]] = {}
    for table_name, frame in tables.items():
        table_reports[table_name] = _validate_frame(table_name, frame)

    for table_name, frame in tables.items():
        _validate_foreign_keys(
            table_name, frame, tables, table_reports[table_name]
        )

    for table_report in table_reports.values():
        _finalize_table_report(table_report)

    return {
        "source": "in_memory",
        "data_modified": False,
        "classification_guide": {
            "reject": "Required structure, values, keys, or references are invalid.",
            "fix": "A deterministic correction is possible, but raw data is unchanged.",
            "warn": "A nullable optional field is absent; review if operationally needed.",
        },
        "tables": table_reports,
        "totals": _summarize(table_reports),
    }


def _summarize(
    table_reports: Mapping[str, dict[str, object]],
) -> dict[str, object]:
    """Aggregate table issue counts by classification."""
    severity_totals: Counter[str] = Counter()
    issue_group_count = 0
    for table_report in table_reports.values():
        severity_counts = table_report["severity_counts"]
        assert isinstance(severity_counts, dict)
        severity_totals.update(
            {severity: int(severity_counts[severity]) for severity in SEVERITIES}
        )
        issues = table_report["issues"]
        assert isinstance(issues, list)
        issue_group_count += len(issues)
    return {
        "tables_checked": len(table_reports),
        "issue_count": sum(severity_totals.values()),
        "issue_groups": issue_group_count,
        "severity_counts": {
            severity: severity_totals[severity] for severity in SEVERITIES
        },
    }


def validate_csv_directory(raw_dir: Path) -> dict[str, object]:
    """Read and validate all expected CSVs, reporting absent source files."""
    raw_dir = raw_dir.expanduser()
    tables: dict[str, pd.DataFrame] = {}
    missing_tables: list[str] = []
    for table_name in TABLE_SCHEMAS:
        csv_path = raw_dir / f"{table_name}.csv"
        if not csv_path.is_file():
            missing_tables.append(table_name)
            continue
        tables[table_name] = pd.read_csv(csv_path)

    report = validate_tables(tables)
    table_reports = report["tables"]
    assert isinstance(table_reports, dict)
    for table_name in missing_tables:
        missing_report = _empty_table_report()
        _add_issue(
            missing_report,
            code="missing_file",
            severity="reject",
            column=None,
            mask=None,
            message=f"Required source file '{table_name}.csv' is missing.",
        )
        _finalize_table_report(missing_report)
        table_reports[table_name] = missing_report

    report["source"] = str(raw_dir.resolve())
    report["tables"] = {
        table_name: table_reports[table_name] for table_name in TABLE_SCHEMAS
    }
    report["totals"] = _summarize(report["tables"])
    return report


def write_report(report: dict[str, object], report_path: Path) -> None:
    """Write a JSON validation report without changing source CSVs."""
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("w", encoding="utf-8") as report_file:
        json.dump(report, report_file, indent=2, ensure_ascii=True)
        report_file.write("\n")


def print_summary(report: Mapping[str, object]) -> None:
    """Print issue counts per table and classification."""
    table_reports = report["tables"]
    assert isinstance(table_reports, dict)
    totals = report["totals"]
    assert isinstance(totals, dict)
    header = f"{'Table':<24} {'Reject':>10} {'Fix':>10} {'Warn':>10} {'Issues':>10}"
    print(header)
    print("-" * len(header))
    for table_name, table_report in table_reports.items():
        severity_counts = table_report["severity_counts"]
        assert isinstance(severity_counts, dict)
        print(
            f"{table_name:<24} {severity_counts['reject']:>10,} "
            f"{severity_counts['fix']:>10,} {severity_counts['warn']:>10,} "
            f"{table_report['issue_count']:>10,}"
        )

    severity_totals = totals["severity_counts"]
    assert isinstance(severity_totals, dict)
    print("-" * len(header))
    print(
        f"{'TOTAL':<24} {severity_totals['reject']:>10,} "
        f"{severity_totals['fix']:>10,} {severity_totals['warn']:>10,} "
        f"{totals['issue_count']:>10,}"
    )


def main() -> None:
    """Validate raw source CSVs and save a report under data/processed/."""
    setup_logging()
    project_root = Path(__file__).resolve().parents[2]
    raw_dir = settings.data_raw_dir or project_root / "data" / "raw"
    processed_dir = settings.data_processed_dir or project_root / "data" / "processed"
    report_path = processed_dir / "validation_report.json"

    report = validate_csv_directory(raw_dir)
    write_report(report, report_path)
    print(f"Validation report: {report_path}")
    print_summary(report)
    LOGGER.info(
        "Validated %d tables: %d issue records across %d issue groups.",
        report["totals"]["tables_checked"],
        report["totals"]["issue_count"],
        report["totals"]["issue_groups"],
    )


if __name__ == "__main__":
    main()
