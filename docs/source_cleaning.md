# Source Data Cleaning

Run from the project root after generating source files and producing the
validation report:

```powershell
python -m src.cleaning.cleaner
```

The cleaner reads the raw CSVs and `data/processed/validation_report.json`,
then writes standardized rows to `data/processed/clean/`, rejected source rows
to `data/processed/rejected/`, and counts to
`data/processed/cleaning_summary.json`. Raw files under `data/raw/` are never
rewritten. The validation report is included as provenance; row-level rules
are recalculated from complete CSVs because the report stores only sample row
numbers for each issue.

## Fixes and defaults

- Trim leading/trailing whitespace from textual values before type conversion.
- Standardize product `category` values using title casing.
- Fill nullable string fields as follows:

| Table.column | Default |
|---|---|
| `customers.phone`, `employees.phone` | `Unknown` |
| `products.description`, `suppliers.contact_email`, `orders.order_notes`, `deliveries.tracking_number`, `inventory_snapshots.notes`, `support_tickets.resolution_notes` | `Not provided` |
| `warehouses.region` | `Unassigned` |
| `order_items.discount_code` | `NONE` |

`deliveries.delivered_date` remains null when a delivery has not yet arrived;
it is not assigned an invented date.

## Rejection and cascade behavior

- Keep the first record for each primary key and quarantine later duplicates.
- Quarantine records with required nulls, invalid types, negative prices or
  quantities, impossible delivery-date order, or foreign keys that do not
  resolve against the retained parent table.
- Quarantine children of rejected orders in `order_items`, `deliveries`, and
  `support_tickets` with reason `cascade_parent_order_rejected`.
- Each rejected CSV preserves source fields and adds a 1-based CSV `source_row`
  number and a semicolon-separated `reject_reason`.
- Every expected table receives a clean CSV and a rejected CSV, including
  header-only rejected files when no rows were quarantined.

The cleaner reruns the validator against its in-memory clean tables and stops
with an error if any reject-severity finding remains. Warnings such as optional
nullable delivery dates are governed by the validator's source contract.
