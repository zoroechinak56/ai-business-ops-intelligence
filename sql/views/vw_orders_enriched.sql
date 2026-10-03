CREATE VIEW vw_orders_enriched AS
WITH item_totals AS (
    SELECT
        order_id,
        COUNT(*) AS line_count,
        SUM(quantity) AS units,
        SUM(quantity * unit_price) AS sales_value
    FROM order_items
    GROUP BY order_id
),
delivery_summary AS (
    SELECT
        order_id,
        MIN(shipped_date) AS first_shipped_date,
        MAX(delivered_date) AS last_delivered_date,
        MIN(promised_delivery_date) AS promised_delivery_date,
        MAX(supplier_id) AS supplier_id,
        MAX(delivery_status) AS delivery_status
    FROM deliveries
    GROUP BY order_id
)
SELECT
    o.order_id,
    o.order_date,
    strftime('%Y-%m', o.order_date) AS order_month,
    o.order_status,
    o.customer_id,
    c.customer_name,
    c.customer_segment,
    o.warehouse_id,
    w.warehouse_name,
    o.employee_id,
    COALESCE(i.line_count, 0) AS line_count,
    COALESCE(i.units, 0) AS units,
    CASE
        WHEN o.order_status = 'fulfilled' THEN COALESCE(i.sales_value, 0)
        ELSE 0
    END AS sales_value,
    d.first_shipped_date,
    d.last_delivered_date,
    d.promised_delivery_date,
    d.supplier_id,
    d.delivery_status
FROM orders AS o
JOIN customers AS c ON c.customer_id = o.customer_id
JOIN warehouses AS w ON w.warehouse_id = o.warehouse_id
LEFT JOIN item_totals AS i ON i.order_id = o.order_id
LEFT JOIN delivery_summary AS d ON d.order_id = o.order_id;
