-- Business question: How many tickets are received by issue category and priority, and what is resolution time?
-- resolution_time_days is NULL because source tickets have no resolved timestamp.
SELECT
    issue_type AS category,
    priority,
    COUNT(*) AS ticket_count,
    SUM(CASE WHEN resolution_status = 'resolved' THEN 1 ELSE 0 END)
        AS resolved_ticket_count,
    CAST(NULL AS REAL) AS avg_resolution_time_days
FROM support_tickets
GROUP BY issue_type, priority
ORDER BY category, priority;
