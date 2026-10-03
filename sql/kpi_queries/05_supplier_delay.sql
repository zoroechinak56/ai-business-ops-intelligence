-- Business question: How does each supplier's late-delivery rate compare with the supplier average?
WITH supplier_rates AS (
    SELECT
        s.supplier_id,
        s.supplier_name,
        COUNT(d.delivery_id) AS delivery_count,
        AVG(CASE WHEN d.supplier_late THEN 1.0 ELSE 0.0 END)
            AS supplier_late_rate
    FROM suppliers AS s
    LEFT JOIN deliveries AS d ON d.supplier_id = s.supplier_id
    GROUP BY s.supplier_id, s.supplier_name
)
SELECT
    supplier_id,
    supplier_name,
    delivery_count,
    supplier_late_rate,
    AVG(supplier_late_rate) OVER () AS average_supplier_late_rate,
    supplier_late_rate - AVG(supplier_late_rate) OVER ()
        AS difference_from_average
FROM supplier_rates
ORDER BY supplier_late_rate DESC, supplier_id;
