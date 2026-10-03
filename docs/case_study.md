# Business Impact Case Study

## Turning operations data into decisions

### Problem

A mid-size e-commerce and distribution operation needs to explain fulfilment
performance across warehouses, suppliers, inventory, and customer support.
Disconnected source files make it hard to trust the data, isolate contributing
segments, or consistently hand evidence to operations leaders.

### Approach

I built a reproducible analytics workflow using a deterministic synthetic
dataset with ten related source tables. The pipeline validates incoming CSVs,
quarantines invalid records, rebuilds a relational database, calculates SQL
KPIs and Python analytics, decomposes monthly fulfilment changes, and produces
an evidence-constrained AI explanation. An n8n workflow export connects a
trigger to execution, threshold alerts, audit records, and a final summary.
Power BI-ready CSV exports and specifications provide a foundation for
management dashboards.

### Findings

The completed May-to-June 2025 run measured a fulfilment decline from **93.99%**
to **88.06%**, or **5.93 percentage points**. Warehouse A's fulfilment rate
fell from **89.50%** to **55.56%** and its volume-weighted contribution was
**-9.57 percentage points**. It accounted for **88.8%** of June unfulfilled
orders.

The separate supplier performance query identified Supplier X at **34.5%**
late versus a **20.0%** all-supplier average, a **14.46 percentage-point**
gap. The period-B inventory query measured SKU group Y at a **32.6%** stock-out
rate (**298** of **915** snapshots), accounting for **46.9%** of all **635**
stock-out snapshots.

The run completed all **8** recorded pipeline steps and emitted **3** alerts.
The values are from generated project outputs. The decomposition is
descriptive, and these correlated operational indicators do not prove
causality.

### Recommendations

- Have warehouse operations review Warehouse A's June fulfilment and
  processing workflow, then track the same order-to-ship and fulfilment
  measures after interventions.
- Have procurement investigate Supplier X's late deliveries and check whether
  inbound reliability aligns with replenishment timing.
- Have inventory planning review SKU group Y's replenishment controls,
  stock-out exposure, and supplier dependencies.
- Use the automated thresholds and audit trail to make recurring monitoring
  repeatable; require an owner to validate each alert before action.

### Expected impact

The intended business impact is faster investigation of service deterioration,
clear ownership of follow-up, and less time spent reconciling conflicting
spreadsheets. Evidence-linked alerts and repeatable reports should help teams
prioritize operational reviews and evaluate subsequent changes using the same
definitions. No financial savings or post-intervention improvement is claimed:
the available results come from synthetic data and do not measure real-world
intervention outcomes.
