# Python Analytics Layer

Run the feature, anomaly, segmentation, and statistical analyses after loading
the relational database:

```powershell
python -m src.analytics.run_analytics
```

The command connects through `DATABASE_URL`; when it is unset it uses
`data/processed/business_ops.sqlite`. It reads the required source tables and
replaces four derived tables in the same database: `order_features`,
`anomalies`, `customer_segments`, and `stat_tests`. A failed output write
rolls back the output-table transaction.

## Methods and fields

- **Order features**: one row per order. `processing_days` is shipped date
  minus order date; `is_late` is true for missing delivery dates or dates later
  than promised; `lead_time_gap` is delivered date minus promised date (a
  positive value means late); `weekday` uses Monday=1 through Sunday=7;
  `warehouse_load` is order count at the same warehouse on the same order date.
  Supplier late rate is the mean `supplier_late` across that supplier's
  deliveries.
- **Anomalies**: order processing times are flagged outside 1.5-IQR fences or
  beyond an absolute 3.0 population z-score. Warehouse-day aggregates use
  Isolation Forest (`contamination=0.05`, `random_state=42`) over order load,
  mean processing time, and late rate. The anomaly rows are distinguished by
  `anomaly_type`; order and warehouse-day findings share one table.
- **Customer segments**: K-Means uses StandardScaler-transformed recency,
  frequency, and monetary features, with a fixed seed. Segment labels are
  assigned from standardized cluster profiles. Recency is days since the
  customer's latest fulfilled order; frequency is fulfilled-order count;
  monetary value is fulfilled order-item quantity × price.
- **Churn risk**: a 0-1 score combines 60% of the positive relative decline in
  average monthly order count between the first and second halves of observed
  months with 40% of the within-run percentile rank of total support-ticket
  count. This is a descriptive score, not a calibrated churn probability.
- **Statistical tests**: Warehouse A versus all other warehouses uses a
  two-sided Mann-Whitney U test and Cliff's delta (positive means Warehouse A
  tends to be slower). Supplier versus late status uses a chi-square test and
  Cramer's V. P-values are unadjusted; they indicate association, not causation.

No root-cause engine or causal attribution is performed in this step.
