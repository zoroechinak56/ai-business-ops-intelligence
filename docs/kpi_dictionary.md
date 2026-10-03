# KPI Dictionary

The SQL layer is in `sql/kpi_queries/` and the Power BI-facing views are in
`sql/views/`. Ratios are returned as fractions from 0 to 1; format as a
percentage in a consuming report. Queries use SQLite-compatible SQL and run
against the source-grain schema.

| Query / KPI | Formula and interpretation | Grain | Owner | Notes |
|---|---|---|---|---|
| `01_fulfilment_rate` — Fulfilment rate | Fulfilled orders / all orders; month-over-month change is current monthly fraction minus the preceding month's fraction (`LAG`). | Order month | Operations | An order is fulfilled when `orders.order_status = 'fulfilled'`. |
| `02_on_time_delivery` — On-time delivery rate | Deliveries delivered on or before promised date / all deliveries. Undelivered rows count as not on time. | Shipped month × warehouse | Logistics | Uses delivery `shipped_date` as the reporting month. |
| `03_sla_breach` — SLA breach rate | Deliveries with no delivered date or delivered after promised date / all deliveries. | Shipped month × warehouse × supplier | Logistics | Source data has no carrier identifier; supplier is the available partner grouping and is not represented as a carrier. |
| `04_warehouse_ranking` — Warehouse processing and delay ranks | Processing time is average `julianday(shipped_date) - julianday(order_date)`; delay rate is deliveries missing the promise or delivered after it / deliveries. `RANK` ranks processing time and `DENSE_RANK` ranks delay rate, descending. | Warehouse, across available history | Warehouse Operations | Average order-to-ship days is the processing-time proxy. |
| `05_supplier_delay` — Supplier late-delivery rate | Rows with `supplier_late` / deliveries; compare each supplier with the unweighted mean of supplier-level rates. | Supplier | Procurement | Each supplier contributes equally to the comparison average, independent of delivery volume. |
| `06_stockout_by_sku_group` — Stock-out rate | Inventory snapshots flagged as stock-outs / all snapshots. | Snapshot month × SKU group | Inventory Planning | A snapshot is the measurement unit, not a distinct product-day count. |
| `07_inventory_turnover` — Sales-value inventory turnover | Fulfilled-order quantity × order-item unit price / average snapshot quantity on hand × current product unit price. | Product × warehouse across available snapshot/order history | Inventory Planning | Only fulfilled orders count as sales. Sales value is a turnover proxy because acquisition cost/COGS is not available. |
| `08_customer_cohorts` — Cohort retention | Distinct customers in a signup-month cohort with an order in an activity month / customers in the signup cohort. | Signup month × activity month | Customer Success | Cohort activity begins at signup month; month offset is included. |
| `09_customer_rfm` — RFM | Recency is days since the customer's most recent fulfilled order relative to the latest fulfilled order in the data; frequency is distinct fulfilled orders; monetary is fulfilled-order quantity × unit price. Scores use quintiles (`NTILE(5)`), with higher scores assigned to more recent/frequent/monetary customers. | Customer | Customer Success | Customers with no fulfilled orders receive a sentinel recency of 99,999 days and zero frequency/monetary value. |
| `10_support_tickets` — Ticket volume and resolution | Ticket count and resolved-ticket count grouped by issue category and priority. Average resolution days is `NULL`. | Issue type × priority | Customer Support | The source has no resolution timestamp, so elapsed resolution time cannot be computed without adding source data. |

## Power BI views

| View | Grain and contents |
|---|---|
| `vw_orders_enriched` | One row per order, with customer/warehouse dimensions, aggregated item units/value, and delivery summary. |
| `vw_monthly_kpis` | One row per order month, with fulfilment, sales value, and delivery on-time measures. |
| `vw_warehouse_performance` | Warehouse × order month, with order counts, average processing days, and SLA breach measures. |
| `vw_supplier_performance` | One row per supplier, with late rate and comparison to the average supplier rate. |
| `vw_inventory_status` | Latest inventory snapshot per product × warehouse, with product/supplier details and stockout/reorder/healthy status. |
| `vw_customer_rfm` | One row per customer, with recency, frequency, monetary value, and quintile scores. |
