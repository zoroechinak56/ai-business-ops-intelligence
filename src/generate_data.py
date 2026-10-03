"""Generate deterministic synthetic operational source data."""

from __future__ import annotations

from calendar import monthrange
import logging
from pathlib import Path

from faker import Faker
import numpy as np
import pandas as pd

from src.config import settings
from src.logger import setup_logging


SEED = 20250301
ORDER_COUNT = 20_400
START_DATE = pd.Timestamp("2025-01-01")
MONTH_COUNT = 6

LOGGER = logging.getLogger(__name__)

TABLE_NAMES = (
    "customers",
    "products",
    "suppliers",
    "warehouses",
    "employees",
    "orders",
    "order_items",
    "deliveries",
    "inventory_snapshots",
    "support_tickets",
)

OPTIONAL_COLUMNS = {
    "customers": "phone",
    "products": "description",
    "suppliers": "contact_email",
    "warehouses": "region",
    "employees": "phone",
    "orders": "order_notes",
    "order_items": "discount_code",
    "deliveries": "tracking_number",
    "inventory_snapshots": "notes",
    "support_tickets": "resolution_notes",
}

ID_COLUMNS = {
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


def _make_monthly_orders(
    rng: np.random.Generator,
    customer_ids: np.ndarray,
    at_risk_customer_ids: set[str],
    warehouse_ids: np.ndarray,
    employee_ids: np.ndarray,
) -> pd.DataFrame:
    """Create dated orders with declining fulfillment in the final month."""
    orders_per_month = ORDER_COUNT // MONTH_COUNT
    order_rows: list[dict[str, object]] = []
    month_indexes: list[int] = []

    for month_index in range(MONTH_COUNT):
        month_start = START_DATE + pd.DateOffset(months=month_index)
        days_in_month = monthrange(month_start.year, month_start.month)[1]
        order_dates = month_start + pd.to_timedelta(
            rng.integers(0, days_in_month, size=orders_per_month), unit="D"
        )
        customer_weights = np.ones(len(customer_ids), dtype=float)
        at_risk_mask = np.isin(customer_ids, list(at_risk_customer_ids))
        customer_weights[at_risk_mask] = 2.8 if month_index < 3 else 0.45
        customer_weights /= customer_weights.sum()
        selected_customers = rng.choice(
            customer_ids, size=orders_per_month, p=customer_weights
        )
        selected_warehouses = rng.choice(
            warehouse_ids, size=orders_per_month, p=(0.25, 0.25, 0.25, 0.25)
        )

        for index in range(orders_per_month):
            order_rows.append(
                {
                    "order_id": f"ORD-{month_index * orders_per_month + index + 1:06d}",
                    "customer_id": selected_customers[index],
                    "warehouse_id": selected_warehouses[index],
                    "employee_id": rng.choice(employee_ids),
                    "order_date": order_dates[index],
                    "order_notes": "Standard online order",
                }
            )
            month_indexes.append(month_index)

    orders = pd.DataFrame(order_rows)
    orders["month_index"] = month_indexes
    return orders


def _create_order_items(
    rng: np.random.Generator,
    orders: pd.DataFrame,
    products: pd.DataFrame,
) -> tuple[pd.DataFrame, np.ndarray, np.ndarray]:
    """Create order lines and return each order's supplier and SKU-group signals."""
    product_weights = np.ones(len(products), dtype=float)
    product_weights *= np.where(products["sku_group"].eq("Y"), 1.5, 1.0)
    product_weights *= np.where(products["supplier_id"].eq("SUP-001"), 1.35, 1.0)
    product_weights /= product_weights.sum()

    line_rows: list[dict[str, object]] = []
    primary_supplier_ids: list[str] = []
    contains_group_y: list[bool] = []

    for order in orders.itertuples(index=False):
        line_count = int(rng.integers(1, 4))
        product_indexes = rng.choice(
            len(products), size=line_count, replace=False, p=product_weights
        )
        selected_products = products.iloc[product_indexes]
        primary_supplier_ids.append(str(selected_products.iloc[0]["supplier_id"]))
        contains_group_y.append(bool(selected_products["sku_group"].eq("Y").any()))

        for product in selected_products.itertuples(index=False):
            line_rows.append(
                {
                    "order_item_id": f"ITEM-{len(line_rows) + 1:07d}",
                    "order_id": order.order_id,
                    "product_id": product.product_id,
                    "quantity": int(rng.integers(1, 6)),
                    "unit_price": round(float(product.unit_price), 2),
                    "discount_code": "NONE",
                }
            )

    return (
        pd.DataFrame(line_rows),
        np.asarray(primary_supplier_ids),
        np.asarray(contains_group_y, dtype=bool),
    )


def _add_fulfillment_and_deliveries(
    rng: np.random.Generator,
    orders: pd.DataFrame,
    primary_supplier_ids: np.ndarray,
    contains_group_y: np.ndarray,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Set monthly fulfillment targets and add warehouse/supplier delivery effects."""
    month_indexes = orders["month_index"].to_numpy(dtype=int)
    warehouse_ids = orders["warehouse_id"].to_numpy()
    supplier_x = primary_supplier_ids == "SUP-001"
    warehouse_a = warehouse_ids == "WH-A"

    risk_score = rng.random(len(orders))
    risk_score += contains_group_y * 0.18
    risk_score += supplier_x * 0.18
    risk_score += warehouse_a * np.where(month_indexes == MONTH_COUNT - 1, 0.55, 0.12)

    fulfilled = np.ones(len(orders), dtype=bool)
    for month_index in range(MONTH_COUNT):
        month_mask = month_indexes == month_index
        target_rate = 0.88 if month_index == MONTH_COUNT - 1 else 0.94
        failure_count = round(int(month_mask.sum()) * (1 - target_rate))
        month_positions = np.flatnonzero(month_mask)
        failures = month_positions[
            np.argsort(risk_score[month_positions])[-failure_count:]
        ]
        fulfilled[failures] = False

    warehouse_a_delay_mean = np.where(
        month_indexes == MONTH_COUNT - 1, 9.0, 3.5
    )
    ship_delay_means = np.where(warehouse_a, warehouse_a_delay_mean, 1.8)
    ship_delay_days = np.maximum(
        0, np.rint(rng.normal(ship_delay_means, 1.1)).astype(int)
    )
    shipped_dates = orders["order_date"] + pd.to_timedelta(ship_delay_days, unit="D")

    supplier_late_rates = np.where(supplier_x, 0.35, 0.18)
    supplier_late = rng.random(len(orders)) < supplier_late_rates
    transit_days = rng.integers(1, 5, size=len(orders)) + supplier_late * 4
    delivered_dates = shipped_dates + pd.to_timedelta(transit_days, unit="D")
    delivered_dates = delivered_dates.where(fulfilled, pd.NaT)

    orders = orders.drop(columns="month_index")
    orders["order_status"] = np.where(fulfilled, "fulfilled", "unfulfilled")
    orders["fulfilled"] = fulfilled

    deliveries = pd.DataFrame(
        {
            "delivery_id": [f"DEL-{index + 1:06d}" for index in range(len(orders))],
            "order_id": orders["order_id"],
            "supplier_id": primary_supplier_ids,
            "warehouse_id": orders["warehouse_id"],
            "shipped_date": shipped_dates,
            "delivered_date": delivered_dates,
            "promised_delivery_date": orders["order_date"] + pd.Timedelta(days=10),
            "supplier_late": supplier_late,
            "delivery_status": np.where(fulfilled, "delivered", "backordered"),
            "tracking_number": [
                f"TRK-{index + 1:08d}" for index in range(len(orders))
            ],
        }
    )
    return orders, deliveries


def _create_inventory_snapshots(
    rng: np.random.Generator, products: pd.DataFrame, warehouse_ids: np.ndarray
) -> pd.DataFrame:
    """Generate weekly stock snapshots with concentrated SKU-group Y shortages."""
    snapshot_dates = pd.date_range(START_DATE, periods=26, freq="W-MON")
    rows: list[dict[str, object]] = []
    snapshot_number = 0

    for snapshot_date in snapshot_dates:
        is_latest_month = snapshot_date.month == 6
        for product in products.itertuples(index=False):
            for warehouse_id in warehouse_ids:
                group_y = product.sku_group == "Y"
                supplier_x = product.supplier_id == "SUP-001"
                stockout_probability = 0.025
                stockout_probability += 0.19 if group_y else 0.0
                stockout_probability += 0.10 if supplier_x else 0.0
                stockout_probability += 0.10 if group_y and supplier_x else 0.0
                if is_latest_month:
                    stockout_probability += 0.07 if group_y or supplier_x else 0.0
                stockout = bool(rng.random() < stockout_probability)
                reorder_point = int(rng.integers(8, 26))
                on_hand = 0 if stockout else int(rng.integers(reorder_point, 140))
                snapshot_number += 1
                rows.append(
                    {
                        "snapshot_id": f"INV-{snapshot_number:07d}",
                        "snapshot_date": snapshot_date,
                        "product_id": product.product_id,
                        "warehouse_id": warehouse_id,
                        "supplier_id": product.supplier_id,
                        "quantity_on_hand": on_hand,
                        "reorder_point": reorder_point,
                        "stockout_flag": stockout,
                        "notes": "Weekly cycle count",
                    }
                )
    return pd.DataFrame(rows)


def _create_support_tickets(
    rng: np.random.Generator,
    fake: Faker,
    customer_ids: np.ndarray,
    at_risk_customer_ids: set[str],
) -> pd.DataFrame:
    """Give a customer cohort declining order propensity and rising ticket rates."""
    ticket_rows: list[dict[str, object]] = []
    at_risk_rates = (0.15, 0.18, 0.22, 0.36, 0.56, 0.82)
    at_risk_mask = np.isin(customer_ids, list(at_risk_customer_ids))

    for customer_index, customer_id in enumerate(customer_ids):
        monthly_rate = at_risk_rates if at_risk_mask[customer_index] else (0.035,) * 6
        for month_index, tickets_this_month in enumerate(
            rng.poisson(monthly_rate)
        ):
            month_start = START_DATE + pd.DateOffset(months=month_index)
            days_in_month = monthrange(month_start.year, month_start.month)[1]
            for _ in range(int(tickets_this_month)):
                ticket_date = month_start + pd.Timedelta(
                    days=int(rng.integers(0, days_in_month))
                )
                ticket_rows.append(
                    {
                        "ticket_id": f"TKT-{len(ticket_rows) + 1:06d}",
                        "customer_id": customer_id,
                        "ticket_date": ticket_date,
                        "issue_type": rng.choice(
                            ("Late delivery", "Order status", "Return", "Product")
                        ),
                        "priority": rng.choice(("low", "medium", "high"), p=(0.5, 0.35, 0.15)),
                        "resolution_status": rng.choice(
                            ("resolved", "open"), p=(0.88, 0.12)
                        ),
                        "resolution_notes": fake.sentence(nb_words=8),
                    }
                )
    return pd.DataFrame(ticket_rows)


def _plant_dirty_records(
    frames: dict[str, pd.DataFrame], rng: np.random.Generator
) -> dict[str, int]:
    """Apply counted source-data defects without running validation or cleaning."""
    dirty_counts: dict[str, int] = {}

    for table_name, frame in frames.items():
        dirty_count = max(1, round(len(frame) * 0.03))
        dirty_indexes = rng.choice(frame.index.to_numpy(), size=dirty_count, replace=False)
        dirty_indexes = rng.permutation(dirty_indexes)
        dirty_counts[table_name] = dirty_count

        duplicate_count = (
            min(max(1, round(dirty_count * 0.2)), dirty_count)
            if len(frame) >= 100
            else 0
        )
        nullable_count = min(
            max(1, round(dirty_count * 0.25)), dirty_count - duplicate_count
        )
        next_index = 0

        id_column = ID_COLUMNS[table_name]
        duplicate_indexes = dirty_indexes[next_index : next_index + duplicate_count]
        next_index += duplicate_count
        if len(frame) > 1:
            for row_index in duplicate_indexes:
                if row_index != frame.index[0]:
                    frame.at[row_index, id_column] = frame.iloc[0][id_column]

        nullable_indexes = dirty_indexes[next_index : next_index + nullable_count]
        next_index += nullable_count
        frame.loc[nullable_indexes, OPTIONAL_COLUMNS[table_name]] = np.nan

        specialized_indexes = dirty_indexes[next_index:]
        if table_name == "deliveries":
            for row_index in specialized_indexes:
                shipped_date = frame.at[row_index, "shipped_date"]
                frame.at[row_index, "delivered_date"] = shipped_date - pd.Timedelta(
                    days=1
                )
        elif table_name == "order_items":
            for row_index in specialized_indexes:
                frame.at[row_index, "quantity"] = -abs(
                    int(frame.at[row_index, "quantity"])
                )
        elif table_name == "products":
            for row_index in specialized_indexes:
                category = str(frame.at[row_index, "category"])
                frame.at[row_index, "category"] = (
                    category.upper() if category.islower() else category.lower()
                )
        elif len(specialized_indexes):
            frame.loc[specialized_indexes, OPTIONAL_COLUMNS[table_name]] = np.nan

    return dirty_counts


def _build_tables(rng: np.random.Generator, fake: Faker) -> dict[str, pd.DataFrame]:
    """Build all related source tables from one seeded random generator."""
    customer_ids = np.asarray([f"CUST-{index + 1:05d}" for index in range(2_000)])
    product_ids = [f"SKU-{index + 1:04d}" for index in range(300)]
    supplier_ids = [f"SUP-{index + 1:03d}" for index in range(8)]
    warehouse_ids = np.asarray(["WH-A", "WH-B", "WH-C", "WH-D"])
    employee_ids = np.asarray([f"EMP-{index + 1:03d}" for index in range(60)])
    at_risk_customer_ids = set(customer_ids[:120])

    customers = pd.DataFrame(
        {
            "customer_id": customer_ids,
            "customer_name": [fake.name() for _ in customer_ids],
            "email": [fake.unique.email() for _ in customer_ids],
            "phone": [fake.phone_number() for _ in customer_ids],
            "city": [fake.city() for _ in customer_ids],
            "state": [fake.state_abbr() for _ in customer_ids],
            "signup_date": [
                START_DATE - pd.Timedelta(days=int(rng.integers(1, 1_500)))
                for _ in customer_ids
            ],
            "customer_segment": rng.choice(
                ("consumer", "small_business", "enterprise"),
                size=len(customer_ids),
                p=(0.65, 0.28, 0.07),
            ),
        }
    )

    supplier_names = ["Supplier X"] + [
        f"Supplier {chr(ord('A') + index)}" for index in range(7)
    ]
    suppliers = pd.DataFrame(
        {
            "supplier_id": supplier_ids,
            "supplier_name": supplier_names,
            "contact_email": [fake.company_email() for _ in supplier_ids],
            "country": rng.choice(
                ("US", "CA", "MX"), size=len(supplier_ids), p=(0.7, 0.2, 0.1)
            ),
            "supplier_tier": rng.choice(
                ("strategic", "preferred", "standard"),
                size=len(supplier_ids),
                p=(0.25, 0.35, 0.4),
            ),
        }
    )
    warehouses = pd.DataFrame(
        {
            "warehouse_id": warehouse_ids,
            "warehouse_name": [f"Warehouse {name[-1]}" for name in warehouse_ids],
            "city": [fake.city() for _ in warehouse_ids],
            "region": ["Northeast", "South", "Midwest", "West"],
            "capacity_units": [25_000, 22_000, 20_000, 18_000],
        }
    )
    employees = pd.DataFrame(
        {
            "employee_id": employee_ids,
            "employee_name": [fake.name() for _ in employee_ids],
            "email": [fake.unique.company_email() for _ in employee_ids],
            "phone": [fake.phone_number() for _ in employee_ids],
            "role": rng.choice(
                ("picker", "packer", "supervisor", "customer_service"),
                size=len(employee_ids),
                p=(0.4, 0.3, 0.1, 0.2),
            ),
            "hire_date": [
                START_DATE - pd.Timedelta(days=int(rng.integers(30, 3_000)))
                for _ in employee_ids
            ],
        }
    )

    sku_groups = np.asarray(list("ABCDEFGHIJ") + ["Y"])
    sku_group_probabilities = np.asarray([0.082] * 10 + [0.18])
    sku_group_probabilities /= sku_group_probabilities.sum()
    categories = np.asarray(("Electronics", "Home", "Apparel", "Office", "Outdoor"))
    products = pd.DataFrame(
        {
            "product_id": product_ids,
            "product_name": [fake.catch_phrase() for _ in product_ids],
            "sku": [f"SKU-{index + 1:04d}" for index in range(len(product_ids))],
            "sku_group": rng.choice(
                sku_groups, size=len(product_ids), p=sku_group_probabilities
            ),
            "category": rng.choice(categories, size=len(product_ids)),
            "supplier_id": rng.choice(
                supplier_ids, size=len(product_ids), p=(0.23,) + (0.11,) * 7
            ),
            "unit_price": np.round(rng.uniform(5, 500, size=len(product_ids)), 2),
            "description": [fake.sentence(nb_words=7) for _ in product_ids],
        }
    )

    orders = _make_monthly_orders(
        rng, customer_ids, at_risk_customer_ids, warehouse_ids, employee_ids
    )
    order_items, primary_supplier_ids, contains_group_y = _create_order_items(
        rng, orders, products
    )
    orders, deliveries = _add_fulfillment_and_deliveries(
        rng, orders, primary_supplier_ids, contains_group_y
    )
    snapshots = _create_inventory_snapshots(rng, products, warehouse_ids)
    support_tickets = _create_support_tickets(
        rng, fake, customer_ids, at_risk_customer_ids
    )

    return {
        "customers": customers,
        "products": products,
        "suppliers": suppliers,
        "warehouses": warehouses,
        "employees": employees,
        "orders": orders,
        "order_items": order_items,
        "deliveries": deliveries,
        "inventory_snapshots": snapshots,
        "support_tickets": support_tickets,
    }


def generate_data(
    output_dir: Path | None = None,
) -> dict[str, dict[str, int]]:
    """Write fixed-seed synthetic CSVs and return per-table row/dirty counts."""
    target_dir = output_dir or settings.data_raw_dir
    if target_dir is None:
        target_dir = Path(__file__).resolve().parents[1] / "data" / "raw"
    target_dir = target_dir.expanduser()
    target_dir.mkdir(parents=True, exist_ok=True)

    rng = np.random.default_rng(SEED)
    fake = Faker()
    fake.seed_instance(SEED)
    frames = _build_tables(rng, fake)
    dirty_counts = _plant_dirty_records(frames, rng)

    summary: dict[str, dict[str, int]] = {}
    for table_name in TABLE_NAMES:
        frame = frames[table_name]
        frame.drop(columns="fulfilled", errors="ignore").to_csv(
            target_dir / f"{table_name}.csv", index=False
        )
        summary[table_name] = {
            "rows": len(frame),
            "dirty_records": dirty_counts[table_name],
        }
    return summary


def print_summary(summary: dict[str, dict[str, int]], output_dir: Path) -> None:
    """Print a concise report of generated row and planted-defect counts."""
    print(f"Synthetic source data written to: {output_dir}")
    print(f"{'Table':<24} {'Rows':>10} {'Dirty records':>15}")
    for table_name, counts in summary.items():
        print(
            f"{table_name:<24} {counts['rows']:>10,} "
            f"{counts['dirty_records']:>15,}"
        )
    print(
        f"{'TOTAL':<24} "
        f"{sum(counts['rows'] for counts in summary.values()):>10,} "
        f"{sum(counts['dirty_records'] for counts in summary.values()):>15,}"
    )


def main() -> None:
    """Generate CSV source tables at the configured raw-data directory."""
    setup_logging()
    output_dir = settings.data_raw_dir
    if output_dir is None:
        output_dir = Path(__file__).resolve().parents[1] / "data" / "raw"
    summary = generate_data(output_dir)
    print_summary(summary, output_dir)
    LOGGER.info("Generated %d raw source tables.", len(summary))


if __name__ == "__main__":
    main()
