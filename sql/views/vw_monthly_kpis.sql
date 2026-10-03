CREATE VIEW vw_monthly_kpis AS
WITH order_metrics AS (
    SELECT
        strftime('%Y-%m', order_date) AS month,
        COUNT(*) AS order_count,
        SUM(CASE WHEN order_status = 'fulfilled' THEN 1 ELSE 0 END)
            AS fulfilled_orders
    FROM orders
    GROUP BY strftime('%Y-%m', order_date)
),
sales_metrics AS (
    SELECT
        strftime('%Y-%m', o.order_date) AS month,
        SUM(oi.quantity * oi.unit_price) AS sales_value
    FROM orders AS o
    JOIN order_items AS oi ON oi.order_id = o.order_id
    WHERE o.order_status = 'fulfilled'
    GROUP BY strftime('%Y-%m', o.order_date)
),
delivery_metrics AS (
    SELECT
        strftime('%Y-%m', shipped_date) AS month,
        COUNT(*) AS delivery_count,
        SUM(
            CASE
                WHEN delivered_date IS NOT NULL
                 AND delivered_date <= promised_delivery_date
                THEN 1 ELSE 0
            END
        ) AS on_time_deliveries
    FROM deliveries
    GROUP BY strftime('%Y-%m', shipped_date)
)
SELECT
    o.month,
    o.order_count,
    o.fulfilled_orders,
    1.0 * o.fulfilled_orders / NULLIF(o.order_count, 0) AS fulfilment_rate,
    COALESCE(s.sales_value, 0) AS sales_value,
    COALESCE(d.delivery_count, 0) AS delivery_count,
    COALESCE(d.on_time_deliveries, 0) AS on_time_deliveries,
    1.0 * d.on_time_deliveries / NULLIF(d.delivery_count, 0) AS on_time_rate
FROM order_metrics AS o
LEFT JOIN sales_metrics AS s ON s.month = o.month
LEFT JOIN delivery_metrics AS d ON d.month = o.month;
