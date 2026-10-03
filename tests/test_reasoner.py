"""Tests for evidence-grounded business explanations."""

from types import SimpleNamespace
from unittest.mock import Mock

from sqlalchemy import create_engine, text

from src.ai_layer.reasoner import (
    collect_evidence,
    deterministic_answer,
    generate_answer,
    validate_answer,
)


def _facts() -> dict[str, object]:
    return {
        "kpi": "fulfilment",
        "period_a": "2025-05",
        "period_b": "2025-06",
        "overall": {
            "value_a": "94.00%",
            "value_b": "88.00%",
            "change_pp": "-6.00",
        },
        "warehouse": {
            "segment": "WH-A",
            "rate_a": "90.00%",
            "rate_b": "55.00%",
            "contribution_pp": "-9.50",
            "share_of_unfulfilled": "88.0%",
        },
        "supplier": {
            "above_average": True,
            "segment": "SUP-Z",
            "late_rate": "100.0%",
            "all_supplier_average_late_rate": "55.6%",
            "gap_pp": "+44.44",
        },
        "sku_group": {
            "segment": "Z",
            "stockout_rate": "75.0%",
            "stockout_snapshots": "3",
            "snapshot_count": "4",
            "all_stockout_snapshots": "5",
            "share_of_stockout_snapshots": "60.0%",
        },
    }


def _valid_answer() -> str:
    return (
        "Finding: Fulfilment changed by -6.00 percentage points.\n"
        "Evidence:\n"
        "- Warehouse WH-A fulfilment was 55.00%.\n"
        "- Supplier SUP-Z had a late rate of 100.0% versus the all-supplier "
        "average of 55.6%, a gap of +44.44 percentage points.\n"
        "- SKU group Z had a stock-out rate of 75.0% (3 of 4 snapshots), "
        "representing 60.0% of all 5 stock-out snapshots in the period.\n"
        "Recommendation:\n"
        "- Review WH-A processing and backlog.\n"
        "- Review supplier SUP-Z and replenishment for SKU group Z."
    )


def _response(content: str) -> SimpleNamespace:
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=content)]
    )


def test_deterministic_fallback_has_required_sections_and_is_audited() -> None:
    """Without a key, the renderer still validates and persists its answer."""
    engine = create_engine("sqlite://")
    try:
        answer = generate_answer(
            "Why did fulfilment fall this month?",
            _facts(),
            engine,
            api_key=None,
        )
        assert validate_answer(answer, _facts()) == []
        assert "Finding:" in answer
        assert "Evidence:" in answer
        assert "Recommendation:" in answer
        assert "investigate late deliveries from supplier SUP-Z" in answer
        assert "Review replenishment and inventory controls for SKU group Z" in answer
        with engine.connect() as connection:
            audit = connection.execute(
                text(
                    "SELECT prompt, response, validation_passed, attempt "
                    "FROM ai_audit_log"
                )
            ).one()
        assert "Computed evidence" in audit.prompt
        assert audit.response == answer
        assert bool(audit.validation_passed)
        assert audit.attempt == 1
    finally:
        engine.dispose()


def test_mocked_anthropic_response_passes_numeric_validation() -> None:
    """The configured client receives computed facts and returns formatted text."""
    engine = create_engine("sqlite://")
    client = Mock()
    client.messages.create.return_value = _response(_valid_answer())
    try:
        answer = generate_answer(
            "Why did fulfilment fall this month?",
            _facts(),
            engine,
            api_key="test-key",
            client=client,
        )
        assert answer == _valid_answer()
        client.messages.create.assert_called_once()
        assert client.messages.create.call_args.kwargs["model"] == "claude-sonnet-5-5"
        assert validate_answer(answer, _facts()) == []
    finally:
        engine.dispose()


def test_unsupported_number_is_rejected_and_retried_once() -> None:
    """A hallucinated numeric value is audited as rejected before one retry."""
    engine = create_engine("sqlite://")
    client = Mock()
    client.messages.create.side_effect = [
        _response(_valid_answer().replace("55.00%", "999.00%")),
        _response(_valid_answer()),
    ]
    try:
        answer = generate_answer(
            "Why did fulfilment fall this month?",
            _facts(),
            engine,
            api_key="test-key",
            client=client,
        )
        assert answer == _valid_answer()
        assert client.messages.create.call_count == 2
        with engine.connect() as connection:
            audits = connection.execute(
                text(
                    "SELECT validation_passed, validation_error, attempt, prompt "
                    "FROM ai_audit_log ORDER BY attempt"
                )
            ).all()
        assert [row.validation_passed for row in audits] == [False, True]
        assert [row.attempt for row in audits] == [1, 2]
        assert "999" not in audits[1].prompt
    finally:
        engine.dispose()


def test_evidence_queries_supplier_lateness_and_period_stockouts() -> None:
    """Select the top above-average supplier and query period-filtered stock-outs."""
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
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
            "INSERT INTO suppliers VALUES ('SUP-X', 'Supplier X'), "
            "('SUP-Y', 'Supplier Y'), ('SUP-Z', 'Supplier Z')"
        )
        connection.exec_driver_sql(
            "INSERT INTO deliveries VALUES "
            "('D1', 'SUP-X', 1), ('D2', 'SUP-X', 1), "
            "('D3', 'SUP-X', 0), ('D4', 'SUP-Y', 0), "
            "('D5', 'SUP-Z', 1), ('D6', 'SUP-Z', 1)"
        )
        connection.exec_driver_sql(
            "INSERT INTO products VALUES ('P-Y', 'Y'), ('P-Z', 'Z')"
        )
        connection.exec_driver_sql(
            "INSERT INTO inventory_snapshots VALUES "
            "('P-Y', '2025-06-01', 1), ('P-Y', '2025-06-02', 0), "
            "('P-Y', '2025-06-03', 1), "
            "('P-Y', '2025-07-01', 1), "
            "('P-Z', '2025-06-01', 1), ('P-Z', '2025-06-02', 1), "
            "('P-Z', '2025-06-03', 1), ('P-Z', '2025-06-04', 0)"
        )
    report = {
        "kpi": "fulfilment",
        "period_a": "2025-05",
        "period_b": "2025-06",
        "value_a": 0.94,
        "value_b": 0.88,
        "change": -0.06,
        "top_factors": [
            {
                "dimension": "warehouse",
                "segment": "WH-A",
                "rate_a": 0.9,
                "rate_b": 0.55,
                "contribution_pp": -9.5,
                "share_of_unfulfilled": 0.88,
            },
            {
                "dimension": "supplier",
                "segment": "SUP-X",
                "rate_a": 0.98,
                "rate_b": 0.89,
                "contribution_pp": -2.5,
            },
            {
                "dimension": "sku_group",
                "segment": "Y",
                "rate_a": 0.89,
                "rate_b": 0.82,
                "contribution_pp": -1.4,
            },
        ],
    }
    try:
        facts = collect_evidence(report, engine)
        assert facts["supplier"]["segment"] == "SUP-Z"
        assert facts["supplier"]["late_rate"] == "100.0%"
        assert facts["supplier"]["all_supplier_average_late_rate"] == "55.6%"
        assert facts["supplier"]["gap_pp"] == "+44.44"
        assert facts["sku_group"]["segment"] == "Z"
        assert facts["sku_group"]["stockout_rate"] == "75.0%"
        assert facts["sku_group"]["stockout_snapshots"] == "3"
        assert facts["sku_group"]["snapshot_count"] == "4"
        assert facts["sku_group"]["all_stockout_snapshots"] == "5"
        assert facts["sku_group"]["share_of_stockout_snapshots"] == "60.0%"
        answer = deterministic_answer(facts)
        assert "gap of +44.44 percentage points" in answer
        assert "Supplier SUP-Z" in answer
        assert "SKU group Z" in answer
        assert "representing 60.0% of all 5 stock-out snapshots" in answer
        assert validate_answer(answer, facts) == []
    finally:
        engine.dispose()


def test_fallback_says_no_supplier_is_above_average() -> None:
    """When query 05 has no positive gap, fallback names no supplier."""
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
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
            "INSERT INTO suppliers VALUES ('SUP-X', 'Supplier X'), "
            "('SUP-Y', 'Supplier Y')"
        )
        connection.exec_driver_sql(
            "INSERT INTO deliveries VALUES ('D1', 'SUP-X', 0), "
            "('D2', 'SUP-Y', 0)"
        )
        connection.exec_driver_sql("INSERT INTO products VALUES ('P-Y', 'Y')")
        connection.exec_driver_sql(
            "INSERT INTO inventory_snapshots VALUES ('P-Y', '2025-06-01', 0)"
        )
    report = {
        "kpi": "fulfilment",
        "period_a": "2025-05",
        "period_b": "2025-06",
        "value_a": 0.94,
        "value_b": 0.88,
        "change": -0.06,
        "top_factors": [
            {
                "dimension": "warehouse",
                "segment": "WH-A",
                "rate_a": 0.9,
                "rate_b": 0.55,
                "contribution_pp": -9.5,
                "share_of_unfulfilled": 0.88,
            },
            {
                "dimension": "sku_group",
                "segment": "Y",
                "rate_a": 0.89,
                "rate_b": 0.82,
                "contribution_pp": -1.4,
            },
        ],
    }
    try:
        facts = collect_evidence(report, engine)
        answer = deterministic_answer(facts)
        assert facts["supplier"] == {"above_average": False}
        assert "- No supplier had a late-delivery rate above the all-supplier average." in answer
        assert "Supplier SUP-" not in answer
        assert validate_answer(answer, facts) == []
    finally:
        engine.dispose()


def test_validation_rejects_missing_evidence_bullet_and_unsupported_number() -> None:
    """Output validation rejects incorrect shape as well as invented values."""
    malformed = _valid_answer().replace("55.00%", "999.00%").replace(
        "representing 60.0%", "representing 12.0%"
    )
    errors = validate_answer(malformed, _facts())

    assert any("SKU-group evidence" in error for error in errors)
    assert any("numeric values" in error for error in errors)


def test_validation_requires_selected_sku_group_in_recommendation() -> None:
    """Recommendations cannot point to a different SKU group than the evidence."""
    answer = _valid_answer().replace(
        "replenishment for SKU group Z",
        "replenishment for SKU group Y",
    )

    errors = validate_answer(answer, _facts())

    assert any("Recommendation must use the SKU group" in error for error in errors)
