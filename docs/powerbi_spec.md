# Power BI Report Specification

## Data preparation and refresh

From the repository root, run the exporter after rebuilding the database,
creating the SQL views, running Python analytics, and generating the latest
root-cause report:

```powershell
python -m src.analytics.export_powerbi
```

The exporter writes UTF-8 CSVs to `powerbi/data/`. The exports are generated
artifacts and are git-ignored. In Power BI Desktop, import the CSVs with names
matching their basenames. Set date columns to the Date data type, count fields
to whole numbers, rates/scores to decimal numbers, and monetary values to the
appropriate currency type. Refresh the exports before refreshing the PBIX.

The six SQL views and requested analytical/dimension tables are exported.
`inventory_snapshots.csv` is included as a supporting fact table for historical
stock-out trends; the latest-only `vw_inventory_status.csv` cannot provide
monthly inventory history on its own. `root_cause.csv` is flattened to one row
per factor, with KPI/period values repeated on each factor row.

## Shared semantic model

Use single-direction, one-to-many relationships from the listed dimension
table to each fact/consumer table. Do not create relationships between fact
tables.

| Dimension / one-side key | Many-side key |
|---|---|
| `dim_date[date]` | `vw_orders_enriched[order_date]`, `order_features[order_date]`, `anomalies[date]`, `inventory_snapshots[snapshot_date]`, `vw_inventory_status[snapshot_date]` |
| `warehouses[warehouse_id]` | `vw_orders_enriched[warehouse_id]`, `vw_warehouse_performance[warehouse_id]`, `order_features[warehouse_id]`, `anomalies[warehouse_id]`, `inventory_snapshots[warehouse_id]`, `vw_inventory_status[warehouse_id]` |
| `suppliers[supplier_id]` | `vw_orders_enriched[supplier_id]`, `vw_supplier_performance[supplier_id]`, `order_features[supplier_id]`, `inventory_snapshots[supplier_id]`, `vw_inventory_status[supplier_id]` |
| `products[product_id]` | `inventory_snapshots[product_id]`, `vw_inventory_status[product_id]` |
| `vw_customer_rfm[customer_id]` | `vw_orders_enriched[customer_id]`, `customer_segments[customer_id]` |

`vw_customer_rfm` is the customer dimension for this model because the export
does not include a separate customers CSV. Keep it on the one side of the two
customer relationships and do not create a relationship between
`customer_segments` and `vw_orders_enriched`.

The warehouse, supplier, and product IDs are also present in other exports as
descriptive attributes. Use dimension-table fields for slicers and axes where
the relationships above apply. Do not relate `suppliers` to `products` in this
model: both are independent dimensions connected to inventory facts, and a
supplier-to-product path would create competing filter paths.

Monthly aggregate views use a `YYYY-MM` text column (`month`) rather than a
date key. Do not create a direct relationship from `dim_date[date]` to these
views. Add this calculated column to `dim_date` if a single calendar slicer
must filter monthly aggregates:

```DAX
YearMonth = FORMAT ( dim_date[date], "yyyy-MM" )
```

Use `TREATAS` in monthly-view measures to apply selected calendar months.
`vw_supplier_performance`, `stat_tests`, and `root_cause` are across-supplier,
whole-dataset, and latest-analysis summaries respectively; they do not become
date-grain facts through a relationship. Keep `root_cause` disconnected and
filter it with its `period_a`, `period_b`, `dimension`, and `segment` columns.

## Dashboard pages

### CEO Overview

**CSV files:** `vw_monthly_kpis.csv`, `vw_orders_enriched.csv`,
`vw_warehouse_performance.csv`, `vw_supplier_performance.csv`,
`vw_inventory_status.csv`, `vw_customer_rfm.csv`, `customer_segments.csv`,
`dim_date.csv`, `warehouses.csv`.

**Model:** Use shared Date, Warehouse, and Supplier relationships. The
`vw_monthly_kpis` month axis remains its own month column (or use the
`YearMonth`/`TREATAS` pattern). Use the shared customer relationship from
`vw_customer_rfm` to `customer_segments` for customer-level filtering.

**DAX measures:**

```DAX
Orders = SUM ( vw_monthly_kpis[order_count] )
Fulfilled Orders = SUM ( vw_monthly_kpis[fulfilled_orders] )
Fulfilment Rate = DIVIDE ( [Fulfilled Orders], [Orders] )
Calendar Fulfilment Rate =
    CALCULATE (
        [Fulfilment Rate],
        TREATAS ( VALUES ( dim_date[YearMonth] ), vw_monthly_kpis[month] )
    )
Sales Value = SUM ( vw_monthly_kpis[sales_value] )
Calendar Sales Value =
    CALCULATE (
        [Sales Value],
        TREATAS ( VALUES ( dim_date[YearMonth] ), vw_monthly_kpis[month] )
    )
Deliveries = SUM ( vw_monthly_kpis[delivery_count] )
On-time Delivery Rate =
    DIVIDE (
        SUM ( vw_monthly_kpis[on_time_deliveries] ),
        [Deliveries]
    )
Calendar On-time Delivery Rate =
    CALCULATE (
        [On-time Delivery Rate],
        TREATAS ( VALUES ( dim_date[YearMonth] ), vw_monthly_kpis[month] )
    )
Average Warehouse Processing Days =
    AVERAGE ( vw_warehouse_performance[avg_processing_days] )
Average Churn Risk = AVERAGE ( customer_segments[churn_risk_score] )
```

Use percentage formats for rates and currency for sales. The average processing
measure is an average of warehouse-month averages when multiple months are
selected; use the Operations page for warehouse-level analysis.

**Visuals:** four top KPI cards (Fulfilment Rate, Sales Value, On-time Delivery
Rate, Orders); monthly fulfilment and on-time trend; warehouse processing
comparison; supplier late-rate variance; small customer churn-risk summary.

**Slicers:** `dim_date[date]` for order-grain visuals; `dim_date[YearMonth]`
for monthly KPI measures using `TREATAS`; warehouse name, supplier name, and
customer segment.

**Wireframe:**

```text
+--------------------------------------------------------------------+
| CEO Overview                         [Date] [Warehouse] [Supplier] |
+-------------+-------------+-------------+-------------+------------+
| Fulfilment  | Sales       | On-time     | Orders      | Churn risk |
+---------------------------+-------------------------+--------------+
| Monthly fulfilment / on-time trend                     | Warehouse |
|                                                       | processing|
+-------------------------------------------------------+------------+
| Supplier late-rate variance                           | Watchlist  |
+--------------------------------------------------------------------+
```

### Operations

**CSV files:** `vw_orders_enriched.csv`, `vw_warehouse_performance.csv`,
`order_features.csv`, `anomalies.csv`, `stat_tests.csv`, `dim_date.csv`,
`warehouses.csv`.

**Model:** Use Date and Warehouse relationships. Keep `anomalies` and
`order_features` as separate facts. `stat_tests` is a disconnected summary
table; display its precomputed p-values/effect sizes as context, not as
filterable order-level evidence.

**DAX measures:**

```DAX
Operational Orders = DISTINCTCOUNT ( order_features[order_id] )
Average Processing Days = AVERAGE ( order_features[processing_days] )
Average Daily Warehouse Load =
    AVERAGEX (
        SUMMARIZE (
            order_features,
            order_features[warehouse_id],
            order_features[order_date],
            "DailyLoad", MAX ( order_features[warehouse_load] )
        ),
        [DailyLoad]
    )
Calendar Warehouse Processing Days =
    CALCULATE (
        AVERAGE ( vw_warehouse_performance[avg_processing_days] ),
        TREATAS ( VALUES ( dim_date[YearMonth] ), vw_warehouse_performance[month] )
    )
Anomaly Rows = COUNTROWS ( anomalies )
Order Processing Anomalies =
    CALCULATE (
        [Anomaly Rows],
        anomalies[anomaly_type] = "order_processing_time"
    )
Warehouse-day Anomalies =
    CALCULATE ( [Anomaly Rows], anomalies[anomaly_type] = "warehouse_day" )
Late Order Share = AVERAGE ( order_features[is_late] )
```

`Late Order Share` is an order-level late flag from the feature table; do not
label it as the delivery-grain SLA KPI.

**Visuals:** warehouse-month processing-days matrix; daily order volume/load
trend; anomaly count and anomaly type; processing-time distribution; Warehouse
A vs other warehouses p-value and Cliff's delta cards from `stat_tests`.

**Slicers:** date (daily fact tables), month / `dim_date[YearMonth]`
(`vw_warehouse_performance`), warehouse, order status, anomaly type.

**Wireframe:**

```text
+-------------------------------------------------------------------+
| Operations                         [Date] [Warehouse] [Status]     |
+------------------+------------------+------------------------------+
| Avg processing   | Avg load         | Anomaly rows                  |
+-------------------------------------+------------------------------+
| Warehouse x month processing matrix | Daily load / processing trend |
+-------------------------------------+------------------------------+
| Anomaly detail table                | Statistical comparison cards |
+-------------------------------------------------------------------+
```

### Inventory

**CSV files:** `inventory_snapshots.csv`, `vw_inventory_status.csv`,
`products.csv`, `warehouses.csv`, `suppliers.csv`, `dim_date.csv`.

**Model:** Use Date, Product, Warehouse, and Supplier relationships for
`inventory_snapshots` and `vw_inventory_status`. The former is historical
snapshot grain; the latter is one latest row per product/warehouse. Avoid
combining their counts in one card without naming which snapshot grain is
being reported.

**DAX measures:**

```DAX
Inventory Snapshots = COUNTROWS ( inventory_snapshots )
Stock-out Snapshots =
    CALCULATE (
        [Inventory Snapshots],
        inventory_snapshots[stockout_flag] = TRUE ()
    )
Stock-out Rate = DIVIDE ( [Stock-out Snapshots], [Inventory Snapshots] )
All-group Stock-out Snapshots =
    CALCULATE (
        [Stock-out Snapshots],
        REMOVEFILTERS ( products[sku_group] )
    )
Share of Stock-outs =
    DIVIDE ( [Stock-out Snapshots], [All-group Stock-out Snapshots] )
Latest Products in Stock-out =
    CALCULATE (
        COUNTROWS ( vw_inventory_status ),
        vw_inventory_status[inventory_status] = "stockout"
    )
Reorder Products =
    CALCULATE (
        COUNTROWS ( vw_inventory_status ),
        vw_inventory_status[inventory_status] = "reorder"
    )
```

**Visuals:** monthly stock-out rate trend; stock-out rate by SKU group; stacked
stock-out snapshot share by group; latest inventory status matrix by warehouse
and product; reorder/stock-out alert table.

**Slicers:** snapshot date, SKU group, category, warehouse, supplier, inventory
status.

**Wireframe:**

```text
+-------------------------------------------------------------------+
| Inventory           [Snapshot Date] [SKU Group] [Warehouse] [...] |
+--------------------+----------------------+-----------------------+
| Stock-out rate     | Stock-out snapshots  | Reorder products      |
+-------------------------------------------+-----------------------+
| Monthly stock-out trend                    | Rate by SKU group     |
+-------------------------------------------+-----------------------+
| Latest product / warehouse status and reorder alert table          |
+-------------------------------------------------------------------+
```

### Customer

**CSV files:** `vw_customer_rfm.csv`, `customer_segments.csv`,
`vw_orders_enriched.csv`, `dim_date.csv`.

**Model:** Use `vw_customer_rfm[customer_id]` as the one-side customer table
related to `customer_segments[customer_id]` and
`vw_orders_enriched[customer_id]`. Use Date to filter orders. Customer RFM is
computed across available fulfilled-order history and is not recomputed by the
date slicer.

**DAX measures:**

```DAX
Customers = DISTINCTCOUNT ( vw_customer_rfm[customer_id] )
Average Churn Risk = AVERAGE ( customer_segments[churn_risk_score] )
At-risk Customers =
    CALCULATE (
        DISTINCTCOUNT ( customer_segments[customer_id] ),
        customer_segments[segment_label] = "At Risk"
    )
Customers with Tickets =
    CALCULATE (
        DISTINCTCOUNT ( customer_segments[customer_id] ),
        customer_segments[ticket_count] > 0
    )
Average RFM Monetary = AVERAGE ( vw_customer_rfm[monetary] )
```

Check segment labels in the CSV before hard-coding further categories; filters
such as `"At Risk"` must match the exported label exactly.

**Visuals:** customer segment distribution; churn-risk distribution; customer
table with segment, ticket count, churn score, RFM scores and recency; frequency
vs monetary scatter; order frequency trend by customer segment.

**Slicers:** customer segment, churn-risk range, ticket-count range, order
date, RFM score.

**Wireframe:**

```text
+-------------------------------------------------------------------+
| Customer        [Segment] [Risk] [Tickets] [Order Date] [RFM]      |
+-----------------------+----------------------+--------------------+
| Customers             | Average churn risk   | At-risk customers  |
+----------------------------------------------+--------------------+
| Segment distribution  | Frequency x monetary                     |
+----------------------------------------------+--------------------+
| Customer risk / RFM / ticket detail table                         |
+-------------------------------------------------------------------+
```

### SLA & Delivery

**CSV files:** `vw_monthly_kpis.csv`, `vw_orders_enriched.csv`,
`vw_supplier_performance.csv`, `vw_warehouse_performance.csv`,
`order_features.csv`, `dim_date.csv`, `warehouses.csv`, `suppliers.csv`.

**Model:** Use Date on `vw_orders_enriched[order_date]` and order features.
Monthly KPI views remain at their month grain and use the monthly axis or
`TREATAS`. Warehouse and Supplier dimensions filter the directly related
tables. Supplier performance is an all-history supplier comparison; it is not
monthly.

**DAX measures:**

```DAX
Delivery Count = SUM ( vw_monthly_kpis[delivery_count] )
On-time Deliveries = SUM ( vw_monthly_kpis[on_time_deliveries] )
On-time Rate = DIVIDE ( [On-time Deliveries], [Delivery Count] )
Calendar On-time Rate =
    CALCULATE (
        [On-time Rate],
        TREATAS ( VALUES ( dim_date[YearMonth] ), vw_monthly_kpis[month] )
    )
Supplier Late Rate =
    AVERAGE ( vw_supplier_performance[supplier_late_rate] )
Supplier Gap vs Average =
    AVERAGE ( vw_supplier_performance[difference_from_average] )
Average Warehouse SLA Breach Rate =
    CALCULATE (
        AVERAGE ( vw_warehouse_performance[sla_breach_rate] ),
        TREATAS ( VALUES ( dim_date[YearMonth] ), vw_warehouse_performance[month] )
    )
Delivered Orders =
    CALCULATE (
        DISTINCTCOUNT ( vw_orders_enriched[order_id] ),
        NOT ISBLANK ( vw_orders_enriched[last_delivered_date] )
    )
Average Order Delivery Days =
    AVERAGEX (
        FILTER (
            vw_orders_enriched,
            NOT ISBLANK ( vw_orders_enriched[first_shipped_date] )
                && NOT ISBLANK ( vw_orders_enriched[last_delivered_date] )
        ),
        DATEDIFF (
            vw_orders_enriched[first_shipped_date],
            vw_orders_enriched[last_delivered_date],
            DAY
        )
    )
```

Warehouse SLA breach rates are pre-aggregated warehouse-month rates; averages
across multiple months are unweighted. The precise overall delivery on-time
rate is computed from monthly counts.

**Visuals:** on-time rate trend; warehouse SLA breach/processing matrix;
supplier late-rate ranking against the average; order delivery detail with
ship/promised/delivered dates; supplier gap conditional-format table.

**Slicers:** shipped month (monthly KPI), order date, warehouse, supplier,
delivery status, order status.

**Wireframe:**

```text
+--------------------------------------------------------------------+
| SLA & Delivery           [Month] [Warehouse] [Supplier] [Status]    |
+----------------+----------------+----------------+-----------------+
| On-time rate   | Deliveries     | Supplier late  | Avg delivery    |
+---------------------------------+----------------------------------+
| Monthly on-time trend           | Warehouse SLA / processing      |
+---------------------------------+----------------------------------+
| Supplier benchmark table        | Delivery-date detail             |
+--------------------------------------------------------------------+
```

### Root Cause

**CSV files:** `root_cause.csv`, `vw_monthly_kpis.csv`, `vw_warehouse_performance.csv`,
`vw_supplier_performance.csv`, `vw_inventory_status.csv`, `stat_tests.csv`.

**Model:** Keep `root_cause` disconnected. Each row is one reported factor and
repeats the overall KPI/period values. Use page/report filters on `kpi`,
`period_a`, and `period_b`; use `dimension` and `segment` as factor axes.
Other listed tables provide independent operational context and must not be
joined to root-cause factor rows.

**DAX measures:**

```DAX
Root Cause Factors = COUNTROWS ( root_cause )
KPI Change (pp) = MAX ( root_cause[change] ) * 100
Factor Contribution (pp) = SUM ( root_cause[contribution_pp] )
Unfulfilled Share = MAX ( root_cause[share_of_unfulfilled] )
Factor p-value = MIN ( root_cause[p_value] )
```

`root_cause` exports only the factors present in the latest JSON report (top
factors per dimension), not every segment. Its `share_of_drop` is the
contribution divided by the overall KPI change; contributions across distinct
dimensions are alternative decompositions and must not be summed together.
P-values are unadjusted statistical support, not proof of causality.

**Visuals:** overall fulfilment change cards; ranked contribution bar chart
with a dimension legend/small multiples; top-factor detail matrix with rates,
contribution, share of drop, unfulfilled share, p-value and ranks; independent
warehouse/supplier/inventory context panels.

**Slicers:** `period_a`, `period_b`, KPI, dimension, segment. The file contains
the latest saved comparison only; selecting a different period requires
regenerating root-cause JSON and rerunning the exporter.

**Wireframe:**

```text
+--------------------------------------------------------------------+
| Root Cause   [From] [To] [KPI] [Dimension] [Segment]                 |
+-------------------------+----------------------+-------------------+
| Period A KPI            | Period B KPI          | Change (pp)       |
+-----------------------------------------------+--------------------+
| Ranked contribution by segment (small multiples by dimension)        |
+-----------------------------------------------+--------------------+
| Factor evidence / p-value table             | Operational context  |
+--------------------------------------------------------------------+
```

## Visual design guide

### Colour palette

| Use | Colour | Hex |
|---|---|---|
| Page background | Cool near-white | `#F4F7FB` |
| Main text / title | Deep navy | `#14263D` |
| Primary brand / selection | Business blue | `#2563EB` |
| Positive / healthy | Teal green | `#0F9D83` |
| Neutral / watch | Amber | `#E6A23C` |
| Negative / breach / stock-out | Clear red | `#D64545` |
| Secondary series | Muted blue | `#76A9E8` |
| Card surface | White | `#FFFFFF` |
| Dividers / borders | Pale slate | `#D9E2EC` |

Use the same semantic colour consistently: green means healthy/improving, amber
means watch, and red means adverse. Do not use colour alone; add labels, icons,
or data labels for accessibility. Use a colour-blind-safe combination for
categorical series rather than encoding categories only as red/green.

### KPI cards

- White surface, thin pale-slate border, 6–10 px corner radius, subtle shadow.
- Large value (28–36 pt), concise label (10–12 pt), optional prior-period
  comparison below.
- Format rates as percentages, monetary values as currency, counts with
  thousands separators, and changes with explicit `pp` for percentage-point
  movement.
- Include a visible date/grain subtitle (for example, “Order month” or “Latest
  inventory snapshot”).
- Use green/amber/red status only when a documented threshold exists; otherwise
  display neutral blue.

### Page naming and navigation

Use these exact page/tab names and order:

1. `01 CEO Overview`
2. `02 Operations`
3. `03 Inventory`
4. `04 Customer`
5. `05 SLA & Delivery`
6. `06 Root Cause`

Keep titles, slicer placement, margins, page background, and card styling
consistent across all pages. Place the page title at top-left and common
slicers across the top-right/top row. Use descriptive visual titles with the
measure and grain, and avoid unlabeled acronyms.
