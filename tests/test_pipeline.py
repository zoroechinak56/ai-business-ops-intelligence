"""Tests for pipeline orchestration, alerts, and run-level audit records."""

import json
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text

from src.analytics import root_cause
from src.automation import run_pipeline as pipeline
from src.validation.validator import TABLE_SCHEMAS


def _alert_database():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE orders (order_date DATE, order_status TEXT)"
        )
        connection.exec_driver_sql(
            "CREATE TABLE suppliers (supplier_id TEXT, supplier_name TEXT)"
        )
        connection.exec_driver_sql(
            "CREATE TABLE deliveries "
            "(delivery_id TEXT, supplier_id TEXT, supplier_late BOOLEAN)"
        )
        connection.exec_driver_sql(
            "CREATE TABLE products (product_id TEXT, sku_group TEXT)"
        )
        connection.exec_driver_sql(
            "CREATE TABLE inventory_snapshots "
            "(product_id TEXT, snapshot_date DATE, stockout_flag BOOLEAN)"
        )
        connection.exec_driver_sql(
            "INSERT INTO orders VALUES "
            "('2025-06-01', 'fulfilled'), ('2025-06-02', 'unfulfilled'), "
            "('2025-06-03', 'unfulfilled'), ('2025-06-04', 'unfulfilled'), "
            "('2025-05-01', 'fulfilled')"
        )
        connection.exec_driver_sql(
            "INSERT INTO suppliers VALUES "
            "('SUP-A', 'Supplier A'), ('SUP-B', 'Supplier B')"
        )
        connection.exec_driver_sql(
            "INSERT INTO deliveries VALUES "
            "('D1', 'SUP-A', 1), ('D2', 'SUP-A', 1), ('D3', 'SUP-A', 1), "
            "('D4', 'SUP-B', 0)"
        )
        connection.exec_driver_sql(
            "INSERT INTO products VALUES ('P1', 'Y'), ('P2', 'A')"
        )
        connection.exec_driver_sql(
            "INSERT INTO inventory_snapshots VALUES "
            "('P1', '2025-06-01', 1), ('P1', '2025-06-02', 1), "
            "('P1', '2025-06-03', 0), ('P2', '2025-06-01', 0), "
            "('P2', '2025-06-02', 0)"
        )
    return engine


def test_alert_rules_use_fulfilment_supplier_gap_and_stockout_rates() -> None:
    """All three independent threshold rules emit numeric alert evidence."""
    engine = _alert_database()
    try:
        alerts = pipeline.evaluate_alerts(engine, "2025-06")
    finally:
        engine.dispose()

    assert {alert["type"] for alert in alerts} == {
        "low_fulfilment",
        "supplier_late_rate",
        "high_stockout_rate",
    }
    fulfilment = next(alert for alert in alerts if alert["type"] == "low_fulfilment")
    supplier = next(alert for alert in alerts if alert["type"] == "supplier_late_rate")
    stockout = next(alert for alert in alerts if alert["type"] == "high_stockout_rate")
    assert fulfilment["value"] == 0.25
    assert supplier["supplier_id"] == "SUP-A"
    assert supplier["gap"] > 0.10
    assert stockout["sku_group"] == "Y"
    assert stockout["value"] == 2 / 3


def test_pipeline_orchestrates_steps_writes_report_and_audit(
    tmp_path: Path, monkeypatch
) -> None:
    """Small mocked source bundle runs the ordered workflow and persists audit."""
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    for table_name in TABLE_SCHEMAS:
        (raw_dir / f"{table_name}.csv").write_text("", encoding="utf-8")

    processed_dir = tmp_path / "processed"
    monkeypatch.setattr(pipeline, "_processed_dir", lambda _root: processed_dir)
    monkeypatch.setattr(
        pipeline,
        "validate_csv_directory",
        lambda _directory: {
            "tables": {"orders": {"rows": 2}},
            "totals": {"issue_count": 0},
        },
    )
    monkeypatch.setattr(pipeline, "clean_csv_directory", lambda *_args: {
        "totals": {"rows_after": 2, "rejected": 0}
    })
    monkeypatch.setattr(pipeline, "_read_cleaning_summary", lambda _path: {})
    monkeypatch.setattr(
        pipeline,
        "rebuild_database",
        lambda *_args: {"orders": 2, "dim_date": 181},
    )
    monkeypatch.setattr(
        pipeline,
        "run_analytics",
        lambda _engine: {"order_features": pd.DataFrame([{"order_id": "O1"}])},
    )
    monkeypatch.setattr(pipeline, "_latest_periods", lambda _engine: ("2025-05", "2025-06"))
    monkeypatch.setattr(root_cause, "_load_source_tables", lambda _engine: {})
    monkeypatch.setattr(
        pipeline,
        "decompose_fulfilment",
        lambda *_args: {
            "kpi": "fulfilment",
            "period_a": "2025-05",
            "period_b": "2025-06",
            "top_factors": [],
        },
    )
    monkeypatch.setattr(pipeline, "reason_about", lambda *_args, **_kwargs: "Finding.")
    monkeypatch.setattr(pipeline, "evaluate_alerts", lambda *_args: [])

    engine = create_engine("sqlite://")
    try:
        result = pipeline.run_pipeline(raw_dir, engine=engine)
        assert set(result) == {"status", "steps", "duration", "alerts"}
        assert result["status"] == "completed"
        assert not result["alerts"]
        assert [step["step"] for step in result["steps"]] == list(
            pipeline.PIPELINE_STEPS
        )
        assert all(step["status"] == "completed" for step in result["steps"])
        with engine.connect() as connection:
            audit_rows = connection.execute(
                text(
                    "SELECT step, status FROM audit_log "
                    "ORDER BY started_at, step"
                )
            ).all()
        assert {row.step for row in audit_rows} == set(pipeline.PIPELINE_STEPS)
        assert all(row.status == "completed" for row in audit_rows)
        report_path = Path(
            next(
                step["message"]
                for step in result["steps"]
                if step["step"] == "summary_report"
            )
        )
        report = report_path.read_text(encoding="utf-8")
        assert "# Business Operations Pipeline Run" in report
        assert "No thresholds were breached." in report
    finally:
        engine.dispose()


def test_file_input_requires_complete_bundle(tmp_path: Path) -> None:
    """A single CSV cannot trigger a destructive partial database rebuild."""
    input_file = tmp_path / "orders.csv"
    input_file.write_text("order_id\nO1\n", encoding="utf-8")

    try:
        pipeline._resolve_raw_dir(input_file)
    except FileNotFoundError as error:
        assert "complete source bundle is required" in str(error)
    else:
        raise AssertionError("Expected missing companion CSVs to fail.")


def test_failed_validation_is_audited_and_later_steps_are_skipped(
    tmp_path: Path, monkeypatch
) -> None:
    """Invalid input records one failed validation and each remaining step."""
    monkeypatch.setattr(pipeline, "_processed_dir", lambda _root: tmp_path / "processed")
    input_file = tmp_path / "orders.csv"
    input_file.write_text("order_id\nO1\n", encoding="utf-8")
    engine = create_engine("sqlite://")
    try:
        result = pipeline.run_pipeline(input_file, engine=engine)
        assert result["status"] == "failed"
        with engine.connect() as connection:
            audit_rows = connection.execute(
                text("SELECT step, status FROM audit_log")
            ).all()
        audit = {row.step: row.status for row in audit_rows}
        assert len(audit_rows) == len(pipeline.PIPELINE_STEPS)
        assert audit["validate"] == "failed"
        assert audit["clean"] == "skipped"
        assert audit["summary_report"] == "completed"
        report = Path(
            next(
                step["message"]
                for step in result["steps"]
                if step["step"] == "summary_report"
            )
        ).read_text(encoding="utf-8")
        assert "| summary_report | completed |" in report
    finally:
        engine.dispose()


def test_n8n_workflow_contains_retry_alert_audit_and_summary_nodes() -> None:
    """The exported workflow remains valid JSON with all required paths wired."""
    workflow_path = Path(__file__).parents[1] / "n8n" / "workflow.json"
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    nodes = {node["name"]: node for node in workflow["nodes"]}
    assert {
        "Webhook Trigger",
        "Run Analytics Pipeline",
        "Wait Before One Retry",
        "Retry Analytics Pipeline Once",
        "Any Alerts?",
        "Send Threshold Alert",
        "Audit Pipeline Completion",
        "Send Final Summary",
        "Audit Retry Failure",
    }.issubset(nodes)
    assert nodes["Run Analytics Pipeline"]["onError"] == "continueErrorOutput"
    assert nodes["Retry Analytics Pipeline Once"]["onError"] == "continueErrorOutput"
    assert nodes["Any Alerts?"]["type"] == "n8n-nodes-base.if"
    assert nodes["Audit Pipeline Completion"]["type"] == "n8n-nodes-base.postgres"
