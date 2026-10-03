-- Business question: Which SKU groups experience the most stock-outs each month?
SELECT
    strftime('%Y-%m', i.snapshot_date) AS month,
    p.sku_group,
    COUNT(*) AS snapshot_count,
    SUM(CASE WHEN i.stockout_flag THEN 1 ELSE 0 END) AS stockout_snapshots,
    1.0 * SUM(CASE WHEN i.stockout_flag THEN 1 ELSE 0 END)
        / NULLIF(COUNT(*), 0) AS stockout_rate
FROM inventory_snapshots AS i
JOIN products AS p ON p.product_id = i.product_id
GROUP BY strftime('%Y-%m', i.snapshot_date), p.sku_group
ORDER BY month, p.sku_group;
