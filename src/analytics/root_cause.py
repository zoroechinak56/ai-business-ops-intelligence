"""Decompose KPI changes into segment-level contributions with statistical support."""

from __future__ import annotations

import argparse
from collections.abc import Mapping
import json
import logging
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import norm
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError

from src.config import settings
from src.logger import setup_logging


DIMENSIONS = ("warehouse", "supplier", "sku_group")
TOP_FACTORS_PER_DIMENSION = 3
LOGGER = logging.getLogger(__name__)


def _read_frame(connection: Connection, table_name: str) -> pd.DataFrame:
    """Read a table using a fixed, internally selected table name."""
    return pd.read_sql_query(text(f"SELECT * FROM {table_name}"), connection)


def _monthly_period(value: str) -> pd.Period:
    """Parse and validate one YYYY-MM period."""
    try:
        period = pd.Period(value, freq="M")
    except (TypeError, ValueError) as error:
        raise ValueError(f"Invalid month '{value}'; expected YYYY-MM.") from error
    if len(value) != 7 or value != str(period):
        raise ValueError(f"Invalid month '{value}'; expected YYYY-MM.")
    return period


def prepare_order_facts(tables: Mapping[str, pd.DataFrame]) -> pd.DataFrame:
    """Join source frames into one order-grain fulfilment segmentation frame.

    An order is assigned to one SKU group using the most frequent group among
    its order lines; ties resolve alphabetically. This exclusive assignment
    keeps segment contributions additive within the SKU-group dimension.
    """
    required_tables = {
        "orders",
        "deliveries",
        "order_items",
        "products",
        "warehouses",
    }
    missing_tables = required_tables - set(tables)
    if missing_tables:
        raise ValueError(
            "Missing source tables: " + ", ".join(sorted(missing_tables))
        )

    orders = tables["orders"]
    deliveries = tables["deliveries"]
    items = tables["order_items"]
    products = tables["products"]
    warehouses = tables["warehouses"]
    required_columns = {
        "orders": {"order_id", "warehouse_id", "order_date", "order_status"},
        "deliveries": {"order_id", "supplier_id"},
        "order_items": {"order_id", "product_id"},
        "products": {"product_id", "sku_group"},
        "warehouses": {"warehouse_id", "region"},
    }
    for table_name, required in required_columns.items():
        missing = required - set(tables[table_name].columns)
        if missing:
            raise ValueError(
                f"{table_name} is missing columns: {', '.join(sorted(missing))}"
            )

    if orders["order_id"].duplicated().any():
        raise ValueError("Orders must contain at most one row per order_id.")

    order_facts = orders.loc[
        :, ["order_id", "warehouse_id", "order_date", "order_status"]
    ].copy()
    order_facts["warehouse"] = order_facts["warehouse_id"]
    order_facts["order_date"] = pd.to_datetime(
        order_facts["order_date"], errors="raise"
    )
    order_facts["fulfilled"] = order_facts["order_status"].eq("fulfilled")

    supplier_assignment = (
        deliveries.loc[:, ["order_id", "supplier_id"]]
        .dropna(subset=["supplier_id"])
        .sort_values(["order_id", "supplier_id"])
        .drop_duplicates("order_id", keep="first")
    )
    order_facts = order_facts.merge(
        supplier_assignment, on="order_id", how="left", validate="one_to_one"
    )

    item_groups = items.loc[:, ["order_id", "product_id"]].merge(
        products.loc[:, ["product_id", "sku_group"]],
        on="product_id",
        how="left",
        validate="many_to_one",
    )
    sku_assignment = (
        item_groups.dropna(subset=["sku_group"])
        .groupby(["order_id", "sku_group"], as_index=False)
        .size()
        .rename(columns={"size": "line_count"})
        .sort_values(
            ["order_id", "line_count", "sku_group"],
            ascending=[True, False, True],
        )
        .drop_duplicates("order_id", keep="first")
        .loc[:, ["order_id", "sku_group"]]
    )
    order_facts = order_facts.merge(
        sku_assignment, on="order_id", how="left", validate="one_to_one"
    )
    order_facts["supplier"] = order_facts["supplier_id"].fillna(
        "Unknown supplier"
    )
    order_facts["sku_group"] = order_facts["sku_group"].fillna("Unknown")
    regions = warehouses.loc[:, ["warehouse_id", "region"]].drop_duplicates(
        "warehouse_id"
    )
    order_facts = order_facts.merge(
        regions, on="warehouse_id", how="left", validate="many_to_one"
    )
    order_facts["region"] = order_facts["region"].fillna("Unknown")
    return order_facts


def _two_proportion_p_value(
    successes_a: int,
    total_a: int,
    successes_b: int,
    total_b: int,
) -> float:
    """Return a two-sided pooled two-proportion z-test p-value."""
    if total_a <= 0 or total_b <= 0:
        return 1.0
    proportion_a = successes_a / total_a
    proportion_b = successes_b / total_b
    pooled = (successes_a + successes_b) / (total_a + total_b)
    variance = pooled * (1 - pooled) * (1 / total_a + 1 / total_b)
    if variance <= 0:
        return 1.0
    z_score = (proportion_b - proportion_a) / np.sqrt(variance)
    return float(2 * norm.sf(abs(z_score)))


def _dimensions_for_facts(facts: pd.DataFrame) -> tuple[str, ...]:
    """Omit region when it is a one-to-one relabeling of warehouse."""
    assignments = facts.loc[:, ["warehouse_id", "region"]].drop_duplicates()
    one_to_one = (
        not assignments.empty
        and assignments["warehouse_id"].nunique() == len(assignments)
        and assignments["region"].nunique() == len(assignments)
    )
    return DIMENSIONS if one_to_one else (*DIMENSIONS, "region")


def _dimension_factors(
    facts: pd.DataFrame,
    dimension: str,
    period_a: pd.Period,
    period_b: pd.Period,
    value_a: float,
    value_b: float,
) -> list[dict[str, Any]]:
    """Calculate volume-weighted contributions for one dimension."""
    start_a = period_a.start_time
    end_a = period_a.end_time
    start_b = period_b.start_time
    end_b = period_b.end_time
    period_a_facts = facts.loc[
        facts["order_date"].between(start_a, end_a, inclusive="both")
    ]
    period_b_facts = facts.loc[
        facts["order_date"].between(start_b, end_b, inclusive="both")
    ]
    if period_a_facts.empty or period_b_facts.empty:
        raise ValueError(
            f"No orders found in both comparison periods "
            f"({period_a}, {period_b})."
        )

    total_a = len(period_a_facts)
    total_b = len(period_b_facts)
    delayed_total_b = int((~period_b_facts["fulfilled"]).sum())
    segments = sorted(
        set(period_a_facts[dimension].astype(str))
        | set(period_b_facts[dimension].astype(str))
    )
    factors: list[dict[str, Any]] = []
    for segment in segments:
        segment_a = period_a_facts.loc[
            period_a_facts[dimension].astype(str).eq(segment)
        ]
        segment_b = period_b_facts.loc[
            period_b_facts[dimension].astype(str).eq(segment)
        ]
        count_a = len(segment_a)
        count_b = len(segment_b)
        fulfilled_a = int(segment_a["fulfilled"].sum())
        fulfilled_b = int(segment_b["fulfilled"].sum())
        rate_a = fulfilled_a / count_a if count_a else 0.0
        rate_b = fulfilled_b / count_b if count_b else 0.0
        share_a = count_a / total_a
        share_b = count_b / total_b

        contribution = share_b * rate_b - share_a * rate_a
        delayed_b = count_b - fulfilled_b
        share_of_delayed = (
            delayed_b / delayed_total_b if delayed_total_b else 0.0
        )
        rate_vs_overall = rate_b - value_b
        share_of_drop = (
            contribution / (value_b - value_a) * 100
            if value_b != value_a
            else 0.0
        )
        factors.append(
            {
                "dimension": dimension,
                "segment": segment,
                "rate_a": rate_a,
                "rate_b": rate_b,
                "orders_a": count_a,
                "orders_b": count_b,
                "contribution": contribution,
                "contribution_pp": contribution * 100,
                "share_of_drop": share_of_drop,
                "rate_vs_overall": rate_vs_overall,
                "share_of_unfulfilled": share_of_delayed,
                "share_of_delayed": share_of_delayed,
                "p_value": _two_proportion_p_value(
                    fulfilled_a, count_a, fulfilled_b, count_b
                ),
            }
        )

    factors.sort(key=lambda row: (row["contribution"], row["segment"]))
    for rank, factor in enumerate(factors, start=1):
        factor["dimension_rank"] = rank
    return factors


def decompose_fulfilment(
    tables: Mapping[str, pd.DataFrame],
    period_a: str,
    period_b: str,
) -> dict[str, Any]:
    """Return fulfilment change and ranked segment contributions from frames."""
    parsed_a = _monthly_period(period_a)
    parsed_b = _monthly_period(period_b)
    if parsed_a == parsed_b:
        raise ValueError("period_a and period_b must be different months.")
    facts = prepare_order_facts(tables)

    periods: dict[pd.Period, pd.DataFrame] = {}
    for period in (parsed_a, parsed_b):
        mask = facts["order_date"].dt.to_period("M").eq(period)
        periods[period] = facts.loc[mask]
        if periods[period].empty:
            raise ValueError(f"No orders found for period {period}.")

    value_a = float(periods[parsed_a]["fulfilled"].mean())
    value_b = float(periods[parsed_b]["fulfilled"].mean())
    change = value_b - value_a
    dimensions = _dimensions_for_facts(facts)
    all_factors: list[dict[str, Any]] = []
    for dimension in dimensions:
        all_factors.extend(
            _dimension_factors(
                facts, dimension, parsed_a, parsed_b, value_a, value_b
            )
        )

    all_factors.sort(
        key=lambda row: (
            row["contribution"],
            row["dimension"],
            row["dimension_rank"],
            row["segment"],
        )
    )
    for rank, factor in enumerate(all_factors, start=1):
        factor["overall_rank"] = rank

    selected_factors = [
        factor
        for dimension in dimensions
        for factor in sorted(
            (row for row in all_factors if row["dimension"] == dimension),
            key=lambda row: row["dimension_rank"],
        )[:TOP_FACTORS_PER_DIMENSION]
    ]
    selected_factors.sort(
        key=lambda row: (
            row["overall_rank"],
            row["dimension"],
            row["segment"],
        )
    )
    top_factors = [
        {
            "dimension": factor["dimension"],
            "segment": factor["segment"],
            "rate_a": factor["rate_a"],
            "rate_b": factor["rate_b"],
            "orders_a": factor["orders_a"],
            "orders_b": factor["orders_b"],
            "contribution_pp": factor["contribution_pp"],
            "share_of_drop": factor["share_of_drop"],
            "rate_vs_overall": factor["rate_vs_overall"],
            "share_of_unfulfilled": factor["share_of_unfulfilled"],
            "share_of_delayed": factor["share_of_delayed"],
            "p_value": factor["p_value"],
            "dimension_rank": factor["dimension_rank"],
            "overall_rank": factor["overall_rank"],
        }
        for factor in selected_factors
    ]
    return {
        "kpi": "fulfilment",
        "period_a": str(parsed_a),
        "period_b": str(parsed_b),
        "value_a": value_a,
        "value_b": value_b,
        "change": change,
        "top_factors": top_factors,
        "method": {
            "contribution": (
                "Volume-weighted contribution: "
                "(share_b*rate_b) - (share_a*rate_a), where each share is "
                "the segment's fraction of all orders in that period. "
                "Contributions sum to the overall KPI change within each "
                "dimension."
            ),
            "share_of_drop": (
                "Contribution divided by total fulfilment change, expressed "
                "as a percentage; the shares sum to 100% within each "
                "dimension when the total change is non-zero."
            ),
            "rate_vs_overall": (
                "Segment fulfilment rate in period_b minus overall "
                "fulfilment rate in period_b."
            ),
            "share_of_unfulfilled": (
                "Segment unfulfilled orders in period_b divided by all "
                "unfulfilled orders in period_b."
            ),
            "share_of_delayed": (
                "Compatibility alias for share_of_unfulfilled."
            ),
            "region_dimension": (
                "Region is omitted when region and warehouse form a "
                "one-to-one mapping, to avoid duplicate factors."
            ),
            "sku_group_assignment": (
                "Each order is assigned to its most frequent SKU group among "
                "order lines; ties resolve alphabetically."
            ),
            "supplier_assignment": (
                "Each order is assigned to the alphabetically first supplier "
                "in its delivery rows."
            ),
            "factor_ranking": (
                "Factors are ranked by volume-weighted contribution "
                "ascending, so the most negative (largest worsening) comes "
                "first; dimension_rank and overall_rank reflect that order."
            ),
            "statistical_test": (
                "Two-sided pooled two-proportion z-test for each displayed "
                "factor; p-values are unadjusted."
            ),
        },
    }


def _database_url(project_root: Path) -> str:
    """Return configured database URL or the project SQLite fallback."""
    if settings.database_url:
        return settings.database_url
    processed_dir = settings.data_processed_dir or project_root / "data" / "processed"
    processed_dir.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{(processed_dir / 'business_ops.sqlite').as_posix()}"


def _load_source_tables(engine: Engine) -> dict[str, pd.DataFrame]:
    """Load the tables required for fulfilment root-cause decomposition."""
    table_names = (
        "orders",
        "deliveries",
        "order_items",
        "products",
        "warehouses",
    )
    with engine.connect() as connection:
        return {name: _read_frame(connection, name) for name in table_names}


def explain_kpi_change(
    kpi: str,
    period_a: str,
    period_b: str,
) -> dict[str, Any]:
    """Explain a KPI change from the configured SQLite/PostgreSQL database."""
    if kpi.strip().lower() not in {"fulfilment", "fulfillment"}:
        raise ValueError(
            "Only the fulfilment KPI is supported by the root-cause engine."
        )
    project_root = Path(__file__).resolve().parents[2]
    engine = create_engine(_database_url(project_root), future=True)
    try:
        tables = _load_source_tables(engine)
        return decompose_fulfilment(tables, period_a, period_b)
    except (SQLAlchemyError, OSError, ValueError):
        LOGGER.exception("KPI root-cause analysis failed.")
        raise
    finally:
        engine.dispose()


def save_result(result: Mapping[str, Any], output_path: Path) -> None:
    """Persist structured results as JSON."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as output_file:
        json.dump(result, output_file, indent=2, ensure_ascii=True)
        output_file.write("\n")


def _print_result(result: Mapping[str, Any]) -> None:
    """Print KPI values followed by the highest-contribution factors."""
    print(
        f"{result['kpi']} {result['period_a']} -> {result['period_b']}: "
        f"{result['value_a']:.2%} -> {result['value_b']:.2%} "
        f"({result['change']:+.2%})"
    )
    factors = result["top_factors"]
    if not factors:
        print("No segment factors were available.")
        return
    print(
        f"{'Rank':>4} {'Dimension':<14} {'Segment':<24} "
        f"{'Contribution':>14} {'Drop share':>12} "
        f"{'Unfulfilled':>12} {'p-value':>12}"
    )
    for factor in factors:
        print(
            f"{factor['overall_rank']:>4} {factor['dimension']:<14} "
            f"{factor['segment']:<24} "
            f"{factor['contribution_pp']:+13.2f}pp "
            f"{factor['share_of_drop']:>11.1f}% "
            f"{factor['share_of_unfulfilled']:>11.1%} "
            f"{factor['p_value']:>12.4g}"
        )


def main() -> None:
    """Parse CLI options, run the analysis, and save the latest JSON."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kpi", default="fulfilment")
    parser.add_argument("--from", dest="period_a", required=True, help="YYYY-MM")
    parser.add_argument("--to", dest="period_b", required=True, help="YYYY-MM")
    arguments = parser.parse_args()

    setup_logging()
    result = explain_kpi_change(
        arguments.kpi, arguments.period_a, arguments.period_b
    )
    project_root = Path(__file__).resolve().parents[2]
    processed_dir = settings.data_processed_dir or project_root / "data" / "processed"
    output_path = processed_dir / "root_cause_latest.json"
    save_result(result, output_path)
    _print_result(result)
    print(f"\nJSON report: {output_path}")


if __name__ == "__main__":
    main()
