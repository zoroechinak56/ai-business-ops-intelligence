CREATE VIEW vw_warehouse_performance AS
SELECT
    w.warehouse_id,
    w.warehouse_name,
    strftime('%Y-%m', o.order_date) AS month,
    COUNT(o.order_id) AS order_count,
    AVG(julianday(d.shipped_date) - julianday(o.order_date))
        AS avg_processing_days,
    SUM(
        CASE
            WHEN d.delivered_date IS NULL
              OR d.delivered_date > d.promised_delivery_date
            THEN 1 ELSE 0
        END
    ) AS sla_breach_count,
    1.0 * SUM(
        CASE
            WHEN d.delivered_date IS NULL
              OR d.delivered_date > d.promised_delivery_date
            THEN 1 ELSE 0
        END
    ) / NULLIF(COUNT(d.delivery_id), 0) AS sla_breach_rate
FROM warehouses AS w
LEFT JOIN orders AS o ON o.warehouse_id = w.warehouse_id
LEFT JOIN deliveries AS d ON d.order_id = o.order_id
GROUP BY w.warehouse_id, w.warehouse_name, strftime('%Y-%m', o.order_date);
