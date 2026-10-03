-- Business question: What share of deliveries meet the promise date each month by warehouse?
SELECT
    strftime('%Y-%m', shipped_date) AS month,
    warehouse_id,
    COUNT(*) AS delivery_count,
    SUM(
        CASE
            WHEN delivered_date IS NOT NULL
             AND delivered_date <= promised_delivery_date
            THEN 1 ELSE 0
        END
    ) AS on_time_deliveries,
    1.0 * SUM(
        CASE
            WHEN delivered_date IS NOT NULL
             AND delivered_date <= promised_delivery_date
            THEN 1 ELSE 0
        END
    ) / NULLIF(COUNT(*), 0) AS on_time_rate
FROM deliveries
GROUP BY strftime('%Y-%m', shipped_date), warehouse_id
ORDER BY month, warehouse_id;
