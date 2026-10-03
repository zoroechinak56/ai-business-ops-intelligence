-- Business question: How is monthly order fulfilment changing over time?
WITH monthly_fulfilment AS (
    SELECT
        strftime('%Y-%m', order_date) AS month,
        COUNT(*) AS total_orders,
        SUM(CASE WHEN order_status = 'fulfilled' THEN 1 ELSE 0 END)
            AS fulfilled_orders
    FROM orders
    GROUP BY strftime('%Y-%m', order_date)
)
SELECT
    month,
    total_orders,
    fulfilled_orders,
    1.0 * fulfilled_orders / NULLIF(total_orders, 0) AS fulfilment_rate,
    fulfilment_rate - LAG(fulfilment_rate) OVER (ORDER BY month)
        AS mom_change
FROM (
    SELECT
        month,
        total_orders,
        fulfilled_orders,
        1.0 * fulfilled_orders / NULLIF(total_orders, 0) AS fulfilment_rate
    FROM monthly_fulfilment
)
ORDER BY month;
