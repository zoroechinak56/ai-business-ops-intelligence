"""Generate evidence-grounded findings and recommendations for KPI changes."""

from __future__ import annotations

import argparse
from datetime import date
import json
import logging
from pathlib import Path
import re
from typing import Any

from anthropic import Anthropic
from anthropic import AnthropicError
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    func,
    text,
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from src.config import settings
from src.logger import setup_logging


LOGGER = logging.getLogger(__name__)
NUMBER_PATTERN = re.compile(
    r"(?<![A-Za-z])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
)
SYSTEM_PROMPT = """You are a business operations analyst.
Use only the computed facts supplied in the user message as evidence.
Do not introduce, calculate, infer, or repeat any number that is not present
in those facts. If evidence is insufficient, say so without adding numbers.
Return exactly this format:
Finding: one sentence stating the KPI change.
Evidence:
- one warehouse bullet with numerical facts
- one supplier bullet stating the highest above-average supplier's late rate,
  all-supplier average, and gap in percentage points; if none is above average,
  explicitly say that no supplier is above average
- one SKU-group bullet stating the highest-stock-out group's rate, snapshot
  counts, and share of all stock-out snapshots in the period
Recommendation:
- one or two concise, actionable recommendations
Do not add other sections or prose."""


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _database_url(project_root: Path) -> str:
    """Return configured database URL or the project SQLite fallback."""
    if settings.database_url:
        return settings.database_url
    processed_dir = settings.data_processed_dir or project_root / "data" / "processed"
    processed_dir.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{(processed_dir / 'business_ops.sqlite').as_posix()}"


def _report_path() -> Path:
    processed_dir = settings.data_processed_dir or _project_root() / "data" / "processed"
    return processed_dir / "root_cause_latest.json"


def load_root_cause_report(path: Path | None = None) -> dict[str, Any]:
    """Read and minimally validate the latest root-cause JSON report."""
    report_file = path or _report_path()
    with report_file.open(encoding="utf-8") as input_file:
        report = json.load(input_file)
    if not isinstance(report, dict):
        raise ValueError(f"Root-cause report must be a JSON object: {report_file}")
    required = {"kpi", "period_a", "period_b", "value_a", "value_b", "change", "top_factors"}
    missing = required - report.keys()
    if missing:
        raise ValueError(
            f"Root-cause report is missing fields: {', '.join(sorted(missing))}"
        )
    if not isinstance(report["top_factors"], list):
        raise ValueError("Root-cause report top_factors must be a list.")
    return report


def _month_bounds(period: str) -> tuple[date, date]:
    """Parse YYYY-MM and return an inclusive start/exclusive next-month bound."""
    if not re.fullmatch(r"\d{4}-\d{2}", period):
        raise ValueError(f"Invalid period '{period}'; expected YYYY-MM.")
    year, month = (int(part) for part in period.split("-"))
    try:
        month_start = date(year, month, 1)
        next_month = (
            date(year + 1, 1, 1)
            if month == 12
            else date(year, month + 1, 1)
        )
    except ValueError as error:
        raise ValueError(f"Invalid period '{period}'; expected YYYY-MM.") from error
    return month_start, next_month


def _top_factor(report: dict[str, Any], dimension: str) -> dict[str, Any]:
    """Return the most adverse reported factor in the requested dimension."""
    factors = [
        factor
        for factor in report["top_factors"]
        if isinstance(factor, dict) and factor.get("dimension") == dimension
    ]
    if not factors:
        raise ValueError(
            f"Root-cause report contains no factor for dimension '{dimension}'."
        )
    return min(factors, key=lambda factor: float(factor["contribution_pp"]))


def collect_evidence(report: dict[str, Any], engine: Engine) -> dict[str, Any]:
    """Combine root-cause warehouse and SQL query-05/06 evidence."""
    warehouse_factor = _top_factor(report, "warehouse")
    _month_bounds(str(report["period_b"]))

    project_root = _project_root()
    supplier_query_path = project_root / "sql" / "kpi_queries" / "05_supplier_delay.sql"
    supplier_query = supplier_query_path.read_text(encoding="utf-8").strip()
    if not supplier_query:
        raise ValueError(f"Supplier-delay query is empty: {supplier_query_path}")
    stockout_query_path = (
        project_root / "sql" / "kpi_queries" / "06_stockout_by_sku_group.sql"
    )
    stockout_query = stockout_query_path.read_text(encoding="utf-8").strip()
    if not stockout_query:
        raise ValueError(f"Stock-out query is empty: {stockout_query_path}")
    stockout_query = stockout_query.rstrip(";")

    with engine.connect() as connection:
        supplier_rows = connection.execute(text(supplier_query)).mappings().all()
        above_average_suppliers = [
            row
            for row in supplier_rows
            if float(row["difference_from_average"]) > 0
        ]
        supplier_row = max(
            above_average_suppliers,
            key=lambda row: float(row["difference_from_average"]),
            default=None,
        )

        stockout_rows = connection.execute(
            text(
                "SELECT * FROM ("
                + stockout_query
                + ") AS monthly_stockouts "
                "WHERE month = :period "
                "ORDER BY stockout_rate DESC, sku_group"
            ),
            {"period": str(report["period_b"])},
        ).mappings().all()
    if not stockout_rows:
        raise ValueError(
            f"No stock-out snapshots found in {report['period_b']}."
        )
    stockout_totals = sum(
        int(row["stockout_snapshots"]) for row in stockout_rows
    )
    stockout_row = min(
        stockout_rows,
        key=lambda row: (
            -float(row["stockout_rate"]),
            str(row["sku_group"]),
        ),
    )
    sku_group = str(stockout_row["sku_group"])
    stockout_share = (
        int(stockout_row["stockout_snapshots"]) / stockout_totals
        if stockout_totals
        else 0.0
    )

    supplier_evidence: dict[str, Any] = {"above_average": supplier_row is not None}
    if supplier_row is not None:
        supplier_evidence.update(
            {
                "segment": str(supplier_row["supplier_id"]),
                "late_rate": f"{float(supplier_row['supplier_late_rate']):.1%}",
                "all_supplier_average_late_rate": (
                    f"{float(supplier_row['average_supplier_late_rate']):.1%}"
                ),
                "gap_pp": (
                    f"{float(supplier_row['difference_from_average']) * 100:+.2f}"
                ),
            }
        )

    return {
        "kpi": str(report["kpi"]),
        "period_a": str(report["period_a"]),
        "period_b": str(report["period_b"]),
        "overall": {
            "value_a": f"{float(report['value_a']):.2%}",
            "value_b": f"{float(report['value_b']):.2%}",
            "change_pp": f"{float(report['change']) * 100:+.2f}",
        },
        "warehouse": {
            "segment": str(warehouse_factor["segment"]),
            "rate_a": f"{float(warehouse_factor['rate_a']):.2%}",
            "rate_b": f"{float(warehouse_factor['rate_b']):.2%}",
            "contribution_pp": f"{float(warehouse_factor['contribution_pp']):+.2f}",
            "share_of_unfulfilled": (
                f"{float(warehouse_factor.get('share_of_unfulfilled', warehouse_factor.get('share_of_delayed', 0))):.1%}"
            ),
        },
        "supplier": supplier_evidence,
        "sku_group": {
            "segment": sku_group,
            "stockout_rate": f"{float(stockout_row['stockout_rate']):.1%}",
            "stockout_snapshots": str(stockout_row["stockout_snapshots"]),
            "snapshot_count": str(stockout_row["snapshot_count"]),
            "all_stockout_snapshots": str(stockout_totals),
            "share_of_stockout_snapshots": f"{stockout_share:.1%}",
        },
    }


def _facts_prompt(facts: dict[str, Any]) -> str:
    """Serialize computed facts without including any uncomputed evidence."""
    return "Computed evidence (JSON):\n" + json.dumps(
        facts, indent=2, sort_keys=True, ensure_ascii=True
    )


def _finding(facts: dict[str, Any]) -> str:
    overall = facts["overall"]
    return (
        f"Finding: {facts['kpi'].capitalize()} fell from {overall['value_a']} "
        f"in {facts['period_a']} to {overall['value_b']} in "
        f"{facts['period_b']}, a change of {overall['change_pp']} percentage "
        "points."
    )


def deterministic_answer(facts: dict[str, Any]) -> str:
    """Render a no-key answer using only values in the computed evidence."""
    warehouse = facts["warehouse"]
    supplier = facts["supplier"]
    sku_group = facts["sku_group"]
    if supplier["above_average"]:
        supplier_bullet = (
            f"- Supplier {supplier['segment']} had a late rate of "
            f"{supplier['late_rate']} versus the all-supplier average of "
            f"{supplier['all_supplier_average_late_rate']}, a gap of "
            f"{supplier['gap_pp']} percentage points."
        )
        supplier_recommendation = (
            f"- Review warehouse {warehouse['segment']}'s fulfilment process "
            f"and investigate late deliveries from supplier {supplier['segment']}."
        )
    else:
        supplier_bullet = (
            "- No supplier had a late-delivery rate above the all-supplier "
            "average."
        )
        supplier_recommendation = (
            f"- Review warehouse {warehouse['segment']}'s fulfilment process "
            "and continue monitoring suppliers against the all-supplier "
            "benchmark."
        )
    return "\n".join(
        (
            _finding(facts),
            "Evidence:",
            (
                f"- Warehouse {warehouse['segment']} fulfilment changed from "
                f"{warehouse['rate_a']} to {warehouse['rate_b']}; contribution "
                f"{warehouse['contribution_pp']} pp; "
                f"{warehouse['share_of_unfulfilled']} of unfulfilled orders."
            ),
            supplier_bullet,
            (
                f"- SKU group {sku_group['segment']} had a stock-out rate of "
                f"{sku_group['stockout_rate']} "
                f"({sku_group['stockout_snapshots']} of "
                f"{sku_group['snapshot_count']} snapshots), representing "
                f"{sku_group['share_of_stockout_snapshots']} of all "
                f"{sku_group['all_stockout_snapshots']} stock-out snapshots "
                "in the period."
            ),
            "Recommendation:",
            supplier_recommendation,
            (
                f"- Review replenishment and inventory controls for SKU group "
                f"{sku_group['segment']}."
            ),
        )
    )


def _numeric_values(text_value: str) -> set[str]:
    """Extract normalized numeric values for exact fact-grounding checks."""
    return {
        token.replace(",", "").lstrip("+")
        for token in NUMBER_PATTERN.findall(text_value)
    }


def validate_answer(answer: str, facts: dict[str, Any]) -> list[str]:
    """Check required response structure and reject numbers absent from facts."""
    errors: list[str] = []
    lines = [line.strip() for line in answer.splitlines() if line.strip()]
    headings = [
        (index, line)
        for index, line in enumerate(lines)
        if line.startswith(("Finding:", "Evidence:", "Recommendation:"))
    ]
    expected = ("Finding:", "Evidence:", "Recommendation:")
    if tuple(line.split(":", 1)[0] + ":" for _, line in headings) != expected:
        errors.append("Answer must contain Finding, Evidence, and Recommendation in order.")
    elif any(not line.partition(":")[2].strip() for _, line in headings if line.startswith("Finding:")):
        errors.append("Finding must contain one sentence.")

    if len(headings) == 3:
        finding_index, _ = headings[0]
        evidence_index, _ = headings[1]
        recommendation_index, _ = headings[2]
        if not (finding_index < evidence_index < recommendation_index):
            errors.append("Answer sections are out of order.")
        evidence_bullets = [
            line
            for line in lines[evidence_index + 1 : recommendation_index]
            if line.startswith("- ")
        ]
        recommendation_bullets = [
            line
            for line in lines[recommendation_index + 1 :]
            if line.startswith("- ")
        ]
        if len(evidence_bullets) != 3:
            errors.append("Evidence must contain exactly three bullets.")
        elif not (
            evidence_bullets[0].startswith("- Warehouse ")
            and evidence_bullets[2].startswith("- SKU group ")
        ):
            errors.append(
                "Evidence bullets must cover warehouse, supplier, then SKU group."
            )
        supplier_bullet = evidence_bullets[1] if len(evidence_bullets) == 3 else ""
        supplier_facts = facts.get("supplier", {})
        if supplier_facts.get("above_average"):
            required_supplier_values = (
                str(supplier_facts["segment"]),
                str(supplier_facts["late_rate"]),
                str(supplier_facts["all_supplier_average_late_rate"]),
                str(supplier_facts["gap_pp"]),
            )
            if (
                not supplier_bullet.startswith("- Supplier ")
                or any(value not in supplier_bullet for value in required_supplier_values)
                or "percentage points" not in supplier_bullet
            ):
                errors.append(
                    "Supplier evidence must state the selected supplier's late "
                    "rate, average, and gap in percentage points."
                )
        elif (
            not supplier_bullet.startswith("- No supplier ")
            or "above the all-supplier average" not in supplier_bullet
        ):
            errors.append(
                "Supplier evidence must state when no supplier is above average."
            )
        sku_facts = facts.get("sku_group", {})
        required_sku_values = (
            str(sku_facts["segment"]),
            str(sku_facts["stockout_rate"]),
            str(sku_facts["stockout_snapshots"]),
            str(sku_facts["snapshot_count"]),
            str(sku_facts["share_of_stockout_snapshots"]),
            str(sku_facts["all_stockout_snapshots"]),
        )
        sku_bullet = evidence_bullets[2] if len(evidence_bullets) == 3 else ""
        if (
            not sku_bullet.startswith("- SKU group ")
            or any(value not in sku_bullet for value in required_sku_values)
            or "all" not in sku_bullet
            or "stock-out snapshots" not in sku_bullet
        ):
            errors.append(
                "SKU-group evidence must state the stock-out rate, snapshot "
                "counts, and share of all period stock-out snapshots."
            )
        if not 1 <= len(recommendation_bullets) <= 2:
            errors.append("Recommendation must contain one or two bullets.")
        elif not any(
            f"SKU group {sku_facts['segment']}" in bullet
            for bullet in recommendation_bullets
        ):
            errors.append(
                "Recommendation must use the SKU group selected by the "
                "period stock-out query."
            )

    fact_numbers = _numeric_values(json.dumps(facts, ensure_ascii=True))
    unsupported = _numeric_values(answer) - fact_numbers
    if unsupported:
        errors.append(
            "Answer includes numeric values absent from the evidence facts."
        )
    return errors


def _audit_table() -> Table:
    metadata = MetaData()
    return Table(
        "ai_audit_log",
        metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("created_at", DateTime(timezone=True), server_default=func.current_timestamp()),
        Column("question", Text, nullable=False),
        Column("prompt", Text, nullable=False),
        Column("response", Text, nullable=False),
        Column("validation_passed", Boolean, nullable=False),
        Column("validation_error", Text, nullable=False),
        Column("attempt", Integer, nullable=False),
        Column("model", String(255), nullable=False),
    )


def _write_audit(
    engine: Engine,
    *,
    question: str,
    prompt: str,
    response: str,
    validation_errors: list[str],
    attempt: int,
    model: str,
) -> None:
    table = _audit_table()
    table.metadata.create_all(engine, tables=[table])
    with engine.begin() as connection:
        connection.execute(
            table.insert(),
            {
                "question": question,
                "prompt": prompt,
                "response": response,
                "validation_passed": not validation_errors,
                "validation_error": "; ".join(validation_errors),
                "attempt": attempt,
                "model": model,
            },
        )


def generate_answer(
    question: str,
    facts: dict[str, Any],
    engine: Engine,
    *,
    api_key: str | None = None,
    model: str = "claude-sonnet-5-5",
    client: Anthropic | None = None,
) -> str:
    """Generate, validate, and audit an answer; retry invalid output once."""
    facts_prompt = _facts_prompt(facts)
    if api_key and client is None:
        client = Anthropic(api_key=api_key)

    for attempt in (1, 2):
        prompt = (
            f"Question: {question}\n\n{facts_prompt}\n\n"
            "Use the supplied facts only. Do not add any number not present "
            "in the facts."
        )
        if attempt == 2:
            prompt += (
                "\n\nThe previous response failed validation. Return a "
                "corrected answer that follows the required structure and "
                "uses only the numeric values in the computed evidence."
            )

        if client is None:
            response_text = deterministic_answer(facts)
        else:
            try:
                response = client.messages.create(
                    model=model,
                    max_tokens=600,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": prompt}],
                )
                response_text = "\n".join(
                    block.text
                    for block in response.content
                    if getattr(block, "type", None) == "text"
                )
                if not response_text:
                    raise ValueError("Anthropic response contained no text.")
            except (AnthropicError, ValueError) as error:
                _write_audit(
                    engine,
                    question=question,
                    prompt=prompt,
                    response=f"API error: {error}",
                    validation_errors=[f"API request failed: {error}"],
                    attempt=attempt,
                    model=model,
                )
                raise RuntimeError("Anthropic reasoning request failed.") from error

        validation_errors = validate_answer(response_text, facts)
        _write_audit(
            engine,
            question=question,
            prompt=prompt,
            response=response_text,
            validation_errors=validation_errors,
            attempt=attempt,
            model=model,
        )
        if not validation_errors:
            return response_text
        LOGGER.warning(
            "AI response validation failed on attempt %s: %s",
            attempt,
            "; ".join(validation_errors),
        )
        if attempt == 2:
            raise ValueError(
                "AI response remained invalid after one retry: "
                + "; ".join(validation_errors)
            )

    raise RuntimeError("Reasoner ended without a response.")


def reason_about(
    question: str,
    *,
    report_path: Path | None = None,
    engine: Engine | None = None,
    client: Anthropic | None = None,
) -> str:
    """Load report and database evidence, then return a validated answer."""
    owns_engine = engine is None
    active_engine = engine or create_engine(_database_url(_project_root()), future=True)
    try:
        report = load_root_cause_report(report_path)
        facts = collect_evidence(report, active_engine)
        return generate_answer(
            question,
            facts,
            active_engine,
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            client=client,
        )
    except (OSError, SQLAlchemyError, ValueError):
        LOGGER.exception("AI reasoning failed.")
        raise
    finally:
        if owns_engine:
            active_engine.dispose()


def main() -> None:
    """Run the AI reasoning layer from the command line."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--question", required=True)
    arguments = parser.parse_args()
    setup_logging()
    print(reason_about(arguments.question))


if __name__ == "__main__":
    main()
