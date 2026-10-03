# Source Data Validation

Run the read-only source checks from the project root:

```powershell
python -m src.validation.validator
```

The command reads the ten expected CSVs in `data/raw/`, prints issue counts by
table and classification, and writes the full report to
`data/processed/validation_report.json` (or to the configured
`DATA_PROCESSED_DIR`). It does not change, clean, or rewrite raw source data.
The output report contains issue codes, affected-row counts, messages, and up
to ten 1-based CSV row numbers as examples. Counts across checks may overlap
when one row violates more than one rule.

## Checks

- Required source columns and values matching each column's declared scalar
  type.
- Nulls in non-nullable fields, including all primary and foreign keys; nulls
  in designated optional fields are reported as warnings.
- Duplicate primary keys.
- Foreign-key values absent from the corresponding supplied parent table.
- Negative product prices, order quantities/prices, warehouse capacity, and
  inventory quantities/reorder points.
- Delivery records whose non-null `delivered_date` precedes `shipped_date`.
- Product categories that do not use title casing.
- Missing expected CSV files.

Nullable fields reflect the generated source contract, including optional
contact/notes fields and `deliveries.delivered_date` for backordered orders.
Expected null delivery dates are allowed without a warning; absent optional
contact and notes values are reported as warnings.

## Classifications

| Classification | Meaning | Examples |
|---|---|---|
| `reject` | A structural or relational problem that should block trusted downstream use. | Missing required column, invalid type, null key, duplicate primary key, unresolved foreign key. |
| `fix` | A row-level value can be corrected by an explicit downstream cleaning rule; this validator only reports it. | Negative quantity or price, delivered-before-shipped date, inconsistent category casing. |
| `warn` | A nullable optional value is absent and merits review but does not alone invalidate the record. | Missing optional phone, description, or notes. |

The in-memory `validate_tables` function accepts a mapping of table names to
DataFrames. Foreign-key checks run only when the relevant parent table is
included in the mapping, allowing focused unit tests without loading the full
dataset.
