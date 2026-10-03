"""Tests for deterministic synthetic source-data generation."""

from pathlib import Path

import pandas as pd
import pytest

from src.generate_data import TABLE_NAMES, generate_data


@pytest.fixture(scope="module")
def generated_tables(tmp_path_factory: pytest.TempPathFactory) -> tuple[
    Path, dict[str, dict[str, int]]
]:
    """Generate one isolated dataset for the source-data checks."""
    output_dir = tmp_path_factory.mktemp("generated_raw_data")
    summary = generate_data(output_dir)
    return output_dir, summary


def test_all_expected_csv_files_exist(
    generated_tables: tuple[Path, dict[str, dict[str, int]]],
) -> None:
    """Every source table is emitted as a CSV file."""
    output_dir, _ = generated_tables
    assert {path.stem for path in output_dir.glob("*.csv")} == set(TABLE_NAMES)


def test_source_tables_have_expected_volumes(
    generated_tables: tuple[Path, dict[str, dict[str, int]]],
) -> None:
    """Dimension sizes and minimum fact-table volumes match the project scope."""
    output_dir, summary = generated_tables
    expected_dimensions = {
        "customers": 2_000,
        "products": 300,
        "suppliers": 8,
        "warehouses": 4,
        "employees": 60,
    }
    for table_name, expected_rows in expected_dimensions.items():
        assert summary[table_name]["rows"] == expected_rows
        assert len(pd.read_csv(output_dir / f"{table_name}.csv")) == expected_rows

    assert summary["orders"]["rows"] >= 20_000
    assert summary["order_items"]["rows"] >= summary["orders"]["rows"]


def test_foreign_keys_are_mostly_valid(
    generated_tables: tuple[Path, dict[str, dict[str, int]]],
) -> None:
    """At least 97% of the key references resolve despite intentional dirty IDs."""
    output_dir, _ = generated_tables
    tables = {
        name: pd.read_csv(output_dir / f"{name}.csv") for name in TABLE_NAMES
    }
    relationships = (
        ("orders", "customer_id", "customers", "customer_id"),
        ("orders", "warehouse_id", "warehouses", "warehouse_id"),
        ("orders", "employee_id", "employees", "employee_id"),
        ("order_items", "order_id", "orders", "order_id"),
        ("order_items", "product_id", "products", "product_id"),
        ("deliveries", "order_id", "orders", "order_id"),
        ("deliveries", "supplier_id", "suppliers", "supplier_id"),
        ("deliveries", "warehouse_id", "warehouses", "warehouse_id"),
        ("inventory_snapshots", "product_id", "products", "product_id"),
        ("inventory_snapshots", "warehouse_id", "warehouses", "warehouse_id"),
        ("inventory_snapshots", "supplier_id", "suppliers", "supplier_id"),
        ("support_tickets", "customer_id", "customers", "customer_id"),
    )
    for source, source_column, target, target_column in relationships:
        values = tables[source][source_column]
        valid_ratio = values.isin(tables[target][target_column]).mean()
        assert valid_ratio >= 0.97, (
            f"{source}.{source_column} has only {valid_ratio:.1%} valid references"
        )


def test_dirty_record_counts_are_reported_and_approximately_three_percent(
    generated_tables: tuple[Path, dict[str, dict[str, int]]],
) -> None:
    """The generator reports a rounded 3% defect count for every source table."""
    _, summary = generated_tables
    for counts in summary.values():
        assert counts["dirty_records"] == max(1, round(counts["rows"] * 0.03))


def test_planted_operational_patterns_are_measurable(
    generated_tables: tuple[Path, dict[str, dict[str, int]]],
) -> None:
    """The output contains the intended monthly, supplier, SKU, and cohort signals."""
    output_dir, _ = generated_tables
    orders = pd.read_csv(output_dir / "orders.csv", parse_dates=["order_date"])
    deliveries = pd.read_csv(
        output_dir / "deliveries.csv",
        parse_dates=["shipped_date", "delivered_date"],
    )
    products = pd.read_csv(output_dir / "products.csv").drop_duplicates(
        "product_id"
    )
    snapshots = pd.read_csv(output_dir / "inventory_snapshots.csv")
    tickets = pd.read_csv(
        output_dir / "support_tickets.csv", parse_dates=["ticket_date"]
    )

    orders["month"] = orders["order_date"].dt.to_period("M")
    orders["fulfilled"] = orders["order_status"].eq("fulfilled")
    fulfillment_rates = orders.groupby("month")["fulfilled"].mean()
    assert fulfillment_rates.loc["2025-01":"2025-05"].eq(0.94).all()
    assert fulfillment_rates.loc["2025-06"] == 0.88

    order_dates = orders.drop_duplicates("order_id").set_index("order_id")[
        "order_date"
    ]
    deliveries["order_date"] = deliveries["order_id"].map(order_dates)
    deliveries["ship_days"] = (
        deliveries["shipped_date"] - deliveries["order_date"]
    ).dt.days
    deliveries["month"] = deliveries["order_date"].dt.to_period("M")
    ship_lags = deliveries.groupby(["month", "warehouse_id"])["ship_days"].mean()
    assert ship_lags.loc[("2025-06", "WH-A")] > 2 * ship_lags.loc[
        ("2025-01", "WH-A")
    ]
    assert ship_lags.loc[("2025-06", "WH-A")] > 3 * ship_lags.loc[
        ("2025-06", "WH-B")
    ]

    late_rates = deliveries.groupby("supplier_id")["supplier_late"].mean()
    other_supplier_rate = late_rates.drop("SUP-001").mean()
    assert late_rates["SUP-001"] >= other_supplier_rate + 0.12

    product_groups = products.set_index("product_id")["sku_group"]
    snapshots["sku_group"] = snapshots["product_id"].map(product_groups)
    stockout_rates = snapshots.groupby("sku_group")["stockout_flag"].mean()
    assert stockout_rates["Y"] > 2 * stockout_rates.drop("Y").max()

    at_risk_ids = {f"CUST-{index:05d}" for index in range(1, 121)}
    at_risk_orders = orders[orders["customer_id"].isin(at_risk_ids)]
    at_risk_tickets = tickets[tickets["customer_id"].isin(at_risk_ids)]
    early_orders = at_risk_orders[
        at_risk_orders["order_date"].dt.month.isin((1, 2, 3))
    ]
    late_orders = at_risk_orders[
        at_risk_orders["order_date"].dt.month.isin((4, 5, 6))
    ]
    early_tickets = at_risk_tickets[
        at_risk_tickets["ticket_date"].dt.month.isin((1, 2, 3))
    ]
    late_tickets = at_risk_tickets[
        at_risk_tickets["ticket_date"].dt.month.isin((4, 5, 6))
    ]
    assert len(early_orders) > len(late_orders)
    assert len(late_tickets) > len(early_tickets)


def test_required_dirty_data_examples_are_present(
    generated_tables: tuple[Path, dict[str, dict[str, int]]],
) -> None:
    """Representative null, duplicate, date, quantity, and casing defects exist."""
    output_dir, _ = generated_tables
    customers = pd.read_csv(output_dir / "customers.csv")
    products = pd.read_csv(output_dir / "products.csv")
    order_items = pd.read_csv(output_dir / "order_items.csv")
    deliveries = pd.read_csv(
        output_dir / "deliveries.csv",
        parse_dates=["shipped_date", "delivered_date"],
    )

    assert customers["phone"].isna().any()
    assert customers["customer_id"].duplicated().any()
    assert order_items["quantity"].lt(0).any()
    assert products["category"].ne(products["category"].str.title()).any()
    assert deliveries["delivered_date"].lt(deliveries["shipped_date"]).any()
