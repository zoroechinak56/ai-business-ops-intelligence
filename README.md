# AI-Powered Business Operations Intelligence & Automation Platform

## Business Problem

A mid-size e-commerce and distribution company stores orders, customers, inventory, suppliers, employees, deliveries, and support tickets in fragmented spreadsheets. Management needs a reliable way to identify delay drivers, customer risk, warehouse inefficiency, SLA breaches, and inventory problems, then turn evidence into auditable action.

## Architecture

```text
Raw data
   -> Validation
   -> SQL database
   -> Python analytics
   -> KPI / statistical layer
   -> Power BI
   -> AI reasoning layer
   -> Business recommendation
   -> n8n automation
   -> Action + audit log
```

## Repository Structure

```text
data/raw/                 Immutable source files
data/processed/           Validated and transformed artifacts
sql/schema/               Database schema
sql/views/                Reusable analytical views
sql/kpi_queries/          KPI, cohort, ranking, and time-based queries
src/ingestion/            Source loading
src/validation/           Input validation
src/cleaning/             Standardization and cleaning
src/analytics/            Features, statistics, and root-cause analysis
src/ai_layer/             Evidence-grounded findings and recommendations
tests/                    Automated tests
notebooks/                Exploratory analysis and demonstrations
n8n/                      Workflow exports
powerbi/                  Power BI project artifacts
docs/                     Architecture and KPI definitions
```

## Setup

Use Python 3.11 and PowerShell from the project root:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python -m pytest
```

Set local credentials and connection details in `.env`. Never commit `.env`; use `.env.example` as the placeholder template.

Common tasks:

```powershell
python tasks.py setup
python tasks.py test
python tasks.py lint
python tasks.py clean
```

## Roadmap

- [x] Step 1a: Create the workspace and repository structure.
- [x] Step 1b: Add dependency, configuration, logging, task, test, and documentation foundations.
- [ ] Step 2: Generate reproducible synthetic source data with intentional operational issues.
- [ ] Step 3: Define and test source-data validation rules.
- [ ] Step 4: Create the SQL database schema and deterministic database rebuild command.
- [ ] Step 5: Implement ingestion, cleaning, and standardized database loading.
- [ ] Step 6: Add SQL views and KPI, cohort, ranking, and time-based queries.
- [ ] Step 7: Build Python feature engineering, segmentation, and anomaly analysis.
- [ ] Step 8: Add statistical/root-cause analysis and tests for validation and KPI logic.
- [ ] Step 9: Define KPI formulas, grains, and owners; build CEO Overview, Operations, Inventory, Customer, SLA & Delivery, and Root Cause Power BI dashboards.
- [ ] Step 10: Produce evidence-grounded AI findings, evidence, and recommendations.
- [ ] Step 11: Build n8n validation, execution, alerting, reporting, and audit workflow.
- [ ] Step 12: Document operations and verify the full reproducible end-to-end flow.

## Initial Git Commit

After reviewing the scaffold, initialize the repository and create the requested first commit:

```powershell
git init
git add .
git commit -m "chore: project scaffold"
```