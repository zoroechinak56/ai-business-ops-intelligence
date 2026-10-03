"""Run the Python analytics layer and persist outputs to the source database."""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from src.analytics.anomaly_detection import detect_anomalies
from src.analytics.features import build_order_features
from src.analytics.segmentation import build_customer_rfm, segment_customers
from src.analytics.statistics import run_statistical_tests
from src.config import settings
from src.logger import setup_logging


OUTPUT_TABLES = (
    "anomalies",
    "customer_segments",
    "stat_tests",
    "order_features",
)
LOGGER = logging.getLogger(__name__)


def _database_url(project_root: Path) -> str:
    """Use configured PostgreSQL or the database loader's SQLite fallback."""
    if settings.database_url:
        return settings.database_url
    processed_dir = settings.data_processed_dir or project_root / "data" / "processed"
    processed_dir.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{(processed_dir / 'business_ops.sqlite').as_posix()}"


def _read_table(connection: object, table_name: str) -> pd.DataFrame:
    """Read a source table while preserving SQLAlchemy 2.x compatibility."""
    return pd.read_sql_query(text(f"SELECT * FROM {table_name}"), connection)


def run_analytics(engine: Engine) -> dict[str, pd.DataFrame]:
    """Compute and replace all four derived analytics tables."""
    required_source_tables = (
        "orders",
        "deliveries",
        "customers",
        "order_items",
        "support_tickets",
    )
    with engine.connect() as connection:
        source_frames = {
            table_name: _read_table(connection, table_name)
            for table_name in required_source_tables
        }

    features = build_order_features(
        source_frames["orders"], source_frames["deliveries"]
    )
    anomalies = detect_anomalies(features)
    rfm = build_customer_rfm(
        source_frames["customers"],
        source_frames["orders"],
        source_frames["order_items"],
    )
    customer_segments = segment_customers(
        rfm,
        source_frames["orders"],
        source_frames["support_tickets"],
    )
    stat_tests = run_statistical_tests(features, source_frames["deliveries"])
    outputs = {
        "anomalies": anomalies,
        "customer_segments": customer_segments,
        "stat_tests": stat_tests,
        "order_features": features,
    }

    with engine.begin() as connection:
        for table_name in OUTPUT_TABLES:
            outputs[table_name].to_sql(
                table_name,
                connection,
                if_exists="replace",
                index=False,
                chunksize=5_000,
            )
        for table_name, expected in outputs.items():
            actual = int(
                connection.execute(
                    text(f"SELECT COUNT(*) FROM {table_name}")
                ).scalar_one()
            )
            if actual != len(expected):
                raise RuntimeError(
                    f"{table_name} write count mismatch: expected "
                    f"{len(expected)}, wrote {actual}."
                )

    return outputs


def main() -> None:
    """Run all Python analytics and print output-table summaries."""
    setup_logging()
    project_root = Path(__file__).resolve().parents[2]
    engine = create_engine(_database_url(project_root), future=True)
    try:
        outputs = run_analytics(engine)
    except (SQLAlchemyError, ValueError, RuntimeError):
        LOGGER.exception("Python analytics run failed.")
        raise
    finally:
        engine.dispose()

    for table_name in OUTPUT_TABLES:
        frame = outputs[table_name]
        print(f"{table_name}: {len(frame):,} rows")
        if table_name == "customer_segments" and not frame.empty:
            print(
                "  segments: "
                + str(frame["segment_label"].value_counts().to_dict())
            )
            print(
                f"  mean churn risk: {frame['churn_risk_score'].mean():.3f}"
            )
        elif table_name == "stat_tests":
            for result in frame.itertuples(index=False):
                print(
                    f"  {result.test_name}: p={result.p_value:.6g}, "
                    f"{result.effect_measure}={result.effect_size:.3f}"
                )
        elif table_name == "anomalies":
            if frame.empty:
                print("  no anomalies flagged")
            else:
                print(
                    "  by type: "
                    + str(frame["anomaly_type"].value_counts().to_dict())
                )
    LOGGER.info("Python analytics outputs persisted: %s", ", ".join(OUTPUT_TABLES))


if __name__ == "__main__":
    main()
