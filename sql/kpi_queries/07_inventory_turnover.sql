-- Business question: What is the six-month sales-value turnover against average inventory value?
-- Sales value is used because purchase/COGS amounts are not present in the source tables.
WITH inventory AS (
    SELECT
        i.product_id,
        i.warehouse_id,
        AVG(i.quantity_on_hand) AS avg_quantity_on_hand
    FROM inventory_snapshots AS i
    GROUP BY i.product_id, i.warehouse_id
),
sales AS (
    SELECT
        oi.product_id,
        o.warehouse_id,
        SUM(oi.quantity * oi.unit_price) AS sales_value,
        SUM(oi.quantity) AS units_sold
    FROM order_items AS oi
    JOIN orders AS o ON o.order_id = oi.order_id
    WHERE o.order_status = 'fulfilled'
    GROUP BY oi.product_id, o.warehouse_id
)
SELECT
    p.product_id,
    p.product_name,
    i.warehouse_id,
    COALESCE(s.units_sold, 0) AS units_sold,
    COALESCE(s.sales_value, 0) AS sales_value,
    i.avg_quantity_on_hand,
    i.avg_quantity_on_hand * p.unit_price AS avg_inventory_value,
    COALESCE(s.sales_value, 0)
        / NULLIF(i.avg_quantity_on_hand * p.unit_price, 0) AS inventory_turnover
FROM inventory AS i
JOIN products AS p ON p.product_id = i.product_id
LEFT JOIN sales AS s
    ON s.product_id = i.product_id
   AND s.warehouse_id = i.warehouse_id
ORDER BY inventory_turnover DESC, p.product_id, i.warehouse_id;
