CREATE VIEW vw_inventory_status AS
WITH ranked_snapshots AS (
    SELECT
        i.snapshot_id,
        i.snapshot_date,
        i.product_id,
        i.warehouse_id,
        i.supplier_id,
        i.quantity_on_hand,
        i.reorder_point,
        i.stockout_flag,
        ROW_NUMBER() OVER (
            PARTITION BY i.product_id, i.warehouse_id
            ORDER BY i.snapshot_date DESC, i.snapshot_id DESC
        ) AS snapshot_rank
    FROM inventory_snapshots AS i
)
SELECT
    i.snapshot_id,
    i.snapshot_date,
    i.product_id,
    p.product_name,
    p.sku_group,
    p.category,
    i.warehouse_id,
    w.warehouse_name,
    i.supplier_id,
    s.supplier_name,
    i.quantity_on_hand,
    i.reorder_point,
    i.stockout_flag,
    CASE
        WHEN i.stockout_flag OR i.quantity_on_hand = 0 THEN 'stockout'
        WHEN i.quantity_on_hand <= i.reorder_point THEN 'reorder'
        ELSE 'healthy'
    END AS inventory_status
FROM ranked_snapshots AS i
JOIN products AS p ON p.product_id = i.product_id
JOIN warehouses AS w ON w.warehouse_id = i.warehouse_id
JOIN suppliers AS s ON s.supplier_id = i.supplier_id
WHERE i.snapshot_rank = 1;
