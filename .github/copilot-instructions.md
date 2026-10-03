# Project Instructions

## Project
Build the portfolio project "AI-Powered Business Operations Intelligence & Automation Platform" for a mid-size e-commerce and distribution company.

## Required Architecture
Follow this data flow strictly:

Raw data -> Validation -> SQL database -> Python analytics -> KPI/statistical layer -> Power BI -> AI reasoning layer -> Business recommendation -> n8n automation -> Action + audit log

Use PostgreSQL, with SQLite only as an acceptable fallback. The Python target is 3.11 and the planned stack is pandas, numpy, scipy, scikit-learn, SQLAlchemy, pandera or Great Expectations, and pytest. Keep secrets and environment-specific settings in `.env`; never hard-code credentials or machine-specific paths.

## Repository Map
- `data/raw`: immutable synthetic source files
- `data/processed`: validated or transformed artifacts
- `sql/schema`, `sql/views`, `sql/kpi_queries`: database objects and analytics SQL
- `src/ingestion`, `src/validation`, `src/cleaning`, `src/analytics`, `src/ai_layer`: application modules
- `notebooks`: exploratory work and demonstrations
- `tests`: automated checks
- `n8n`: workflow exports
- `powerbi`: BI project artifacts
- `docs`: architecture, KPI definitions, and operating notes

## Working Rules
- Implement one user-approved step at a time. After completing a step, stop and state what the user should verify before proceeding.
- Explain the business reason briefly before presenting implementation changes.
- Use production-style Python: type hints, docstrings, logging, configuration from environment variables, and focused tests.
- Seed synthetic data deterministically and include discoverable operational problems such as a slow warehouse, unreliable supplier, SKU-group stock-outs, and dirty records.
- Every KPI must have a documented formula, grain, and owner in `docs`.
- The AI reasoning module may use only computed KPI and root-cause results as evidence. It must not invent figures and must return Finding, Evidence, and Recommendation.
- Keep database rebuilds reproducible from one command.
- Keep changes scoped to the current step and follow existing project conventions.
