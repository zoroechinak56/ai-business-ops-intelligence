# Fulfilment Root-Cause Decomposition

Run the current fulfilment analysis from the project root:

```powershell
python -m src.analytics.root_cause --kpi fulfilment --from 2025-05 --to 2025-06
```

The command reads `orders`, `deliveries`, `order_items`, `products`, and
`warehouses` from the configured `DATABASE_URL`, or
`data/processed/business_ops.sqlite` when no URL is configured. It prints a
ranked summary and writes the structured result to
`data/processed/root_cause_latest.json` (or the configured
`DATA_PROCESSED_DIR`). At this step, only the fulfilment KPI is supported.

## Decomposition and dimensions

The overall fulfilment rate is fulfilled orders / all orders in each month.
For each segment `i`, its volume-weighted contribution is:

```text
(share_b * rate_b) - (share_a * rate_a)
```

Here, each `share` is that segment's orders divided by all orders in the
corresponding period. Contributions within each dimension sum to the total KPI
change; values are returned in percentage points. `share_of_drop` is
contribution / total KPI change, expressed as a percentage. Its contributions
sum to 100% across all segments within each dimension when the KPI changed;
factors that offset a decline have negative shares. (The report displays only
the top three segments per dimension.) `share_of_unfulfilled` is the segment's
unfulfilled orders in period B divided by all unfulfilled orders in period B.

Warehouse and supplier are order assignments. The source schema has no carrier;
supplier is the available supplier/delivery grouping. Region is resolved from
the warehouse and is reported as a separate dimension only when it is not a
one-to-one mapping of warehouses, avoiding duplicate factors. A multi-line
order is assigned to the most frequent SKU group among its order lines; ties
resolve alphabetically. This exclusive SKU group assignment makes
per-dimension contributions additive. Missing dimension values are grouped as
`Unknown`.

Factors are ranked by ascending volume-weighted contribution, which puts the
largest negative (worsening) contribution first. The result includes each
segment's rates in both periods, order counts, `rate_vs_overall` (the segment's
period-B rate minus the overall period-B rate), `share_of_unfulfilled`,
dimension rank, and overall rank. `share_of_delayed` remains as a compatibility
alias for `share_of_unfulfilled`. The JSON includes the top three factors per
reported dimension (up to twelve total).

## Statistical support

Each displayed factor receives a two-sided pooled two-proportion z-test
comparing its segment fulfilment rate between the two periods. The returned
p-values are unadjusted and do not establish causality. Small or zero samples
are retained for contribution accounting; an unavailable comparison receives
a conservative p-value of 1.

This is a descriptive decomposition, not the AI reasoning layer. It does not
generate recommendations or claim that a factor caused the KPI movement.
