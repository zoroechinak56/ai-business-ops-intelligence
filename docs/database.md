# Relational Database Rebuild

After generating and cleaning source data, run:

```powershell
python -m src.ingestion.load_db
```

The command reads `data/processed/clean/*.csv` and
`data/processed/cleaning_summary.json`. With `DATABASE_URL` set in `.env`, it
connects to PostgreSQL. If it is unset, it uses
`data/processed/business_ops.sqlite`. Each run drops/recreates the source
tables and loads records in parent-before-child dependency order in one
transaction. A count mismatch against each table's `rows_after` cleaning
summary value fails and rolls back the rebuild.

The portable DDL is in `sql/schema/01_schema.sql`; it defines all ten source
tables, foreign keys, primary keys, non-null columns, non-negative / positive
quantity and price rules, delivery date ordering, and lookup/date indexes.
`sql/schema/02_dim_date.sql` defines the calendar table. The loader populates
one row per day from 2025-01-01 through 2025-06-30, using ISO week numbers and
Monday=1 through Sunday=7 weekday numbering.
