# Planted Synthetic Data Issues

`python -m src.generate_data` writes ten deterministic CSV source tables to
`data/raw/` (or the directory configured by `DATA_RAW_DIR`). The generator uses
seed `20250301` and covers January through June 2025. Each table contains
approximately 3% deliberately dirty records, rounded to at least one record
per table. This minimum means the smallest lookup tables have a higher dirty
percentage; overall, the generated rows contain approximately 3% dirty records.
The command prints row counts and the number of records deliberately changed
in each file.

## Operational signals

1. **Warehouse A is slower to ship.** Its ordinary order-to-ship delay averages
   about 3.5 days, versus about 1.8 days at warehouses B-D. In June, Warehouse
   A's average rises to about 9 days, while B-D remain near 1.8 days.
2. **Supplier X is less reliable.** Supplier X's `supplier_late` rate is set to
   35%, compared with 18% for each other supplier. Late supplier deliveries
   also extend the linked order's transit time.
3. **SKU group Y stocks out more often.** Weekly inventory snapshots assign
   group Y a higher stock-out probability, increased further when the supplier
   is Supplier X and in the latest month.
4. **Fulfillment falls in June.** Orders are fulfilled at a 94% monthly target
   from January through May and an 88% target in June. Failures are preferentially
   assigned to orders exposed to Warehouse A in June, Supplier X, or SKU group Y.
5. **A customer cohort shows churn risk.** The first 120 customers receive
   higher order-sampling weight early in the period and lower weight later.
   Their monthly support-ticket rate rises over the six months; other customers
   have a low, steady ticket rate.

## Deliberately dirty source records

Dirty records are included in the raw CSVs and are not validated or cleaned by
this step. The defects include:

- Null values in optional source columns.
- Duplicate values in identifier columns.
- `delivered_date` values earlier than `shipped_date`.
- Negative `quantity` values in order items.
- Inconsistent upper/lower casing in product categories.

Foreign-key relationships are otherwise generated from the source identifiers.
The duplicate identifier defects can make a small number of references
ambiguous or orphaned; downstream validation is expected to identify such
records.
