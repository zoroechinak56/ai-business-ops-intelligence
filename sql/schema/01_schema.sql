-- Portable source schema for PostgreSQL and SQLite.
-- Rebuild drops dependent tables first, then creates them in dependency order.
DROP TABLE IF EXISTS support_tickets;
DROP TABLE IF EXISTS deliveries;
DROP TABLE IF EXISTS order_items;
DROP TABLE IF EXISTS inventory_snapshots;
DROP TABLE IF EXISTS orders;
DROP TABLE IF EXISTS products;
DROP TABLE IF EXISTS employees;
DROP TABLE IF EXISTS customers;
DROP TABLE IF EXISTS warehouses;
DROP TABLE IF EXISTS suppliers;
DROP TABLE IF EXISTS dim_date;

CREATE TABLE suppliers (
    supplier_id TEXT PRIMARY KEY,
    supplier_name TEXT NOT NULL,
    contact_email TEXT NOT NULL,
    country TEXT NOT NULL,
    supplier_tier TEXT NOT NULL
);

CREATE TABLE warehouses (
    warehouse_id TEXT PRIMARY KEY,
    warehouse_name TEXT NOT NULL,
    city TEXT NOT NULL,
    region TEXT NOT NULL,
    capacity_units INTEGER NOT NULL CHECK (capacity_units >= 0)
);

CREATE TABLE customers (
    customer_id TEXT PRIMARY KEY,
    customer_name TEXT NOT NULL,
    email TEXT NOT NULL,
    phone TEXT NOT NULL,
    city TEXT NOT NULL,
    state TEXT NOT NULL,
    signup_date DATE NOT NULL
        CHECK (signup_date >= '1900-01-01'),
    customer_segment TEXT NOT NULL
);

CREATE TABLE employees (
    employee_id TEXT PRIMARY KEY,
    employee_name TEXT NOT NULL,
    email TEXT NOT NULL,
    phone TEXT NOT NULL,
    role TEXT NOT NULL,
    hire_date DATE NOT NULL
        CHECK (hire_date >= '1900-01-01')
);

CREATE TABLE products (
    product_id TEXT PRIMARY KEY,
    product_name TEXT NOT NULL,
    sku TEXT NOT NULL,
    sku_group TEXT NOT NULL,
    category TEXT NOT NULL,
    supplier_id TEXT NOT NULL REFERENCES suppliers(supplier_id),
    unit_price NUMERIC(12, 2) NOT NULL CHECK (unit_price >= 0),
    description TEXT NOT NULL
);

CREATE TABLE orders (
    order_id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL REFERENCES customers(customer_id),
    warehouse_id TEXT NOT NULL REFERENCES warehouses(warehouse_id),
    employee_id TEXT NOT NULL REFERENCES employees(employee_id),
    order_date DATE NOT NULL
        CHECK (order_date >= '1900-01-01'),
    order_notes TEXT NOT NULL,
    order_status TEXT NOT NULL
);

CREATE TABLE order_items (
    order_item_id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL REFERENCES orders(order_id),
    product_id TEXT NOT NULL REFERENCES products(product_id),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    unit_price NUMERIC(12, 2) NOT NULL CHECK (unit_price >= 0),
    discount_code TEXT NOT NULL
);

CREATE TABLE deliveries (
    delivery_id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL REFERENCES orders(order_id),
    supplier_id TEXT NOT NULL REFERENCES suppliers(supplier_id),
    warehouse_id TEXT NOT NULL REFERENCES warehouses(warehouse_id),
    shipped_date DATE NOT NULL,
    delivered_date DATE,
    promised_delivery_date DATE NOT NULL,
    supplier_late BOOLEAN NOT NULL,
    delivery_status TEXT NOT NULL,
    tracking_number TEXT NOT NULL,
    CHECK (delivered_date IS NULL OR delivered_date >= shipped_date)
);

CREATE TABLE inventory_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    snapshot_date DATE NOT NULL,
    product_id TEXT NOT NULL REFERENCES products(product_id),
    warehouse_id TEXT NOT NULL REFERENCES warehouses(warehouse_id),
    supplier_id TEXT NOT NULL REFERENCES suppliers(supplier_id),
    quantity_on_hand INTEGER NOT NULL CHECK (quantity_on_hand >= 0),
    reorder_point INTEGER NOT NULL CHECK (reorder_point >= 0),
    stockout_flag BOOLEAN NOT NULL,
    notes TEXT NOT NULL
);

CREATE TABLE support_tickets (
    ticket_id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL REFERENCES customers(customer_id),
    ticket_date DATE NOT NULL,
    issue_type TEXT NOT NULL,
    priority TEXT NOT NULL,
    resolution_status TEXT NOT NULL,
    resolution_notes TEXT NOT NULL
);

CREATE INDEX idx_products_supplier_id ON products(supplier_id);
CREATE INDEX idx_orders_customer_id ON orders(customer_id);
CREATE INDEX idx_orders_warehouse_id ON orders(warehouse_id);
CREATE INDEX idx_orders_employee_id ON orders(employee_id);
CREATE INDEX idx_orders_order_date ON orders(order_date);
CREATE INDEX idx_order_items_order_id ON order_items(order_id);
CREATE INDEX idx_order_items_product_id ON order_items(product_id);
CREATE INDEX idx_deliveries_order_id ON deliveries(order_id);
CREATE INDEX idx_deliveries_supplier_id ON deliveries(supplier_id);
CREATE INDEX idx_deliveries_warehouse_id ON deliveries(warehouse_id);
CREATE INDEX idx_deliveries_shipped_date ON deliveries(shipped_date);
CREATE INDEX idx_deliveries_delivered_date ON deliveries(delivered_date);
CREATE INDEX idx_deliveries_promised_delivery_date
    ON deliveries(promised_delivery_date);
CREATE INDEX idx_inventory_product_id ON inventory_snapshots(product_id);
CREATE INDEX idx_inventory_warehouse_id ON inventory_snapshots(warehouse_id);
CREATE INDEX idx_inventory_supplier_id ON inventory_snapshots(supplier_id);
CREATE INDEX idx_inventory_snapshot_date ON inventory_snapshots(snapshot_date);
CREATE INDEX idx_support_tickets_customer_id ON support_tickets(customer_id);
CREATE INDEX idx_support_tickets_ticket_date ON support_tickets(ticket_date);
