"""Validate a source-data batch and run the complete analytics/reasoning flow."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import UTC, datetime
import json
import logging
from pathlib import Path
import time
from typing import Any, Generator
from uuid import uuid4

import pandas as pd
from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    text,
    update,
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from src.analytics.root_cause import decompose_fulfilment, save_result
from src.analytics.run_analytics import run_analytics
from src.ai_layer.reasoner import reason_about
from src.cleaning.cleaner import clean_csv_directory
from src.config import settings
from src.ingestion.load_db import _read_cleaning_summary, rebuild_database
from src.logger import setup_logging
from src.validation.validator import (
    TABLE_SCHEMAS,
    validate_csv_directory,
    write_report,
)


PIPELINE_STEPS = (
    "validate",
    "clean",
    "reload_database",
    "run_analytics",
    "root_cause",
    "ai_reasoner",
    "evaluate_alerts",
    "summary_report",
)
FULFILMENT_THRESHOLD = 0.90
SUPPLIER_GAP_THRESHOLD = 0.10
STOCKOUT_RATE_THRESHOLD = 0.20
LOGGER = logging.getLogger(__name__)


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _processed_dir(project_root: Path) -> Path:
    return settings.data_processed_dir or project_root / "data" / "processed"


def _database_url(project_root: Path) -> str:
    if settings.database_url:
        return settings.database_url
    processed_dir = _processed_dir(project_root)
    processed_dir.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{(processed_dir / 'business_ops.sqlite').as_posix()}"


def _resolve_raw_dir(file_path: Path) -> Path:
    """Resolve a dataset directory or a CSV inside a complete source bundle."""
    resolved = file_path.expanduser().resolve()
    if resolved.is_dir():
        return resolved
    if not resolved.is_file():
        raise FileNotFoundError(f"Pipeline input does not exist: {resolved}")
    if resolved.suffix.lower() != ".csv":
        raise ValueError(f"Pipeline input must be a CSV or directory: {resolved}")
    if resolved.stem not in TABLE_SCHEMAS:
        raise ValueError(
            f"CSV name must match a source table: {', '.join(TABLE_SCHEMAS)}."
        )
    raw_dir = resolved.parent
    missing = [
        f"{table_name}.csv"
        for table_name in TABLE_SCHEMAS
        if not (raw_dir / f"{table_name}.csv").is_file()
    ]
    if missing:
        raise FileNotFoundError(
            "A complete source bundle is required to rebuild the database; "
            "missing beside the input CSV: "
            + ", ".join(missing)
        )
    return raw_dir


def _audit_table() -> Table:
    metadata = MetaData()
    return Table(
        "audit_log",
        metadata,
        Column("run_id", String(36), primary_key=True),
        Column("step", String(64), primary_key=True),
        Column("status", String(16), nullable=False),
        Column("started_at", DateTime(timezone=True), nullable=False),
        Column("ended_at", DateTime(timezone=True)),
        Column("rows_processed", Integer, nullable=False, default=0),
        Column("message", Text, nullable=False, default=""),
    )


def _ensure_audit_table(engine: Engine) -> Table:
    table = _audit_table()
    table.metadata.create_all(engine, tables=[table])
    return table


@contextmanager
def _audited_step(
    engine: Engine,
    table: Table,
    run_id: str,
    step: str,
) -> Generator[dict[str, Any], None, None]:
    started_at = datetime.now(UTC)
    state: dict[str, Any] = {"rows_processed": 0, "message": ""}
    with engine.begin() as connection:
        connection.execute(
            table.insert().values(
                run_id=run_id,
                step=step,
                status="running",
                started_at=started_at,
                ended_at=None,
                rows_processed=0,
                message="",
            )
        )
    try:
        yield state
    except Exception as error:
        with engine.begin() as connection:
            connection.execute(
                update(table)
                .where(table.c.run_id == run_id, table.c.step == step)
                .values(
                    status="failed",
                    ended_at=datetime.now(UTC),
                    rows_processed=int(state["rows_processed"]),
                    message=str(error)[:4000],
                )
            )
        raise
    else:
        with engine.begin() as connection:
            connection.execute(
                update(table)
                .where(table.c.run_id == run_id, table.c.step == step)
                .values(
                    status="completed",
                    ended_at=datetime.now(UTC),
                    rows_processed=int(state["rows_processed"]),
                    message=str(state["message"])[:4000],
                )
            )


def _mark_skipped_steps(
    engine: Engine,
    table: Table,
    run_id: str,
    steps: tuple[str, ...],
    message: str,
) -> None:
    now = datetime.now(UTC)
    with engine.begin() as connection:
        for step in steps:
            connection.execute(
                table.insert().values(
                    run_id=run_id,
                    step=step,
                    status="skipped",
                    started_at=now,
                    ended_at=now,
                    rows_processed=0,
                    message=message[:4000],
                )
            )


def _latest_periods(engine: Engine) -> tuple[str, str]:
    """Return the two latest order months without dialect-specific SQL."""
    with engine.connect() as connection:
        dates = pd.read_sql_query(
            text("SELECT order_date FROM orders"),
            connection,
        )["order_date"]
    months = sorted(pd.to_datetime(dates, errors="raise").dt.to_period("M").unique())
    if len(months) < 2:
        raise ValueError("At least two distinct order months are required.")
    return str(months[-2]), str(months[-1])


def evaluate_alerts(engine: Engine, period_b: str) -> list[dict[str, Any]]:
    """Evaluate the three business thresholds against computed database facts."""
    with engine.connect() as connection:
        orders = pd.read_sql_query(
            text("SELECT order_date, order_status FROM orders"),
            connection,
        )
        supplier_query_path = (
            _project_root() / "sql" / "kpi_queries" / "05_supplier_delay.sql"
        )
        supplier_query = supplier_query_path.read_text(encoding="utf-8").strip()
        if not supplier_query:
            raise ValueError(f"Supplier query is empty: {supplier_query_path}")
        suppliers = connection.execute(text(supplier_query)).mappings().all()

        stockout_rows = connection.execute(
            text(
                """
                SELECT
                    p.sku_group,
                    COUNT(*) AS snapshot_count,
                    SUM(CASE WHEN i.stockout_flag THEN 1 ELSE 0 END)
                        AS stockout_snapshots,
                    1.0 * SUM(CASE WHEN i.stockout_flag THEN 1 ELSE 0 END)
                        / NULLIF(COUNT(*), 0) AS stockout_rate
                FROM inventory_snapshots AS i
                JOIN products AS p ON p.product_id = i.product_id
                WHERE i.snapshot_date >= :period_start
                  AND i.snapshot_date < :next_period_start
                GROUP BY p.sku_group
                ORDER BY stockout_rate DESC, p.sku_group
                """
            ),
            {
                "period_start": pd.Period(period_b, freq="M").start_time.date(),
                "next_period_start": (
                    pd.Period(period_b, freq="M") + 1
                ).start_time.date(),
            },
        ).mappings().all()

    orders["order_date"] = pd.to_datetime(orders["order_date"], errors="raise")
    monthly_orders = orders.loc[
        orders["order_date"].dt.to_period("M").eq(pd.Period(period_b, freq="M"))
    ]
    if monthly_orders.empty:
        raise ValueError(f"No orders found for alert period {period_b}.")
    fulfilment_rate = float(
        monthly_orders["order_status"].eq("fulfilled").mean()
    )

    alerts: list[dict[str, Any]] = []
    if fulfilment_rate < FULFILMENT_THRESHOLD:
        alerts.append(
            {
                "type": "low_fulfilment",
                "period": period_b,
                "value": fulfilment_rate,
                "threshold": FULFILMENT_THRESHOLD,
                "message": (
                    f"Fulfilment in {period_b} is {fulfilment_rate:.1%}, "
                    f"below the {FULFILMENT_THRESHOLD:.0%} threshold."
                ),
            }
        )
    for supplier in suppliers:
        gap = float(supplier["difference_from_average"])
        if gap > SUPPLIER_GAP_THRESHOLD:
            alerts.append(
                {
                    "type": "supplier_late_rate",
                    "supplier_id": str(supplier["supplier_id"]),
                    "supplier_name": str(supplier["supplier_name"]),
                    "value": float(supplier["supplier_late_rate"]),
                    "average": float(supplier["average_supplier_late_rate"]),
                    "gap": gap,
                    "threshold": SUPPLIER_GAP_THRESHOLD,
                    "message": (
                        f"{supplier['supplier_name']} late rate is "
                        f"{float(supplier['supplier_late_rate']):.1%}, "
                        f"{gap:.1%} above the supplier average."
                    ),
                }
            )
    for row in stockout_rows:
        stockout_rate = float(row["stockout_rate"])
        if stockout_rate > STOCKOUT_RATE_THRESHOLD:
            alerts.append(
                {
                    "type": "high_stockout_rate",
                    "period": period_b,
                    "sku_group": str(row["sku_group"]),
                    "value": stockout_rate,
                    "stockout_snapshots": int(row["stockout_snapshots"]),
                    "snapshot_count": int(row["snapshot_count"]),
                    "threshold": STOCKOUT_RATE_THRESHOLD,
                    "message": (
                        f"SKU group {row['sku_group']} stock-out rate is "
                        f"{stockout_rate:.1%} in {period_b}, above "
                        f"{STOCKOUT_RATE_THRESHOLD:.0%}."
                    ),
                }
            )
    return alerts


def _write_summary_report(
    path: Path,
    *,
    run_id: str,
    status: str,
    input_path: Path,
    period_a: str | None,
    period_b: str | None,
    steps: list[dict[str, Any]],
    alerts: list[dict[str, Any]],
    answer: str | None,
    error: str | None,
    duration: float,
) -> None:
    """Write a human-readable run summary in Markdown."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Business Operations Pipeline Run",
        "",
        f"- Run ID: `{run_id}`",
        f"- Status: **{status}**",
        f"- Input: `{input_path}`",
        f"- Comparison periods: `{period_a or 'not available'}` → "
        f"`{period_b or 'not available'}`",
        f"- Duration: {duration:.2f} seconds",
        "",
        "## Steps",
        "",
        "| Step | Status | Rows | Message |",
        "|---|---|---:|---|",
    ]
    lines.extend(
        f"| {step['step']} | {step['status']} | "
        f"{step['rows_processed']} | {str(step['message']).replace('|', '/')} |"
        for step in steps
    )
    lines.extend(["", "## Alerts", ""])
    if alerts:
        lines.extend(f"- **{alert['type']}**: {alert['message']}" for alert in alerts)
    else:
        lines.append("- No thresholds were breached.")
    if answer:
        lines.extend(["", "## AI Explanation", "", answer])
    if error:
        lines.extend(["", "## Error", "", error])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_pipeline(
    file_path: Path,
    *,
    question: str = "Why did fulfilment fall this month?",
    engine: Engine | None = None,
) -> dict[str, Any]:
    """Run, audit, and summarize each pipeline stage for a complete CSV bundle."""
    started = time.perf_counter()
    run_id = str(uuid4())
    project_root = _project_root()
    processed_dir = _processed_dir(project_root)
    report_dir = processed_dir / "reports"
    report_path = report_dir / f"pipeline_{run_id}.md"
    input_path = file_path.expanduser().resolve()
    raw_dir: Path | None = None
    active_engine = engine or create_engine(_database_url(project_root), future=True)
    owns_engine = engine is None
    steps: list[dict[str, Any]] = []
    alerts: list[dict[str, Any]] = []
    answer: str | None = None
    period_a: str | None = None
    period_b: str | None = None
    error_message: str | None = None
    status = "completed"
    current_step: str | None = None

    try:
        audit_table = _ensure_audit_table(active_engine)
        current_step = "validate"
        with _audited_step(active_engine, audit_table, run_id, "validate") as state:
            raw_dir = _resolve_raw_dir(input_path)
            validation = validate_csv_directory(raw_dir)
            validation_path = processed_dir / "validation_report.json"
            write_report(validation, validation_path)
            state["rows_processed"] = sum(
                int(table_report["rows"])
                for table_report in validation["tables"].values()
            )
            state["message"] = (
                f"{validation['totals']['issue_count']} findings; report "
                f"{validation_path}"
            )
        steps.append(
            {
                "step": "validate",
                "status": "completed",
                "rows_processed": state["rows_processed"],
                "message": state["message"],
            }
        )

        current_step = "clean"
        with _audited_step(active_engine, audit_table, run_id, "clean") as state:
            cleaning = clean_csv_directory(
                raw_dir,
                processed_dir / "validation_report.json",
                processed_dir,
            )
            state["rows_processed"] = int(cleaning["totals"]["rows_after"])
            state["message"] = (
                f"{cleaning['totals']['rejected']} rows quarantined; "
                f"{state['rows_processed']} rows retained"
            )
        steps.append(
            {
                "step": "clean",
                "status": "completed",
                "rows_processed": state["rows_processed"],
                "message": state["message"],
            }
        )

        current_step = "reload_database"
        with _audited_step(
            active_engine, audit_table, run_id, "reload_database"
        ) as state:
            cleaning_summary = _read_cleaning_summary(
                processed_dir / "cleaning_summary.json"
            )
            row_counts = rebuild_database(
                active_engine,
                processed_dir / "clean",
                cleaning_summary,
            )
            state["rows_processed"] = sum(row_counts.values())
            state["message"] = (
                f"Rebuilt {len(row_counts)} tables; "
                f"{state['rows_processed']} rows loaded"
            )
        steps.append(
            {
                "step": "reload_database",
                "status": "completed",
                "rows_processed": state["rows_processed"],
                "message": state["message"],
            }
        )

        current_step = "run_analytics"
        with _audited_step(
            active_engine, audit_table, run_id, "run_analytics"
        ) as state:
            analytics = run_analytics(active_engine)
            state["rows_processed"] = sum(len(frame) for frame in analytics.values())
            state["message"] = f"Updated {len(analytics)} analytics tables"
        steps.append(
            {
                "step": "run_analytics",
                "status": "completed",
                "rows_processed": state["rows_processed"],
                "message": state["message"],
            }
        )

        current_step = "root_cause"
        with _audited_step(active_engine, audit_table, run_id, "root_cause") as state:
            period_a, period_b = _latest_periods(active_engine)
            from src.analytics.root_cause import _load_source_tables

            root_result = decompose_fulfilment(
                _load_source_tables(active_engine),
                period_a,
                period_b,
            )
            root_report_path = processed_dir / "root_cause_latest.json"
            save_result(root_result, root_report_path)
            state["rows_processed"] = len(root_result["top_factors"])
            state["message"] = (
                f"Analysed {period_a} → {period_b}; "
                f"saved {root_report_path}"
            )
        steps.append(
            {
                "step": "root_cause",
                "status": "completed",
                "rows_processed": state["rows_processed"],
                "message": state["message"],
            }
        )

        current_step = "ai_reasoner"
        with _audited_step(active_engine, audit_table, run_id, "ai_reasoner") as state:
            answer = reason_about(
                question,
                report_path=root_report_path,
                engine=active_engine,
            )
            state["rows_processed"] = 1
            state["message"] = "Generated and audited evidence-grounded explanation"
        steps.append(
            {
                "step": "ai_reasoner",
                "status": "completed",
                "rows_processed": 1,
                "message": "Generated and audited evidence-grounded explanation",
            }
        )

        current_step = "evaluate_alerts"
        with _audited_step(
            active_engine, audit_table, run_id, "evaluate_alerts"
        ) as state:
            if period_b is None:
                raise ValueError("Root-cause analysis did not set the latest period.")
            alerts = evaluate_alerts(active_engine, period_b)
            state["rows_processed"] = len(alerts)
            state["message"] = (
                f"{len(alerts)} threshold alerts"
                if alerts
                else "No alert thresholds breached"
            )
        steps.append(
            {
                "step": "evaluate_alerts",
                "status": "completed",
                "rows_processed": len(alerts),
                "message": f"{len(alerts)} threshold alerts",
            }
        )
        current_step = None

    except Exception as error:
        status = "failed"
        error_message = f"{type(error).__name__}: {error}"
        LOGGER.exception("Pipeline run %s failed.", run_id)
        if current_step is not None:
            steps.append(
                {
                    "step": current_step,
                    "status": "failed",
                    "rows_processed": 0,
                    "message": error_message,
                }
            )
        elif not steps:
            steps.append(
                {
                    "step": "validate",
                    "status": "failed",
                    "rows_processed": 0,
                    "message": error_message,
                }
            )
    finally:
        completed = {step["step"] for step in steps}
        missing_steps = tuple(
            step
            for step in PIPELINE_STEPS[:-1]
            if step not in completed
        )
        if missing_steps:
            try:
                audit_table = _ensure_audit_table(active_engine)
                _mark_skipped_steps(
                    active_engine,
                    audit_table,
                    run_id,
                    missing_steps,
                    error_message or "Step was not reached.",
                )
                steps.extend(
                    {
                        "step": step,
                        "status": "skipped",
                        "rows_processed": 0,
                        "message": error_message or "Step was not reached.",
                    }
                    for step in missing_steps
                )
            except SQLAlchemyError:
                LOGGER.exception("Failed to write skipped-step audit records.")
                status = "failed"
                error_message = (
                    (error_message + "; " if error_message else "")
                    + "Could not audit skipped steps."
                )

        duration = time.perf_counter() - started
        summary_state = {
            "step": "summary_report",
            "status": "completed",
            "rows_processed": 1,
            "message": str(report_path),
        }
        steps.append(summary_state)
        try:
            if "audit_table" in locals():
                with _audited_step(
                    active_engine, audit_table, run_id, "summary_report"
                ) as summary_audit_state:
                    _write_summary_report(
                        report_path,
                        run_id=run_id,
                        status=status,
                        input_path=input_path,
                        period_a=period_a,
                        period_b=period_b,
                        steps=steps,
                        alerts=alerts,
                        answer=answer,
                        error=error_message,
                        duration=duration,
                    )
                    summary_audit_state["rows_processed"] = 1
                    summary_audit_state["message"] = str(report_path)
            else:
                _write_summary_report(
                    report_path,
                    run_id=run_id,
                    status=status,
                    input_path=input_path,
                    period_a=period_a,
                    period_b=period_b,
                    steps=steps,
                    alerts=alerts,
                    answer=answer,
                    error=error_message,
                    duration=duration,
                )
            if "audit_table" not in locals():
                audit_table = _ensure_audit_table(active_engine)
                with active_engine.begin() as connection:
                    connection.execute(
                        audit_table.insert().values(
                            run_id=run_id,
                            step="summary_report",
                            status="completed",
                            started_at=datetime.now(UTC),
                            ended_at=datetime.now(UTC),
                            rows_processed=1,
                            message=str(report_path),
                        )
                    )
        except (OSError, SQLAlchemyError):
            LOGGER.exception("Failed to write pipeline summary report.")
            status = "failed"
            summary_state["status"] = "failed"
            summary_state["message"] = "Failed to write Markdown summary."
            error_message = (
                (error_message + "; " if error_message else "")
                + "Could not write the Markdown summary."
            )
        if owns_engine:
            active_engine.dispose()

    duration = time.perf_counter() - started
    return {
        "status": status,
        "steps": steps,
        "duration": round(duration, 3),
        "alerts": alerts,
    }


def main() -> None:
    """Parse the input batch and print the final JSON status object."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--file",
        required=True,
        type=Path,
        help="Dataset directory or CSV inside a complete ten-table source bundle.",
    )
    parser.add_argument(
        "--question",
        default="Why did fulfilment fall this month?",
        help="Question for the AI reasoning step.",
    )
    arguments = parser.parse_args()
    setup_logging()
    result = run_pipeline(arguments.file, question=arguments.question)
    print(json.dumps(result, indent=2, ensure_ascii=True))
    if result["status"] != "completed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
