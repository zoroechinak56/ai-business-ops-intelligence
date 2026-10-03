-- The rebuild module populates this calendar for 2025-01-01 through 2025-06-30.
-- week uses ISO-8601 week numbers; weekday is Monday=1 through Sunday=7.
CREATE TABLE dim_date (
    date DATE PRIMARY KEY,
    year INTEGER NOT NULL,
    month INTEGER NOT NULL CHECK (month BETWEEN 1 AND 12),
    week INTEGER NOT NULL CHECK (week BETWEEN 1 AND 53),
    weekday INTEGER NOT NULL CHECK (weekday BETWEEN 1 AND 7),
    is_weekend BOOLEAN NOT NULL
);

CREATE INDEX idx_dim_date_year_month ON dim_date(year, month);
