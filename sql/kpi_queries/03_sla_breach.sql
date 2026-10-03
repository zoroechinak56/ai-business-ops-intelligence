-- Business question: Where and when are delivery SLAs breached by warehouse and supplier?
-- Supplier is the available delivery partner dimension; this source has no carrier field.
SELECT
    strftime('%Y-%m', d.shipped_date) AS month,
    d.warehouse_id,
    d.supplier_id AS supplier_id,
    s.supplier_name,
    COUNT(*) AS delivery_count,
    SUM(
        CASE
            WHEN d.delivered_date IS NULL
              OR d.delivered_date > d.promised_delivery_date
            THEN 1 ELSE 0
        END
    ) AS breached_deliveries,
    1.0 * SUM(
        CASE
            WHEN d.delivered_date IS NULL
              OR d.delivered_date > d.promised_delivery_date
            THEN 1 ELSE 0
        END
    ) / NULLIF(COUNT(*), 0) AS sla_breach_rate
FROM deliveries AS d
JOIN suppliers AS s ON s.supplier_id = d.supplier_id
GROUP BY
    strftime('%Y-%m', d.shipped_date),
    d.warehouse_id,
    d.supplier_id,
    s.supplier_name
ORDER BY month, warehouse_id, supplier_id;
