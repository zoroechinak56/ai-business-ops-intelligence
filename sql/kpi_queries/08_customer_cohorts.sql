-- Business question: How many customers from each signup-month cohort remain active in later order months?
WITH customer_cohorts AS (
    SELECT
        customer_id,
        strftime('%Y-%m', signup_date) AS cohort_month
    FROM customers
),
cohort_sizes AS (
    SELECT cohort_month, COUNT(*) AS cohort_customers
    FROM customer_cohorts
    GROUP BY cohort_month
),
monthly_activity AS (
    SELECT DISTINCT
        customer_id,
        strftime('%Y-%m', order_date) AS activity_month
    FROM orders
),
retained AS (
    SELECT
        c.cohort_month,
        a.activity_month,
        COUNT(DISTINCT a.customer_id) AS active_customers
    FROM customer_cohorts AS c
    JOIN monthly_activity AS a ON a.customer_id = c.customer_id
    WHERE a.activity_month >= c.cohort_month
    GROUP BY c.cohort_month, a.activity_month
)
SELECT
    r.cohort_month,
    r.activity_month,
    CAST(
        (CAST(substr(r.activity_month, 1, 4) AS INTEGER)
         - CAST(substr(r.cohort_month, 1, 4) AS INTEGER)) * 12
        + CAST(substr(r.activity_month, 6, 2) AS INTEGER)
        - CAST(substr(r.cohort_month, 6, 2) AS INTEGER)
        AS INTEGER
    ) AS months_since_signup,
    s.cohort_customers,
    r.active_customers,
    1.0 * r.active_customers / NULLIF(s.cohort_customers, 0)
        AS retention_rate
FROM retained AS r
JOIN cohort_sizes AS s ON s.cohort_month = r.cohort_month
ORDER BY r.cohort_month, r.activity_month;
