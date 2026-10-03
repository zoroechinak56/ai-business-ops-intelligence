CREATE VIEW vw_customer_rfm AS
WITH order_value AS (
    SELECT
        o.order_id,
        o.customer_id,
        o.order_date,
        SUM(oi.quantity * oi.unit_price) AS order_monetary_value
    FROM orders AS o
    LEFT JOIN order_items AS oi ON oi.order_id = o.order_id
    WHERE o.order_status = 'fulfilled'
    GROUP BY o.order_id, o.customer_id, o.order_date
),
customer_rfm AS (
    SELECT
        c.customer_id,
        c.customer_name,
        c.customer_segment,
        MIN(
            julianday(
                (SELECT MAX(order_date) FROM orders
                 WHERE order_status = 'fulfilled')
            )
            - julianday(v.order_date)
        ) AS recency_days,
        COUNT(DISTINCT v.order_id) AS frequency,
        COALESCE(SUM(v.order_monetary_value), 0) AS monetary
    FROM customers AS c
    LEFT JOIN order_value AS v ON v.customer_id = c.customer_id
    GROUP BY c.customer_id, c.customer_name, c.customer_segment
)
SELECT
    customer_id,
    customer_name,
    customer_segment,
    COALESCE(recency_days, 99999) AS recency_days,
    frequency,
    monetary,
    NTILE(5) OVER (
        ORDER BY COALESCE(recency_days, 99999) ASC, customer_id
    )
        AS recency_score,
    NTILE(5) OVER (ORDER BY frequency DESC, customer_id) AS frequency_score,
    NTILE(5) OVER (ORDER BY monetary DESC, customer_id) AS monetary_score
FROM customer_rfm;
