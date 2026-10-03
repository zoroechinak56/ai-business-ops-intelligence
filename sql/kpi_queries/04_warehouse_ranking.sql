-- Business question: Which warehouses process orders most slowly and have the most delays?
WITH warehouse_metrics AS (
    SELECT
        o.warehouse_id,
        COUNT(*) AS order_count,
        AVG(julianday(d.shipped_date) - julianday(o.order_date))
            AS avg_processing_days,
        AVG(
            CASE
                WHEN d.delivered_date IS NULL
                  OR d.delivered_date > d.promised_delivery_date
                THEN 1.0 ELSE 0.0
            END
        ) AS delay_rate
    FROM orders AS o
    JOIN deliveries AS d ON d.order_id = o.order_id
    GROUP BY o.warehouse_id
)
SELECT
    warehouse_id,
    order_count,
    avg_processing_days,
    delay_rate,
    RANK() OVER (ORDER BY avg_processing_days DESC) AS processing_rank,
    DENSE_RANK() OVER (ORDER BY delay_rate DESC) AS delay_rank
FROM warehouse_metrics
ORDER BY processing_rank, delay_rank, warehouse_id;
