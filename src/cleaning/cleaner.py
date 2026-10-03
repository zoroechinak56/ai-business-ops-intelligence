"""Standardize validated source data and quarantine rejected records."""

from __future__ import annotations

from collections.abc import Mapping
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from src.config import settings
from src.logger import setup_logging
from src.validation.validator import (
    FOREIGN_KEYS,
    NON_NEGATIVE_COLUMNS,
    PRIMARY_KEYS,
    TABLE_SCHEMAS,
    validate_tables,
)


TABLE_ORDER = (
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

OPTIONAL_DEFAULTS = {
    "customers": {"phone": "Unknown"},
    "products": {"description": "Not provided"},
    "suppliers": {"contact_email": "Not provided"},
    "warehouses": {"region": "Unassigned"},
    "employees": {"phone": "Unknown"},
    "orders": {"order_notes": "Not provided"},
    "order_items": {"discount_code": "NONE"},
    "deliveries": {"tracking_number": "Not provided"},
    "inventory_snapshots": {"notes": "Not provided"},
    "support_tickets": {"resolution_notes": "Not provided"},
}

LOGGER = logging.getLogger(__name__)


def _append_reason(reasons: list[list[str]], mask: pd.Series, reason: str) -> None:
    """Append a reason once to every row selected by a boolean mask."""
    for position in np.flatnonzero(mask.to_numpy()):
        if reason not in reasons[position]:
            reasons[position].append(reason)


def _normalize_table(
    table_name: str, source_frame: pd.DataFrame
) -> tuple[pd.DataFrame, pd.Series, pd.DataFrame]:
    """Trim text, apply safe defaults, and return local rejection reasons."""
    schema = TABLE_SCHEMAS[table_name]
    missing_columns = [column for column in schema if column not in source_frame]
    if missing_columns:
        raise ValueError(
            f"{table_name} is missing required columns: {', '.join(missing_columns)}"
        )

    original = source_frame.copy(deep=True).reset_index(drop=True)
    frame = original.copy(deep=True)
    changed = pd.Series(False, index=frame.index)
    reasons: list[list[str]] = [[] for _ in range(len(frame))]

    for column in schema:
        values = frame[column]
        trimmed = values.map(lambda value: value.strip() if isinstance(value, str) else value)
        whitespace_changed = values.map(
            lambda value: isinstance(value, str) and value != value.strip()
        )
        changed |= whitespace_changed
        trimmed = trimmed.map(
            lambda value: pd.NA
            if isinstance(value, str) and not value
            else value
        )
        frame[column] = trimmed

    for column, spec in schema.items():
        if column not in frame:
            continue
        values = frame[column]
        if spec.kind in ("integer", "number"):
            numeric = pd.to_numeric(values, errors="coerce")
            invalid_numeric = values.notna() & numeric.isna()
            _append_reason(reasons, invalid_numeric, f"invalid_type:{column}")
            if spec.kind == "integer":
                fractional = numeric.notna() & numeric.mod(1).ne(0)
                _append_reason(reasons, fractional, f"invalid_type:{column}")
                numeric = numeric.mask(fractional)
            frame[column] = numeric
            continue

        if spec.kind == "boolean":
            normalized = values.map(
                lambda value: (
                    value
                    if isinstance(value, (bool, np.bool_))
                    else {"true": True, "false": False}.get(value.strip().lower())
                    if isinstance(value, str)
                    else pd.NA
                )
            )
            invalid_boolean = values.notna() & normalized.isna()
            _append_reason(reasons, invalid_boolean, f"invalid_type:{column}")
            frame[column] = normalized
            continue

        if spec.kind == "date":
            parsed = pd.to_datetime(values, errors="coerce")
            invalid_date = values.notna() & parsed.isna()
            _append_reason(reasons, invalid_date, f"invalid_type:{column}")
            frame[column] = parsed

    if table_name == "products":
        category = frame["category"]
        title_cased = category.map(
            lambda value: value.title() if isinstance(value, str) else value
        )
        changed |= category.ne(title_cased).fillna(False)
        frame["category"] = title_cased

    for column, default in OPTIONAL_DEFAULTS.get(table_name, {}).items():
        values = frame[column]
        missing = values.isna()
        changed |= missing
        frame[column] = values.fillna(default)

    for column, spec in schema.items():
        values = frame[column]
        if spec.kind == "string":
            missing_required = values.isna()
        elif spec.kind == "integer":
            missing_required = values.isna()
        elif spec.kind == "number":
            missing_required = values.isna()
        elif spec.kind == "boolean":
            missing_required = values.isna()
        else:
            missing_required = values.isna()
        if not spec.nullable:
            _append_reason(reasons, missing_required, f"null_required:{column}")

        if spec.kind == "string":
            invalid_type = values.notna() & ~values.map(
                lambda value: isinstance(value, str)
            )
        elif spec.kind == "integer":
            numeric_values = pd.to_numeric(values, errors="coerce")
            invalid_type = values.notna() & (
                numeric_values.isna() | numeric_values.mod(1).ne(0)
            )
        elif spec.kind == "number":
            invalid_type = values.notna() & pd.to_numeric(
                values, errors="coerce"
            ).isna()
        elif spec.kind == "boolean":
            invalid_type = values.notna() & ~values.map(
                lambda value: isinstance(value, (bool, np.bool_))
            )
        else:
            invalid_type = values.notna() & pd.to_datetime(
                values, errors="coerce"
            ).isna()
        _append_reason(reasons, invalid_type, f"invalid_type:{column}")

    for column in NON_NEGATIVE_COLUMNS.get(table_name, ()):
        negative = pd.to_numeric(frame[column], errors="coerce").lt(0).fillna(False)
        _append_reason(reasons, negative, f"negative_value:{column}")

    if table_name == "deliveries":
        invalid_delivery_order = (
            frame["delivered_date"].notna()
            & frame["shipped_date"].notna()
            & frame["delivered_date"].lt(frame["shipped_date"])
        )
        _append_reason(
            reasons, invalid_delivery_order, "delivered_before_shipped"
        )

    primary_key = PRIMARY_KEYS[table_name]
    duplicate_after_first = frame[primary_key].notna() & frame[
        primary_key
    ].duplicated(keep="first")
    _append_reason(reasons, duplicate_after_first, f"duplicate_primary_key:{primary_key}")

    return frame, changed, pd.DataFrame(
        {"reject_reason": ["; ".join(row_reasons) for row_reasons in reasons]}
    )


def clean_tables(
    tables: Mapping[str, pd.DataFrame],
    validation_report: Mapping[str, object],
) -> tuple[dict[str, pd.DataFrame], dict[str, pd.DataFrame], dict[str, object]]:
    """Clean in-memory source tables and quarantine rejected rows.

    Input frames are copied and remain untouched. The supplied validation
    report is retained as provenance; row-level decisions are recalculated
    because its sample row lists are capped and are not exhaustive.
    """
    unknown_tables = set(tables) - set(TABLE_SCHEMAS)
    if unknown_tables:
        raise ValueError(f"Unknown source tables: {', '.join(sorted(unknown_tables))}")
    if not isinstance(validation_report.get("tables"), Mapping):
        raise ValueError("Validation report must contain a 'tables' mapping.")

    clean_frames: dict[str, pd.DataFrame] = {}
    rejected_frames: dict[str, pd.DataFrame] = {}
    table_summaries: dict[str, dict[str, int]] = {}
    rejected_primary_keys: dict[str, set[object]] = {}

    for table_name in TABLE_ORDER:
        if table_name not in tables:
            continue
        raw_frame = tables[table_name].reset_index(drop=True).copy(deep=True)
        normalized, changed, local_issues = _normalize_table(table_name, raw_frame)
        reason_lists = [
            [] if not reason else reason.split("; ")
            for reason in local_issues["reject_reason"]
        ]

        for column, (parent_name, parent_key) in FOREIGN_KEYS.get(
            table_name, {}
        ).items():
            if parent_name not in clean_frames:
                if parent_name not in tables:
                    continue
                raise ValueError(
                    f"Parent table '{parent_name}' must be processed before "
                    f"{table_name}."
                )
            parent_values = clean_frames[parent_name][parent_key].dropna()
            values = normalized[column]
            orphan_mask = values.notna() & ~values.isin(parent_values)
            if parent_name == "orders":
                rejected_order_ids = rejected_primary_keys.get("orders", set())
                cascade_mask = orphan_mask & values.isin(rejected_order_ids)
                _append_reason(
                    reason_lists, cascade_mask, "cascade_parent_order_rejected"
                )
                orphan_mask &= ~cascade_mask
            _append_reason(
                reason_lists, orphan_mask, f"orphan_foreign_key:{column}"
            )

        reject_mask = pd.Series([bool(reasons) for reasons in reason_lists])
        primary_key = PRIMARY_KEYS[table_name]
        rejected_primary_keys[table_name] = set(
            normalized.loc[reject_mask, primary_key].dropna().tolist()
        )
        clean_frame = normalized.loc[~reject_mask].copy().reset_index(drop=True)
        rejected_frame = raw_frame.loc[reject_mask].copy().reset_index(drop=True)
        rejected_frame.insert(0, "source_row", np.flatnonzero(reject_mask) + 2)
        rejected_frame["reject_reason"] = [
            "; ".join(reason_lists[position])
            for position in np.flatnonzero(reject_mask)
        ]

        fixed_count = int((changed & ~reject_mask).sum())
        clean_frames[table_name] = clean_frame
        rejected_frames[table_name] = rejected_frame
        table_summaries[table_name] = {
            "rows_before": len(raw_frame),
            "rows_after": len(clean_frame),
            "fixed": fixed_count,
            "rejected": len(rejected_frame),
        }

    post_validation = validate_tables(clean_frames)
    post_totals = post_validation["totals"]
    assert isinstance(post_totals, dict)
    post_severity_counts = post_totals["severity_counts"]
    assert isinstance(post_severity_counts, dict)
    if int(post_severity_counts["reject"]) > 0:
        raise RuntimeError(
            "Clean output still contains reject-severity validation findings: "
            f"{post_severity_counts['reject']}."
        )

    input_totals = validation_report.get("totals", {})
    return clean_frames, rejected_frames, {
        "data_modified": False,
        "input_validation_totals": input_totals,
        "tables": table_summaries,
        "totals": {
            "rows_before": sum(item["rows_before"] for item in table_summaries.values()),
            "rows_after": sum(item["rows_after"] for item in table_summaries.values()),
            "fixed": sum(item["fixed"] for item in table_summaries.values()),
            "rejected": sum(item["rejected"] for item in table_summaries.values()),
        },
        "post_clean_validation": post_totals,
    }


def clean_csv_directory(
    raw_dir: Path,
    validation_report_path: Path,
    processed_dir: Path,
) -> dict[str, object]:
    """Read source CSVs and report, then write clean and rejected CSV outputs."""
    with validation_report_path.open(encoding="utf-8") as report_file:
        validation_report = json.load(report_file)
    if not isinstance(validation_report, dict):
        raise ValueError("Validation report must contain a JSON object.")

    tables: dict[str, pd.DataFrame] = {}
    missing_files: list[str] = []
    for table_name in TABLE_SCHEMAS:
        csv_path = raw_dir / f"{table_name}.csv"
        if not csv_path.is_file():
            missing_files.append(table_name)
            continue
        tables[table_name] = pd.read_csv(csv_path)
    if missing_files:
        raise FileNotFoundError(
            "Missing required raw CSV files: " + ", ".join(missing_files)
        )

    clean_frames, rejected_frames, summary = clean_tables(tables, validation_report)
    clean_dir = processed_dir / "clean"
    rejected_dir = processed_dir / "rejected"
    clean_dir.mkdir(parents=True, exist_ok=True)
    rejected_dir.mkdir(parents=True, exist_ok=True)

    for table_name in TABLE_SCHEMAS:
        clean_frames[table_name].to_csv(clean_dir / f"{table_name}.csv", index=False)
        rejected_frames[table_name].to_csv(
            rejected_dir / f"{table_name}.csv", index=False
        )

    summary_path = processed_dir / "cleaning_summary.json"
    with summary_path.open("w", encoding="utf-8") as summary_file:
        json.dump(summary, summary_file, indent=2, ensure_ascii=True)
        summary_file.write("\n")
    return summary


def print_summary(summary: Mapping[str, object]) -> None:
    """Print before/after/fixed/rejected counts for each table."""
    table_summaries = summary["tables"]
    assert isinstance(table_summaries, dict)
    header = (
        f"{'Table':<24} {'Before':>10} {'After':>10} "
        f"{'Fixed':>10} {'Rejected':>10}"
    )
    print(header)
    print("-" * len(header))
    for table_name, counts in table_summaries.items():
        assert isinstance(counts, dict)
        print(
            f"{table_name:<24} {counts['rows_before']:>10,} "
            f"{counts['rows_after']:>10,} {counts['fixed']:>10,} "
            f"{counts['rejected']:>10,}"
        )
    totals = summary["totals"]
    assert isinstance(totals, dict)
    print("-" * len(header))
    print(
        f"{'TOTAL':<24} {totals['rows_before']:>10,} "
        f"{totals['rows_after']:>10,} {totals['fixed']:>10,} "
        f"{totals['rejected']:>10,}"
    )
    post_validation = summary["post_clean_validation"]
    assert isinstance(post_validation, dict)
    severity_counts = post_validation["severity_counts"]
    assert isinstance(severity_counts, dict)
    print(f"Post-clean reject findings: {severity_counts['reject']}")


def main() -> None:
    """Clean raw source CSVs into processed clean/rejected directories."""
    setup_logging()
    project_root = Path(__file__).resolve().parents[2]
    raw_dir = settings.data_raw_dir or project_root / "data" / "raw"
    processed_dir = settings.data_processed_dir or project_root / "data" / "processed"
    summary = clean_csv_directory(
        raw_dir,
        processed_dir / "validation_report.json",
        processed_dir,
    )
    print_summary(summary)
    LOGGER.info(
        "Cleaned %d tables: %d rows retained, %d rows rejected.",
        len(summary["tables"]),
        summary["totals"]["rows_after"],
        summary["totals"]["rejected"],
    )


if __name__ == "__main__":
    main()
